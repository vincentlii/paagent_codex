"""Single transport path for capability-aware model calls."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from pa_agent.ai.provider_capabilities import (
    ProviderCapabilities,
    resolve_provider_capabilities,
    response_format_for,
)
from pa_agent.ai.provider_failures import (
    ModelCallFailure,
    ProviderFailure,
    classify_provider_exception,
    structured_output_parameter_rejected,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelCallResult:
    reply: Any
    capabilities: ProviderCapabilities
    structured_output_mode: str
    attempts: tuple[dict[str, Any], ...]


class ModelCallRunner:
    """Run one logical call with bounded transport retries and explicit fallback."""

    def __init__(
        self,
        client: Any,
        provider_settings: Any,
        *,
        max_transport_attempts: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        logger_: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._settings = provider_settings
        self._max_attempts = max(1, int(max_transport_attempts))
        self._sleep = sleep
        self._log = logger_ or logger

    def run_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        stage: str,
        schema: Mapping[str, Any] | None,
        on_reasoning_token: Callable[[str], None] | None,
        on_content_token: Callable[[str], None] | None,
        cancel_token: Any,
        thinking: bool,
        reasoning_effort: str,
        structured_output: bool = True,
        on_attempt_error: Callable[[Exception], None] | None = None,
    ) -> ModelCallResult:
        capabilities = resolve_provider_capabilities(
            self._settings,
            client=self._client,
        )
        response_format = (
            response_format_for(
                capabilities,
                schema=schema,
                schema_name=stage,
            )
            if structured_output
            else None
        )
        attempts: list[dict[str, Any]] = []
        structured_downgraded = False

        attempt_number = 0
        while attempt_number < self._max_attempts:
            attempt_number += 1
            started = time.monotonic()
            kwargs: dict[str, Any] = {
                "on_reasoning_token": on_reasoning_token,
                "on_content_token": on_content_token,
                "cancel_token": cancel_token,
                "thinking": thinking,
                "reasoning_effort": reasoning_effort,
            }
            if response_format is not None:
                kwargs["response_format"] = response_format
            try:
                reply = self._client.stream_chat(messages, **kwargs)
                if not str(getattr(reply, "content", "") or "").strip():
                    failure = ProviderFailure(
                        kind="empty_response",
                        message="provider returned empty content",
                        retryable=True,
                    )
                    attempts.append(
                        _attempt_entry(
                            attempt_number,
                            response_format,
                            "failed",
                            failure,
                            started,
                        )
                    )
                    if on_attempt_error is not None:
                        on_attempt_error(RuntimeError(failure.message))
                    if attempt_number < self._max_attempts:
                        self._sleep(_retry_delay(attempt_number))
                        continue
                    raise ModelCallFailure(
                        failure,
                        attempts=tuple(attempts),
                    )
                attempts.append(
                    _attempt_entry(
                        attempt_number,
                        response_format,
                        "completed",
                        None,
                        started,
                    )
                )
                return ModelCallResult(
                    reply=reply,
                    capabilities=capabilities,
                    structured_output_mode=(
                        str(response_format.get("type"))
                        if response_format is not None
                        else "text"
                    ),
                    attempts=tuple(attempts),
                )
            except ModelCallFailure:
                raise
            except Exception as exc:
                failure = classify_provider_exception(exc)
                attempts.append(
                    _attempt_entry(
                        attempt_number,
                        response_format,
                        "failed",
                        failure,
                        started,
                    )
                )
                if on_attempt_error is not None:
                    on_attempt_error(exc)
                if (
                    response_format is not None
                    and not structured_downgraded
                    and structured_output_parameter_rejected(exc, failure)
                ):
                    structured_downgraded = True
                    response_format = None
                    self._log.warning(
                        "Provider rejected structured output for %s; "
                        "retrying once in text mode",
                        stage,
                    )
                    continue
                if failure.retryable and attempt_number < self._max_attempts:
                    self._sleep(_retry_delay(attempt_number))
                    continue
                raise ModelCallFailure(
                    failure,
                    original=exc,
                    attempts=tuple(attempts),
                ) from exc

        raise AssertionError("model call loop exhausted without result")


def _attempt_entry(
    number: int,
    response_format: dict[str, Any] | None,
    status: str,
    failure: ProviderFailure | None,
    started: float,
) -> dict[str, Any]:
    return {
        "attempt": number,
        "status": status,
        "structured_output_mode": (
            str(response_format.get("type")) if response_format is not None else "text"
        ),
        "latency_ms": round((time.monotonic() - started) * 1000, 3),
        "failure_kind": failure.kind if failure is not None else None,
        "status_code": failure.status_code if failure is not None else None,
        "retryable": failure.retryable if failure is not None else None,
    }


def _retry_delay(attempt_number: int) -> float:
    return min(1.0, 0.2 * (2 ** max(0, attempt_number - 1)))
