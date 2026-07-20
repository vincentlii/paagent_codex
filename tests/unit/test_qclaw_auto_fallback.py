"""Tests for bounded, run-scoped provider fallback."""
from __future__ import annotations

from copy import deepcopy
from unittest.mock import MagicMock, patch

import openai
import pytest

from pa_agent.ai.provider_failures import ModelCallFailure
from pa_agent.config.settings import Settings
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from tests.fixtures.validators import schema_test_validator


def _reply() -> MagicMock:
    return MagicMock(
        content='{"gate_result":"wait"}',
        reasoning_content="",
        raw={},
        usage=MagicMock(
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            cached_prompt_tokens=0,
        ),
        latency_ms=1.0,
    )


def _orchestrator(settings: Settings, client: MagicMock) -> TwoStageOrchestrator:
    return TwoStageOrchestrator(
        client=client,
        assembler=MagicMock(),
        router=MagicMock(),
        validator=schema_test_validator(),
        pending_writer=MagicMock(),
        exp_reader=MagicMock(),
        settings=settings,
    )


def _call(orchestrator: TwoStageOrchestrator) -> object:
    return orchestrator._stream_chat_resilient(
        [{"role": "user", "content": "return json"}],
        stage="stage1",
        on_reasoning_token=None,
        on_content_token=None,
        cancel_token=MagicMock(is_set=MagicMock(return_value=False)),
        thinking=True,
        reasoning_effort="max",
        stage_label="Stage 1",
    )


def test_stream_chat_uses_bounded_ephemeral_fallback_without_mutating_settings() -> None:
    settings = Settings()
    settings.provider.model = "openclaw"
    settings.provider.base_url = "http://127.0.0.1:53555/v1"
    before = deepcopy(settings.model_dump())

    primary = MagicMock()
    primary.stream_chat.side_effect = openai.APIConnectionError(
        request=MagicMock(),
        message="Connection error.",
    )
    fallback = MagicMock()
    fallback.stream_chat.return_value = _reply()
    orchestrator = _orchestrator(settings, primary)
    fallback_provider = settings.provider.model_copy(
        update={"structured_output_mode": "text"}
    )

    with patch.object(
        orchestrator,
        "_build_ephemeral_fallback",
        return_value=("QClaw", fallback, fallback_provider),
    ) as build:
        result = _call(orchestrator)

    assert result.content
    assert primary.stream_chat.call_count == 2
    assert fallback.stream_chat.call_count == 1
    build.assert_called_once_with("openclaw")
    assert settings.model_dump() == before
    assert result._pa_model_call_meta["provider_fallback_used"] is True
    assert [
        item["provider_role"]
        for item in result._pa_model_call_meta["transport_attempts"]
    ] == ["primary", "primary", "fallback"]


@pytest.mark.parametrize("status_code", [400, 401, 402, 403, 422])
def test_non_retryable_provider_failure_never_falls_back(status_code: int) -> None:
    settings = Settings()
    client = MagicMock()
    response = MagicMock(status_code=status_code, headers={})
    client.stream_chat.side_effect = openai.APIStatusError(
        "request failed",
        response=response,
        body=None,
    )
    orchestrator = _orchestrator(settings, client)

    with (
        patch.object(orchestrator, "_build_ephemeral_fallback") as build,
        pytest.raises(ModelCallFailure) as caught,
    ):
        _call(orchestrator)

    assert caught.value.failure.status_code == status_code
    assert caught.value.failure.retryable is False
    assert client.stream_chat.call_count == 1
    build.assert_not_called()


def test_ephemeral_fallback_builds_from_a_clone() -> None:
    settings = Settings()
    settings.provider.model = "openclaw"
    before = deepcopy(settings.model_dump())
    orchestrator = _orchestrator(settings, MagicMock())
    built_client = object()

    def apply(candidate: Settings) -> None:
        candidate.provider.base_url = "http://127.0.0.1:53555/v1"
        candidate.provider.api_key = "runtime-only"
        return None

    with (
        patch(
            "pa_agent.ai.qclaw_connector.apply_qclaw_provider_to_settings",
            side_effect=apply,
        ),
        patch("pa_agent.ai.qclaw_connector.is_openclaw_model", return_value=True),
        patch("pa_agent.ai.cursor_connector.is_openclaw_cs_model", return_value=False),
        patch("pa_agent.ai.workbuddy_connector.is_openclaw_wb_model", return_value=False),
        patch("pa_agent.ai.client_factory.create_ai_client", return_value=built_client),
    ):
        result = orchestrator._build_ephemeral_fallback("openclaw")

    assert result is not None
    assert result[0] == "QClaw"
    assert result[1] is built_client
    assert result[2].api_key == "runtime-only"
    assert settings.model_dump() == before
