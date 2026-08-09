from __future__ import annotations

import copy
import json

from pa_agent.ai.decision_tree import build_stage2_gate_wait_response
from pa_agent.ai.json_validator import JsonValidator, Ok, ValidationError
from pa_agent.config.settings import ValidationSettings
from tests.fixtures.ai_payloads import VALID_STAGE2_NO_ORDER
from tests.integration.conftest import VALID_STAGE1, VALID_STAGE2, make_frame


def _validator() -> JsonValidator:
    return JsonValidator(ValidationSettings())


def test_current_stage_contracts_validate() -> None:
    validator = _validator()
    frame = make_frame()

    stage1 = validator.validate(
        "stage1", json.dumps(VALID_STAGE1, ensure_ascii=False), kline_frame=frame
    )
    stage2 = validator.validate(
        "stage2",
        json.dumps(VALID_STAGE2, ensure_ascii=False),
        kline_frame=frame,
        stage1_json=VALID_STAGE1,
    )

    assert isinstance(stage1, Ok)
    assert isinstance(stage2, Ok)


def test_passive_limit_confirmation_wording_is_a_warning_not_a_failure() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"].update(
        {
            "order_type": "限价单",
            "entry_basis_bar": None,
            "entry_basis_extreme": None,
            "entry_rule": "等待下一根已收盘阳线确认后才激活",
        }
    )

    result = _validator().validate(
        "stage2",
        json.dumps(stage2, ensure_ascii=False),
        kline_frame=make_frame(),
        stage1_json=VALID_STAGE1,
    )

    assert isinstance(result, Ok)
    assert any(
        "confirmation before a passive limit" in warning.message
        for warning in result.warnings
    )


def test_malformed_json_is_rejected_instead_of_semantic_repair() -> None:
    raw = '{"cycle_position": "normal_channel"; "direction": "bullish"}'

    result = _validator().validate("stage1", raw)

    assert isinstance(result, ValidationError)
    assert result.category == "a"


def test_truncated_json_is_not_completed_with_invented_gate_trace() -> None:
    raw = '{"cycle_position": "normal_channel", "direction": "bullish"'

    result = _validator().validate("stage1", raw)

    assert isinstance(result, ValidationError)
    assert result.category == "a"
    assert "AUTO" not in result.raw_text


def test_missing_required_field_is_category_b() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    del stage1["direction"]

    result = _validator().validate(
        "stage1", json.dumps(stage1, ensure_ascii=False), kline_frame=make_frame()
    )

    assert isinstance(result, ValidationError)
    assert result.category == "b"
    assert "direction" in result.missing_fields


def test_custom_contract_error_is_not_reported_as_zero_schema_errors() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    next(item for item in stage1["gate_trace"] if item["node_id"] == "1.2")[
        "branch"
    ] = "spike"

    result = _validator().validate(
        "stage1", json.dumps(stage1, ensure_ascii=False), kline_frame=make_frame()
    )

    assert isinstance(result, ValidationError)
    assert result.message.startswith("validation error:")
    assert "0 schema error(s)" not in result.message


def test_schema_errors_are_deduplicated_by_field_path() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["cycle_position"] = "spike"
    stage1["spike_stage"] = "transitioning"
    stage1["market_phase"] = "transitioning"
    stage1["transition_risk"] = None
    stage1["pa_evidence"]["spike_analysis"] = {
        "anchor_bar": "K3",
        "post_anchor_state": "pause_or_pullback_present",
        "first_pause_or_pullback_bar": "K2",
    }
    next(item for item in stage1["gate_trace"] if item["node_id"] == "1.2")[
        "branch"
    ] = "spike"

    result = _validator().validate(
        "stage1", json.dumps(stage1, ensure_ascii=False), kline_frame=make_frame()
    )

    assert isinstance(result, ValidationError)
    assert [
        field for field in result.invalid_fields if "transition_risk" in field
    ] == ["transition_risk"]
    assert result.message.startswith("1 schema error(s)")


