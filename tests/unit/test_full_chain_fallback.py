from __future__ import annotations

from unittest.mock import MagicMock

from pa_agent.ai.codex_analysis_backend import FullChainFallbackRequired
from pa_agent.ai.provider_failures import ProviderFailure
from pa_agent.orchestrator.backend_fallback import FullChainFallbackOrchestrator
from pa_agent.util.threading import OrchestratorEvent
from pa_agent.util.threading import CancelToken
from tests.unit.test_decision_nodes_orchestrator import (
    _insufficient_frame_19bars,
    _make_orchestrator,
)


def test_full_chain_fallback_discards_primary_events_and_restarts_cold_backend() -> None:
    failure = FullChainFallbackRequired(
        ProviderFailure(kind="timeout", message="codex timeout", retryable=True),
        attempts=({"stage": "stage2", "analysis_backend": "codex_agent"},),
    )
    primary = MagicMock()

    def fail(_frame, _cancel, on_event, **_kwargs):
        on_event(OrchestratorEvent.Stage1Started)
        raise failure

    primary.submit.side_effect = fail
    cold = MagicMock()
    cold_record = MagicMock()
    cold_record.role_runtime = {"existing": True}

    def succeed(_frame, _cancel, on_event, **_kwargs):
        on_event(OrchestratorEvent.Stage1Started)
        on_event(OrchestratorEvent.RecordSaved)
        return cold_record

    cold.submit.side_effect = succeed
    wrapper = FullChainFallbackOrchestrator(primary=primary, cold_fallback=cold)
    events: list[OrchestratorEvent] = []

    result = wrapper.submit(object(), object(), events.append, htf_context={})

    assert result is cold_record
    assert events == [OrchestratorEvent.Stage1Started, OrchestratorEvent.RecordSaved]
    assert cold.submit.call_count == 1
    assert result.role_runtime["backend_fallback"]["from"] == "codex_agent"
    assert result.role_runtime["backend_fallback"]["to"] == "direct"
    assert result.role_runtime["backend_fallback"]["restart_scope"] == "full_pa_chain"


def test_fallback_discards_every_primary_callback_and_injects_audit_before_cold_run() -> None:
    failure = FullChainFallbackRequired(
        ProviderFailure(kind="timeout", message="codex timeout", retryable=True),
        attempts=({"stage": "stage2", "analysis_backend": "codex_agent"},),
    )
    primary = MagicMock()

    def fail(_frame, _cancel, on_event, **callbacks):
        on_event(OrchestratorEvent.Stage1Started)
        callbacks["on_stage1_content"]("codex-stage1")
        callbacks["on_stage_prompt"]("stage1", "system", "codex-prompt")
        callbacks["on_stage2_files"](["codex-file"])
        raise failure

    primary.submit.side_effect = fail
    persisted: list[dict] = []
    cold_record = MagicMock()

    class Cold:
        def submit(self, _frame, _cancel, on_event, **callbacks):
            fallback_context = callbacks["backend_fallback_context"]
            persisted.append(dict(fallback_context))
            cold_record.role_runtime = {"backend_fallback": dict(fallback_context)}
            on_event(OrchestratorEvent.Stage1Started)
            callbacks["on_stage1_content"]("direct-stage1")
            return cold_record

    wrapper = FullChainFallbackOrchestrator(primary=primary, cold_fallback=Cold())
    visible: list[tuple[str, object]] = []

    result = wrapper.submit(
        object(),
        object(),
        lambda event: visible.append(("event", event)),
        on_stage1_content=lambda value: visible.append(("content", value)),
        on_stage_prompt=lambda *value: visible.append(("prompt", value)),
        on_stage2_files=lambda value: visible.append(("files", value)),
    )

    assert visible == [
        ("event", OrchestratorEvent.Stage1Started),
        ("content", "direct-stage1"),
    ]
    assert persisted[0]["from"] == "codex_agent"
    assert result.role_runtime["backend_fallback"] == persisted[0]


def test_backend_fallback_context_is_on_record_before_orchestrator_persists() -> None:
    orchestrator, _client, _assembler, writer = _make_orchestrator()
    token = CancelToken()
    token.set()
    fallback_context = {
        "from": "codex_agent",
        "to": "direct",
        "restart_scope": "full_pa_chain",
    }

    result = orchestrator.submit(
        _insufficient_frame_19bars(),
        token,
        lambda _event: None,
        backend_fallback_context=fallback_context,
    )

    persisted = writer.save_partial.call_args.args[0]
    assert persisted.role_runtime["backend_fallback"] == fallback_context
    assert result.role_runtime["backend_fallback"] == fallback_context
