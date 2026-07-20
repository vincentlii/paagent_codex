from __future__ import annotations

from pa_agent.ai.factual_grounding import (
    validate_future_confirmation_narrative,
    validate_inference_only_narrative,
    validate_k_reference_bounds,
    validate_vpa_capability_narrative,
    validate_vpa_comparator_narrative,
    validate_vpa_k_references,
)


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
