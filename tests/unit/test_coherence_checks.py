from __future__ import annotations

import copy
from dataclasses import replace

from pa_agent.ai.coherence_checks import (
    collect_stage1_quality_warnings,
    collect_stage2_quality_warnings,
)
from pa_agent.ai.coherence_checks import (
    validate_stage1_coherence as _validate_stage1_hard,
)
from pa_agent.ai.coherence_checks import (
    validate_stage2_coherence as _validate_stage2_hard,
)
from tests.integration.conftest import VALID_STAGE1, VALID_STAGE2, make_frame
from tests.unit.test_analysis_fact_catalog import make_mixed_frame


def validate_stage1_coherence(stage1: dict, *, kline_frame: object) -> list[str]:
    """Return all hard and advisory findings for focused detector tests."""
    return [
        *_validate_stage1_hard(stage1, kline_frame=kline_frame),
        *collect_stage1_quality_warnings(stage1, kline_frame=kline_frame),
    ]


def validate_stage2_coherence(
    stage2: dict,
    stage1: dict,
    *,
    kline_frame: object,
) -> list[str]:
    """Return all hard and advisory findings for focused detector tests."""
    return [
        *_validate_stage2_hard(stage2, stage1, kline_frame=kline_frame),
        *collect_stage2_quality_warnings(stage2, kline_frame=kline_frame),
    ]


def test_valid_stage_outputs_are_internally_coherent() -> None:
    frame = make_frame()

    assert validate_stage1_coherence(VALID_STAGE1, kline_frame=frame) == []
    assert validate_stage2_coherence(VALID_STAGE2, VALID_STAGE1, kline_frame=frame) == []


def test_stage2_pa_fields_cannot_bypass_vpa_with_volume_claims() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"]["reasoning"] = "K1 volume is high, so enter now."

    errors = validate_stage2_coherence(stage2, VALID_STAGE1, kline_frame=make_frame())

    assert "decision.reasoning violates the PA price-only evidence contract" in errors


def test_stage2_bar_analysis_must_reference_the_frozen_snapshot() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["bar_analysis"]["signal_bar"]["bar"] = "K99"

    errors = validate_stage2_coherence(stage2, VALID_STAGE1, kline_frame=make_frame())

    assert "bar_analysis.signal_bar.bar K99 not found in current K-line frame" in errors


def test_stage1_rejects_support_on_the_wrong_side_of_latest_close() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["support_levels"] = ["2050"]

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert "support_levels[0] must be strictly below K1.close" in errors


def test_stage1_rejects_unstructured_support_or_resistance_text() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["support_levels"] = ["K5 low"]
    stage1["resistance_levels"] = ["near channel top"]

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert "support_levels[0] must be a positive numeric price or price range" in errors
    assert "resistance_levels[0] must be a positive numeric price or price range" in errors


def test_stage1_requires_structure_levels_in_near_to_far_order() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["support_levels"] = ["2020", "2030"]
    stage1["resistance_levels"] = ["2060", "2050"]

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert "support_levels must be ordered near-to-far below K1.close" in errors
    assert "resistance_levels must be ordered near-to-far above K1.close" in errors


def test_stage1_limits_each_structure_level_side_to_three_entries() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["support_levels"] = ["2030", "2020", "2010", "2000"]

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert "support_levels may contain at most 3 levels" in errors


def test_stage1_requires_k1_as_the_latest_closed_bar() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_analysis"]["last_closed_bar"] = "K2"

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert "bar_analysis.last_closed_bar must be K1" in errors


def test_stage1_signal_bar_must_reference_the_frozen_snapshot() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_analysis"]["signal_bar"]["bar"] = "K99"

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert "bar_analysis.signal_bar.bar K99 not found in current K-line frame" in errors


def test_stage1_null_signal_bar_requires_invalid_quality() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_analysis"]["signal_bar"]["bar"] = None
    stage1["bar_analysis"]["signal_bar"]["quality"] = "strong"

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert "bar_analysis.signal_bar.quality must be invalid when bar is null" in errors


