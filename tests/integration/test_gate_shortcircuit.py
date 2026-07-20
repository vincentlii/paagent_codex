"""Integration: Stage1 gate wait short-circuits Stage2 API call."""
from __future__ import annotations

import copy
from unittest.mock import MagicMock

from tests.fixtures.validators import schema_test_validator
from pa_agent.ai.json_validator import JsonValidator
from pa_agent.ai.router import route_strategy_files
from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog
from pa_agent.config.settings import PromptSettings, Settings
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken, OrchestratorEvent

from .conftest import VALID_STAGE1, make_reply


def test_gate_wait_skips_stage2_chat(
    frame, pending_writer, assembler, exp_reader,
) -> None:
    stage1_wait = copy.deepcopy(VALID_STAGE1)
    stage1_wait["gate_result"] = "wait"
    stage1_wait["gate_trace"] = [
        {
            "node_id": "1.2",
            "question": "是否能识别市场周期？",
            "answer": "否",
            "action": "等待",
            "reason": "无法识别周期",
            "bar_range": "K5-K1",
        }
    ]
    stage1_wait["cycle_position"] = "unknown"

    client = MagicMock()
    client.stream_chat.return_value = make_reply(stage1_wait)

    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=schema_test_validator(),
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )

    events: list[OrchestratorEvent] = []
    record = orchestrator.submit(
        frame=frame,
        cancel_token=CancelToken(),
        on_event=events.append,
    )

    assert client.stream_chat.call_count == 1
    assert record.stage2_decision is not None
    assert record.stage2_decision.get("gate_shortcircuited") is True
    assert record.stage2_decision["decision"]["order_type"] == "不下单"
    assert OrchestratorEvent.Stage2Done in events


def test_vpa_gate_wait_preserves_validated_context_instead_of_claiming_missing_data(
    frame, pending_writer, assembler, exp_reader,
) -> None:
    catalog = build_vpa_evidence_catalog(frame)
    evidence_id = next(
        item["evidence_id"]
        for item in catalog["evidence"]
        if item.get("source") == "volume"
    )
    stage1_wait = copy.deepcopy(VALID_STAGE1)
    stage1_wait.update(
        {
            "cycle_position": "unknown",
            "gate_result": "wait",
            "gate_trace": [
                {
                    "node_id": "1.2",
                    "question": "是否能识别市场周期？",
                    "answer": "否",
                    "action": "等待",
                    "reason": "当前存在多个合理周期假设。",
                    "bar_range": "K5-K1",
                }
            ],
            "vpa_context": {
                "catalog_id": catalog["catalog_id"],
                "data_status": catalog["data_status"],
                "summary": "Source quality limits the interpretation.",
                "observations": [
                    {
                        "evidence_ids": [evidence_id],
                        "finding_kind": "effort_result",
                        "interpretation": "The cited evidence adds independent effort/result context.",
                    }
                ],
                "background_relation": "supports",
                "entry_timing_relation": "challenges",
                "next_confirmation": {
                    "state": "pending_closed_bar",
                    "trigger": "Wait for the next closed bar and assess acceptance.",
                    "requires_closed_bar": True,
                },
            },
        }
    )
    client = MagicMock()
    client.stream_chat.return_value = make_reply(stage1_wait)
    settings = Settings(prompt=PromptSettings(vpa_mode="shadow"))
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=JsonValidator(settings),
        pending_writer=pending_writer,
        exp_reader=exp_reader,
        settings=settings,
        paired_arm="vpa",
        vpa_evidence_catalog=catalog,
    )

    record = orchestrator.submit(frame, CancelToken(), lambda _event: None)

    impact = record.stage2_decision["vpa_impact"]
    assert impact["background_relation"] == "supports"
    assert impact["entry_timing_relation"] == "challenges"
    assert impact["decision_effect"] == "keep_brooks_baseline"
    assert impact["evidence_used"] == []
    assert "未执行 VPA 入场影响评估" in impact["reason"]
