"""Deterministic, integrity-checked job bundles for a read-only agent worker."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping
from uuid import uuid4

AgentRole = Literal["stage1", "stage2", "vpa_evidence", "joint_arbiter"]
_ROLE_FACT_DOMAINS: dict[AgentRole, frozenset[str] | None] = {
    "stage1": frozenset({"price", "indicator"}),
    "stage2": frozenset({"price", "indicator"}),
    "vpa_evidence": None,
    "joint_arbiter": None,
}
_RESOURCE_NAMES = (
    "request.json",
    "output_schema.json",
    "facts.json",
    "bars.json",
    "knowledge.json",
    "runtime.json",
    "budget.json",
)


class JobIntegrityError(RuntimeError):
    """A job is partial, malformed, or does not match its frozen digests."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_digest(value: str, name: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    return normalized


@dataclass(frozen=True)
class AgentJobBudget:
    max_evidence_calls: int = 8
    max_evidence_bytes: int = 65_536
    max_initial_context_bytes: int = 32_768
    max_wall_seconds: int = 180
    max_output_bytes: int = 65_536

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class FrozenAgentJobManifest:
    schema_version: int
    task_id: str
    request_id: str
    role: AgentRole
    attempt: int
    snapshot_hash: str
    cutoff_ts_open: int
    runtime_version: str
    resource_digests: Mapping[str, str]
    job_digest: str

    def as_dict(self, *, include_job_digest: bool = True) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "request_id": self.request_id,
            "role": self.role,
            "attempt": self.attempt,
            "snapshot_hash": self.snapshot_hash,
            "cutoff_ts_open": self.cutoff_ts_open,
            "runtime_version": self.runtime_version,
            "resource_digests": dict(self.resource_digests),
        }
        if include_job_digest:
            payload["job_digest"] = self.job_digest
        return payload


@dataclass(frozen=True)
class FrozenAgentJob:
    manifest: FrozenAgentJobManifest
    request: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    fact_projection: Mapping[str, Any]
    bars: tuple[Mapping[str, Any], ...]
    knowledge_documents: Mapping[str, str]
    runtime: Mapping[str, Any]
    budget: AgentJobBudget

    def resources(self) -> dict[str, Any]:
        return {
            "request.json": dict(self.request),
            "output_schema.json": dict(self.output_schema),
            "facts.json": dict(self.fact_projection),
            "bars.json": [dict(item) for item in self.bars],
            "knowledge.json": dict(self.knowledge_documents),
            "runtime.json": dict(self.runtime),
            "budget.json": asdict(self.budget),
        }


