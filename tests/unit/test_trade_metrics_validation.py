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
            "still_valid": True,
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


def test_rr_below_one_is_rejected_without_moving_prices() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"]["take_profit_price"] = 2060.0
    stage2["decision"]["take_profit_price_2"] = 2070.0
    before = copy.deepcopy(stage2["decision"])

    result = _validate(stage2)

    assert isinstance(result, ValidationError)
    assert any("metrics:" in field and "below minimum" in field for field in result.invalid_fields)
    assert stage2["decision"] == before


def test_failed_trader_equation_is_rejected_not_coerced_to_no_order() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"]["estimated_win_rate"] = 20
    before_type = stage2["decision"]["order_type"]

    result = _validate(stage2)

    assert isinstance(result, ValidationError)
    assert any("trader equation fails" in field for field in result.invalid_fields)
    assert stage2["decision"]["order_type"] == before_type


def test_tp2_must_extend_beyond_tp1() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"]["take_profit_price_2"] = stage2["decision"]["take_profit_price"]

    result = _validate(stage2)

    assert isinstance(result, ValidationError)
    assert any("take_profit_price_2" in field for field in result.invalid_fields)


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
