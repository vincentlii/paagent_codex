from __future__ import annotations

import copy
import json

import jsonschema

from pa_agent.ai.fact_catalog import build_analysis_fact_catalog
from pa_agent.ai.pa_provider_contracts import (
    STAGE1_DRAFT_SCHEMA,
    STAGE2_DRAFT_SCHEMA,
    materialize_stage1_draft,
    materialize_stage2_draft,
)
from pa_agent.ai.prompts.schemas import STAGE1_SCHEMA, STAGE2_SCHEMA
from pa_agent.ai.json_validator import JsonValidator, Ok
from tests.integration.conftest import VALID_STAGE1, VALID_STAGE2, make_frame


def _trace_draft(trace: list[dict]) -> list[dict]:
    return [
        {
            "node_id": item["node_id"],
            "answer": item["answer"],
            "reason": item["reason"],
            **({"branch": item["branch"]} if item.get("branch") is not None else {}),
            "evidence_refs": ["PA.WINDOW.K5"],
        }
        for item in trace
    ]


def _stage1_draft() -> dict:
    draft = copy.deepcopy(VALID_STAGE1)
    for key in (
        "cycle_position",
        "alternative_cycle_position",
        "direction",
        "entry_setup",
        "gate_result",
    ):
        draft.pop(key)
    draft["protocol_version"] = "pa_draft_v2"
    draft["diagnosis_confidence_reasoning"] = "主假设证据更完整，但仍保留区间化解释。"
    draft["market_hypotheses"] = {
        "primary": {
            "cycle_position": "normal_channel",
            "direction": "bullish",
            "setup_hint": "buy on pullback",
            "thesis": "多头通道仍在延续。",
            "supporting_fact_refs": ["PA.WINDOW.K5", "PA.BAR.K1"],
            "challenging_fact_refs": ["PA.BAR.K2"],
            "theory_rule_ids": ["BROOKS.CHANNEL.CONTEXT"],
            "confirmation_condition": "下一根已收盘K线保持推进。",
            "disconfirmation_condition": "通道结构被明确破坏。",
        },
        "alternatives": [
            {
                "cycle_position": "trading_range",
                "direction": "neutral",
                "setup_hint": "wait for range edge",
                "thesis": "近期推进也可能只是区间摆动。",
                "supporting_fact_refs": ["PA.WINDOW.K5"],
                "challenging_fact_refs": ["PA.BAR.K1"],
                "theory_rule_ids": ["BROOKS.CHANNEL.CONTEXT"],
                "confirmation_condition": "后续收盘继续重叠。",
                "disconfirmation_condition": "出现持续趋势跟随。",
            }
        ],
    }
    draft["pa_evidence"] = {"spike_analysis": None}
    draft["gate_trace"] = _trace_draft(VALID_STAGE1["gate_trace"])
    return draft


def test_stage1_draft_materializes_legacy_view_without_inventing_hypotheses() -> None:
    frame = make_frame()
    catalog = build_analysis_fact_catalog(frame)
    draft = _stage1_draft()

    jsonschema.Draft7Validator(STAGE1_DRAFT_SCHEMA).validate(draft)
    result = materialize_stage1_draft(draft, catalog=catalog)
    jsonschema.Draft7Validator(STAGE1_SCHEMA).validate(result)

    assert result["cycle_position"] == "normal_channel"
    assert result["direction"] == "bullish"
    assert result["alternative_cycle_position"] == "trading_range"
    assert result["entry_setup"] == "buy on pullback"
    assert result["gate_result"] == "proceed"
    assert result["diagnosis_confidence_reasoning"].startswith("主假设")
    assert result["market_hypotheses"]["primary"]["hypothesis_id"] == "pa-primary"
    assert result["market_hypotheses"]["alternatives"][0]["direction"] == "neutral"
    assert result["gate_trace"][0]["question"]
    assert result["gate_trace"][0]["bar_range"] == "K5-K1"
    assert result["pa_evidence"]["fact_refs"] == [
        "PA.WINDOW.K5",
        "PA.BAR.K1",
        "PA.BAR.K2",
    ]


