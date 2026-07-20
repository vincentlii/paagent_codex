"""Property-level checks for strict model JSON classification."""
from __future__ import annotations

import copy
import json

from pa_agent.ai.json_validator import Ok, ValidationError
from tests.fixtures.ai_payloads import VALID_STAGE1, VALID_STAGE2_NO_ORDER
from tests.fixtures.validators import strict_test_validator

validator = strict_test_validator()


def _valid_stage1() -> dict:
    return copy.deepcopy(VALID_STAGE1)


def _valid_stage2() -> dict:
    return copy.deepcopy(VALID_STAGE2_NO_ORDER)


def test_valid_stage1_returns_ok() -> None:
    result = validator.validate("stage1", json.dumps(_valid_stage1()))
    assert isinstance(result, Ok), result


def test_stage1_diagnosis_confidence_score_accepted() -> None:
    obj = _valid_stage1()
    obj["diagnosis_confidence"] = 70
    assert isinstance(validator.validate("stage1", json.dumps(obj)), Ok)


def test_stage1_diagnosis_confidence_legacy_string_rejected() -> None:
    obj = _valid_stage1()
    obj["diagnosis_confidence"] = "high"
    result = validator.validate("stage1", json.dumps(obj))
    assert isinstance(result, ValidationError)
    assert result.category == "c"
    assert "diagnosis_confidence" in result.invalid_fields


def test_valid_stage2_no_order_returns_ok() -> None:
    result = validator.validate("stage2", json.dumps(_valid_stage2()))
    assert isinstance(result, Ok), result


def test_syntax_error_is_category_a() -> None:
    result = validator.validate("stage1", "{not valid json")
    assert isinstance(result, ValidationError)
    assert result.category == "a"


def test_missing_required_field_is_category_b() -> None:
    obj = _valid_stage1()
    del obj["cycle_position"]
    result = validator.validate("stage1", json.dumps(obj))
    assert isinstance(result, ValidationError)
    assert result.category == "b"
    assert "cycle_position" in result.missing_fields


def test_invalid_enum_value_is_category_c() -> None:
    obj = _valid_stage1()
    obj["direction"] = "sideways"
    result = validator.validate("stage1", json.dumps(obj))
    assert isinstance(result, ValidationError)
    assert result.category == "c"


def test_stage1_bar_role_alias_is_not_rewritten() -> None:
    obj = _valid_stage1()
    obj["bar_by_bar_summary"][0]["role"] = "continuation"
    result = validator.validate("stage1", json.dumps(obj))
    assert isinstance(result, ValidationError)
    assert result.category == "c"
    assert "bar_by_bar_summary.0.role" in result.invalid_fields


def test_plain_text_is_category_d() -> None:
    result = validator.validate("stage1", "I cannot provide a JSON response.")
    assert isinstance(result, ValidationError)
    assert result.category == "d"


def test_stage2_plain_text_is_category_d_not_stub() -> None:
    result = validator.validate("stage2", "修正完成，但本轮没有输出 JSON。")
    assert isinstance(result, ValidationError)
    assert result.category == "d"


def test_no_order_with_non_null_price_is_category_c() -> None:
    obj = _valid_stage2()
    obj["decision"]["entry_price"] = 2650.0
    result = validator.validate("stage2", json.dumps(obj))
    assert isinstance(result, ValidationError)
    assert result.category == "c"


def test_markdown_fenced_json_is_accepted_as_transport_wrapper() -> None:
    raw = f"```json\n{json.dumps(_valid_stage1())}\n```"
    assert isinstance(validator.validate("stage1", raw), Ok)


def test_truncated_stage1_is_never_repaired() -> None:
    obj = _valid_stage1()
    del obj["gate_trace"]
    del obj["gate_result"]
    truncated = json.dumps(obj, ensure_ascii=False)[:-1] + ","
    result = validator.validate("stage1", truncated)
    assert isinstance(result, ValidationError)
    assert result.category == "a"


def test_stage2_json_with_trailing_fence_is_accepted() -> None:
    raw = json.dumps(_valid_stage2(), ensure_ascii=False, indent=2) + "\n```"
    assert isinstance(validator.validate("stage2", raw), Ok)
