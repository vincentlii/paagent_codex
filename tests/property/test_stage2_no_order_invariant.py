"""Property checks for Stage 2 order/null and immutable-basis invariants."""
from __future__ import annotations

import copy
import json

from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from pa_agent.ai.json_validator import Ok, ValidationError
from tests.fixtures.ai_payloads import VALID_STAGE2_NO_ORDER, VALID_STAGE2_ORDER
from tests.fixtures.validators import schema_test_validator

validator = schema_test_validator()

_PRICE_FIELDS = (
    "entry_price",
    "take_profit_price",
    "take_profit_price_2",
    "stop_loss_price",
)


def _no_order() -> dict:
    return copy.deepcopy(VALID_STAGE2_NO_ORDER)


def _order() -> dict:
    return copy.deepcopy(VALID_STAGE2_ORDER)


def test_no_order_all_null_accepted() -> None:
    result = validator.validate("stage2", json.dumps(_no_order()))
    assert isinstance(result, Ok), result


@given(
    price_val=st.one_of(
        st.floats(allow_nan=False, allow_infinity=False),
        st.integers(),
    )
)
@h_settings(max_examples=100)
def test_no_order_with_non_null_price_rejected(price_val: float | int) -> None:
    for field in _PRICE_FIELDS:
        obj = _no_order()
        obj["decision"][field] = price_val
        result = validator.validate("stage2", json.dumps(obj))
        assert isinstance(result, ValidationError), (field, price_val, result)
        assert result.category == "c"


def test_executable_order_with_complete_fields_accepted() -> None:
    result = validator.validate("stage2", json.dumps(_order()))
    assert isinstance(result, Ok), result


def test_breakout_order_requires_extreme_basis() -> None:
    obj = _order()
    obj["decision"]["entry_basis_bar"] = None
    obj["decision"]["entry_basis_extreme"] = None
    result = validator.validate("stage2", json.dumps(obj))
    assert isinstance(result, ValidationError)
    assert result.category == "c"


def test_breakout_order_direction_extreme_mismatch_is_rejected_not_rewritten() -> None:
    obj = _order()
    obj["decision"]["entry_basis_extreme"] = "low"
    result = validator.validate("stage2", json.dumps(obj))
    assert isinstance(result, ValidationError)
    assert result.category == "c"
    assert obj["decision"]["entry_basis_extreme"] == "low"


@given(field=st.sampled_from(_PRICE_FIELDS))
@h_settings(max_examples=20)
def test_executable_order_with_null_price_rejected(field: str) -> None:
    obj = _order()
    obj["decision"][field] = None
    result = validator.validate("stage2", json.dumps(obj))
    assert isinstance(result, ValidationError), (field, result)
    assert result.category == "c"