def test_spike_cycle_requires_structured_spike_evidence() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["cycle_position"] = "spike"
    stage1["spike_stage"] = "active"
    next(item for item in stage1["gate_trace"] if item["node_id"] == "1.2")[
        "branch"
    ] = "spike"

    result = _validator().validate(
        "stage1", json.dumps(stage1, ensure_ascii=False), kline_frame=make_frame()
    )

    assert isinstance(result, ValidationError)
    assert "pa_evidence.spike_analysis" in result.invalid_fields


def test_non_spike_cycle_rejects_stale_spike_evidence() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["pa_evidence"]["spike_analysis"] = {
        "anchor_bar": "K5",
        "post_anchor_state": "continuation_only",
        "first_pause_or_pullback_bar": None,
    }

    result = _validator().validate(
        "stage1", json.dumps(stage1, ensure_ascii=False), kline_frame=make_frame()
    )

    assert isinstance(result, ValidationError)
    assert "pa_evidence.spike_analysis" in result.invalid_fields


def test_spike_pause_state_requires_a_closed_pause_bar_reference() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1.update(
        {
            "cycle_position": "spike",
            "spike_stage": "transitioning",
            "market_phase": "transitioning",
            "transition_risk": "medium",
        }
    )
    next(item for item in stage1["gate_trace"] if item["node_id"] == "1.2")[
        "branch"
    ] = "spike"
    stage1["pa_evidence"]["spike_analysis"] = {
        "anchor_bar": "K5",
        "post_anchor_state": "pause_or_pullback_present",
        "first_pause_or_pullback_bar": None,
    }

    result = _validator().validate(
        "stage1", json.dumps(stage1, ensure_ascii=False), kline_frame=make_frame()
    )

    assert isinstance(result, ValidationError)
    assert (
        "pa_evidence.spike_analysis.first_pause_or_pullback_bar"
        in result.invalid_fields
    )


def test_spike_pause_bar_exact_alias_is_normalized_at_model_boundary() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1.update(
        {
            "cycle_position": "spike",
            "spike_stage": "transitioning",
            "market_phase": "transitioning",
            "transition_risk": "medium",
        }
    )
    next(item for item in stage1["gate_trace"] if item["node_id"] == "1.2")[
        "branch"
    ] = "spike"
    stage1["pa_evidence"]["spike_analysis"] = {
        "anchor_bar": "K5",
        "post_anchor_state": "pause_or_pullback_present",
        "first_pullback_bar": "K4",
    }

    result = _validator().validate(
        "stage1", json.dumps(stage1, ensure_ascii=False), kline_frame=make_frame()
    )

    assert isinstance(result, Ok)
    assert result.obj["pa_evidence"]["spike_analysis"] == {
        "anchor_bar": "K5",
        "post_anchor_state": "pause_or_pullback_present",
        "first_pause_or_pullback_bar": "K4",
    }
    assert any(
        event["path"]
        == "pa_evidence.spike_analysis.first_pause_or_pullback_bar"
        for event in result.normalization_events
    )


def test_spike_pause_bar_alias_does_not_override_canonical_field() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1.update(
        {
            "cycle_position": "spike",
            "spike_stage": "transitioning",
            "market_phase": "transitioning",
            "transition_risk": "medium",
        }
    )
    next(item for item in stage1["gate_trace"] if item["node_id"] == "1.2")[
        "branch"
    ] = "spike"
    stage1["pa_evidence"]["spike_analysis"] = {
        "anchor_bar": "K5",
        "post_anchor_state": "pause_or_pullback_present",
        "first_pause_or_pullback_bar": "K4",
        "first_pullback_bar": "K3",
    }

    result = _validator().validate(
        "stage1", json.dumps(stage1, ensure_ascii=False), kline_frame=make_frame()
    )

    assert isinstance(result, ValidationError)
    assert "pa_evidence.spike_analysis" in result.invalid_fields