def test_stage1_proceed_requires_mandatory_trace_nodes() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["gate_trace"] = [
        item for item in stage1["gate_trace"] if item["node_id"] != "2.4"
    ]

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("2.4" in error and "缺少" in error for error in errors)


def test_stage1_trace_branch_must_match_model_summary() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    node = next(item for item in stage1["gate_trace"] if item["node_id"] == "2.3")
    node["branch"] = "bearish"

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("direction" in error and "不一致" in error for error in errors)


def test_trace_may_not_reference_bars_outside_snapshot() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["gate_trace"][0]["bar_range"] = "K21-K1"

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("K21" in error for error in errors)


def test_trace_reason_may_not_cite_bars_outside_declared_range() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["gate_trace"][0]["bar_range"] = "K2-K1"
    stage1["gate_trace"][0]["reason"] = "K4 与 K1 的结构共同支持判断"

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("bar_range=K2-K1" in error and "K4" in error for error in errors)


def test_trace_reason_detects_chinese_adjacent_k_reference_outside_declared_range() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["gate_trace"][0]["bar_range"] = "K2-K1"
    stage1["gate_trace"][0]["reason"] = "K4后出现的 K1区间结构共同支持判断"

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("bar_range=K2-K1" in error and "K4" in error for error in errors)


def test_non_skipped_trace_node_requires_a_concise_reason() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["gate_trace"][0]["reason"] = ""

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("reason is required" in error for error in errors)


def test_pa_output_cannot_use_volume_facts() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][0]["reason"] = "K1 volume was 8500, so activity contracted."

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("price-only" in error for error in errors)


def test_pa_bar_reason_cannot_restate_raw_price_measurements() -> None:
    for reason in (
        "K3 high=4035.655 and close_position=0.460, so follow-through is mixed.",
        "K3 close=1.08231, so the FX follow-through is mixed.",
        "K3 close=183, so the stock follow-through is mixed.",
        "K3 ATR 1250 leaves the crypto move extended.",
    ):
        stage1 = copy.deepcopy(VALID_STAGE1)
        stage1["bar_by_bar_summary"][2]["reason"] = reason

        errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

        assert any("numeric market facts" in error for error in errors)


def test_pa_reason_cites_fact_id_without_copying_canonical_key_values() -> None:
    for payload in (
        "PA.BAR.K2 close_zone=lower_quartile supports weak follow-through.",
        "PA.PAIR.K2.K3 newer_low_vs_older_low=above limits bearish extension.",
    ):
        stage1 = copy.deepcopy(VALID_STAGE1)
        stage1["bar_by_bar_summary"][1]["reason"] = payload

        errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

        assert any("canonical key=value" in error for error in errors)


def test_gate_reason_cannot_restate_program_measurements_or_bar_counts() -> None:
    cases = (
        (
            "K20-K1 pullback depth is 30%-50%, so the channel remains normal.",
            "numeric market facts",
        ),
        (
            "PA.WINDOW.K8 gives a 5/8 bearish share, so pressure dominates.",
            "numeric market facts",
        ),
        (
            "PA.WINDOW.K8 has 5 bars below EMA20, so pressure dominates.",
            "must cite PA.WINDOW instead of restating an exact bar count",
        ),
    )
    for reason, expected in cases:
        stage1 = copy.deepcopy(VALID_STAGE1)
        stage1["gate_trace"][0]["bar_range"] = "K20-K1"
        stage1["gate_trace"][0]["reason"] = reason

        errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

        assert any(
            "gate_trace[0].reason" in error and expected in error
            for error in errors
        )


def test_gate_reason_allows_node_kline_and_indicator_period_references() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["gate_trace"][0]["bar_range"] = "K20-K1"
    stage1["gate_trace"][0]["reason"] = (
        "Node 1.2 uses K20-K1 and PA.WINDOW.K8 as evidence; EMA20 context "
        "supports a qualitative Brooks interpretation."
    )

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert not any(
        "gate_trace[0].reason" in error and "numeric market facts" in error
        for error in errors
    )


