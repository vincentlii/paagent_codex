"""Unix-user-isolated spool bridge for the Codex CLI worker."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from pa_agent.agent_runtime.codex_worker import (
    CodexCliConfig,
    CodexCliWorker,
    CodexWorkerAuthenticationError,
    CodexWorkerError,
    CodexWorkerOutputError,
    CodexWorkerExecutionError,
    safe_worker_diagnostic,
    CodexWorkerRateLimited,
    CodexWorkerResult,
    CodexWorkerStopped,
    CodexWorkerTimeout,
    CodexWorkerUnavailable,
)
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore, JobIntegrityError
from pa_agent.agent_runtime.runtime_identity import codex_runtime_identity
from pa_agent.ai.deepseek_client import AIUsage
from pa_agent.ai.role_contracts import AnalysisRoleStop


@dataclass(frozen=True)
class CodexSpoolConfig:
    job_root: Path
    inbox_root: Path
    result_root: Path
    poll_seconds: float = 0.1
    response_grace_seconds: float = 15.0
    lease_seconds: float = 660.0
    acceptance_root: Path | None = None
    health_interval_seconds: float = 30.0

    @property
    def accepted_results_root(self) -> Path:
        return self.acceptance_root or (self.result_root / ".accepted")


class CodexSpoolClient:
    """Publish a bounded dispatch and wait for the isolated worker result."""

    def __init__(self, config: CodexSpoolConfig) -> None:
        self._config = config

    def run(
        self,
        published_job: str | Path,
        *,
        timeout_seconds: float | None = None,
        persist_session: bool = False,
        resume_thread_id: str | None = None,
        parent_job_digest: str | None = None,
    ) -> CodexWorkerResult:
        dispatch: Path | None = None
        result: Path | None = None
        deadline = time.monotonic() + (
            180.0 + self._config.response_grace_seconds
        )
        try:
            published = Path(published_job).resolve()
            root = self._config.job_root.resolve()
            if published.parent != root:
                raise CodexWorkerOutputError("published job is outside the isolated spool")
            job = FrozenAgentJobStore(root).open(published.name)
            CodexCliWorker._validate_session_options(
                job,
                persist_session=bool(persist_session),
                resume_thread_id=resume_thread_id,
                parent_job_digest=parent_job_digest,
            )
            expected_runtime = dict(job.runtime.get("backend_runtime_identity") or {})
            if not expected_runtime:
                raise CodexWorkerOutputError("frozen job has no backend runtime identity")
            request_id = uuid4().hex
            dispatch = self._config.inbox_root / f"{request_id}.json"
            result = self._config.result_root / f"{request_id}.json"
            self._config.inbox_root.mkdir(parents=True, exist_ok=True)
            self._config.result_root.mkdir(parents=True, exist_ok=True)
            effective_timeout = min(
                float(job.budget.max_wall_seconds),
                max(1.0, float(timeout_seconds)),
            ) if timeout_seconds is not None else float(job.budget.max_wall_seconds)
            dispatch_started = time.monotonic()
            _atomic_json(
                dispatch,
                {
                    "schema_version": 3,
                    "request_id": request_id,
                    "job_digest": job.manifest.job_digest,
                    "expected_runtime_identity": expected_runtime,
                    "acceptance_key": job.manifest.acceptance_key,
                    "lease_seconds": max(
                        self._config.lease_seconds,
                        effective_timeout + self._config.response_grace_seconds + 30.0,
                    ),
                    "deadline_epoch": time.time() + effective_timeout,
                    "dispatched_at_epoch": time.time(),
                    "persist_session": bool(persist_session),
                    "resume_thread_id": resume_thread_id,
                    "parent_job_digest": parent_job_digest,
                },
            )
            deadline = time.monotonic() + (
                effective_timeout
                + self._config.response_grace_seconds
            )
            while time.monotonic() < deadline:
                if result.is_file():
                    try:
                        envelope = json.loads(result.read_text(encoding="utf-8"))
                    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
                        raise CodexWorkerOutputError(
                            "isolated result is malformed"
                        ) from exc
                    decoded = _decode_result(
                        envelope,
                        request_id=request_id,
                        job_digest=job.manifest.job_digest,
                        runtime_identity=expected_runtime,
                        acceptance_key=job.manifest.acceptance_key,
                    )
                    self._accept_once(
                        acceptance_key=job.manifest.acceptance_key,
                        request_id=request_id,
                        envelope=envelope,
                    )
                    return replace(decoded, timings={**decoded.timings,
                        "spool_wall_ms": (time.monotonic() - dispatch_started) * 1000})
                time.sleep(self._config.poll_seconds)
            raise CodexWorkerTimeout("isolated Codex worker response timed out")
        except CodexWorkerError:
            raise
        except JobIntegrityError as exc:
            raise CodexWorkerOutputError("isolated frozen job is invalid") from exc
        except OSError as exc:
            raise CodexWorkerUnavailable("isolated Codex spool is unavailable") from exc
        finally:
            try:
                if dispatch is not None:
                    dispatch.unlink(missing_ok=True)
                if result is not None:
                    result.unlink(missing_ok=True)
            except OSError:
                # Cleanup must not mask the authoritative execution result.
                pass

    def _accept_once(
        self,
        *,
        acceptance_key: str,
        request_id: str,
        envelope: dict[str, Any],
    ) -> None:
        if not acceptance_key:
            return
        root = self._config.accepted_results_root
        root.mkdir(parents=True, exist_ok=True)
        marker = root / f"{acceptance_key}.json"
        digest = hashlib.sha256(
            json.dumps(
                envelope,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        try:
            with marker.open("x", encoding="utf-8") as handle:
                json.dump(
                    {
                        "schema_version": 1,
                        "acceptance_key": acceptance_key,
                        "request_id": request_id,
                        "result_digest": digest,
                    },
                    handle,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise CodexWorkerOutputError(
                "duplicate Codex role result acceptance was rejected"
            ) from exc


class CodexSpoolService:
    """Execute dispatches as the dedicated Codex Unix user."""

    def __init__(
        self,
        *,
        spool: CodexSpoolConfig,
        worker: CodexCliWorker,
        max_workers: int = 2,
        runtime_identity_provider: Callable[[], dict[str, Any]] | None = None,
        idle_maintenance: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._spool = spool
        self._worker = worker
        self._max_workers = max(1, min(int(max_workers), 4))
        self._runtime_identity_provider = runtime_identity_provider
        self._idle_maintenance = idle_maintenance
        self._next_maintenance_at = 0.0
        self._maintenance_status: dict[str, Any] = {}

    def check(self) -> list[str]:
        errors: list[str] = []
        for path in (self._spool.job_root, self._spool.inbox_root, self._spool.result_root):
            if not path.is_dir():
                errors.append(f"Codex spool directory is unavailable: {path}")
        if self._runtime_identity_provider is not None:
            try:
                self._runtime_identity_provider()
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"Codex worker runtime identity is unavailable: {exc}")
        login_health = getattr(self._worker, "login_health", None)
        if callable(login_health):
            state = str(login_health())
            if state != "healthy":
                errors.append(f"Codex ChatGPT authentication is {state}")
        return errors

    def serve_forever(self) -> None:
        self._recover_claims()
        next_health_at = 0.0
        with ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="pa-codex",
        ) as executor:
            active: set[Any] = set()
            while True:
                now = time.monotonic()
                if now >= next_health_at:
                    self._publish_health()
                    next_health_at = now + max(
                        5.0,
                        float(self._spool.health_interval_seconds),
                    )
                self._recover_claims()
                active = {future for future in active if not future.done()}
                self._maintain_if_idle(active=bool(active), now=now)
                capacity = self._max_workers - len(active)
                for dispatch in sorted(self._spool.inbox_root.glob("*.json"))[:capacity]:
                    claimed = dispatch.with_suffix(".running")
                    try:
                        os.replace(dispatch, claimed)
                    except OSError:
                        continue
                    active.add(executor.submit(self._execute, claimed))
                time.sleep(self._spool.poll_seconds)

    def _maintain_if_idle(self, *, active: bool, now: float) -> None:
        if (active or self._idle_maintenance is None or now < self._next_maintenance_at
                or any(self._spool.inbox_root.glob("*.json"))
                or any(self._spool.inbox_root.glob("*.running"))):
            return
        try:
            self._maintenance_status = dict(self._idle_maintenance())
        except (OSError, RuntimeError, ValueError):
            self._maintenance_status = {"errors": 1}
        self._next_maintenance_at = now + 3600

    def _publish_health(self) -> None:
        """Publish only credential-free worker health for the trusted host."""
        try:
            login_health = getattr(self._worker, "login_health", None)
            auth_state = str(login_health()) if callable(login_health) else "unknown"
            runtime = (
                dict(self._runtime_identity_provider())
                if self._runtime_identity_provider is not None
                else {}
            )
            _atomic_json(
                self._spool.result_root / ".worker-status.json",
                {
                    "schema_version": 1,
                    "updated_at_epoch": time.time(),
                    "service_state": "running",
                    "auth_state": auth_state,
                    "runtime_version": runtime.get("runtime_version"),
                    "runtime_asset_digest": runtime.get("runtime_asset_digest"),
                    "codex_binary_digest": runtime.get("codex_binary_digest"),
                    "session_retention": dict(self._maintenance_status),
                },
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            # Health publication must not kill an otherwise recoverable worker.
            return

    def _recover_claims(self) -> None:
        now = time.time()
        for claimed in self._spool.inbox_root.glob("*.running"):
            try:
                raw = json.loads(claimed.read_text(encoding="utf-8"))
                lease_seconds = max(1.0, float(raw.get("lease_seconds") or 660.0))
                lease_active = now < claimed.stat().st_mtime + lease_seconds
                if lease_active:
                    continue
                if (self._spool.result_root / f"{claimed.stem}.json").is_file():
                    claimed.unlink()
                    continue
                os.replace(claimed, claimed.with_suffix(".json"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue

    def _execute(self, claimed: Path) -> None:
        request_id = claimed.stem
        result_path = self._spool.result_root / f"{request_id}.json"
        try:
            raw = json.loads(claimed.read_text(encoding="utf-8"))
            if raw.get("schema_version") not in {1, 2, 3} or raw.get("request_id") != request_id:
                raise CodexWorkerOutputError("invalid isolated dispatch identity")
            digest = str(raw.get("job_digest") or "")
            published = self._spool.job_root / digest
            job = FrozenAgentJobStore(self._spool.job_root).open(digest)
            expected_runtime = dict(raw.get("expected_runtime_identity") or {})
            if str(raw.get("acceptance_key") or "") != job.manifest.acceptance_key:
                raise CodexWorkerOutputError("dispatch acceptance identity mismatch")
            frozen_runtime = dict(job.runtime.get("backend_runtime_identity") or {})
            actual_runtime = (
                self._runtime_identity_provider()
                if self._runtime_identity_provider is not None
                else frozen_runtime
            )
            if not expected_runtime or expected_runtime != frozen_runtime:
                raise CodexWorkerOutputError(
                    "dispatch runtime identity does not match frozen job"
                )
            if not _runtime_identity_matches(
                actual_runtime,
                expected_runtime,
            ):
                raise CodexWorkerOutputError(
                    "isolated worker runtime identity does not match frozen job"
                )
            deadline_epoch = float(
                raw.get("deadline_epoch")
                or (time.time() + float(job.budget.max_wall_seconds))
            )
            remaining = deadline_epoch - time.time()
            if remaining <= 0:
                raise CodexWorkerTimeout("isolated Codex dispatch deadline expired")
            queued_at = raw.get("dispatched_at_epoch")
            queue_ms = max(0.0, (time.time() - float(queued_at)) * 1000) if queued_at is not None else None
            persist_session = bool(raw.get("persist_session", False))
            resume_thread_id = raw.get("resume_thread_id")
            parent_job_digest = raw.get("parent_job_digest")
            CodexCliWorker._validate_session_options(
                job,
                persist_session=persist_session,
                resume_thread_id=resume_thread_id,
                parent_job_digest=parent_job_digest,
            )
            result = self._worker.run(
                published,
                timeout_seconds=remaining,
                persist_session=persist_session,
                resume_thread_id=resume_thread_id,
                parent_job_digest=parent_job_digest,
            )
            envelope = {
                "schema_version": 1,
                "request_id": request_id,
                "job_digest": digest,
                "runtime_identity": actual_runtime,
                "acceptance_key": job.manifest.acceptance_key,
                "status": "ok",
                "content": result.content,
                "thread_id": result.thread_id,
                "usage": asdict(result.usage),
                "latency_ms": result.latency_ms,
                "timings": {**result.timings, **({"queue_ms": queue_ms} if queue_ms is not None else {})},
                "evidence_events": list(result.evidence_events),
                "subagent_events": list(result.subagent_events),
                "collaboration": dict(result.collaboration),
                "usage_observation": dict(result.usage_observation),
                "outbox_path": str(result.outbox_path),
                "model": result.model,
                "reasoning_effort": result.reasoning_effort,
                "profile_fingerprint": result.profile_fingerprint,
            }
        except CodexWorkerError as exc:
            envelope = {
                "schema_version": 1,
                "request_id": request_id,
                "job_digest": str(locals().get("digest") or ""),
                "runtime_identity": dict(locals().get("actual_runtime") or {}),
                "status": "error",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            diagnostic = safe_worker_diagnostic(getattr(exc, "diagnostic", None))
            if diagnostic:
                envelope["diagnostic"] = diagnostic
                envelope["acceptance_key"] = job.manifest.acceptance_key
            if getattr(exc, "collaboration", None):
                envelope["collaboration"] = dict(exc.collaboration)
                envelope["acceptance_key"] = job.manifest.acceptance_key
            if getattr(exc, "observed_usage", None) is not None:
                envelope["observed_usage"] = asdict(exc.observed_usage)
                envelope["usage_observation"] = dict(getattr(exc, "usage_observation", {}))
                envelope["acceptance_key"] = job.manifest.acceptance_key
            if isinstance(exc, CodexWorkerStopped):
                envelope["role_stop"] = exc.stop.model_dump(mode="json")
                envelope["acceptance_key"] = job.manifest.acceptance_key
        except (JobIntegrityError, ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            envelope = {
                "schema_version": 1,
                "request_id": request_id,
                "job_digest": str(locals().get("digest") or ""),
                "runtime_identity": dict(locals().get("actual_runtime") or {}),
                "status": "error",
                "error_type": "CodexWorkerOutputError",
                "message": "isolated dispatch or frozen job is invalid",
            }
        except OSError:
            envelope = {
                "schema_version": 1,
                "request_id": request_id,
                "job_digest": str(locals().get("digest") or ""),
                "runtime_identity": dict(locals().get("actual_runtime") or {}),
                "status": "error",
                "error_type": "CodexWorkerUnavailable",
                "message": "isolated Codex spool is unavailable",
            }
        except Exception:
            envelope = {
                "schema_version": 1,
                "request_id": request_id,
                "job_digest": str(locals().get("digest") or ""),
                "runtime_identity": dict(locals().get("actual_runtime") or {}),
                "status": "error",
                "error_type": "CodexWorkerOutputError",
                "message": "isolated Codex worker rejected an internal invariant",
            }
        try:
            _atomic_json(result_path, envelope)
        finally:
            claimed.unlink(missing_ok=True)


def _decode_result(
    raw: dict[str, Any],
    *,
    request_id: str,
    job_digest: str,
    runtime_identity: dict[str, Any],
    acceptance_key: str,
) -> CodexWorkerResult:
    if raw.get("schema_version") != 1:
        raise CodexWorkerOutputError("isolated result schema is unsupported")
    if raw.get("request_id") != request_id or raw.get("job_digest") != job_digest:
        raise CodexWorkerOutputError("isolated result identity does not match dispatch")
    if raw.get("status") == "ok" and raw.get("acceptance_key") != acceptance_key:
        raise CodexWorkerOutputError("isolated result acceptance identity mismatch")
    if raw.get("status") != "ok":
        collaboration = dict(raw.get("collaboration") or {})
        if (collaboration or raw.get("observed_usage") or raw.get("diagnostic")) and (
                raw.get("acceptance_key") != acceptance_key
                or (collaboration and collaboration.get("job_digest") != job_digest)
                or not _runtime_identity_matches(dict(raw.get("runtime_identity") or {}), runtime_identity)):
            raise CodexWorkerOutputError("isolated collaboration failure identity mismatch")
        if raw.get("error_type") == "CodexWorkerStopped":
            if raw.get("acceptance_key") != acceptance_key or not _runtime_identity_matches(
                dict(raw.get("runtime_identity") or {}), runtime_identity,
            ):
                raise CodexWorkerOutputError("isolated role stop identity mismatch")
            try:
                stop = AnalysisRoleStop.model_validate(raw.get("role_stop"))
            except ValueError as exc:
                raise CodexWorkerOutputError("isolated role stop is malformed") from exc
            error = CodexWorkerStopped(stop)
            error.collaboration = collaboration
            error.diagnostic = safe_worker_diagnostic(raw.get("diagnostic"))
            if raw.get("observed_usage") is not None:
                error.observed_usage = AIUsage(**raw["observed_usage"])
                error.usage_observation = dict(raw.get("usage_observation") or {})
            raise error
        error_types = {
            "CodexWorkerAuthenticationError": CodexWorkerAuthenticationError,
            "CodexWorkerRateLimited": CodexWorkerRateLimited,
            "CodexWorkerTimeout": CodexWorkerTimeout,
            "CodexWorkerUnavailable": CodexWorkerUnavailable,
            "CodexWorkerOutputError": CodexWorkerOutputError,
            "CodexWorkerExecutionError": CodexWorkerExecutionError,
        }
        error = error_types.get(str(raw.get("error_type")), CodexWorkerOutputError)
        failure = error(str(raw.get("message") or "isolated Codex worker failed"))
        failure.collaboration = collaboration
        failure.diagnostic = safe_worker_diagnostic(raw.get("diagnostic"))
        if raw.get("observed_usage") is not None:
            failure.observed_usage = AIUsage(**raw["observed_usage"])
            failure.usage_observation = dict(raw.get("usage_observation") or {})
        raise failure
    result_runtime = dict(raw.get("runtime_identity") or {})
    if not _runtime_identity_matches(result_runtime, runtime_identity):
        raise CodexWorkerOutputError("isolated result runtime identity does not match job")
    expected_model = str(runtime_identity.get("model") or "")
    expected_effort = str(runtime_identity.get("reasoning_effort") or "")
    if runtime_identity.get("profile_fingerprint") or runtime_identity.get(
        "analysis_profile_fingerprint"
    ):
        if expected_model and str(raw.get("model") or "") != expected_model:
            raise CodexWorkerOutputError("isolated result model does not match job")
        if expected_effort and str(raw.get("reasoning_effort") or "") != expected_effort:
            raise CodexWorkerOutputError(
                "isolated result reasoning effort does not match job"
            )
        expected_profile = str(
            runtime_identity.get("profile_fingerprint")
            or runtime_identity.get("analysis_profile_fingerprint")
            or ""
        )
        if str(raw.get("profile_fingerprint") or "") != expected_profile:
            raise CodexWorkerOutputError(
                "isolated result profile fingerprint does not match job"
            )
    usage = dict(raw.get("usage") or {})
    return CodexWorkerResult(
        content=str(raw.get("content") or ""),
        thread_id=str(raw.get("thread_id") or "") or None,
        usage=AIUsage(**usage),
        latency_ms=float(raw.get("latency_ms") or 0.0),
        timings=dict(raw.get("timings") or {}),
        evidence_events=tuple(dict(item) for item in raw.get("evidence_events") or ()),
        subagent_events=tuple(dict(item) for item in raw.get("subagent_events") or ()),
        collaboration=dict(raw.get("collaboration") or {}),
        usage_observation=dict(raw.get("usage_observation") or {}),
        outbox_path=Path(str(raw.get("outbox_path") or "isolated-outbox.json")),
        model=str(raw.get("model") or ""),
        reasoning_effort=str(raw.get("reasoning_effort") or ""),
        profile_fingerprint=str(raw.get("profile_fingerprint") or ""),
    )


def _runtime_identity_matches(
    actual: dict[str, Any],
    expected: dict[str, Any],
) -> bool:
    """Compare immutable runtime assets while letting jobs select the model."""
    if not expected.get("profile_fingerprint") and not expected.get(
        "analysis_profile_fingerprint"
    ):
        return actual == expected
    selection_keys = {
        "model",
        "reasoning_effort",
        "profile_fingerprint",
        "analysis_profile_fingerprint",
        "service_tier",
        "investigation_policy",
        "stage_subagents",
    }
    if expected.get("investigation_policy", "agentic") not in {"fixed", "agentic"}:
        return False
    if expected.get("service_tier") not in {None, "fast"}:
        return False
    from pa_agent.agent_runtime.execution_contract import STAGE_SUBAGENT_POLICIES

    if expected.get("stage_subagents", "off") not in STAGE_SUBAGENT_POLICIES:
        return False
    actual_immutable = {
        key: value for key, value in actual.items() if key not in selection_keys
    }
    expected_immutable = {
        key: value for key, value in expected.items() if key not in selection_keys
    }
    return actual_immutable == expected_immutable


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
        encoding="utf-8",
    )
    if os.name == "posix":
        temporary.chmod(0o660)
    os.replace(temporary, path)


def _service_from_environment() -> CodexSpoolService:
    root = Path(os.environ.get("PA_AGENT_CODEX_STATE_DIR", "/var/lib/pa-agent-codex"))
    binary = os.environ.get("PA_AGENT_CODEX_BINARY", "codex")
    model = os.environ.get("PA_AGENT_CODEX_MODEL", "").strip()
    effort = os.environ.get("PA_AGENT_CODEX_REASONING", "high").strip()
    catalog = os.environ.get("PA_AGENT_CODEX_MODEL_CATALOG", "").strip()
    spool = CodexSpoolConfig(
        job_root=root / "jobs",
        inbox_root=root / "inbox",
        result_root=root / "results",
    )
    worker = CodexCliWorker(
        CodexCliConfig(
            codex_home=root / "home",
            runtime_root=root / "runtime",
            outbox_root=root / "outbox",
            model=model,
            reasoning_effort=effort,
            codex_binary=binary,
            model_catalog=Path(catalog) if catalog else None,
            allow_experimental_subagents=(
                os.environ.get("PA_AGENT_CODEX_SUBAGENT_RESEARCH", "0") == "1"
            ),
        )
    )
    return CodexSpoolService(
        spool=spool,
        worker=worker,
        idle_maintenance=lambda: worker.prune_idle_sessions(spool.inbox_root),
        max_workers=int(os.environ.get("PA_AGENT_CODEX_MAX_WORKERS", "2")),
        runtime_identity_provider=lambda: codex_runtime_identity(
            model=model,
            reasoning_effort=effort,
            codex_binary=binary,
            model_catalog=Path(catalog) if catalog else None,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PA Agent isolated Codex worker")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    service = _service_from_environment()
    if args.check:
        errors = service.check()
        for error in errors:
            print(error)
        return 2 if errors else 0
    service.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
