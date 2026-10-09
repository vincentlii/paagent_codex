"""Constrained Codex implementation of the analysis-role backend seam."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from pa_agent.agent_runtime.codex_worker import (
    CodexCliWorker,
    CodexWorkerAuthenticationError,
    CodexWorkerAvailabilityError,
    CodexWorkerError,
    CodexWorkerOutputError,
    CodexWorkerRateLimited,
    CodexWorkerTimeout,
)
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore, JobIntegrityError
from pa_agent.agent_runtime.publisher import analysis_role_request_digest
from pa_agent.agent_runtime.repair_contract import (
    PATCH_ENVELOPE_SCHEMA,
    apply_patch_envelope,
    extract_repair_context,
)
from pa_agent.ai.analysis_role_backend import AnalysisRoleRequest, AnalysisRoleResult
from pa_agent.ai.deepseek_client import AIReply, CancelledError
from pa_agent.ai.provider_capabilities import normalize_strict_output_instance
from pa_agent.ai.provider_failures import ModelCallFailure, ProviderFailure
from pa_agent.ai.token_counter import estimate_tokens


class FullChainFallbackRequired(ModelCallFailure):
    """Availability failure that must restart the complete PA chain on Direct."""


class CodexCircuitBreaker:
    """Persistent availability breaker with exactly one half-open probe."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 900.0,
        max_cooldown_seconds: float = 3600.0,
        state_path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be positive")
        if max_cooldown_seconds < cooldown_seconds:
            raise ValueError("max_cooldown_seconds must not be below cooldown")
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._max_cooldown_seconds = max_cooldown_seconds
        self._state_path = Path(state_path) if state_path is not None else None
        self._clock = clock
        self._failures = 0
        self._opened_until = 0.0
        self._cooldown_level = 0
        self._probe_in_flight = False
        self._auth_locked = False
        self._lock = threading.Lock()
        self._load()

    def allow(self) -> bool:
        with self._lock:
            now = self._clock()
            if self._auth_locked:
                if now < self._opened_until or self._probe_in_flight:
                    return False
                self._probe_in_flight = True
                self._save()
                return True
            if not self._opened_until:
                return True
            if now < self._opened_until or self._probe_in_flight:
                return False
            self._probe_in_flight = True
            self._save()
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_until = 0.0
            self._cooldown_level = 0
            self._probe_in_flight = False
            self._auth_locked = False
            self._save()

    def record_availability_failure(self, *, authentication: bool = False) -> None:
        with self._lock:
            self._failures += 1
            self._probe_in_flight = False
            if authentication:
                self._auth_locked = True
                # A rare bounded probe detects operator login recovery without
                # turning every scheduled analysis into an auth retry storm.
                self._opened_until = self._clock() + self._max_cooldown_seconds
            elif self._failures >= self._failure_threshold or self._opened_until:
                cooldown = min(
                    self._cooldown_seconds * (2**self._cooldown_level),
                    self._max_cooldown_seconds,
                )
                self._opened_until = self._clock() + cooldown
                self._cooldown_level += 1
            self._save()

    def record_authentication_restored(self) -> None:
        with self._lock:
            self._auth_locked = False
            self._failures = 0
            self._opened_until = 0.0
            self._probe_in_flight = False
            self._cooldown_level = 0
            self._save()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._state()

    def _state(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "failures": self._failures,
            "opened_until": self._opened_until,
            "cooldown_level": self._cooldown_level,
            "probe_in_flight": self._probe_in_flight,
            "auth_locked": self._auth_locked,
        }

    def _load(self) -> None:
        if self._state_path is None or not self._state_path.is_file():
            return
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            if raw.get("schema_version") != 1:
                return
            self._failures = max(0, int(raw.get("failures") or 0))
            self._opened_until = max(0.0, float(raw.get("opened_until") or 0.0))
            self._cooldown_level = max(0, int(raw.get("cooldown_level") or 0))
            self._probe_in_flight = False
            self._auth_locked = bool(raw.get("auth_locked"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._failures = 0
            self._opened_until = 0.0
            self._cooldown_level = 0
            self._probe_in_flight = False
            self._auth_locked = False

    def _save(self) -> None:
        if self._state_path is None:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_name(
            f".{self._state_path.name}.{uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(self._state(), separators=(",", ":"), sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, self._state_path)
        except OSError:
            temporary.unlink(missing_ok=True)


class CodexAnalysisRoleBackend:
    """Run bounded roles, with one verified PA Stage 1→Stage 2 continuation."""

    validation_retry_limit = 1

    def __init__(
        self,
        *,
        worker: CodexCliWorker,
        publish_job: Callable[[AnalysisRoleRequest, int], Path],
        request_full_chain_fallback: bool = False,
        circuit_breaker: CodexCircuitBreaker | None = None,
        runtime_identity: dict[str, Any] | None = None,
        cycle_wall_seconds: float = 600.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._worker = worker
        self._publish_job = publish_job
        self._request_full_chain_fallback = bool(request_full_chain_fallback)
        self._circuit = circuit_breaker or CodexCircuitBreaker()
        self._runtime_identity = dict(runtime_identity or {})
        self._cycle_wall_seconds = max(1.0, float(cycle_wall_seconds))
        self._monotonic = monotonic
        self._cycle_started_at: float | None = None
        self._role_attempts: dict[str, int] = {}
        self._attempt_lock = threading.Lock()
        self._pa_session: dict[str, Any] | None = None

    @property
    def backend_id(self) -> str:
        return "codex_agent"

    @property
    def runtime_identity(self) -> dict[str, Any]:
        return dict(self._runtime_identity)

    def bind_role(self, *, client: Any, settings: Any) -> CodexAnalysisRoleBackend:
        del client, settings
        return self

    def run(
        self,
        request: AnalysisRoleRequest,
        *,
        on_reasoning_token: Callable[[str], None] | None,
        on_content_token: Callable[[str], None] | None,
        cancel_token: Any,
        on_attempt_error: Callable[[Exception], None] | None = None,
    ) -> AnalysisRoleResult:
        started = time.monotonic()
        del on_reasoning_token  # hidden Codex reasoning is deliberately never persisted
        if cancel_token is not None and bool(cancel_token.is_set()):
            raise ModelCallFailure(
                ProviderFailure(
                    kind="cancelled",
                    message="Codex analysis was cancelled",
                    retryable=False,
                ),
                original=CancelledError("cancelled"),
            )
        if not self._circuit.allow():
            self._raise_failure(
                ProviderFailure(
                    kind="unavailable",
                    message="Codex circuit is open",
                    retryable=True,
                ),
                attempts=(),
            )

        attempt = self._next_attempt(request)
        request, session_options = self._prepare_session_invocation(request, attempt)
        if self._cycle_started_at is None:
            self._cycle_started_at = self._monotonic()
        remaining_cycle_seconds = self._cycle_wall_seconds - (
            self._monotonic() - self._cycle_started_at
        )
        if remaining_cycle_seconds <= 0:
            if request.agent_context and request.role in {"stage1", "stage2", "vpa_evidence"}:
                from pa_agent.agent_runtime.codex_worker import CodexWorkerStopped
                from pa_agent.ai.role_contracts import AnalysisRoleStop
                stopped = CodexWorkerStopped(AnalysisRoleStop(
                    role=request.role, status="incomplete", reason="analysis cycle wall-clock budget exceeded",
                ))
                if on_attempt_error is not None:
                    on_attempt_error(stopped)
                self._raise_failure(ProviderFailure(
                    kind="role_stopped", message=stopped.stop.reason, retryable=False,
                ), attempts=({
                    "stage": request.role,
                    "analysis_backend": self.backend_id,
                    "attempt": attempt,
                },), original=stopped)
            self._circuit.record_availability_failure()
            self._raise_failure(
                ProviderFailure(
                    kind="timeout",
                    message="Codex analysis cycle exceeded its wall-clock budget",
                    retryable=True,
                ),
                attempts=({
                    "stage": request.role,
                    "analysis_backend": self.backend_id,
                    "attempt": attempt,
                },),
            )
        if request.snapshot_hash is None or request.cutoff_ts_open is None:
            self._circuit.record_success()
            self._raise_failure(
                ProviderFailure(
                    kind="invalid_request",
                    message="Codex analysis requires a frozen snapshot identity",
                    retryable=False,
                ),
                attempts=({
                    "stage": request.role,
                    "analysis_backend": self.backend_id,
                    "attempt": attempt,
                },),
            )
        try:
            published = self._publish_job(request, attempt)
            job = FrozenAgentJobStore(published.parent).open(published.name)
            _verify_job_request(job, request, attempt=attempt)
        except OSError as exc:
            if on_attempt_error is not None:
                on_attempt_error(exc)
            self._circuit.record_availability_failure()
            self._raise_failure(
                ProviderFailure(
                    kind="unavailable",
                    message="Codex frozen job spool is unavailable",
                    retryable=True,
                ),
                attempts=({
                    "stage": request.role,
                    "analysis_backend": self.backend_id,
                    "attempt": attempt,
                },),
                original=exc,
            )
        except (TypeError, ValueError, KeyError, JobIntegrityError) as exc:
            if on_attempt_error is not None:
                on_attempt_error(exc)
            self._circuit.record_success()
            self._raise_failure(
                ProviderFailure(
                    kind="invalid_request",
                    message="Codex frozen job publication or identity check failed",
                    retryable=False,
                ),
                attempts=({
                    "stage": request.role,
                    "analysis_backend": self.backend_id,
                    "attempt": attempt,
                },),
                original=exc,
            )
        frozen_runtime_identity = dict(
            job.runtime.get("backend_runtime_identity") or {}
        )
        if frozen_runtime_identity != self._runtime_identity:
            self._circuit.record_success()
            self._raise_failure(
                ProviderFailure(
                    kind="invalid_request",
                    message="Codex frozen job runtime identity mismatch",
                    retryable=False,
                ),
                attempts=({
                    "stage": request.role,
                    "analysis_backend": self.backend_id,
                    "attempt": attempt,
                },),
            )
        attempt_meta = {
            "stage": request.role,
            "analysis_backend": self.backend_id,
            "job_digest": job.manifest.job_digest,
            "attempt": attempt,
            "investigation_policy": frozen_runtime_identity.get(
                "investigation_policy", "agentic"
            ),
            "knowledge_delivery_policy": job.core_context.get(
                "knowledge_delivery_policy"
            ),
            "stage_subagents": frozen_runtime_identity.get("stage_subagents", "off"),
        }
        try:
            result = self._worker.run(
                published,
                timeout_seconds=remaining_cycle_seconds,
                persist_session=session_options["persist_session"],
                resume_thread_id=session_options["resume_thread_id"],
                parent_job_digest=session_options["parent_job_digest"],
            )
        except CodexWorkerError as exc:
            if getattr(exc, "observed_usage", None) is not None:
                from dataclasses import asdict
                attempt_meta.update(usage=asdict(exc.observed_usage),
                                    usage_observation=dict(getattr(exc, "usage_observation", {})),
                                    model=job.manifest.model, reasoning_effort=job.manifest.reasoning_effort,
                                    latency_ms=(time.monotonic() - started) * 1000)
                exc.analysis_attempt = dict(attempt_meta)
            if getattr(exc, "collaboration", None):
                attempt_meta["collaboration"] = dict(exc.collaboration)
                attempt_meta["usage"] = dict(exc.collaboration.get("aggregate_usage") or {})
                attempt_meta["model"] = job.manifest.model
                attempt_meta["reasoning_effort"] = job.manifest.reasoning_effort
                attempt_meta["latency_ms"] = (time.monotonic() - started) * 1000
                exc.analysis_attempt = dict(attempt_meta)
            if on_attempt_error is not None:
                on_attempt_error(exc)
            failure = _worker_failure(exc)
            if isinstance(exc, CodexWorkerAvailabilityError):
                self._circuit.record_availability_failure(
                    authentication=isinstance(exc, CodexWorkerAuthenticationError)
                )
            else:
                self._circuit.record_success()
            self._raise_failure(failure, attempts=(attempt_meta,), original=exc)

        self._circuit.record_success()
        # Freeze observations before normalization/repair can fail. This is a
        # failure ledger, not a fabricated reply or accepted trading Draft.
        if result.collaboration:
            from dataclasses import asdict
            attempt_meta.update(collaboration=dict(result.collaboration), usage=asdict(result.usage),
                                model=job.manifest.model, reasoning_effort=job.manifest.reasoning_effort,
                                latency_ms=result.latency_ms)
        repair_context = extract_repair_context(request.messages)
        content = result.content
        strict_null_pruned_count = 0
        if repair_context is not None:
            try:
                envelope = json.loads(result.content)
                if not isinstance(envelope, dict):
                    raise ValueError("repair envelope must be an object")
                content = json.dumps(
                    apply_patch_envelope(repair_context, envelope),
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                if attempt_meta.get("collaboration"):
                    exc.analysis_attempt = dict(attempt_meta)
                if on_attempt_error is not None:
                    on_attempt_error(exc)
                self._raise_failure(
                    ProviderFailure(
                        kind="invalid_request",
                        message="Codex repair envelope exceeded Validator authority",
                        retryable=False,
                    ),
                    attempts=(attempt_meta,),
                    original=exc,
                )
        else:
            try:
                normalized, strict_null_pruned_count = normalize_strict_output_instance(
                    json.loads(content),
                    dict(request.schema or {}),
                )
                content = json.dumps(
                    normalized,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                if attempt_meta.get("collaboration"):
                    exc.analysis_attempt = dict(attempt_meta)
                if on_attempt_error is not None:
                    on_attempt_error(exc)
                self._raise_failure(
                    ProviderFailure(
                        kind="invalid_request",
                        message="Codex structured output normalization failed",
                        retryable=False,
                    ),
                    attempts=(attempt_meta,),
                    original=exc,
                )
        if on_content_token is not None:
            on_content_token(content)
        evidence_bytes = sum(
            max(0, int(event.get("response_bytes") or 0))
            for event in result.evidence_events
        )
        initial_context_bytes = len(
            json.dumps(
                job.request.get("messages", []),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        initial_context_tokens = estimate_tokens(
            [dict(item) for item in job.request.get("messages", [])]
        )
        retrieval_context_tokens = sum(
            max(0, int(event.get("response_tokens") or 0))
            for event in result.evidence_events
        )
        decision_packet = job.core_context.get("stage1_decision_packet") or {}
        optional_operations = {
            "list_fact_ids", "get_measurements", "get_pa_evidence", "get_facts",
            "get_bars", "search_allowed_knowledge", "read_allowed_knowledge",
            "measure_price_window", "project_price_leg",
        }
        runtime = {
            "analysis_backend": self.backend_id,
            "role": request.role,
            "provider_id": "deepseek_codex" if frozen_runtime_identity.get("provider") == "deepseek_codex_responses" else "openai_codex",
            "structured_output_mode": "text" if frozen_runtime_identity.get("provider") == "deepseek_codex_responses" else "json_schema",
            "requested_model": job.manifest.model
            or self._runtime_identity.get("model"),
            "effective_model": job.manifest.model
            or self._runtime_identity.get("model"),
            "profile_fingerprint": job.manifest.profile_fingerprint
            or self._runtime_identity.get("profile_fingerprint")
            or self._runtime_identity.get("analysis_profile_fingerprint"),
            "requested_thinking": request.thinking,
            "requested_reasoning_effort": request.reasoning_effort,
            "effective_thinking": True,
            "effective_reasoning_effort": job.manifest.reasoning_effort
            or self._runtime_identity.get("reasoning_effort", request.reasoning_effort),
            "requested_service_tier": frozen_runtime_identity.get("service_tier", "standard"),
            "model": job.manifest.model or self._runtime_identity.get("model"),
            "investigation_policy": frozen_runtime_identity.get(
                "investigation_policy", "agentic"
            ),
            "knowledge_delivery_policy": job.core_context.get(
                "knowledge_delivery_policy"
            ),
            "stage_subagents": frozen_runtime_identity.get("stage_subagents", "off"),
            "runtime_version": self._runtime_identity.get("runtime_version"),
            "provider_fallback_used": False,
            "transport_attempts": [attempt_meta],
            "job_digest": job.manifest.job_digest,
            "thread_id": result.thread_id,
            "initial_context_bytes": initial_context_bytes,
            "initial_context_tokens": initial_context_tokens,
            "evidence_call_count": len(result.evidence_events),
            "evidence_response_bytes": evidence_bytes,
            "retrieval_context_tokens": retrieval_context_tokens,
            "evidence_denied_count": sum(
                1 for event in result.evidence_events if event.get("status") != "ok"
            ),
            "evidence_latency_ms": sum(
                max(0.0, float(event.get("latency_ms") or 0.0))
                for event in result.evidence_events
            ),
            "context_policy": job.core_context.get("context_policy"),
            "decision_packet_bytes": (
                int(decision_packet.get("packet_bytes") or 0) if decision_packet else None
            ),
            "decision_packet_tokens": (
                estimate_tokens([{"role": "tool", "content": json.dumps(
                    decision_packet, ensure_ascii=False, allow_nan=False,
                    separators=(",", ":"), sort_keys=True,
                )}]) if decision_packet else None
            ),
            "optional_evidence_call_count": sum(
                1 for event in result.evidence_events
                if event.get("status") == "ok" and event.get("operation") in optional_operations
            ),
            "evidence_events": [dict(item) for item in result.evidence_events],
            "subagent_events": [dict(item) for item in result.subagent_events],
            "collaboration": dict(result.collaboration),
            "usage_observation": dict(result.usage_observation),
            "circuit": self._circuit.snapshot(),
            "repair_mode": repair_context is not None,
            "strict_null_pruned_count": strict_null_pruned_count,
            "session_mode": (
                "pa_continuation"
                if session_options["resume_thread_id"]
                else "pa_session_origin"
                if session_options["persist_session"]
                else "ephemeral"
            ),
            "parent_job_digest": session_options["parent_job_digest"],
        }
        from pa_agent.ai.analysis_metrics import call_metrics
        runtime["timings"] = result.timings
        runtime["metrics"] = call_metrics(result.usage, runtime, wall_ms=(time.monotonic() - started) * 1000)
        reply = AIReply(
            content=content,
            reasoning_content="",
            raw={
                "content": content,
                "codex": {
                    "job_digest": job.manifest.job_digest,
                    "thread_id": result.thread_id,
                },
            },
            usage=result.usage,
            request_id=job.manifest.request_id,
            latency_ms=result.latency_ms,
        )
        reply._pa_model_call_meta = dict(runtime)
        if session_options["persist_session"]:
            if not result.thread_id:
                self._raise_failure(
                    ProviderFailure(
                        kind="invalid_request",
                        message="Codex PA Stage 1 did not return a resumable thread identity",
                        retryable=False,
                    ),
                    attempts=(attempt_meta,),
                )
            with self._attempt_lock:
                self._pa_session = {
                    "thread_id": result.thread_id,
                    "parent_job_digest": job.manifest.job_digest,
                    "task_id": str((request.agent_context or {}).get("task_id") or ""),
                    "snapshot_hash": str(request.snapshot_hash or "").lower(),
                    "cutoff_ts_open": int(request.cutoff_ts_open or 0),
                }
        return AnalysisRoleResult(
            reply=reply,
            backend_id=self.backend_id,
            runtime=runtime,
        )

    def _next_attempt(self, request: AnalysisRoleRequest) -> int:
        task_id = str((request.agent_context or {}).get("task_id") or "")
        key = "|".join(
            (
                task_id,
                str(request.snapshot_hash or "").lower(),
                str(request.cutoff_ts_open or ""),
                request.role,
            )
        )
        with self._attempt_lock:
            attempt = self._role_attempts.get(key, 0) + 1
            self._role_attempts[key] = attempt
            return attempt

    def _prepare_session_invocation(
        self,
        request: AnalysisRoleRequest,
        attempt: int,
    ) -> tuple[AnalysisRoleRequest, dict[str, Any]]:
        """Bind continuation from trusted worker output, never caller authority."""
        context = dict(request.agent_context or {})
        context.pop("pa_session_continuation", None)
        task_id = str(context.get("task_id") or "")
        identity = {
            "task_id": task_id,
            "snapshot_hash": str(request.snapshot_hash or "").lower(),
            "cutoff_ts_open": int(request.cutoff_ts_open or 0),
        }
        options = {
            "persist_session": False,
            "resume_thread_id": None,
            "parent_job_digest": None,
        }
        with self._attempt_lock:
            if request.role == "stage1":
                # A new Stage 1 or any Stage 1 repair invalidates the previous
                # candidate.  Repair history contains an untrusted draft.
                self._pa_session = None
                options["persist_session"] = bool(task_id and attempt == 1)
            elif request.role == "stage2" and attempt == 1:
                candidate = dict(self._pa_session or {})
                self._pa_session = None
                if candidate and all(candidate.get(key) == value for key, value in identity.items()):
                    options["resume_thread_id"] = candidate["thread_id"]
                    options["parent_job_digest"] = candidate["parent_job_digest"]
                    context["pa_session_continuation"] = {
                        "parent_job_digest": candidate["parent_job_digest"],
                        "thread_id": candidate["thread_id"],
                    }
        if context != dict(request.agent_context or {}):
            request = replace(request, agent_context=context)
        return request, options

    def _raise_failure(
        self,
        failure: ProviderFailure,
        *,
        attempts: tuple[dict[str, Any], ...],
        original: BaseException | None = None,
    ) -> None:
        if self._request_full_chain_fallback and failure.kind in {
            "authentication",
            "rate_limited",
            "timeout",
            "connection",
            "unavailable",
        }:
            raise FullChainFallbackRequired(
                failure,
                original=original,
                attempts=attempts,
            ) from original
        raise ModelCallFailure(
            failure,
            original=original,
            attempts=attempts,
        ) from original


def _worker_failure(exc: CodexWorkerError) -> ProviderFailure:
    from pa_agent.agent_runtime.codex_worker import CodexWorkerStopped

    if isinstance(exc, CodexWorkerStopped):
        return ProviderFailure(
            kind="role_stopped",
            message=exc.stop.reason,
            retryable=False,
        )
    if isinstance(exc, CodexWorkerAuthenticationError):
        return ProviderFailure(
            kind="authentication",
            message="Codex authentication failed",
            retryable=False,
        )
    if isinstance(exc, CodexWorkerRateLimited):
        return ProviderFailure(
            kind="rate_limited",
            message="Codex was rate limited",
            retryable=True,
        )
    if isinstance(exc, CodexWorkerTimeout):
        return ProviderFailure(
            kind="timeout",
            message="Codex execution timed out",
            retryable=True,
        )
    if isinstance(exc, CodexWorkerAvailabilityError):
        return ProviderFailure(
            kind="unavailable",
            message="Codex execution unavailable",
            retryable=True,
        )
    if isinstance(exc, CodexWorkerOutputError):
        return ProviderFailure(
            kind="invalid_request",
            message="Codex output rejected before PA validation",
            retryable=False,
        )
    return ProviderFailure(
        kind="internal",
        message="Codex worker failed",
        retryable=False,
    )


def _verify_job_request(
    job: Any,
    request: AnalysisRoleRequest,
    *,
    attempt: int,
) -> None:
    """Bind a published immutable bundle to the exact invocation that requested it."""
    if job.manifest.role != request.role:
        raise ValueError("frozen job role mismatch")
    if job.manifest.attempt != attempt:
        raise ValueError("frozen job attempt mismatch")
    source_request_digest = job.request.get("source_request_digest")
    if source_request_digest is not None:
        if source_request_digest != analysis_role_request_digest(request, attempt):
            raise ValueError("frozen job source request mismatch")
    elif list(job.request.get("messages") or []) != request.messages:
        raise ValueError("frozen job messages mismatch")
    expected_schema = (
        PATCH_ENVELOPE_SCHEMA
        if extract_repair_context(request.messages) is not None
        else dict(request.schema or {})
    )
    if dict(job.output_schema) != dict(expected_schema):
        raise ValueError("frozen job output schema mismatch")
    if request.snapshot_hash is not None:
        if job.manifest.snapshot_hash != str(request.snapshot_hash).lower():
            raise ValueError("frozen job snapshot mismatch")
    if request.cutoff_ts_open is not None:
        if job.manifest.cutoff_ts_open != int(request.cutoff_ts_open):
            raise ValueError("frozen job cutoff mismatch")