def test_pa_bar_reason_allows_brooks_and_program_reference_numbers() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][2]["reason"] = (
        "PA.PAIR.K3.K4 supports a possible H2-style 2BR continuation on 15m, "
        "but acceptance is pending."
    )

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert not any("numeric market facts" in error for error in errors)


def test_htf_context_cannot_restate_raw_program_measurements() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["htf_context"] = (
        "H1 close=4035.29 and EMA20=4040.59; the higher timeframe remains mixed."
    )

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert any("htf_context" in error and "numeric market facts" in error for error in errors)


def test_htf_context_cannot_hide_integer_prices_in_prose() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["htf_context"] = (
        "HTF.H1-H60 shows price falling from 4030 to 3970 before rebounding."
    )

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert any("htf_context" in error and "numeric market facts" in error for error in errors)


def test_htf_context_allows_h_references_and_contextual_interpretation() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["htf_context"] = (
        "H1 remains below the earlier H6 swing area, so the higher timeframe "
        "challenges immediate continuation."
    )

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert not any("htf_context" in error and "numeric market facts" in error for error in errors)


def test_htf_context_allows_timeframe_and_indicator_period_notation() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["htf_context"] = (
        "1小时背景中，HTF.H1-H6仍位于EMA20下方，限制当前周期的上行延续。"
    )

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert not any("htf_context" in error and "numeric market facts" in error for error in errors)


def test_unbounded_extreme_claim_is_rejected() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][0]["reason"] = "K1 created a new low and extended the move."

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("unbounded" in error for error in errors)


def test_local_close_near_bar_low_is_not_a_window_new_low_claim() -> None:
    frame = make_frame()
    k1 = replace(frame.bars[0], open=2046.0, high=2050.0, low=2040.0, close=2041.0)
    frame = replace(frame, bars=(k1, *frame.bars[1:]))
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][0]["reason"] = (
        "K1 收盘位置接近本棒最低点，显示该棒卖方占优。"
    )

    errors = validate_stage1_coherence(stage1, kline_frame=frame)

    assert not any("unbounded" in error for error in errors)


def test_local_close_position_claim_cannot_contradict_bar_geometry() -> None:
    frame = make_frame()
    k1 = replace(frame.bars[0], open=2046.0, high=2050.0, low=2040.0, close=2041.0)
    frame = replace(frame, bars=(k1, *frame.bars[1:]))
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][0]["reason"] = "K1 收盘接近本棒最高点。"

    errors = validate_stage1_coherence(stage1, kline_frame=frame)

    assert any("close position" in error for error in errors)


def test_middle_half_close_cannot_be_described_as_near_the_bar_high() -> None:
    frame = make_mixed_frame()
    k3 = replace(
        frame.bars[2],
        open=4073.0,
        high=4080.0,
        low=4070.0,
        close=4074.6,
    )
    frame = replace(frame, bars=(*frame.bars[:2], k3, *frame.bars[3:]))
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][2]["reason"] = "K3 closed near its own high; follow-through is mixed."

    errors = validate_stage1_coherence(stage1, kline_frame=frame)

    assert any(
        "close position" in error and "close_zone=middle_half" in error
        for error in errors
    )


def test_explicit_bar_price_claim_must_match_the_frozen_bar() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["gate_trace"][0]["reason"] = "K6低点为2020.00，随后价格重心上移。"

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any(
        "K6.low" in error and "2020.00" in error and "2018" in error
        for error in errors
    )


def test_explicit_bar_price_claim_handles_field_before_k_reference() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["gate_trace"][0]["reason"] = (
        "低点从K6的2020.00升至K1低点2028.00，价格重心上移。"
    )

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("K6.low" in error and "2020.00" in error for error in errors)


def test_explicit_bar_price_claim_allows_display_rounding() -> None:
    frame = make_frame()
    k6 = replace(frame.bars[5], low=2018.004)
    frame = replace(frame, bars=(*frame.bars[:5], k6, *frame.bars[6:]))
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["gate_trace"][0]["reason"] = "K6的2018.00低点仍是当前结构参考。"

    errors = validate_stage1_coherence(stage1, kline_frame=frame)

    assert not any("K6.low" in error for error in errors)


