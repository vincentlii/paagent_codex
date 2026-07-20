from __future__ import annotations

from pa_agent.ai.market_features import (
    MARKET_FEATURES_SECTION_PREFIX,
    compute_simple_market_features,
    inject_market_features_section,
    render_simple_market_features,
)
from tests.integration.conftest import make_frame


def test_rolling_windows_are_numeric_and_include_k1() -> None:
    frame = make_frame()
    facts = compute_simple_market_features(frame)

    assert [window.bars for window in facts.windows] == [5, 10, 20]
    assert facts.latest_close == frame.bars[0].close
    assert facts.windows[0].high == max(bar.high for bar in frame.bars[:5])
    assert facts.windows[0].low == min(bar.low for bar in frame.bars[:5])
    assert not hasattr(facts, "trend")
    assert not hasattr(facts, "setup")


def test_k1_comparison_excludes_k1_from_reference_window() -> None:
    frame = make_frame()
    facts = compute_simple_market_features(frame)
    comparison = facts.k1_vs_prior[0]

    assert comparison.reference_bars == 5
    assert comparison.prior_high == max(bar.high for bar in frame.bars[1:6])
    assert comparison.k1_high_minus_prior_high == (
        frame.bars[0].high - comparison.prior_high
    )


def test_render_states_epistemic_boundary() -> None:
    text = render_simple_market_features(compute_simple_market_features(make_frame()))

    assert text.startswith(MARKET_FEATURES_SECTION_PREFIX)
    assert "不得把窗口包络直接等同交易区间" in text
    assert "均由 LLM 结合上下文判断" in text


def test_injection_replaces_existing_fact_block_once() -> None:
    first = inject_market_features_section("before\n\n请根据以上数据", "## 程序可复算市场事实\nA")
    second = inject_market_features_section(first, "## 程序可复算市场事实\nB")

    assert second.count(MARKET_FEATURES_SECTION_PREFIX) == 1
    assert "\nB" in second
    assert "\nA" not in second