def test_stage1_gate_result_is_protocol_materialization() -> None:
    frame = make_frame()
    catalog = build_analysis_fact_catalog(frame)
    draft = _stage1_draft()
    draft["market_hypotheses"]["primary"]["cycle_position"] = "unknown"
    draft["gate_trace"] = [
        {
            "node_id": "1.2",
            "answer": "否",
            "reason": "无法稳定识别周期。",
            "evidence_refs": ["PA.WINDOW.K20"],
        }
    ]

    result = materialize_stage1_draft(draft, catalog=catalog)

    assert result["gate_result"] == "unknown"


def test_stage2_draft_inherits_diagnosis_and_materializes_math_trace_terminal() -> None:
    frame = make_frame()
    catalog = build_analysis_fact_catalog(frame)
    stage1 = materialize_stage1_draft(_stage1_draft(), catalog=catalog)
    draft = copy.deepcopy(VALID_STAGE2)
    draft["protocol_version"] = "pa_draft_v2"
    draft["decision_intent"] = "trade"
    draft.pop("terminal")
    draft.pop("next_cycle_prediction")
    draft["decision"].pop("diagnosis_confidence")
    draft["decision"].pop("diagnosis_confidence_reasoning")
    draft["decision_trace"] = [
        item
        for item in _trace_draft(VALID_STAGE2["decision_trace"])
        if item["node_id"] != "10.3"
    ]

    jsonschema.Draft7Validator(STAGE2_DRAFT_SCHEMA).validate(draft)
    result = materialize_stage2_draft(
        draft,
        stage1=stage1,
        catalog=catalog,
    )
    jsonschema.Draft7Validator(STAGE2_SCHEMA).validate(result)

    assert result["decision"]["diagnosis_confidence"] == 75
    assert result["decision"]["diagnosis_confidence_reasoning"].startswith("主假设")
    equation = next(
        item for item in result["decision_trace"] if item["node_id"] == "10.3"
    )
    assert equation["answer"] == "是"
    assert "risk=" in equation["reason"]
    assert result["terminal"]["outcome"] == "trade"
    assert result["next_cycle_prediction"]["unpredictable"] is True


def test_validator_negotiates_draft_schema_and_returns_canonical_stage1() -> None:
    frame = make_frame()
    catalog = build_analysis_fact_catalog(frame)
    validator = JsonValidator()

    assert validator.schema_for("stage1")["properties"]["protocol_version"]["const"] == "pa_draft_v2"
    result = validator.validate(
        "stage1", json.dumps(_stage1_draft(), ensure_ascii=False),
        kline_frame=frame, analysis_fact_catalog=catalog,
    )

    assert isinstance(result, Ok)
    assert result.obj["gate_result"] == "proceed"
    assert result.obj["market_hypotheses"]["alternatives"][0]["direction"] == "neutral"


def test_no_order_draft_does_not_invent_trader_equation() -> None:
    frame = make_frame()
    catalog = build_analysis_fact_catalog(frame)
    draft = copy.deepcopy(VALID_STAGE2)
    draft["protocol_version"] = "pa_draft_v2"
    draft["decision_intent"] = "wait"
    draft.pop("terminal")
    draft.pop("next_cycle_prediction")
    draft["decision"].pop("diagnosis_confidence")
    draft["decision"].pop("diagnosis_confidence_reasoning")
    draft["decision"].update({
        "order_type": "不下单", "order_direction": None, "entry_price": None,
        "entry_basis_bar": None, "entry_basis_extreme": None, "entry_rule": None,
        "take_profit_price": None, "take_profit_price_2": None, "stop_loss_price": None,
        "trade_confidence": None, "estimated_win_rate": None,
        "estimated_win_rate_reasoning": None, "invalidation_condition": None,
    })
    draft["decision_trace"] = [
        {"node_id": "9.0", "answer": "否", "reason": "no closed signal", "evidence_refs": ["PA.BAR.K1"]},
        {"node_id": "9.0P", "answer": "否", "reason": "no passive plan", "evidence_refs": ["PA.WINDOW.K5"]},
    ]

    result = materialize_stage2_draft(draft, stage1=materialize_stage1_draft(_stage1_draft(), catalog=catalog), catalog=catalog)

    assert all(item["node_id"] != "10.3" for item in result["decision_trace"])
    assert result["terminal"] == {"node_id": "9.0P", "outcome": "wait", "label": "LLM wait intent"}
    jsonschema.Draft7Validator(STAGE2_SCHEMA).validate(result)