def test_stage2_explicit_bar_price_claim_uses_the_same_fact_guard() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"]["reasoning"] = (
        "计划止损参考K6低点2020，其他市场结构由当前背景解释。"
    )

    errors = validate_stage2_coherence(
        stage2,
        VALID_STAGE1,
        kline_frame=make_frame(),
    )

    assert any("K6.low" in error and "2020" in error for error in errors)


def test_adjacent_pair_fact_can_ground_a_non_k1_extreme_claim() -> None:
    frame = make_mixed_frame()
    k3 = replace(frame.bars[2], high=4100.0, close=4098.0)
    frame = replace(frame, bars=(*frame.bars[:2], k3, *frame.bars[3:]))
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["pa_evidence"]["fact_refs"].append("PA.PAIR.K3.K4")
    stage1["bar_by_bar_summary"][2]["reason"] = (
        "PA.PAIR.K3.K4 shows K3 made a new high versus K4; acceptance remains uncertain."
    )

    errors = validate_stage1_coherence(stage1, kline_frame=frame)

    assert not any("unbounded" in error for error in errors)


def test_direct_negated_adjacent_extreme_claim_is_checked_as_a_pair() -> None:
    frame = make_mixed_frame()
    k3 = replace(frame.bars[2], high=4100.0, close=4098.0)
    frame = replace(frame, bars=(*frame.bars[:2], k3, *frame.bars[3:]))
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][2]["reason"] = (
        "K3 did not make a new high versus K4, so confirmation is weak."
    )

    errors = validate_stage1_coherence(stage1, kline_frame=frame)

    assert any("negated extreme claim conflicts" in error for error in errors)
    assert not any("unbounded negated" in error for error in errors)


def test_stage1_rejects_retry_deliberation_leaking_into_a_formal_reason() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][2]["reason"] = (
        "K3跟随上涨。核对数据：上述说法错误，应改为确认力度一般。"
    )

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert any("repair deliberation" in error for error in errors)


def test_bounded_and_program_consistent_extreme_claim_is_allowed() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][4]["reason"] = (
        "K5 broke above the K8-K6 high, creating a new high for K8-K5."
    )

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert not any("unbounded" in error for error in errors)
    assert not any("K5 did not break" in error for error in errors)


def test_extreme_claim_cannot_borrow_a_k_reference_from_a_later_clause() -> None:
    frame = make_mixed_frame()
    k1 = replace(frame.bars[0], high=4100.0)
    frame = replace(frame, bars=(k1, *frame.bars[1:]))
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][0]["reason"] = (
        "K1 created a new high; K4 is only background."
    )

    errors = validate_stage1_coherence(stage1, kline_frame=frame)

    assert any("unbounded new-high/new-low" in error for error in errors)


def test_extreme_claim_cannot_borrow_a_pa_fact_from_a_later_clause() -> None:
    frame = make_mixed_frame()
    k1 = replace(frame.bars[0], high=4110.0)
    frame = replace(frame, bars=(k1, *frame.bars[1:]))
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][0]["reason"] = (
        "K1 created a new high; PA.WINDOW.K5 is only a side note."
    )

    errors = validate_stage1_coherence(stage1, kline_frame=frame)

    assert any("unbounded new-high/new-low" in error for error in errors)


def test_explicit_window_cannot_ground_a_false_extreme_claim() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][0]["reason"] = "K1 是 K5-K1 窗口内的最低点。"

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("unbounded" in error for error in errors)


def test_negated_aggregate_extreme_language_is_not_a_positive_extreme_claim() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    node = next(item for item in stage1["gate_trace"] if item["node_id"] == "2.5")
    node["bar_range"] = "K8-K1"
    node["reason"] = "K8-K1 低点未能持续新低，缺乏趋势跟踪所需动量。"

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert not any("unbounded" in error for error in errors)