def test_spike_pause_state_requires_the_complete_transition_tuple() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1.update(
        {
            "cycle_position": "spike",
            "spike_stage": "active",
            "market_phase": "stable",
            "transition_risk": None,
        }
    )
    next(item for item in stage1["gate_trace"] if item["node_id"] == "1.2")[
        "branch"
    ] = "spike"
    stage1["pa_evidence"]["spike_analysis"] = {
        "anchor_bar": "K3",
        "post_anchor_state": "pause_or_pullback_present",
        "first_pause_or_pullback_bar": "K2",
    }

    result = _validator().validate(
        "stage1", json.dumps(stage1, ensure_ascii=False), kline_frame=make_frame()
    )

    assert isinstance(result, ValidationError)
    assert "spike_stage" in result.invalid_fields
    assert "market_phase" in result.invalid_fields
    assert "transition_risk" in result.invalid_fields


def test_spike_without_a_later_closed_bar_cannot_name_a_pause_bar() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1.update({"cycle_position": "spike", "spike_stage": "active"})
    next(item for item in stage1["gate_trace"] if item["node_id"] == "1.2")[
        "branch"
    ] = "spike"
    stage1["pa_evidence"]["spike_analysis"] = {
        "anchor_bar": "K1",
        "post_anchor_state": "no_closed_bar_after_anchor",
        "first_pause_or_pullback_bar": "K2",
    }

    result = _validator().validate(
        "stage1", json.dumps(stage1, ensure_ascii=False), kline_frame=make_frame()
    )

    assert isinstance(result, ValidationError)
    assert (
        "pa_evidence.spike_analysis.first_pause_or_pullback_bar"
        in result.invalid_fields
    )


def test_stable_market_phase_cannot_carry_transition_risk() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["transition_risk"] = "medium"

    result = _validator().validate(
        "stage1", json.dumps(stage1, ensure_ascii=False), kline_frame=make_frame()
    )

    assert isinstance(result, ValidationError)
    assert "transition_risk" in result.invalid_fields


def test_non_spike_cycle_cannot_carry_spike_stage() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["spike_stage"] = "active"

    result = _validator().validate(
        "stage1", json.dumps(stage1, ensure_ascii=False), kline_frame=make_frame()
    )

    assert isinstance(result, ValidationError)
    assert "spike_stage" in result.invalid_fields


def test_no_order_with_price_is_rejected_not_cleared() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2_NO_ORDER)
    stage2["decision"]["entry_price"] = 123.0

    result = _validator().validate(
        "stage2",
        json.dumps(stage2, ensure_ascii=False),
        kline_frame=make_frame(),
        stage1_json=VALID_STAGE1,
    )

    assert isinstance(result, ValidationError)
    assert result.category == "c"
    assert stage2["decision"]["entry_price"] == 123.0


def test_no_order_has_no_trade_confidence_for_a_nonexistent_attempt() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2_NO_ORDER)
    stage2["decision"]["trade_confidence"] = None

    result = _validator().validate(
        "stage2",
        json.dumps(stage2, ensure_ascii=False),
        kline_frame=make_frame(),
        stage1_json=VALID_STAGE1,
    )

    assert isinstance(result, Ok)


def test_missing_entry_bar_requires_the_complete_pending_state() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2_NO_ORDER)
    stage2["bar_analysis"]["entry_bar"].update(
        {
            "bar": None,
            "strength": "strong",
            "follow_through": True,
            "still_valid": True,
            "freshness": "fresh",
        }
    )

    result = _validator().validate(
        "stage2",
        json.dumps(stage2, ensure_ascii=False),
        kline_frame=make_frame(),
        stage1_json=VALID_STAGE1,
    )

    assert isinstance(result, ValidationError)
    assert "bar_analysis.entry_bar.freshness" in result.invalid_fields