class FrozenAgentJobBuilder:
    """Build one role-safe bundle without acquiring data or interpreting facts."""

    def __init__(
        self,
        *,
        runtime_version: str,
        agents_text: str,
        skill_documents: Mapping[str, str],
    ) -> None:
        self._runtime_version = str(runtime_version or "").strip()
        self._agents_text = str(agents_text)
        self._skill_documents = {
            str(key): str(value) for key, value in skill_documents.items()
        }
        if not self._runtime_version:
            raise ValueError("runtime_version is required")

    def build(
        self,
        *,
        task_id: str,
        request_id: str,
        role: AgentRole,
        attempt: int,
        snapshot_hash: str,
        cutoff_ts_open: int,
        messages: list[dict[str, Any]],
        output_schema: Mapping[str, Any],
        fact_projection: Mapping[str, Any],
        bars: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        knowledge_documents: Mapping[str, str],
        allowed_knowledge_ids: tuple[str, ...] | list[str],
        budget: AgentJobBudget,
        backend_runtime_identity: Mapping[str, Any] | None = None,
    ) -> FrozenAgentJob:
        if role not in _ROLE_FACT_DOMAINS:
            raise ValueError(f"unsupported agent role: {role}")
        if int(attempt) <= 0:
            raise ValueError("attempt must be positive")
        snapshot = _require_digest(snapshot_hash, "snapshot_hash")
        cutoff = int(cutoff_ts_open)
        if cutoff < 0:
            raise ValueError("cutoff_ts_open must be non-negative")
        frozen_bars = [dict(item) for item in bars]
        if any(int(item.get("ts_open", 0)) > cutoff for item in frozen_bars):
            raise ValueError("bar timestamp exceeds frozen snapshot cutoff")
        bar_seqs = [int(item.get("seq", 0)) for item in frozen_bars]
        if any(seq <= 0 for seq in bar_seqs) or len(bar_seqs) != len(set(bar_seqs)):
            raise ValueError("bars require unique positive sequence numbers")

        facts = self._role_safe_facts(role, fact_projection, snapshot)
        allowed_ids = tuple(dict.fromkeys(str(item) for item in allowed_knowledge_ids))
        selected_knowledge: dict[str, str] = {}
        for knowledge_id in allowed_ids:
            if knowledge_id not in knowledge_documents:
                raise ValueError(f"unknown knowledge document: {knowledge_id}")
            selected_knowledge[knowledge_id] = str(knowledge_documents[knowledge_id])

        request = {
            "task_id": str(task_id),
            "request_id": str(request_id),
            "role": role,
            "attempt": int(attempt),
            "messages": messages,
        }
        runtime = {
            "runtime_version": self._runtime_version,
            "agents_text": self._agents_text,
            "skills": self._role_skills(role),
            "backend_runtime_identity": dict(backend_runtime_identity or {}),
        }
        resources = {
            "request.json": request,
            "output_schema.json": dict(output_schema),
            "facts.json": facts,
            "bars.json": frozen_bars,
            "knowledge.json": selected_knowledge,
            "runtime.json": runtime,
            "budget.json": asdict(budget),
        }
        resource_digests = {
            name: _sha256_bytes(_canonical_bytes(resources[name]))
            for name in _RESOURCE_NAMES
        }
        unsigned = {
            "schema_version": 1,
            "task_id": str(task_id),
            "request_id": str(request_id),
            "role": role,
            "attempt": int(attempt),
            "snapshot_hash": snapshot,
            "cutoff_ts_open": cutoff,
            "runtime_version": self._runtime_version,
            "resource_digests": resource_digests,
        }
        job_digest = _sha256_bytes(_canonical_bytes(unsigned))
        manifest = FrozenAgentJobManifest(
            **unsigned,
            job_digest=job_digest,
        )
        return FrozenAgentJob(
            manifest=manifest,
            request=MappingProxyType(request),
            output_schema=MappingProxyType(dict(output_schema)),
            fact_projection=MappingProxyType(facts),
            bars=tuple(MappingProxyType(item) for item in frozen_bars),
            knowledge_documents=MappingProxyType(selected_knowledge),
            runtime=MappingProxyType(runtime),
            budget=budget,
        )

    def _role_skills(self, role: AgentRole) -> dict[str, str]:
        try:
            return {role: self._skill_documents[role]}
        except KeyError as exc:
            raise ValueError(f"missing runtime skill for role: {role}") from exc

    @staticmethod
    def _role_safe_facts(
        role: AgentRole,
        projection: Mapping[str, Any],
        snapshot_hash: str,
    ) -> dict[str, Any]:
        projection_snapshot = str(projection.get("snapshot_hash") or "").lower()
        if projection_snapshot and projection_snapshot != snapshot_hash:
            raise ValueError("fact projection snapshot_hash does not match job snapshot")
        allowed_domains = _ROLE_FACT_DOMAINS[role]
        all_facts = [dict(item) for item in projection.get("facts", [])]
        facts = (
            all_facts
            if allowed_domains is None
            else [item for item in all_facts if str(item.get("domain")) in allowed_domains]
        )
        visible = {str(item.get("fact_id")) for item in facts}
        relations = [
            dict(item)
            for item in projection.get("relations", [])
            if str(item.get("source_id")) in visible
            and str(item.get("target_id")) in visible
        ]
        return {
            "catalog_id": projection.get("catalog_id"),
            "snapshot_hash": snapshot_hash,
            "facts": facts,
            "relations": relations,
        }


