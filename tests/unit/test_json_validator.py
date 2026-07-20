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


def test_next_bar_is_required_only_when_feature_is_enabled() -> None:
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

    assert isinstance(enabled, ValidationError)
    assert "next_bar_prediction" in enabled.missing_fields
    assert isinstance(disabled, Ok)


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
