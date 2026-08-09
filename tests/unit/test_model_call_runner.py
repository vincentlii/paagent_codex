from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pa_agent.ai.model_call_runner import ModelCallRunner
from pa_agent.ai.provider_capabilities import (
    resolve_provider_capabilities,
    response_format_for,
)
from pa_agent.ai.provider_failures import ModelCallFailure
from pa_agent.config.settings import AIProviderSettings


class _StatusError(RuntimeError):
    def __init__(self, status_code: int, message: str = "request failed") -> None:
        super().__init__(message)
        self.status_code = status_code


def _reply(content: str = '{"ok":true}') -> MagicMock:
    return MagicMock(content=content)


def _run(
    client: MagicMock,
    settings: AIProviderSettings,
    *,
    attempts: int = 2,
) -> object:
    return ModelCallRunner(
        client,
        settings,
        max_transport_attempts=attempts,
        sleep=lambda _: None,
    ).run_stream(
        [{"role": "user", "content": "return json"}],
        stage="stage1",
        schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        on_reasoning_token=None,
        on_content_token=None,
        cancel_token=None,
        thinking=True,
        reasoning_effort="high",
    )


def test_deepseek_uses_documented_json_object_mode() -> None:
    settings = AIProviderSettings(
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
    )
    client = MagicMock()
    client.stream_chat.return_value = _reply()

    result = _run(client, settings)

    assert result.structured_output_mode == "json_object"
    assert client.stream_chat.call_args.kwargs["response_format"] == {
        "type": "json_object"
    }


def test_rejected_structured_output_downgrades_once_to_text() -> None:
    settings = AIProviderSettings(
        base_url="https://gateway.example/v1",
        model="custom",
        structured_output_mode="json_object",
    )
    client = MagicMock()
    client.stream_chat.side_effect = [
        _StatusError(400, "unknown response_format parameter"),
        _reply(),
    ]

    result = _run(client, settings)

    assert [item["structured_output_mode"] for item in result.attempts] == [
        "json_object",
        "text",
    ]
    assert "response_format" not in client.stream_chat.call_args.kwargs


def test_retryable_failure_is_retried_with_a_bounded_attempt_ledger() -> None:
    settings = AIProviderSettings(structured_output_mode="text")
    client = MagicMock()
    client.stream_chat.side_effect = [_StatusError(429), _reply()]

    result = _run(client, settings)

    assert client.stream_chat.call_count == 2
    assert result.attempts[0]["failure_kind"] == "rate_limited"
    assert result.attempts[1]["status"] == "completed"


@pytest.mark.parametrize("status_code", [400, 401, 402, 403, 422])
def test_non_retryable_failure_stops_after_one_attempt(status_code: int) -> None:
    settings = AIProviderSettings(structured_output_mode="text")
    client = MagicMock()
    client.stream_chat.side_effect = _StatusError(status_code)

    with pytest.raises(ModelCallFailure) as caught:
        _run(client, settings)

    assert client.stream_chat.call_count == 1
    assert caught.value.failure.status_code == status_code
    assert caught.value.failure.retryable is False


def test_empty_content_retries_then_reports_a_typed_failure() -> None:
    settings = AIProviderSettings(structured_output_mode="text")
    client = MagicMock()
    client.stream_chat.return_value = _reply("  ")

    with pytest.raises(ModelCallFailure) as caught:
        _run(client, settings)

    assert client.stream_chat.call_count == 2
    assert caught.value.failure.kind == "empty_response"
    assert len(caught.value.attempts) == 2


def test_unknown_gateway_defaults_to_text_mode() -> None:
    settings = AIProviderSettings(
        base_url="https://gateway.example/v1",
        model="custom",
    )

    capabilities = resolve_provider_capabilities(settings)

    assert capabilities.structured_output_mode == "text"
    assert response_format_for(
        capabilities,
        schema=None,
        schema_name="stage1",
    ) is None


