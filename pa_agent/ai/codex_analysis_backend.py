"""Constrained Codex implementation of the analysis-role backend seam."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

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
from pa_agent.ai.analysis_role_backend import AnalysisRoleRequest, AnalysisRoleResult
from pa_agent.ai.deepseek_client import AIReply, CancelledError
from pa_agent.ai.provider_failures import ModelCallFailure, ProviderFailure


class FullChainFallbackRequired(ModelCallFailure):
    """Availability failure that must restart the complete PA chain on Direct."""


class CodexCircuitBreaker:
    """Small process-local breaker that prevents a failing scheduler from storming Codex."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be positive")
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._failures = 0
        self._opened_until = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = self._clock()
            if self._opened_until and now >= self._opened_until:
                self._opened_until = 0.0
                self._failures = 0
            return not self._opened_until

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_until = 0.0

    def record_availability_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._failure_threshold:
                self._opened_until = self._clock() + self._cooldown_seconds


class CodexAnalysisRoleBackend:
    """Run one role as a fresh Codex task over a published frozen job."""

    validation_retry_limit = 1

    def __init__(
        self,
        *,
        worker: CodexCliWorker,
        publish_job: Callable[[AnalysisRoleRequest, int], Path],
        request_full_chain_fallback: bool = False,
        circuit_breaker: CodexCircuitBreaker | None = None,
        runtime_identity: dict[str, Any] | None = None,
    ) -> None:
        self._worker = worker
        self._publish_job = publish_job
        self._request_full_chain_fallback = bool(request_full_chain_fallback)
        self._circuit = circuit_breaker or CodexCircuitBreaker()
        self._runtime_identity = dict(runtime_identity or {})
        self._role_attempts: dict[str, int] = {}
        self._attempt_lock = threading.Lock()

    @property
    def backend_id(self) -> str:
        return "codex_agent"

    @property
    def runtime_identity(self) -> dict[str, Any]:
        return dict(self._runtime_identity)

    def run(
        self,
        request: AnalysisRoleRequest,
        *,
        on_reasoning_token: Callable[[str], None] | None,
        on_content_token: Callable[[str], None] | None,
        cancel_token: Any,
        on_attempt_error: Callable[[Exception], None] | None = None,
    ) -> AnalysisRoleResult:
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

        attempt = self._next_attempt(request.role)
        if request.snapshot_hash is None or request.cutoff_ts_open is None:
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
        }
        try:
            result = self._worker.run(published)
        except CodexWorkerError as exc:
            if on_attempt_error is not None:
                on_attempt_error(exc)
            failure = _worker_failure(exc)
            if isinstance(exc, CodexWorkerAvailabilityError):
                self._circuit.record_availability_failure()
            self._raise_failure(failure, attempts=(attempt_meta,), original=exc)

        self._circuit.record_success()
        if on_content_token is not None:
            on_content_token(result.content)
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
        runtime = {
            "analysis_backend": self.backend_id,
            "role": request.role,
            "provider_id": "openai_codex",
            "structured_output_mode": "json_schema",
            "requested_thinking": request.thinking,
            "requested_reasoning_effort": request.reasoning_effort,
            "effective_thinking": True,
            "effective_reasoning_effort": self._runtime_identity.get(
                "reasoning_effort", request.reasoning_effort
            ),
            "model": self._runtime_identity.get("model"),
            "runtime_version": self._runtime_identity.get("runtime_version"),
            "provider_fallback_used": False,
            "transport_attempts": [attempt_meta],
            "job_digest": job.manifest.job_digest,
            "thread_id": result.thread_id,
            "initial_context_bytes": initial_context_bytes,
            "evidence_call_count": len(result.evidence_events),
            "evidence_response_bytes": evidence_bytes,
        }
        reply = AIReply(
            content=result.content,
            reasoning_content="",
            raw={
                "content": result.content,
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
        return AnalysisRoleResult(
            reply=reply,
            backend_id=self.backend_id,
            runtime=runtime,
        )

    def _next_attempt(self, role: str) -> int:
        with self._attempt_lock:
            attempt = self._role_attempts.get(role, 0) + 1
            self._role_attempts[role] = attempt
            return attempt

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
    if list(job.request.get("messages") or []) != request.messages:
        raise ValueError("frozen job messages mismatch")
    if dict(job.output_schema) != dict(request.schema or {}):
        raise ValueError("frozen job output schema mismatch")
    if request.snapshot_hash is not None:
        if job.manifest.snapshot_hash != str(request.snapshot_hash).lower():
            raise ValueError("frozen job snapshot mismatch")
    if request.cutoff_ts_open is not None:
        if job.manifest.cutoff_ts_open != int(request.cutoff_ts_open):
            raise ValueError("frozen job cutoff mismatch")