def test_negated_bar_extreme_cannot_contradict_program_comparison() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["pa_evidence"]["fact_refs"].append("PA.COMPARE.K1.PRIOR5")
    stage1["bar_by_bar_summary"][0]["reason"] = (
        "K1 相对 PA.COMPARE.K1.PRIOR5 未创新高。"
    )

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("negated extreme claim conflicts" in error for error in errors)


def test_declared_program_window_can_ground_current_bar_extreme_claim() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][0]["reason"] = (
        "K1 是 PA.WINDOW.K5内唯一最高点，顺势背景仍有效。"
    )

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert not any("unbounded" in error for error in errors)


def test_declared_program_window_cannot_ground_wrong_current_bar_extreme_claim() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][0]["reason"] = (
        "K1 是 PA.WINDOW.K5内唯一最低点，顺势背景仍有效。"
    )

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("unbounded" in error for error in errors)


def test_declared_prior_window_comparison_can_ground_k1_new_high_claim() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["pa_evidence"]["fact_refs"].append("PA.COMPARE.K1.PRIOR5")
    stage1["bar_by_bar_summary"][0]["reason"] = (
        "K1 相对 PA.COMPARE.K1.PRIOR5 创出新高，延续上行压力。"
    )

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert not any("unbounded" in error for error in errors)


def test_mixed_window_cannot_be_described_with_wrong_count_or_continuous_direction() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["key_signals"] = [
        "K8-K1 has 7 bullish bars and is continuously bullish."
    ]

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert any("bullish count" in error for error in errors)
    assert any("continuous bullish" in error for error in errors)


def test_k_label_before_candle_direction_is_not_parsed_as_a_count() -> None:
    for label in ("K5 阳线", "K 5 阳线", "第5根阳线"):
        stage1 = copy.deepcopy(VALID_STAGE1)
        stage1["bar_by_bar_summary"][4]["reason"] = (
            f"{label}突破 K6-K4 压力区，收盘靠近本棒高点。"
        )

        errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

        assert not any("bullish count" in error for error in errors)


def test_explicit_chinese_candle_count_is_still_validated() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["key_signals"] = ["K8-K1 共 7 根阳线，方向明显偏多。"]

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert any("bullish count" in error for error in errors)


def test_count_claim_is_not_bound_to_an_unrelated_range_in_the_same_reason() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    node = stage1["gate_trace"][-1]
    node["bar_range"] = "K10-K1"
    node["reason"] = (
        "K8-K1 shows bearish persistence (PA.WINDOW.K8); the K6-K8 run was "
        "followed by only 1 bullish bar at K5 before rejection."
    )

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert not any("claimed 1, actual" in error for error in errors)
    assert any("must cite PA.WINDOW instead of restating" in error for error in errors)


def test_bar_cannot_claim_break_of_a_range_it_did_not_exceed() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][1]["reason"] = "K2 broke above the K4-K3 high."

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert any("K2" in error and "K4-K3" in error and "high" in error for error in errors)


def test_bar_break_parser_accepts_chinese_adjacent_k_references() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][1]["reason"] = (
        "\u5468\u671fK2\u7a81\u7834K4-K3\u9ad8\u70b9\uff0c\u4f46\u7a0b\u5e8f\u51e0\u4f55\u4e0d\u652f\u6301\u3002"
    )

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert any("K2" in error and "K4-K3" in error and "high" in error for error in errors)


def test_spike_anchor_must_exist_in_the_frozen_frame() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["pa_evidence"]["spike_analysis"] = {
        "anchor_bar": "K99",
        "post_anchor_state": "no_closed_bar_after_anchor",
        "first_pause_or_pullback_bar": None,
    }

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert any("anchor_bar K99" in error and "not found" in error for error in errors)


def test_no_closed_bar_after_anchor_requires_latest_closed_bar_anchor() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["pa_evidence"]["spike_analysis"] = {
        "anchor_bar": "K2",
        "post_anchor_state": "no_closed_bar_after_anchor",
        "first_pause_or_pullback_bar": None,
    }

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert any("no_closed_bar_after_anchor" in error and "K1" in error for error in errors)


