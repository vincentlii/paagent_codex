from __future__ import annotations

import copy
import json

from pa_agent.ai.json_validator import JsonValidator, Ok, ValidationError
from pa_agent.config.settings import ValidationSettings
from pa_agent.util.trade_metrics import passes_trader_equation, validate_order_trade_metrics
from tests.integration.conftest import VALID_STAGE1, VALID_STAGE2, make_frame


def _validate(stage2: dict):
    return JsonValidator(ValidationSettings()).validate(
        "stage2",
        json.dumps(stage2, ensure_ascii=False),
        kline_frame=make_frame(),
        stage1_json=VALID_STAGE1,
    )


def _planned_limit_stage2() -> dict:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"].update(
        {
            "order_type": "限价单",
            "entry_price": 2040.0,
            "stop_loss_price": 2020.0,
            "take_profit_price": 2070.0,
            "take_profit_price_2": 2100.0,
            "entry_basis_bar": None,
            "entry_basis_extreme": None,
            "entry_rule": "pullback to a model-identified structural anchor",
        }
    )
    stage2["bar_analysis"] = {
        "signal_bar": {
            "bar": None,
            "quality": "invalid",
            "pattern": "none",
            "reason": "planned limit path has no closed signal bar yet",
        },
        "entry_bar": {
            "bar": None,
            "strength": "not_triggered",
            "follow_through": None,
            "still_valid": None,
            "freshness": "pending",
        },
        "second_entry": {"is_second_entry": False, "type": "none"},
    }
    stage2["decision_trace"] = [
        {
            "node_id": "9.0",
            "question": "Is there a closed signal bar of sufficient quality?",
            "answer": "否",
            "reason": "planned limit path has no closed signal bar yet",
            "bar_range": "K20-K1",
        },
        {
            "node_id": "9.0P",
            "question": "Is a background-driven planned limit justified?",
            "answer": "是",
            "reason": "model identified background, location and structural anchor",
            "bar_range": "K20-K1",
        },
        *[
            item
            for item in VALID_STAGE2["decision_trace"]
            if item["node_id"] in {"10.1", "10.2", "10.3", "11.2", "14"}
        ],
    ]
    return stage2


def test_program_recomputes_trader_equation_from_prices() -> None:
    assert passes_trader_equation(55, risk=20, reward=30) is True
    assert passes_trader_equation(40, risk=20, reward=20) is False


def test_valid_structural_trade_metrics_pass() -> None:
    assert validate_order_trade_metrics(VALID_STAGE2["decision"]) == []
    assert isinstance(_validate(copy.deepcopy(VALID_STAGE2)), Ok)


def test_rr_below_one_passes_when_trader_equation_is_positive() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"]["take_profit_price"] = 2068.0
    stage2["decision"]["take_profit_price_2"] = 2080.0
    result = _validate(stage2)

    assert isinstance(result, Ok)
    assert result.obj["decision"]["order_type"] == stage2["decision"]["order_type"]


def test_failed_trader_equation_is_program_rejected_without_model_retry() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"]["estimated_win_rate"] = 20
    before = copy.deepcopy(stage2)

    result = _validate(stage2)

    assert isinstance(result, Ok)
    assert result.obj["decision"] == stage2["decision"]
    disposition = result.obj["execution_disposition"]
    assert disposition["status"] == "suppressed"
    assert disposition["reason_codes"] == ["trader_equation_failed"]
    assert any(
        event["path"] == "execution_policy"
        for event in result.normalization_events
    )
    assert stage2 == before


def test_invalid_tp2_is_blocked_by_execution_policy() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"]["take_profit_price_2"] = stage2["decision"]["take_profit_price"]

    result = _validate(stage2)

    assert isinstance(result, Ok)
    assert result.obj["decision"] == stage2["decision"]
    assert result.obj["execution_disposition"]["status"] == "suppressed"
    policy_event = next(
        event
        for event in result.normalization_events
        if event["path"] == "execution_policy"
    )
    assert any("take_profit_price_2" in reason for reason in policy_event["reasons"])


def test_stale_entry_bar_cannot_support_new_order() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["bar_analysis"]["entry_bar"]["freshness"] = "stale"

    result = _validate(stage2)

    assert isinstance(result, ValidationError)
    assert any("signal_chain:" in field and "stale" in field for field in result.invalid_fields)


def test_market_order_requires_concrete_entry_bar() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"].update(
        {
            "order_type": "市价单",
            "entry_basis_bar": None,
            "entry_basis_extreme": None,
            "entry_rule": None,
        }
    )
    stage2["bar_analysis"]["entry_bar"] = {
        "bar": None,
        "strength": "not_triggered",
        "follow_through": None,
        "still_valid": True,
        "freshness": "pending",
    }

    result = _validate(stage2)

    assert isinstance(result, ValidationError)
    assert any("market order requires" in field for field in result.invalid_fields)


def test_planned_limit_without_signal_bar_remains_available() -> None:
    stage2 = _planned_limit_stage2()

    result = _validate(stage2)

    assert isinstance(result, Ok)


def test_absent_pending_entry_clears_placeholder_follow_through_state() -> None:
    stage2 = _planned_limit_stage2()
    stage2["bar_analysis"]["entry_bar"].update(
        {"follow_through": True, "still_valid": True}
    )

    result = _validate(stage2)

    assert isinstance(result, Ok)
    entry_bar = result.obj["bar_analysis"]["entry_bar"]
    assert entry_bar["follow_through"] is None
    assert entry_bar["still_valid"] is None


def test_pending_entry_requires_the_complete_pending_state() -> None:
    stage2 = _planned_limit_stage2()
    stage2["bar_analysis"]["entry_bar"].update(
        {
            "strength": "strong",
            "freshness": "fresh",
            "follow_through": None,
            "still_valid": None,
        }
    )

    result = _validate(stage2)

    assert isinstance(result, ValidationError)
    assert any(
        "pending entry requires freshness=pending" in field
        for field in result.invalid_fields
    )


def test_follow_through_rejects_unbounded_string_values() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["bar_analysis"]["entry_bar"]["follow_through"] = "banana"

    result = _validate(stage2)

    assert isinstance(result, ValidationError)


def test_market_order_requires_valid_confirmed_entry_state() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"]["order_type"] = "市价单"
    stage2["decision"]["entry_basis_bar"] = None
    stage2["decision"]["entry_basis_extreme"] = None
    stage2["decision"]["entry_rule"] = None
    stage2["bar_analysis"]["entry_bar"].update(
        {"follow_through": False, "still_valid": False}
    )

    result = _validate(stage2)

    assert isinstance(result, ValidationError)
    assert any(
        "market order requires follow_through=true" in field
        for field in result.invalid_fields
    )
    assert any(
        "executable order requires still_valid=true" in field
        for field in result.invalid_fields
    )


def test_planned_limit_entry_must_be_on_future_pullback_side_of_k1_close() -> None:
    stage2 = _planned_limit_stage2()
    stage2["decision"]["entry_price"] = 2045.0

    result = _validate(stage2)

    assert isinstance(result, ValidationError)
    assert any(
        "§9.0P planned long limit" in field for field in result.invalid_fields
    )


def test_planned_short_limit_entry_must_be_above_k1_close() -> None:
    stage2 = _planned_limit_stage2()
    stage2["decision"].update({"order_direction": "做空", "entry_price": 2040.0})

    errors = JsonValidator._check_signal_chain(stage2, make_frame())

    assert any("§9.0P planned short limit" in error for error in errors)
