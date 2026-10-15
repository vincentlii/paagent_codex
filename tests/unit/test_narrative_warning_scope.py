"""Regressions for observed prose false positives; no model or exchange calls."""

import copy
from dataclasses import replace

import pytest

from pa_agent.ai.coherence_checks import collect_stage1_quality_warnings
from pa_agent.ai.factual_grounding import (
    classify_vpa_comparator_narrative,
    validate_future_confirmation_narrative,
)
from tests.integration.conftest import VALID_STAGE1, make_frame
from tests.unit.test_analysis_fact_catalog import make_mixed_frame
from tests.unit.test_joint_pipeline_runtime import joint_draft, run_joint


def ema_warnings(text, *, path="entry_setup", **kwargs):
    draft = copy.deepcopy(VALID_STAGE1)
    draft[path] = text
    return [
        w
        for w in collect_stage1_quality_warnings(draft, kline_frame=make_frame(), **kwargs)
        if "EMA slope" in w
    ]


def test_ema_does_not_treat_market_direction_or_timeframe_as_slope():
    assert (
        ema_warnings(
            "4小时背景仍偏空，HTF.H2显示价格处于EMA20下方；"
            "HTF.H1反弹不足以消除更大周期下行背景。",
            path="htf_context",
        )
        == []
    )


def test_ema_numbers_must_belong_to_an_explicit_slope_claim():
    assert ema_warnings("EMA20下方仍有4个结构位，价格下行背景未变。") == []


def test_ema_uses_the_delivered_htf_frame():
    main = make_frame()
    htf = replace(
        main,
        timeframe="4h",
        indicators=replace(main.indicators, ema20=tuple(100.0 + i for i in range(len(main.bars)))),
    )
    assert (
        ema_warnings(
            "EMA20下行。", path="htf_context", htf_context={"status": "available", "frame": htf}
        )
        == []
    )


@pytest.mark.parametrize("text", ["EMA20斜率=-999。", "EMA20 大幅下行 -999，K5-K1延续。"])
def test_explicit_wrong_ema_delta_remains_a_warning(text):
    assert any("not one of" in w for w in ema_warnings(text))


def test_ema_missing_htf_scope_is_unverified_instead_of_contradictory():
    warnings = ema_warnings("EMA20下行。", path="htf_context")
    assert warnings and all("unverified" in w and "conflict" not in w for w in warnings)


@pytest.mark.parametrize(
    "text", ["EMA20上行4小时。", "EMA20下行5根K线。", "如果EMA20下行，则等待。"]
)
def test_ema_duration_or_condition_is_unverified(text):
    assert all("unverified" in w for w in ema_warnings(text))


def test_ema_mixed_timeframes_do_not_guess_the_subject():
    warnings = ema_warnings("4小时背景与1小时不同，EMA20下行。")
    assert warnings and all("unverified" in w for w in warnings)


def test_exact_correct_ema_delta_is_not_warned():
    from pa_agent.ai.market_features import compute_simple_market_features

    delta = compute_simple_market_features(make_frame()).ema_delta_5
    assert delta is not None
    assert ema_warnings(f"EMA20斜率={delta}，参考K5-K1。") == []


def test_negated_extreme_uses_the_sentence_subject_not_the_summary_row():
    frame = make_mixed_frame()
    draft = copy.deepcopy(VALID_STAGE1)
    # K1.low > K2.low. This is K2's row, but the negated predicate is about K1.
    draft["bar_by_bar_summary"][1]["reason"] = (
        "PA.BAR.K2表现为下探后收回；K1随后收低，" "但PA.PAIR.K1.K2显示K1未跌破K2低点。"
    )
    warnings = collect_stage1_quality_warnings(draft, kline_frame=frame)
    assert not any("negated extreme claim conflicts" in w for w in warnings)
    draft["bar_by_bar_summary"][1]["reason"] = "K2未跌破K1低点。"
    assert any(
        "negated extreme claim conflicts" in w
        for w in collect_stage1_quality_warnings(draft, kline_frame=frame)
    )


def comparator(text, facts, *, bar_range="K1", evidence_id="vpa-e001"):
    return classify_vpa_comparator_narrative(
        text,
        evidence_items=[{"evidence_id": evidence_id, "bar_range": bar_range, "facts": facts}],
        path="vpa_report.summary",
    )


@pytest.mark.parametrize(
    "text,key",
    [
        ("vpa-w040：较新窗口的因果相对活动仍高于基线。", "current"),
        ("vpa-w040：前窗因果相对活动高于基线。", "prior"),
    ],
)
def test_window_baseline_uses_the_explicit_window(text, key):
    facts = {
        "current_window": "K40-K1",
        "prior_window": "K80-K41",
        "current_volume_vs_causal_baseline_relation": "below",
        "prior_volume_vs_causal_baseline_relation": "below",
    }
    facts[f"{key}_volume_vs_causal_baseline_relation"] = "above"
    assert comparator(text, facts, bar_range="K80-K1", evidence_id="vpa-w040") == ([], [])
    facts[f"{key}_volume_vs_causal_baseline_relation"] = "below"
    errors, _ = comparator(text, facts, bar_range="K80-K1", evidence_id="vpa-w040")
    assert any("conflicts" in e for e in errors)