def test_spike_continuation_requires_a_later_closed_bar() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["pa_evidence"]["spike_analysis"] = {
        "anchor_bar": "K1",
        "post_anchor_state": "continuation_only",
        "first_pause_or_pullback_bar": None,
    }

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert any("continuation_only" in error and "later closed bar" in error for error in errors)


def test_spike_pause_bar_must_be_more_recent_than_its_anchor() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["pa_evidence"]["spike_analysis"] = {
        "anchor_bar": "K4",
        "post_anchor_state": "pause_or_pullback_present",
        "first_pause_or_pullback_bar": "K5",
    }

    errors = validate_stage1_coherence(stage1, kline_frame=make_mixed_frame())

    assert any("first_pause_or_pullback_bar" in error and "more recent" in error for error in errors)


def test_pa_evidence_cannot_invent_theory_rule_id() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["pa_evidence"]["theory_rule_ids"] = ["BROOKS.INVENTED.CERTAINTY_RULE"]

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("unknown theory rule ID" in error for error in errors)


def test_stage2_cannot_reverse_stage1_direction_on_same_snapshot() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"]["order_direction"] = "做空"
    stage2["decision_trace"].append(
        {
            "node_id": "2.3",
            "question": "重新判断方向？",
            "answer": "是",
            "branch": "bearish",
            "reason": "stage2 changed its mind",
            "bar_range": "K20-K1",
        }
    )

    errors = validate_stage2_coherence(stage2, VALID_STAGE1, kline_frame=make_frame())

    assert any("回到阶段一重做方向诊断" in error for error in errors)


def test_stage2_owns_concrete_setup_when_stage1_hint_is_empty() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["entry_setup"] = ""

    errors = validate_stage2_coherence(
        VALID_STAGE2,
        stage1,
        kline_frame=make_frame(),
    )

    assert errors == []


def test_coherence_validator_does_not_reclassify_llm_bar_semantics() -> None:
    frame = make_frame()
    small_bull = replace(frame.bars[0], close=frame.bars[0].open + 0.0001)
    frame = replace(frame, bars=(small_bull, *frame.bars[1:]))

    errors = validate_stage1_coherence(VALID_STAGE1, kline_frame=frame)

    assert errors == []


def test_bar_type_direction_must_match_program_candle_direction() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][0]["bar_type"] = "trend_bear"

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("bar_by_bar_summary[0].bar_type" in error for error in errors)


def test_inside_and_outside_labels_must_match_exact_geometry() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["bar_by_bar_summary"][0]["bar_type"] = "inside"

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("inside" in error and "geometry" in error for error in errors)


def test_ema_slope_number_cannot_reuse_close_to_ema_distance() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["htf_context"] = "EMA20 大幅下行 -100.0，K5-K1 延续。"

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("EMA slope" in error for error in errors)


def test_vague_numeric_leg_count_requires_an_explicit_k_range() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["entry_setup"] = "反弹超过2根K线，等待做空。"

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert any("numeric leg count" in error for error in errors)


def test_chinese_adjacent_k_range_bounds_a_numeric_leg_count() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["entry_setup"] = (
        "\u53cd\u5f39\u8d85\u8fc72\u6839K\u7ebf\uff0c\u53c2\u8003\u5468\u671fK8-K1\u3002"
    )

    errors = validate_stage1_coherence(stage1, kline_frame=make_frame())

    assert not any("numeric leg count" in error for error in errors)


def test_unverified_one_tick_claim_requires_disclosure() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"]["entry_rule"] = "在 K2 高点上方 1 跳动挂单。"

    errors = validate_stage2_coherence(stage2, VALID_STAGE1, kline_frame=make_frame())

    assert any("tick size" in error for error in errors)


def test_unverified_one_tick_principle_is_allowed_with_disclosure() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"]["entry_rule"] = (
        "按 K2 高点外 1 跳原则挂单；真实 tick size 未确认，执行前由交易接口校验。"
    )

    errors = validate_stage2_coherence(stage2, VALID_STAGE1, kline_frame=make_frame())

    assert not any("tick size" in error for error in errors)
