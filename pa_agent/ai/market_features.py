"""Deterministic rolling-window facts supplied to the model.

No field in this module is a setup, trend, range, breakout, support/resistance,
or trade candidate. The model assigns those meanings from background and location.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from pa_agent.data.base import KlineBar, KlineFrame

MARKET_FEATURES_SECTION_PREFIX = "## 程序可复算市场事实"
_DEFAULT_LOOKBACK = 40
_WINDOWS = (5, 10, 20, 40)


@dataclass(frozen=True)
class RollingWindowFacts:
    bars: int
    high: float
    low: float
    width: float
    width_atr: float | None
    latest_close_position: float | None
    distance_to_high_atr: float | None
    distance_to_low_atr: float | None
    bullish_bars: int
    bearish_bars: int
    flat_bars: int
    mean_body_ratio: float | None
    mean_pair_overlap: float | None


@dataclass(frozen=True)
class PriorWindowComparison:
    reference_bars: int
    prior_high: float
    prior_low: float
    k1_high_minus_prior_high: float
    k1_low_minus_prior_low: float
    k1_close_minus_prior_high: float
    k1_close_minus_prior_low: float


@dataclass(frozen=True)
class SimpleMarketFeatures:
    latest_close: float | None
    atr14: float | None
    ema20: float | None
    close_minus_ema: float | None
    close_minus_ema_atr: float | None
    ema_delta_5: float | None
    ema_delta_10: float | None
    windows: tuple[RollingWindowFacts, ...]
    k1_vs_prior: tuple[PriorWindowComparison, ...]


def compute_simple_market_features(
    frame: KlineFrame,
    *,
    lookback: int = _DEFAULT_LOOKBACK,
) -> SimpleMarketFeatures:
    """Compute numeric facts whose formulas have one reproducible meaning."""
    if not frame.bars:
        return SimpleMarketFeatures(None, None, None, None, None, None, None, (), ())

    close = float(frame.bars[0].close)
    atr = _finite_at(frame.indicators.atr14, 0)
    ema = _finite_at(frame.indicators.ema20, 0)
    windows = tuple(
        _rolling_window(frame.bars[: min(size, len(frame.bars), lookback)], atr)
        for size in _window_sizes(len(frame.bars), lookback)
    )
    comparisons = tuple(
        comparison
        for size in (5, 10, 20)
        for comparison in (_compare_k1_to_prior(frame.bars, size),)
        if comparison is not None
    )
    close_minus_ema = close - ema if ema is not None else None
    return SimpleMarketFeatures(
        latest_close=_round(close),
        atr14=_round_optional(atr),
        ema20=_round_optional(ema),
        close_minus_ema=_round_optional(close_minus_ema),
        close_minus_ema_atr=_round_optional(
            close_minus_ema / atr if close_minus_ema is not None and atr and atr > 0 else None
        ),
        ema_delta_5=_ema_delta(frame, 5),
        ema_delta_10=_ema_delta(frame, 10),
        windows=windows,
        k1_vs_prior=comparisons,
    )


def render_simple_market_features(features: SimpleMarketFeatures) -> str:
    """Render facts without assigning market or trade labels."""
    lines = [
        MARKET_FEATURES_SECTION_PREFIX,
        "",
        "以下只包含公式唯一的数值事实。不得把窗口包络直接等同交易区间，"
        "也不得把越过前窗高低点直接等同有效突破。",
    ]
    if features.latest_close is None:
        return "\n".join(lines + ["- 无可用K线。"])

    lines.extend(
        [
            "",
            "### 当前数值",
            f"- K1 close={features.latest_close}；ATR14={_fmt(features.atr14)}；EMA20={_fmt(features.ema20)}",
            f"- 价格距 EMA20（不是 EMA 斜率）：close-EMA20={_fmt(features.close_minus_ema)}；"
            f"(close-EMA20)/ATR={_fmt(features.close_minus_ema_atr)}",
            f"- EMA20 自身变化（斜率事实）：当前减5棒前={_fmt(features.ema_delta_5)}；"
            f"当前减10棒前={_fmt(features.ema_delta_10)}",
            "- 禁止把 close-EMA20 的正负或绝对值描述成 EMA20 自身上行/下行幅度；"
            "EMA 方向只能引用上一行的 EMA20 自身变化。",
            "",
            "### 滚动窗口统计（窗口包含K1）",
            "窗口 | high | low | width/ATR | close位置 | 距high/ATR | 距low/ATR | 阳/阴/平 | 平均实体比 | 平均相邻重叠",
        ]
    )
    for fact in features.windows:
        lines.append(
            f"K{fact.bars}-K1 | {fact.high} | {fact.low} | {_fmt(fact.width_atr)} | "
            f"{_fmt(fact.latest_close_position)} | {_fmt(fact.distance_to_high_atr)} | "
            f"{_fmt(fact.distance_to_low_atr)} | {fact.bullish_bars}/{fact.bearish_bars}/{fact.flat_bars} | "
            f"{_fmt(fact.mean_body_ratio)} | {_fmt(fact.mean_pair_overlap)}"
        )

    lines.extend(
        [
            "",
            "### K1与此前窗口比较（此前窗口不含K1）",
            "此前棒数 | prior_high | prior_low | K1.high-prior_high | K1.low-prior_low | K1.close-prior_high | K1.close-prior_low",
        ]
    )
    for fact in features.k1_vs_prior:
        lines.append(
            f"{fact.reference_bars} | {fact.prior_high} | {fact.prior_low} | "
            f"{fact.k1_high_minus_prior_high} | {fact.k1_low_minus_prior_low} | "
            f"{fact.k1_close_minus_prior_high} | {fact.k1_close_minus_prior_low}"
        )
    lines.append("")
    lines.append("趋势、周期、Setup、位置意义、突破有效性与交易时机均由 LLM 结合上下文判断。")
    return "\n".join(lines)


def inject_market_features_section(prompt: str, features_block: str) -> str:
    """Replace the previous factual block in a cached incremental prompt."""
    if not features_block.strip():
        return prompt
    block = features_block.strip()
    if MARKET_FEATURES_SECTION_PREFIX in prompt:
        start = prompt.index(MARKET_FEATURES_SECTION_PREFIX)
        tail = prompt[start:]
        end = len(tail)
        for marker in ("\n## ", "\n请根据以上数据", "\n请基于上方完整", "\n请基于上一轮结论"):
            position = tail.find(marker, 1)
            if position >= 0:
                end = min(end, position)
        return prompt[:start] + block + prompt[start + end :]
    for anchor in ("请根据以上数据", "请基于上方完整", "请基于上一轮结论"):
        if anchor in prompt:
            index = prompt.index(anchor)
            return prompt[:index].rstrip() + "\n\n" + block + "\n\n" + prompt[index:]
    return prompt.rstrip() + "\n\n" + block + "\n"


def _window_sizes(bar_count: int, lookback: int) -> tuple[int, ...]:
    sizes = {min(size, bar_count, lookback) for size in _WINDOWS}
    return tuple(sorted(size for size in sizes if size > 0))


def _rolling_window(bars: tuple[KlineBar, ...], atr: float | None) -> RollingWindowFacts:
    high = max(float(bar.high) for bar in bars)
    low = min(float(bar.low) for bar in bars)
    close = float(bars[0].close)
    width = high - low
    body_ratios = [
        abs(float(bar.close) - float(bar.open)) / (float(bar.high) - float(bar.low))
        for bar in bars
        if float(bar.high) > float(bar.low)
    ]
    overlaps = [
        value
        for current, previous in zip(bars, bars[1:])
        for value in (_pair_overlap(current, previous),)
        if value is not None
    ]
    return RollingWindowFacts(
        bars=len(bars),
        high=_round(high),
        low=_round(low),
        width=_round(width),
        width_atr=_round_optional(width / atr if atr and atr > 0 else None),
        latest_close_position=_round_optional((close - low) / width if width > 0 else None),
        distance_to_high_atr=_round_optional((high - close) / atr if atr and atr > 0 else None),
        distance_to_low_atr=_round_optional((close - low) / atr if atr and atr > 0 else None),
        bullish_bars=sum(bar.close > bar.open for bar in bars),
        bearish_bars=sum(bar.close < bar.open for bar in bars),
        flat_bars=sum(bar.close == bar.open for bar in bars),
        mean_body_ratio=_mean(body_ratios),
        mean_pair_overlap=_mean(overlaps),
    )


def _compare_k1_to_prior(
    bars: tuple[KlineBar, ...],
    requested: int,
) -> PriorWindowComparison | None:
    prior = bars[1 : 1 + requested]
    if not prior:
        return None
    k1 = bars[0]
    prior_high = max(float(bar.high) for bar in prior)
    prior_low = min(float(bar.low) for bar in prior)
    return PriorWindowComparison(
        reference_bars=len(prior),
        prior_high=_round(prior_high),
        prior_low=_round(prior_low),
        k1_high_minus_prior_high=_round(float(k1.high) - prior_high),
        k1_low_minus_prior_low=_round(float(k1.low) - prior_low),
        k1_close_minus_prior_high=_round(float(k1.close) - prior_high),
        k1_close_minus_prior_low=_round(float(k1.close) - prior_low),
    )


def _pair_overlap(current: KlineBar, previous: KlineBar) -> float | None:
    overlap = max(0.0, min(current.high, previous.high) - max(current.low, previous.low))
    union = max(current.high, previous.high) - min(current.low, previous.low)
    return overlap / union if union > 0 else None


def _ema_delta(frame: KlineFrame, bars_back: int) -> float | None:
    current = _finite_at(frame.indicators.ema20, 0)
    previous = _finite_at(frame.indicators.ema20, bars_back)
    if current is None or previous is None:
        return None
    return _round(current - previous)


def _finite_at(values: tuple[float, ...], index: int) -> float | None:
    if index >= len(values):
        return None
    value = float(values[index])
    return value if math.isfinite(value) else None


def _mean(values: list[float]) -> float | None:
    return _round_optional(sum(values) / len(values)) if values else None


def _round(value: float) -> float:
    return round(float(value), 6)


def _round_optional(value: float | None) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return _round(value)


def _fmt(value: float | None) -> str:
    return "N/A" if value is None else str(value)
