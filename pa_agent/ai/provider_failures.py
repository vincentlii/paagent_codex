"""Typed provider failures used by every model call path."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProviderFailureKind = Literal[
    "cancelled",
    "role_stopped",
    "invalid_request",
    "authentication",
    "quota",
    "rate_limited",
    "timeout",
    "connection",
    "unavailable",
    "empty_response",
    "internal",
]

_RETRYABLE = frozenset(
    {"rate_limited", "timeout", "connection", "unavailable", "empty_response"}
)


@dataclass(frozen=True)
class ProviderFailure:
    kind: ProviderFailureKind
    message: str
    status_code: int | None = None
    retryable: bool = False


class ModelCallFailure(RuntimeError):
    def __init__(
        self,
        failure: ProviderFailure,
        *,
        original: BaseException | None = None,
        attempts: tuple[dict, ...] = (),
    ) -> None:
        super().__init__(failure.message)
        self.failure = failure
        self.original = original
        self.attempts = attempts


def classify_provider_exception(exc: BaseException) -> ProviderFailure:
    from pa_agent.ai.deepseek_client import CancelledError

    if isinstance(exc, CancelledError):
        return _failure("cancelled", str(exc), retryable=False)

    status = _status_code(exc)
    if status in {400, 404, 409, 422}:
        return _failure("invalid_request", str(exc), status, False)
    if status in {401, 403}:
        return _failure("authentication", str(exc), status, False)
    if status == 402:
        return _failure("quota", str(exc), status, False)
    if status == 429:
        return _failure("rate_limited", str(exc), status, True)
    if status in {408, 504}:
        return _failure("timeout", str(exc), status, True)
    if status is not None and status >= 500:
        return _failure("unavailable", str(exc), status, True)

    try:
        import openai  # type: ignore[import]

        if isinstance(exc, openai.APITimeoutError):
            return _failure("timeout", str(exc), retryable=True)
        if isinstance(exc, openai.APIConnectionError):
            return _failure("connection", str(exc), retryable=True)
    except ImportError:
        pass

    try:
        import httpx  # type: ignore[import]

        if isinstance(exc, httpx.TimeoutException):
            return _failure("timeout", str(exc), retryable=True)
        if isinstance(
            exc,
            (
                httpx.ReadError,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            ),
        ):
            return _failure("connection", str(exc), retryable=True)
    except ImportError:
        pass

    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, TimeoutError)):
        return _failure(
            "timeout" if isinstance(exc, TimeoutError) else "connection",
            str(exc),
            retryable=True,
        )
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) in {
        10053,
        10054,
        10060,
    }:
        return _failure("connection", str(exc), retryable=True)
    cause = exc.__cause__
    if cause is not None and cause is not exc:
        nested = classify_provider_exception(cause)
        if nested.kind != "internal":
            return nested
    return _failure("internal", str(exc) or type(exc).__name__, retryable=False)


def structured_output_parameter_rejected(
    exc: BaseException,
    failure: ProviderFailure,
) -> bool:
    if isinstance(exc, TypeError) and "response_format" in str(exc):
        return True
    if failure.kind != "invalid_request":
        return False
    message = str(exc).lower()
    return "response_format" in message or "json_schema" in message or "json object" in message


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    if value is None:
        response = getattr(exc, "response", None)
        value = getattr(response, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _failure(
    kind: ProviderFailureKind,
    message: str,
    status_code: int | None = None,
    retryable: bool | None = None,
) -> ProviderFailure:
    return ProviderFailure(
        kind=kind,
        message=message,
        status_code=status_code,
        retryable=kind in _RETRYABLE if retryable is None else retryable,
    )
