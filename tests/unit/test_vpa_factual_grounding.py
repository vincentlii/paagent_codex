from __future__ import annotations

from pa_agent.ai.factual_grounding import (
    collect_unreconciled_vpa_comparator_warnings,
    validate_future_confirmation_narrative,
    validate_inference_only_narrative,
    validate_k_reference_bounds,
    validate_vpa_capability_narrative,
    validate_vpa_comparator_narrative,
    validate_vpa_k_references,
    validate_vpa_window_transition_narrative,
)


def test_divergent_rolling_and_seasonality_comparators_require_disclosure_warning() -> None:
    evidence = {
        "vpa-e001": {
            "evidence_id": "vpa-e001",
            "bar_range": "K1",
            "facts": {
                "volume_vs_baseline_relation": "above",
                "volume_vs_seasonality_relation": "below",
            },
        }
    }
    warnings = collect_unreconciled_vpa_comparator_warnings(
        "K1 相对因果滚动基线活动较高，支持承接候选。",
        cited_evidence_ids={"vpa-e001"},
        evidence_items=evidence,
        path="vpa_report.observations[0].interpretation",
    )

    assert len(warnings) == 1
    assert "rolling/seasonality divergence" in warnings[0]

    assert collect_unreconciled_vpa_comparator_warnings(
        "K1 高于因果滚动基线，但低于同时间槽季节性基线，因此仅作未决候选。",
        cited_evidence_ids={"vpa-e001"},
        evidence_items=evidence,
        path="vpa_report.observations[0].interpretation",
    ) == []


def test_inference_narrative_allows_evidence_ids_but_not_numbers() -> None:
    assert validate_inference_only_narrative(
        "vpa-e001 and vpa-c002 support the contextual interpretation.",
        path="reason",
    ) == []
    assert validate_inference_only_narrative(
        "vpa-e001 reports volume 8500.",
        path="reason",
    )


def test_local_close_position_is_not_misclassified_as_new_low() -> None:
    assert validate_inference_only_narrative(
        "K1 closed near its own lowest point, limiting the bullish result.",
        path="vpa_report.observations[0].interpretation",
    ) == []


def test_cross_window_extreme_requires_a_bounded_reference() -> None:
    for text in (
        "K1 closed at the lowest price in the K5-K1 window.",
        "K1 收盘位于 K5-K1 窗口最低点。",
    ):
        errors = validate_inference_only_narrative(
            text,
            path="vpa_report.observations[0].interpretation",
        )
        assert any("unbounded new-high/new-low" in error for error in errors)


def test_future_confirmation_accepts_an_explicit_closed_bar_anchor() -> None:
    assert validate_future_confirmation_narrative(
        "等待下一根已收盘 K 线接受并收于 K1 高点上方，否则视为未确认。",
        path="vpa_report.next_confirmation.trigger",
    ) == []


def test_latest_higher_timeframe_is_not_an_unbounded_price_high() -> None:
    assert validate_inference_only_narrative(
        "最新高周期回落提示动能放缓，不能代替小时级确认。",
        path="stage1.htf_context",
    ) == []
    assert validate_inference_only_narrative(
        "K1 创新高，支持延续。", path="stage1.key_signals",
    )


def test_future_confirmation_accepts_subsequent_closed_price() -> None:
    assert validate_future_confirmation_narrative(
        "观察后续已闭盘价相对K1低点的响应：是否在其下方持续接受并延伸。",
        path="vpa_report.next_confirmation.trigger",
    ) == []
    assert validate_future_confirmation_narrative(
        "观察K1之后的下一根闭盘棒：若收盘越过K1高点，再评估是否被接受。",
        path="vpa_report.next_confirmation.trigger",
    ) == []


def test_future_confirmation_rejects_unanchored_or_forming_bar_text() -> None:
    unanchored = validate_future_confirmation_narrative(
        "下一根已收盘K线需要创新高，否则可靠性降低。",
        path="vpa_report.next_confirmation.trigger",
    )
    forming = validate_future_confirmation_narrative(
        "观察下一根K线能否站上K1高点。",
        path="vpa_report.next_confirmation.trigger",
    )
    assert any("explicit K-line or structure anchor" in item for item in unanchored)
    assert any("future closed bar" in item for item in forming)

    contradictory = validate_future_confirmation_narrative(
        "Wait for the next closed bar while using K0 forming bar confirmation.",
        path="vpa_report.next_confirmation.trigger",
    )
    assert any("must not use K0" in item for item in contradictory)


def test_future_volume_confirmation_requires_a_comparator() -> None:
    errors = validate_future_confirmation_narrative(
        "等待下一根已收盘K线站上K1高点且成交量放大。",
        path="vpa_report.next_confirmation.trigger",
    )
    assert any("explicit comparator" in error for error in errors)
    assert validate_future_confirmation_narrative(
        "等待下一根已收盘K线站上K1高点，且相对因果基线的活动不减弱。",
        path="vpa_report.next_confirmation.trigger",
    ) == []


def test_comparator_detects_k_reference_adjacent_to_chinese_text() -> None:
    errors = validate_vpa_comparator_narrative(
        "K1区间成交量高于因果基线。",
        evidence_items=[
            {"bar_range": "K1", "facts": {"volume_vs_baseline_relation": "below"}}
        ],
        path="vpa_report.summary",
    )
    assert any("K1" in error and "conflicts" in error for error in errors)


