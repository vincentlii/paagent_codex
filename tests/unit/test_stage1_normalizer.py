from __future__ import annotations

import copy

from pa_agent.ai.coherence_checks import validate_stage1_coherence
from pa_agent.ai.stage1_normalizer import normalize_stage1
from tests.integration.conftest import VALID_STAGE1, make_frame


def test_stage1_normalizer_is_copy_on_write_and_shape_only() -> None:
    raw = copy.deepcopy(VALID_STAGE1)
    raw["detected_patterns"] = ["WEDGE", "wedge", " H2 "]
    raw["gate_trace"][0]["node_id"] = "§1.2"
    raw["gate_trace"][0]["bar_range"] = "K1-K20"
    before = copy.deepcopy(raw)

    out = normalize_stage1(raw, kline_frame=make_frame())

    assert raw == before
    assert out["detected_patterns"] == ["wedge", "h2"]
    assert out["gate_trace"][0]["node_id"] == "1.2"
    assert out["gate_trace"][0]["bar_range"] == "K20-K1"
    assert out["cycle_position"] == before["cycle_position"]
    assert out["direction"] == before["direction"]
    assert "strategy_files_needed" not in out


def test_stage1_normalizer_does_not_fill_missing_market_fields() -> None:
    out = normalize_stage1({"gate_trace": []})

    assert out == {"gate_trace": []}


def test_stage1_normalizer_materializes_multi_window_trace_coverage() -> None:
    raw = copy.deepcopy(VALID_STAGE1)
    node = next(item for item in raw["gate_trace"] if item["node_id"] == "2.2")
    node["bar_range"] = "K20-K11"
    node["reason"] = "长程背景参考 K20-K11，近期方向参考 K10-K1，二者存在冲突。"

    out = normalize_stage1(raw, kline_frame=make_frame())

    assert node["bar_range"] == "K20-K11"
    assert out["gate_trace"][3]["bar_range"] == "K20-K1"
    assert not any(
        "bar_range" in error
        for error in validate_stage1_coherence(out, kline_frame=make_frame())
    )


def test_stage1_vpa_off_has_zero_field_leakage() -> None:
    out = normalize_stage1(
        {"vpa_context": {"summary": "old"}, "wyckoff_phase": "candidate"},
        vpa_enabled=False,
    )

    assert out == {}


def test_stage1_incremental_metadata_is_program_owned_fact() -> None:
    raw = copy.deepcopy(VALID_STAGE1)
    raw["incremental_delta"] = {
        "new_closed_bars": [],
        "changed_fields": [],
        "summary": "LLM contextual summary",
    }
    previous = copy.deepcopy(VALID_STAGE1)
    previous["direction"] = "neutral"

    out = normalize_stage1(
        raw,
        incremental_new_bar_count=2,
        incremental_previous_stage1=previous,
    )

    assert out["incremental_delta"]["new_closed_bars"] == ["K1", "K2"]
    assert "direction" in out["incremental_delta"]["changed_fields"]
    assert out["incremental_delta"]["summary"] == "LLM contextual summary"