def test_stage2_missing_signal_bar_cannot_claim_signal_quality_or_pattern() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"].update(
        {
            "order_type": "限价单",
            "entry_basis_bar": None,
            "entry_basis_extreme": None,
            "entry_rule": None,
        }
    )
    stage2["bar_analysis"]["signal_bar"].update(
        {"bar": None, "quality": "strong", "pattern": "H1"}
    )
    stage2["bar_analysis"]["entry_bar"].update(
        {
            "bar": None,
            "strength": "not_triggered",
            "follow_through": None,
            "still_valid": None,
            "freshness": "pending",
        }
    )

    result = _validator().validate(
        "stage2",
        json.dumps(stage2, ensure_ascii=False),
        kline_frame=make_frame(),
        stage1_json=VALID_STAGE1,
    )

    assert isinstance(result, ValidationError)
    assert "bar_analysis.signal_bar.quality" in result.invalid_fields
    assert "bar_analysis.signal_bar.pattern" in result.invalid_fields


def test_missing_auxiliary_next_bar_prediction_is_program_degraded() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    del stage2["next_bar_prediction"]
    raw = json.dumps(stage2, ensure_ascii=False)

    enabled = _validator().validate(
        "stage2", raw, kline_frame=make_frame(), stage1_json=VALID_STAGE1
    )
    disabled = _validator().validate(
        "stage2",
        raw,
        kline_frame=make_frame(),
        stage1_json=VALID_STAGE1,
        skip_next_bar=True,
    )

    assert isinstance(enabled, Ok)
    assert enabled.obj["next_bar_prediction"]["unpredictable"] is True
    assert any(
        event["path"] == "next_bar_prediction"
        for event in enabled.normalization_events
    )
    assert isinstance(disabled, Ok)
    assert "next_bar_prediction" not in disabled.obj


def test_breakout_price_must_be_strictly_outside_basis_bar_without_repricing() -> None:
    frame = make_frame()
    stage2 = copy.deepcopy(VALID_STAGE2)
    k2 = frame.bars[1]
    stage2["decision"]["entry_price"] = k2.high

    result = _validator().validate(
        "stage2",
        json.dumps(stage2, ensure_ascii=False),
        kline_frame=frame,
        stage1_json=VALID_STAGE1,
    )

    assert isinstance(result, ValidationError)
    assert any(field.startswith("breakout_price:") for field in result.invalid_fields)
    assert stage2["decision"]["entry_price"] == k2.high


def test_gate_short_circuit_payload_matches_strict_stage2_schema() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["gate_result"] = "wait"
    stage1["gate_trace"] = [
        {
            "node_id": "1.2",
            "question": "Can the cycle be identified?",
            "answer": "等待",
            "reason": "insufficient structure",
            "bar_range": "K20-K1",
        }
    ]
    payload = build_stage2_gate_wait_response(stage1)

    result = _validator().validate(
        "stage2",
        json.dumps(payload, ensure_ascii=False),
        kline_frame=make_frame(),
        stage1_json=stage1,
    )

    assert isinstance(result, Ok)
    assert result.obj["gate_shortcircuited"] is True


def test_probability_summary_mismatch_is_normalized_and_audited() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["next_bar_prediction"]["direction"] = "neutral"
    stage2["next_cycle_prediction"]["cycle"] = "trading_range"

    result = _validator().validate(
        "stage2",
        json.dumps(stage2, ensure_ascii=False),
        kline_frame=make_frame(),
        stage1_json=VALID_STAGE1,
    )

    assert isinstance(result, Ok)
    assert result.obj["next_bar_prediction"]["direction"] == "bullish"
    assert result.obj["next_cycle_prediction"]["cycle"] == "normal_channel"
    assert {event["path"] for event in result.normalization_events} == {
        "next_bar_prediction.direction",
        "next_cycle_prediction.cycle",
    }