def test_text_call_never_inherits_provider_json_mode() -> None:
    settings = AIProviderSettings(
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
    )
    client = MagicMock()
    client.stream_chat.return_value = _reply("plain text")

    result = ModelCallRunner(
        client,
        settings,
        sleep=lambda _: None,
    ).run_stream(
        [{"role": "user", "content": "chat"}],
        stage="free_chat",
        schema=None,
        on_reasoning_token=None,
        on_content_token=None,
        cancel_token=None,
        thinking=True,
        reasoning_effort="high",
        structured_output=False,
    )

    assert result.structured_output_mode == "text"
    assert "response_format" not in client.stream_chat.call_args.kwargs


def test_structured_output_downgrade_keeps_one_compatibility_attempt() -> None:
    settings = AIProviderSettings(
        base_url="https://gateway.example/v1",
        model="custom",
        structured_output_mode="json_object",
    )
    client = MagicMock()
    client.stream_chat.side_effect = [
        _StatusError(429, "rate limited"),
        _StatusError(400, "unknown response_format parameter"),
        _reply(),
    ]

    result = _run(client, settings, attempts=2)

    assert client.stream_chat.call_count == 3
    assert [item["structured_output_mode"] for item in result.attempts] == [
        "json_object",
        "json_object",
        "text",
    ]


def test_text_fallback_retains_remaining_transient_retry() -> None:
    settings = AIProviderSettings(
        base_url="https://gateway.example/v1",
        model="custom",
        structured_output_mode="json_object",
    )
    client = MagicMock()
    client.stream_chat.side_effect = [
        _StatusError(400, "unknown response_format parameter"),
        _StatusError(429, "rate limited"),
        _reply(),
    ]

    result = _run(client, settings, attempts=2)

    assert client.stream_chat.call_count == 3
    assert result.attempts[-1]["structured_output_mode"] == "text"


def test_attempt_ledger_records_effective_reasoning_controls() -> None:
    settings = AIProviderSettings(structured_output_mode="text")
    client = MagicMock()
    client.stream_chat.return_value = _reply()

    result = ModelCallRunner(client, settings, sleep=lambda _: None).run_stream(
        [{"role": "user", "content": "chat"}],
        stage="stage2",
        schema=None,
        on_reasoning_token=None,
        on_content_token=None,
        cancel_token=None,
        thinking=False,
        reasoning_effort="max",
        structured_output=False,
    )

    assert result.attempts[0]["thinking"] is False
    assert result.attempts[0]["reasoning_effort"] is None
    assert client.stream_chat.call_args.kwargs["thinking"] is False
    assert client.stream_chat.call_args.kwargs["reasoning_effort"] is None


def test_deepseek_ledger_distinguishes_requested_and_provider_effective_effort() -> None:
    settings = AIProviderSettings(
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        structured_output_mode="text",
    )
    client = MagicMock()
    client.stream_chat.return_value = _reply()

    result = ModelCallRunner(client, settings, sleep=lambda _: None).run_stream(
        [{"role": "user", "content": "chat"}],
        stage="stage2",
        schema=None,
        on_reasoning_token=None,
        on_content_token=None,
        cancel_token=None,
        thinking=True,
        reasoning_effort="low",
        structured_output=False,
    )

    attempt = result.attempts[0]
    assert attempt["requested_reasoning_effort"] == "low"
    assert attempt["effective_reasoning_effort"] == "high"
    assert attempt["reasoning_effort"] == "high"
    assert client.stream_chat.call_args.kwargs["reasoning_effort"] == "high"


def test_deepseek_model_through_gateway_uses_the_same_effective_effort() -> None:
    settings = AIProviderSettings(
        base_url="https://gateway.example/v1",
        model="deepseek-v4-pro",
        structured_output_mode="text",
    )
    client = MagicMock()
    client.stream_chat.return_value = _reply()

    result = ModelCallRunner(client, settings, sleep=lambda _: None).run_stream(
        [{"role": "user", "content": "chat"}],
        stage="stage2",
        schema=None,
        on_reasoning_token=None,
        on_content_token=None,
        cancel_token=None,
        thinking=True,
        reasoning_effort="medium",
        structured_output=False,
    )

    attempt = result.attempts[0]
    assert attempt["requested_reasoning_effort"] == "medium"
    assert attempt["effective_reasoning_effort"] == "high"
    assert client.stream_chat.call_args.kwargs["reasoning_effort"] == "high"
