from __future__ import annotations

from unittest.mock import MagicMock

from pa_agent.ai.analysis_role_backend import (
    AnalysisRoleRequest,
    DirectAnalysisRoleBackend,
)
from pa_agent.config.settings import Settings


def test_direct_backend_runs_one_role_and_exposes_runtime_metadata() -> None:
    settings = Settings()
    client = MagicMock()
    reply = MagicMock(content='{"ok":true}')
    client.stream_chat.return_value = reply
    backend = DirectAnalysisRoleBackend(
        client=client,
        settings=settings,
        allow_provider_fallback=False,
    )
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }

    result = backend.run(
        AnalysisRoleRequest(
            role="stage1",
            messages=[{"role": "user", "content": "diagnose"}],
            schema=schema,
            thinking=True,
            reasoning_effort="low",
        ),
        on_reasoning_token=None,
        on_content_token=None,
        cancel_token=None,
    )

    assert result.reply is reply
    assert result.backend_id == "direct"
    assert result.runtime["role"] == "stage1"
    assert result.runtime["requested_reasoning_effort"] == "low"
    assert reply._pa_model_call_meta["analysis_backend"] == "direct"
    assert client.stream_chat.call_args.kwargs["thinking"] is True


def test_direct_backend_instances_do_not_share_clients() -> None:
    settings = Settings()
    first_client = MagicMock()
    second_client = MagicMock()

    first = DirectAnalysisRoleBackend(
        client=first_client,
        settings=settings.model_copy(deep=True),
    )
    second = DirectAnalysisRoleBackend(
        client=second_client,
        settings=settings.model_copy(deep=True),
    )

    assert first is not second
    assert first.client is first_client
    assert second.client is second_client