class FrozenAgentJobStore:
    """Atomically publish and verify immutable job directories."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def publish(self, job: FrozenAgentJob) -> Path:
        self._root.mkdir(parents=True, exist_ok=True)
        target = self._root / job.manifest.job_digest
        if target.exists():
            loaded = self.open(job.manifest.job_digest)
            if loaded.manifest.job_digest != job.manifest.job_digest:
                raise JobIntegrityError("existing job digest mismatch")
            return target
        temporary = self._root / f".{job.manifest.job_digest}.{uuid4().hex}.tmp"
        temporary.mkdir()
        try:
            for name, payload in job.resources().items():
                self._write_file(temporary / name, _canonical_bytes(payload))
            self._write_file(
                temporary / "manifest.json",
                _canonical_bytes(job.manifest.as_dict()),
            )
            if os.name == "posix":
                temporary.chmod(0o550)
            os.replace(temporary, target)
        except Exception:
            self._remove_partial(temporary)
            raise
        return target

    def open(self, job_digest: str) -> FrozenAgentJob:
        digest = _require_digest(job_digest, "job_digest")
        directory = self._root / digest
        manifest_path = directory / "manifest.json"
        if not directory.is_dir() or not manifest_path.is_file():
            raise JobIntegrityError("job is not atomically published")
        try:
            raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = self._parse_manifest(raw_manifest)
            if manifest.job_digest != digest:
                raise JobIntegrityError("job directory does not match manifest digest")
            resources: dict[str, Any] = {}
            for name in _RESOURCE_NAMES:
                path = directory / name
                encoded = path.read_bytes()
                expected = manifest.resource_digests.get(name)
                if expected is None or _sha256_bytes(encoded) != expected:
                    raise JobIntegrityError(f"resource digest mismatch: {name}")
                resources[name] = json.loads(encoded.decode("utf-8"))
            calculated = _sha256_bytes(
                _canonical_bytes(manifest.as_dict(include_job_digest=False))
            )
            if calculated != manifest.job_digest:
                raise JobIntegrityError("manifest digest mismatch")
            budget = AgentJobBudget(**resources["budget.json"])
            return FrozenAgentJob(
                manifest=manifest,
                request=MappingProxyType(resources["request.json"]),
                output_schema=MappingProxyType(resources["output_schema.json"]),
                fact_projection=MappingProxyType(resources["facts.json"]),
                bars=tuple(
                    MappingProxyType(item) for item in resources["bars.json"]
                ),
                knowledge_documents=MappingProxyType(resources["knowledge.json"]),
                runtime=MappingProxyType(resources["runtime.json"]),
                budget=budget,
            )
        except JobIntegrityError:
            raise
        except (
            KeyError,
            OSError,
            UnicodeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise JobIntegrityError(f"invalid frozen job: {exc}") from exc

    @staticmethod
    def _parse_manifest(raw: Any) -> FrozenAgentJobManifest:
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise JobIntegrityError("unsupported or missing job manifest")
        role = str(raw["role"])
        if role not in _ROLE_FACT_DOMAINS:
            raise JobIntegrityError("unsupported role in job manifest")
        resource_digests = {
            str(name): _require_digest(value, f"resource digest {name}")
            for name, value in dict(raw["resource_digests"]).items()
        }
        if set(resource_digests) != set(_RESOURCE_NAMES):
            raise JobIntegrityError("job resource set is incomplete")
        return FrozenAgentJobManifest(
            schema_version=1,
            task_id=str(raw["task_id"]),
            request_id=str(raw["request_id"]),
            role=role,  # type: ignore[arg-type]
            attempt=int(raw["attempt"]),
            snapshot_hash=_require_digest(raw["snapshot_hash"], "snapshot_hash"),
            cutoff_ts_open=int(raw["cutoff_ts_open"]),
            runtime_version=str(raw["runtime_version"]),
            resource_digests=MappingProxyType(resource_digests),
            job_digest=_require_digest(raw["job_digest"], "job_digest"),
        )

    @staticmethod
    def _write_file(path: Path, encoded: bytes) -> None:
        with path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            try:
                path.chmod(0o440)
            except OSError:
                pass

    @staticmethod
    def _remove_partial(directory: Path) -> None:
        if not directory.exists():
            return
        for path in directory.iterdir():
            try:
                path.chmod(0o600)
            except OSError:
                pass
            path.unlink(missing_ok=True)
        directory.rmdir()
