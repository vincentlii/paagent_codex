"""Backend seam for role-scoped market analysis calls."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Protocol

from pa_agent.ai.model_call_runner import ModelCallResult, ModelCallRunner
from pa_agent.ai.provider_failures import ModelCallFailure

AnalysisRole = Literal["stage1", "stage2", "vpa_evidence", "joint_arbiter"]


@dataclass(frozen=True)
class AnalysisRoleRequest:
    """One backend-neutral role invocation over already assembled messages."""

    role: AnalysisRole
    messages: list[dict[str, Any]]
    schema: Mapping[str, Any] | None
    thinking: bool
    reasoning_effort: str
    structured_output: bool = True
    snapshot_hash: str | None = None
    cutoff_ts_open: int | None = None
    agent_context: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class AnalysisRoleResult:
    """Provider reply plus the runtime facts needed by audit callers."""

    reply: Any
    backend_id: str
    runtime: dict[str, Any]


class AnalysisRoleBackend(Protocol):
    """Execute one analysis role without owning validation or orchestration."""

    @property
    def backend_id(self) -> str: ...

    def run(
        self,
        request: AnalysisRoleRequest,
        *,
        on_reasoning_token: Callable[[str], None] | None,
        on_content_token: Callable[[str], None] | None,
        cancel_token: Any,
        on_attempt_error: Callable[[Exception], None] | None = None,
    ) -> AnalysisRoleResult: ...


class DirectAnalysisRoleBackend:
    """Current Provider path behind the backend-neutral role interface."""

    def __init__(
        self,
        *,
        client: Any,
        settings: Any,
        allow_provider_fallback: bool = True,
        profile_identity: Mapping[str, Any] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._allow_provider_fallback = bool(allow_provider_fallback)
        self._profile_identity = dict(profile_identity or {})
        self._logger = logger or logging.getLogger(__name__)

    @property
    def backend_id(self) -> str:
        return "direct"

    @property
    def client(self) -> Any:
        """Expose the owned client identity for assembly and isolation checks."""
        return self._client

    @property
    def runtime_identity(self) -> dict[str, Any]:
        """Expose the profile identity frozen into the owning task."""
        return dict(self._profile_identity)

    def run(
        self,
        request: AnalysisRoleRequest,
        *,
        on_reasoning_token: Callable[[str], None] | None,
        on_content_token: Callable[[str], None] | None,
        cancel_token: Any,
        on_attempt_error: Callable[[Exception], None] | None = None,
    ) -> AnalysisRoleResult:
        provider = self._provider_settings()
        try:
            result = self._run_once(
                client=self._client,
                provider=provider,
                request=request,
                on_reasoning_token=on_reasoning_token,
                on_content_token=on_content_token,
                cancel_token=cancel_token,
                on_attempt_error=on_attempt_error,
            )
            return self._complete(
                request,
                result,
                provider_fallback_used=False,
            )
        except ModelCallFailure as primary_failure:
            if (
                not primary_failure.failure.retryable
                or not self._allow_provider_fallback
            ):
                raise
            fallback = self._build_ephemeral_fallback()
            if fallback is None:
                raise
            name, fallback_client, fallback_provider = fallback
            self._logger.info(
                "%s direct provider unavailable (%s); trying ephemeral %s fallback",
                request.role,
                primary_failure.failure.kind,
                name,
            )
            try:
                result = self._run_once(
                    client=fallback_client,
                    provider=fallback_provider,
                    request=request,
                    on_reasoning_token=on_reasoning_token,
                    on_content_token=on_content_token,
                    cancel_token=cancel_token,
                    on_attempt_error=on_attempt_error,
                )
            except ModelCallFailure as fallback_failure:
                combined_attempts = (
                    *(
                        {**dict(attempt), "provider_role": "primary"}
                        for attempt in primary_failure.attempts
                    ),
                    *(
                        {**dict(attempt), "provider_role": "fallback"}
                        for attempt in fallback_failure.attempts
                    ),
                )
                raise ModelCallFailure(
                    fallback_failure.failure,
                    original=fallback_failure.original,
                    attempts=combined_attempts,
                ) from fallback_failure
            return self._complete(
                request,
                result,
                provider_fallback_used=True,
                prior_attempts=primary_failure.attempts,
            )

    @staticmethod
    def _run_once(
        *,
        client: Any,
        provider: Any,
        request: AnalysisRoleRequest,
        on_reasoning_token: Callable[[str], None] | None,
        on_content_token: Callable[[str], None] | None,
        cancel_token: Any,
        on_attempt_error: Callable[[Exception], None] | None,
    ) -> ModelCallResult:
        return ModelCallRunner(client, provider).run_stream(
            request.messages,
            stage=request.role,
            schema=request.schema,
            on_reasoning_token=on_reasoning_token,
            on_content_token=on_content_token,
            cancel_token=cancel_token,
            thinking=request.thinking,
            reasoning_effort=request.reasoning_effort,
            structured_output=request.structured_output,
            on_attempt_error=on_attempt_error,
        )

    def _provider_settings(self) -> Any:
        provider = getattr(self._settings, "provider", None)
        if provider is not None:
            return provider
        provider = getattr(self._client, "_settings", None)
        if provider is not None:
            return provider
        from pa_agent.config.settings import AIProviderSettings

        return AIProviderSettings(structured_output_mode="text")

    def _complete(
        self,
        request: AnalysisRoleRequest,
        result: ModelCallResult,
        *,
        provider_fallback_used: bool,
        prior_attempts: tuple[dict[str, Any], ...] = (),
    ) -> AnalysisRoleResult:
        provider_id = str(result.capabilities.provider_id)
        transport_attempts = [
            {**dict(attempt), "provider_role": "primary"}
            for attempt in prior_attempts
        ]
        transport_attempts.extend(
            {
                **dict(attempt),
                "provider_role": (
                    "fallback" if provider_fallback_used else "primary"
                ),
                "provider_id": provider_id,
            }
            for attempt in result.attempts
        )
        last_attempt = result.attempts[-1] if result.attempts else {}
        runtime = {
            "analysis_backend": self.backend_id,
            "role": request.role,
            "provider_id": provider_id,
            "model": getattr(provider, "model", None),
            "requested_model": self._profile_identity.get(
                "deepseek_model", getattr(provider, "model", None)
            ),
            "effective_model": getattr(provider, "model", None),
            "structured_output_mode": result.structured_output_mode,
            "requested_thinking": last_attempt.get("requested_thinking"),
            "requested_reasoning_effort": last_attempt.get(
                "requested_reasoning_effort"
            ),
            "effective_thinking": last_attempt.get("effective_thinking"),
            "effective_reasoning_effort": last_attempt.get(
                "effective_reasoning_effort"
            ),
            "thinking": last_attempt.get("thinking"),
            "reasoning_effort": last_attempt.get("reasoning_effort"),
            "profile_fingerprint": self._profile_identity.get(
                "profile_fingerprint"
            ),
            "provider_fallback_used": provider_fallback_used,
            "transport_attempts": transport_attempts,
        }
        try:
            result.reply._pa_model_call_meta = dict(runtime)
        except (AttributeError, TypeError):
            self._logger.debug("Provider reply does not support call metadata annotation")
        return AnalysisRoleResult(
            reply=result.reply,
            backend_id=self.backend_id,
            runtime=runtime,
        )

    def _build_ephemeral_fallback(self) -> tuple[str, Any, Any] | None:
        model_copy = getattr(self._settings, "model_copy", None)
        if not callable(model_copy):
            return None
        candidate = model_copy(deep=True)
        provider = getattr(candidate, "provider", None)
        if provider is None:
            return None
        original_model = str(getattr(provider, "model", "") or "")
        name = ""
        error: str | None = None

        from pa_agent.ai.cursor_connector import (
            apply_cursor_provider_to_settings,
            is_openclaw_cs_model,
        )
        from pa_agent.ai.qclaw_connector import (
            apply_qclaw_provider_to_settings,
            is_openclaw_model,
        )
        from pa_agent.ai.workbuddy_connector import (
            apply_workbuddy_provider_to_settings,
            is_openclaw_wb_model,
        )

        if is_openclaw_wb_model(original_model):
            name = "WorkBuddy"
            error = apply_workbuddy_provider_to_settings(candidate)
        elif is_openclaw_cs_model(original_model):
            name = "Cursor"
            error = apply_cursor_provider_to_settings(
                candidate,
                preferred_model=original_model,
            )
        elif is_openclaw_model(original_model):
            name = "QClaw"
            error = apply_qclaw_provider_to_settings(candidate)
        else:
            return None
        if error:
            self._logger.warning("%s fallback unavailable: %s", name, error)
            return None

        from pa_agent.ai.client_factory import create_ai_client

        fallback_provider = candidate.provider
        return (
            name,
            create_ai_client(fallback_provider, logger_=self._logger),
            fallback_provider,
        )