def test_comparator_binds_each_clause_to_its_bar() -> None:
    evidence = [
        {
            "evidence_id": "vpa-e001",
            "bar_range": "K1",
            "facts": {"volume_vs_baseline_relation": "above"},
        },
        {
            "evidence_id": "vpa-e002",
            "bar_range": "K2",
            "facts": {"volume_vs_baseline_relation": "below"},
        },
    ]
    for text in (
        "K1 above causal baseline，K2 below causal baseline。",
        "K1 above causal baseline. K2 below causal baseline.",
    ):
        assert validate_vpa_comparator_narrative(
            text,
            evidence_items=evidence,
            path="vpa_report.observations[0].interpretation",
        ) == []


def test_comparator_binds_an_explicit_evidence_id() -> None:
    errors = validate_vpa_comparator_narrative(
        "vpa-e001 below causal baseline.",
        evidence_items=[
            {
                "evidence_id": "vpa-e001",
                "bar_range": "K1",
                "facts": {"volume_vs_baseline_relation": "above"},
            }
        ],
        path="vpa_report.observations[0].interpretation",
    )
    assert any("vpa-e001" in error and "conflicts" in error for error in errors)


def test_contextual_window_rejects_only_an_explicit_direction_restatement() -> None:
    errors = validate_vpa_comparator_narrative(
        "vpa-w005: K5-K1（反弹段） versus K10-K6（上涨段）。",
        evidence_items=[
            {
                "evidence_id": "vpa-w005",
                "bar_range": "K10-K1",
                "facts": {
                    "current_window": "K5-K1",
                    "prior_window": "K10-K6",
                    "current_progress_direction": "negative",
                    "prior_progress_direction": "positive",
                },
            }
        ],
        path="vpa_report.observations[0].interpretation",
    )

    assert any("current window direction claim" in item for item in errors)


def test_explicit_window_transition_checks_each_frozen_comparison() -> None:
    evidence = [
        {
            "evidence_id": "vpa-w020", "bar_range": "K40-K1",
            "facts": {
                "current_window": "K20-K1", "prior_window": "K40-K21",
                "current_progress_direction": "positive",
                "prior_progress_direction": "negative",
            },
        },
        {
            "evidence_id": "vpa-w040", "bar_range": "K80-K1",
            "facts": {
                "current_window": "K40-K1", "prior_window": "K80-K41",
                "current_progress_direction": "negative",
                "prior_progress_direction": "positive",
            },
        },
    ]
    path = "vpa_report.observations[0].interpretation"
    wrong = "K40-K1相对K80-K41、以及K20-K1相对K40-K21，均呈现方向由负转正的窗口差异。"
    errors = validate_vpa_window_transition_narrative(wrong, evidence_items=evidence, path=path)
    assert any("window transition claim for vpa-w040" in item for item in errors)
    assert not any("vpa-w020" in item for item in errors)

    valid = "K40-K1相对K80-K41净进展由正转负；K20-K1相对K40-K21净进展由负转正。"
    assert validate_vpa_window_transition_narrative(valid, evidence_items=evidence, path=path) == []
    negated = "K40-K1相对K80-K41净进展并非由负转正，仍需观察。"
    assert validate_vpa_window_transition_narrative(negated, evidence_items=evidence, path=path) == []
    tentative = "K40-K1相对K80-K41净进展可能由负转正，仍需观察。"
    assert validate_vpa_window_transition_narrative(tentative, evidence_items=evidence, path=path) == []
    ambiguous = "K40-K1相对K80-K41、K20-K1相对K40-K21，方向出现变化。"
    assert validate_vpa_window_transition_narrative(ambiguous, evidence_items=evidence, path=path) == []
    different_metric = "K40-K1相对K80-K41，某项指标由负转正。"
    assert validate_vpa_window_transition_narrative(different_metric, evidence_items=evidence, path=path) == []


def test_capability_guard_distinguishes_claim_from_disclaimer() -> None:
    capabilities = {
        "actor_attribution": False,
        "interpretation_ceiling": "relative_activity_only",
    }
    assert validate_vpa_capability_narrative(
        "Volume cannot identify institutional buyers.",
        path="reason",
        capabilities=capabilities,
        market_session="unclassified",
    ) == []
    assert validate_vpa_capability_narrative(
        "Institutional buyers absorbed supply in London.",
        path="reason",
        capabilities=capabilities,
        market_session="unclassified",
    )
    assert validate_vpa_capability_narrative(
        "Unknown volume nevertheless proves institutional buyers were active.",
        path="reason",
        capabilities=capabilities,
        market_session="unclassified",
    )


def test_k_reference_must_be_covered_by_cited_evidence() -> None:
    evidence = [{"bar_range": "K3-K1"}, {"bar_range": "K8"}]
    assert validate_vpa_k_references(
        "K2 and K8 are cited.",
        evidence_items=evidence,
        path="reason",
    ) == []
    errors = validate_vpa_k_references(
        "K4 is not covered.",
        evidence_items=evidence,
        path="reason",
    )
    assert any("K4 outside" in item for item in errors)


def test_k_reference_bounds_use_only_frozen_closed_bars() -> None:
    assert validate_k_reference_bounds(
        "K1 and K8 are available.",
        maximum=8,
        path="reason",
    ) == []
    errors = validate_k_reference_bounds(
        "K0 and K9 are outside.",
        maximum=8,
        path="reason",
    )
    assert len(errors) == 2
