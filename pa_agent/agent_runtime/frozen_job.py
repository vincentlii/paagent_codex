"""Deterministic, integrity-checked job bundles for a read-only agent worker."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
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
_RESOURCE_NAMES_V1 = (
    "request.json",
    "output_schema.json",
    "facts.json",
    "bars.json",
    "knowledge.json",
    "runtime.json",
    "budget.json",
)
_RESOURCE_NAMES_V2 = (*_RESOURCE_NAMES_V1, "knowledge_manifest.json")
_RESOURCE_NAMES_V3 = (*_RESOURCE_NAMES_V2, "repair_findings.json")
_RESOURCE_NAMES = (*_RESOURCE_NAMES_V3, "core_context.json")


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
    max_evidence_response_bytes: int = 65_536
    max_evidence_bytes: int = 262_144
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
    model: str = ""
    reasoning_effort: str = ""
    profile_fingerprint: str = ""
    created_at_epoch: int = 0
    expires_at_epoch: int = 0
    acceptance_key: str = ""

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
        # Schema-1 jobs created before profile-bound execution did not contain
        # these keys.  Omitting an entirely empty identity preserves their
        # canonical digest while new jobs freeze all three controls.
        if self.model or self.reasoning_effort or self.profile_fingerprint:
            payload.update(
                {
                    "model": self.model,
                    "reasoning_effort": self.reasoning_effort,
                    "profile_fingerprint": self.profile_fingerprint,
                }
            )
        if self.schema_version >= 2:
            payload.update(
                {
                    "created_at_epoch": self.created_at_epoch,
                    "expires_at_epoch": self.expires_at_epoch,
                    "acceptance_key": self.acceptance_key,
                }
            )
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
    knowledge_documents: Mapping[str, Any]
    runtime: Mapping[str, Any]
    budget: AgentJobBudget
    knowledge_manifest: Mapping[str, Any] = field(default_factory=dict)
    repair_findings: Mapping[str, Any] = field(default_factory=dict)
    core_context: Mapping[str, Any] = field(default_factory=dict)

    def resources(self) -> dict[str, Any]:
        resources = {
            "request.json": dict(self.request),
            "output_schema.json": dict(self.output_schema),
            "facts.json": dict(self.fact_projection),
            "bars.json": [dict(item) for item in self.bars],
            "knowledge.json": dict(self.knowledge_documents),
            "runtime.json": dict(self.runtime),
            "budget.json": asdict(self.budget),
        }
        if self.manifest.schema_version >= 2:
            resources["knowledge_manifest.json"] = dict(self.knowledge_manifest)
        if self.manifest.schema_version >= 3:
            resources["repair_findings.json"] = dict(self.repair_findings)
        if self.manifest.schema_version >= 4:
            resources["core_context.json"] = dict(self.core_context)
        return resources


class FrozenAgentJobBuilder:
    """Build one role-safe bundle without acquiring data or interpreting facts."""

    def __init__(
        self,
        *,
        runtime_version: str,
        agents_text: str,
        skill_documents: Mapping[str, str],
        clock: Any = time.time,
        job_ttl_seconds: int = 900,
    ) -> None:
        self._runtime_version = str(runtime_version or "").strip()
        self._agents_text = str(agents_text)
        self._skill_documents = {
            str(key): str(value) for key, value in skill_documents.items()
        }
        self._clock = clock
        self._job_ttl_seconds = int(job_ttl_seconds)
        if not self._runtime_version:
            raise ValueError("runtime_version is required")
        if self._job_ttl_seconds <= 0:
            raise ValueError("job_ttl_seconds must be positive")

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
        knowledge_documents: Mapping[str, Any],
        allowed_knowledge_ids: tuple[str, ...] | list[str],
        budget: AgentJobBudget,
        backend_runtime_identity: Mapping[str, Any] | None = None,
        source_request_digest: str | None = None,
        knowledge_manifest: Mapping[str, Any] | None = None,
        repair_findings: Mapping[str, Any] | None = None,
        core_context: Mapping[str, Any] | None = None,
    ) -> FrozenAgentJob:
        if role not in _ROLE_FACT_DOMAINS:
            raise ValueError(f"unsupported agent role: {role}")
        if int(attempt) <= 0:
            raise ValueError("attempt must be positive")
        snapshot = _require_digest(snapshot_hash, "snapshot_hash")
        cutoff = int(cutoff_ts_open)
        if cutoff < 0:
            raise ValueError("cutoff_ts_open must be non-negative")
        source_bars = [dict(item) for item in bars]
        if any(int(item.get("ts_open", 0)) > cutoff for item in source_bars):
            raise ValueError("bar timestamp exceeds frozen snapshot cutoff")
        bar_seqs = [int(item.get("seq", 0)) for item in source_bars]
        if any(seq <= 0 for seq in bar_seqs) or len(bar_seqs) != len(set(bar_seqs)):
            raise ValueError("bars require unique positive sequence numbers")
        frozen_bars = self._role_safe_bars(role, source_bars)
        frozen_core = dict(core_context or {})
        self._validate_core_context(
            frozen_core,
            task_id=str(task_id),
            snapshot_hash=snapshot,
            cutoff_ts_open=cutoff,
        )

        facts = self._role_safe_facts(role, fact_projection, snapshot)
        allowed_ids = tuple(dict.fromkeys(str(item) for item in allowed_knowledge_ids))
        selected_knowledge: dict[str, Any] = {}
        for knowledge_id in allowed_ids:
            if knowledge_id not in knowledge_documents:
                raise ValueError(f"unknown knowledge document: {knowledge_id}")
            selected_knowledge[knowledge_id] = knowledge_documents[knowledge_id]

        request = {
            "task_id": str(task_id),
            "request_id": str(request_id),
            "role": role,
            "attempt": int(attempt),
            "messages": messages,
        }
        if source_request_digest is not None:
            request["source_request_digest"] = _require_digest(
                source_request_digest,
                "source_request_digest",
            )
        runtime_identity = dict(backend_runtime_identity or {})
        model = str(runtime_identity.get("model") or "").strip()
        reasoning_effort = str(
            runtime_identity.get("reasoning_effort") or ""
        ).strip()
        profile_fingerprint = str(
            runtime_identity.get("profile_fingerprint")
            or runtime_identity.get("analysis_profile_fingerprint")
            or ""
        ).strip().lower()
        if profile_fingerprint:
            _require_digest(profile_fingerprint, "profile_fingerprint")
        runtime = {
            "runtime_version": self._runtime_version,
            "agents_text": self._agents_text,
            "skills": self._role_skills(role, repair=bool(repair_findings)),
            "backend_runtime_identity": runtime_identity,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "profile_fingerprint": profile_fingerprint,
        }
        resources = {
            "request.json": request,
            "output_schema.json": dict(output_schema),
            "facts.json": facts,
            "bars.json": frozen_bars,
            "knowledge.json": selected_knowledge,
            "runtime.json": runtime,
            "budget.json": asdict(budget),
            "knowledge_manifest.json": dict(knowledge_manifest or {}),
            "repair_findings.json": dict(repair_findings or {}),
            "core_context.json": frozen_core,
        }
        resource_digests = {
            name: _sha256_bytes(_canonical_bytes(resources[name]))
            for name in _RESOURCE_NAMES
        }
        created_at = int(self._clock())
        acceptance_key = _sha256_bytes(
            _canonical_bytes(
                {
                    "task_id": str(task_id),
                    "role": role,
                    "attempt": int(attempt),
                }
            )
        )
        unsigned = {
            "schema_version": 4,
            "task_id": str(task_id),
            "request_id": str(request_id),
            "role": role,
            "attempt": int(attempt),
            "snapshot_hash": snapshot,
            "cutoff_ts_open": cutoff,
            "runtime_version": self._runtime_version,
            "resource_digests": resource_digests,
            "created_at_epoch": created_at,
            "expires_at_epoch": created_at + self._job_ttl_seconds,
            "acceptance_key": acceptance_key,
        }
        if model or reasoning_effort or profile_fingerprint:
            unsigned.update(
                {
                    "model": model,
                    "reasoning_effort": reasoning_effort,
                    "profile_fingerprint": profile_fingerprint,
                }
            )
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
            knowledge_manifest=MappingProxyType(dict(knowledge_manifest or {})),
            repair_findings=MappingProxyType(dict(repair_findings or {})),
            core_context=MappingProxyType(frozen_core),
        )

    @staticmethod
    def _validate_core_context(
        context: Mapping[str, Any],
        *,
        task_id: str,
        snapshot_hash: str,
        cutoff_ts_open: int,
    ) -> None:
        if context and str(context.get("task_id") or "") != task_id:
            raise ValueError("core context task identity mismatch")
        identity = context.get("frozen_snapshot") or {}
        if not isinstance(identity, Mapping):
            raise ValueError("core context frozen snapshot is malformed")
        if identity:
            if str(identity.get("snapshot_hash") or "").lower() != snapshot_hash:
                raise ValueError("core context snapshot identity mismatch")
            if int(identity.get("cutoff_ts_open", -1)) != cutoff_ts_open:
                raise ValueError("core context cutoff identity mismatch")
        higher_timeframe = context.get("higher_timeframe") or {}
        if isinstance(higher_timeframe, Mapping):
            evidence = higher_timeframe.get("evidence") or []
            if not isinstance(evidence, list):
                raise ValueError("higher-timeframe evidence must be an array")
            if any(
                not isinstance(item, Mapping)
                or int(item.get("ts_open", -1)) > cutoff_ts_open
                for item in evidence
            ):
                raise ValueError("higher-timeframe evidence exceeds frozen cutoff")

    def _role_skills(self, role: AgentRole, *, repair: bool) -> dict[str, str]:
        try:
            selected = {role: self._skill_documents[role]}
            if repair:
                selected["draft_pointer_repair"] = self._skill_documents[
                    "draft_pointer_repair"
                ]
            return selected
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
            "role_evidence": (
                projection.get("role_evidence")
                if role in {"vpa_evidence", "joint_arbiter"}
                else None
            ),
        }

    @staticmethod
    def _role_safe_bars(
        role: AgentRole,
        bars: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if role not in {"stage1", "stage2"}:
            return [dict(item) for item in bars]
        price_fields = ("seq", "ts_open", "open", "high", "low", "close", "closed")
        return [
            {key: item[key] for key in price_fields if key in item}
            for item in bars
        ]


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

    def open(self, job_digest: str, *, allow_expired: bool = False) -> FrozenAgentJob:
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
            if (
                manifest.expires_at_epoch
                and not allow_expired
                and int(time.time()) >= manifest.expires_at_epoch
            ):
                raise JobIntegrityError("frozen job has expired")
            resources: dict[str, Any] = {}
            resource_names = (
                _RESOURCE_NAMES
                if manifest.schema_version >= 4
                else _RESOURCE_NAMES_V3
                if manifest.schema_version >= 3
                else _RESOURCE_NAMES_V2
                if manifest.schema_version >= 2
                else _RESOURCE_NAMES_V1
            )
            for name in resource_names:
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
                knowledge_manifest=MappingProxyType(
                    resources.get("knowledge_manifest.json") or {}
                ),
                repair_findings=MappingProxyType(
                    resources.get("repair_findings.json") or {}
                ),
                core_context=MappingProxyType(
                    resources.get("core_context.json") or {}
                ),
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
        if not isinstance(raw, dict) or raw.get("schema_version") not in {1, 2, 3, 4}:
            raise JobIntegrityError("unsupported or missing job manifest")
        schema_version = int(raw["schema_version"])
        role = str(raw["role"])
        if role not in _ROLE_FACT_DOMAINS:
            raise JobIntegrityError("unsupported role in job manifest")
        resource_digests = {
            str(name): _require_digest(value, f"resource digest {name}")
            for name, value in dict(raw["resource_digests"]).items()
        }
        expected_resources = (
            _RESOURCE_NAMES
            if schema_version >= 4
            else _RESOURCE_NAMES_V3
            if schema_version >= 3
            else _RESOURCE_NAMES_V2
            if schema_version >= 2
            else _RESOURCE_NAMES_V1
        )
        if set(resource_digests) != set(expected_resources):
            raise JobIntegrityError("job resource set is incomplete")
        return FrozenAgentJobManifest(
            schema_version=schema_version,
            task_id=str(raw["task_id"]),
            request_id=str(raw["request_id"]),
            role=role,  # type: ignore[arg-type]
            attempt=int(raw["attempt"]),
            snapshot_hash=_require_digest(raw["snapshot_hash"], "snapshot_hash"),
            cutoff_ts_open=int(raw["cutoff_ts_open"]),
            runtime_version=str(raw["runtime_version"]),
            resource_digests=MappingProxyType(resource_digests),
            job_digest=_require_digest(raw["job_digest"], "job_digest"),
            model=str(raw.get("model") or "").strip(),
            reasoning_effort=str(raw.get("reasoning_effort") or "").strip(),
            profile_fingerprint=str(
                raw.get("profile_fingerprint")
                or raw.get("analysis_profile_fingerprint")
                or ""
            ).strip().lower(),
            created_at_epoch=int(raw.get("created_at_epoch") or 0),
            expires_at_epoch=int(raw.get("expires_at_epoch") or 0),
            acceptance_key=str(raw.get("acceptance_key") or ""),
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