def test_missing_or_unbound_comparator_is_unverified_not_a_conflict():
    for text, facts in [
        ("K1高于因果基线。", {}),
        (
            "vpa-e001：窗口高于因果基线。",
            {
                "current_volume_vs_causal_baseline_relation": "above",
                "prior_volume_vs_causal_baseline_relation": "below",
            },
        ),
    ]:
        errors, warnings = comparator(text, facts)
        assert errors == []
        assert any("unverified" in w for w in warnings)


def test_comparator_does_not_cross_a_different_comparison():
    assert comparator(
        "K1高于前一根，但低于因果基线。",
        {"volume_vs_baseline_relation": "below", "volume_vs_previous_relation": "above"},
    ) == ([], [])


@pytest.mark.parametrize(
    "text",
    [
        "K1并非高于因果基线。",
        "K1 not above causal baseline.",
        "K1相对因果基线并没有更高。",
        "K1 is not actually above causal baseline.",
    ],
)
def test_negated_comparator_is_not_an_affirmative_fact(text):
    errors, warnings = comparator(text, {"volume_vs_baseline_relation": "below"})
    assert errors == []
    assert any("unverified" in w for w in warnings)


@pytest.mark.parametrize(
    "text",
    [
        "观察下一根收盘对K2高点的保持或收回，并比较该棒相对其自身因果成交量基线的活动。",
        "等待下一根K线收盘并站稳支撑位。",
    ],
)
def test_future_closed_bar_criterion_accepts_normal_chinese(text):
    assert validate_future_confirmation_narrative(text, path="next_confirmation") == []


def test_future_preclose_trigger_is_not_a_closed_bar_criterion():
    warnings = validate_future_confirmation_narrative(
        "观察下一根收盘前是否突破K2高点。", path="next_confirmation"
    )
    assert any("future closed bar" in w for w in warnings)


def test_no_comparator_is_not_applicable():
    assert comparator("背景仍需后续响应，暂时保持中性。", {}) == ([], [])


def test_conditional_and_multiple_subjects_do_not_assert_fact_conflicts():
    for text in (
        "如果K1高于因果基线，可等待接受。",
        "K1和K2高于因果基线。",
        "如果K1高于前棒，高于因果基线才确认。",
    ):
        errors, warnings = comparator(text, {"volume_vs_baseline_relation": "below"})
        assert errors == []
        assert any("unverified" in w for w in warnings)


def test_window_with_both_subjects_does_not_assume_current():
    errors, warnings = comparator(
        "vpa-w040：较新与前窗的活动高于基线。",
        {
            "current_volume_vs_causal_baseline_relation": "above",
            "prior_volume_vs_causal_baseline_relation": "below",
        },
        bar_range="K80-K1",
        evidence_id="vpa-w040",
    )
    assert errors == []
    assert any("unverified" in w for w in warnings)


@pytest.mark.parametrize("codex", [False, True])
def test_joint_allows_vpa_explanations_without_changing_the_proposed_order(codex):
    draft = joint_draft()
    draft["decision"]["risk_assessment"] = "VPA成交量来自单一交易所，不能单独确认突破接受。"
    before = copy.deepcopy(draft)
    record, calls, _ = run_joint(stage2_drafts=[draft], codex=codex, retry=True)
    assert record.exception is None
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    assert not any("price-only" in str(event) for event in record.validation_audit)
    for key in ("entry_price", "stop_loss_price", "take_profit_price", "risk_assessment"):
        assert record.stage2_decision["decision"][key] == before["decision"][key]
    assert draft == before


def test_prompt_explains_output_language_execution_and_interpretation_boundaries():
    from pa_agent.ai.prompt_assembler import (
        _LANGUAGE_ZH_RULE,
        _PA_TERMINOLOGY_ZH,
        _stage2_output_rules,
    )
    from pa_agent.config.paths import PROMPT_DIR

    persona = (PROMPT_DIR / "提示词大纲_人设与思维方式.txt").read_text(encoding="utf-8")
    assert "内部推理" not in _LANGUAGE_ZH_RULE
    assert "思考与 JSON" not in persona
    assert "程序不自动下单" not in persona
    assert "突破入场" in _PA_TERMINOLOGY_ZH
    assert "已通过" in _stage2_output_rules(joint_stage2=True)
    assert "逐句" in _stage2_output_rules(joint_stage2=True)
