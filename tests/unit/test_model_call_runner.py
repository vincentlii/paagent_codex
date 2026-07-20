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
