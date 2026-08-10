"""Integration test: happy path — both stages succeed.

Task 11.4
"""
from __future__ import annotations

import copy
from unittest.mock import MagicMock

from tests.fixtures.validators import schema_test_validator
from pa_agent.ai.router import route_strategy_files
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken, OrchestratorEvent

from .conftest import VALID_STAGE1, VALID_STAGE2, make_reply


def _provider_drafts() -> tuple[dict, dict]:
    stage1 = copy.deepcopy(VALID_STAGE1)
    for key in ("cycle_position", "alternative_cycle_position", "direction", "entry_setup", "gate_result"):
        stage1.pop(key)
    stage1.update({
        "protocol_version": "pa_draft_v2",
        "diagnosis_confidence_reasoning": "The primary PA hypothesis has coherent evidence.",
        "market_hypotheses": {
            "primary": {
                "cycle_position": "normal_channel", "direction": "bullish",
                "setup_hint": "buy on pullback", "thesis": "bull channel continuation",
                "supporting_fact_refs": ["PA.WINDOW.K5", "PA.BAR.K1"],
                "challenging_fact_refs": ["PA.BAR.K2"],
                "theory_rule_ids": ["BROOKS.CHANNEL.CONTEXT"],
                "confirmation_condition": "continued bullish follow-through",
                "disconfirmation_condition": "clear channel failure",
            },
            "alternatives": [],
        },
        "pa_evidence": {"spike_analysis": None},
    })
    stage1["gate_trace"] = [
        {"node_id": item["node_id"], "answer": item["answer"], "reason": item["reason"],
         "evidence_refs": ["PA.WINDOW.K5"], **({"branch": item["branch"]} if item.get("branch") else {})}
        for item in VALID_STAGE1["gate_trace"]
    ]

    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2.update({"protocol_version": "pa_draft_v2", "decision_intent": "trade"})
    stage2.pop("terminal")
    stage2.pop("next_cycle_prediction")
    stage2["decision"].pop("diagnosis_confidence")
    stage2["decision"].pop("diagnosis_confidence_reasoning")
    stage2["decision_trace"] = [
        {"node_id": item["node_id"], "answer": item["answer"], "reason": item["reason"],
         "evidence_refs": ["PA.WINDOW.K5"]}
        for item in VALID_STAGE2["decision_trace"] if item["node_id"] != "10.3"
    ]
    return stage1, stage2


def test_happy_path(frame, pending_writer, assembler, exp_reader):
    """Both stages return valid JSON → full record saved, counter stays at 0."""
    client = MagicMock()
    client.stream_chat.side_effect = [
        make_reply(VALID_STAGE1),
        make_reply(VALID_STAGE2),
    ]

    validator = schema_test_validator()
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=validator,
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )

    events: list[OrchestratorEvent] = []
    cancel_token = CancelToken()

    record = orchestrator.submit(
        frame=frame,
        cancel_token=cancel_token,
        on_event=events.append,
    )

    # Event sequence
    assert events == [
        OrchestratorEvent.Stage1Started,
        OrchestratorEvent.Stage1Done,
        OrchestratorEvent.Stage2Started,
        OrchestratorEvent.Stage2Done,
        OrchestratorEvent.RecordSaved,
    ]

    # Record has both stages populated
    assert record.stage1_diagnosis is not None
    assert record.stage2_decision is not None

    # save_full was called (not save_partial)
    pending_writer.save_full.assert_called_once_with(record)
    pending_writer.save_partial.assert_not_called()


def test_provider_drafts_complete_the_real_orchestrator_path(
    frame, pending_writer, assembler, exp_reader,
):
    stage1, stage2 = _provider_drafts()
    client = MagicMock()
    client.stream_chat.side_effect = [make_reply(stage1), make_reply(stage2)]
    orchestrator = TwoStageOrchestrator(
        client=client, assembler=assembler, router=route_strategy_files,
        validator=schema_test_validator(), pending_writer=pending_writer,
        exp_reader=exp_reader,
    )

    record = orchestrator.submit(
        frame=frame, cancel_token=CancelToken(), on_event=lambda _event: None,
    )

    assert record.run_outcome["status"] == "completed"
    assert record.stage1_diagnosis["gate_result"] == "proceed"
    assert record.stage1_diagnosis["market_hypotheses"]["primary"]["hypothesis_id"] == "pa-primary"
    assert record.stage2_decision["terminal"]["outcome"] == "trade"
    assert any(item["node_id"] == "10.3" for item in record.stage2_decision["decision_trace"])
    assert record.stage1_response["content"].find('"protocol_version"') >= 0
    pending_writer.save_full.assert_called_once()
