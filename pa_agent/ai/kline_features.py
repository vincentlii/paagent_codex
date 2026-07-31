"""Reproducible per-bar measurements for model grounding.

This module deliberately does not classify trend bars, signals, setups, momentum,
breakouts, or follow-through. Those concepts depend on context and remain model
judgements.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from pa_agent.data.base import KlineBar, KlineFrame

CLOSE_ZONE_LOWER_BOUNDARY = 0.25
CLOSE_ZONE_UPPER_BOUNDARY = 0.75


def classify_close_position_zone(value: float | None) -> str:
    """Map a normalized close location to a reporting bucket, not a PA label."""
    if value is None or not math.isfinite(float(value)):
        return "unavailable"
    position = float(value)
    if position >= CLOSE_ZONE_UPPER_BOUNDARY:
        return "upper_quartile"
    if position <= CLOSE_ZONE_LOWER_BOUNDARY:
        return "lower_quartile"
    return "middle_half"


def bar_candle_direction_label(bar: KlineBar) -> str:
    """Return the exact close-versus-open relationship."""
    if bar.close > bar.open:
        return "阳线"
    if bar.close < bar.open:
        return "阴线"
    return "平"


@dataclass(frozen=True)
class KlineGeometryFeature:
    seq: int
    candle_direction: str
    full_range: float
    body_size: float
    body_ratio: float | None
    upper_wick_ratio: float | None
    lower_wick_ratio: float | None
    close_position: float | None
    close_zone: str
    atr14: float | None
    range_atr_ratio: float | None
    signed_body_atr: float | None
    close_progress_atr: float | None
    ema20: float | None
    close_minus_ema: float | None
    close_minus_ema_atr: float | None
    relation_to_previous: str
    overlap_prev_ratio: float | None
    high_delta_prev: float | None
    low_delta_prev: float | None
    close_delta_prev: float | None


def compute_kline_geometry_features(
    frame: KlineFrame,
    *,
    limit: int | None = None,
) -> list[KlineGeometryFeature]:
    """Measure exact geometry on the complete newest-first frame."""
    features: list[KlineGeometryFeature] = []
    for index, bar in enumerate(frame.bars):
        previous = frame.bars[index + 1] if index + 1 < len(frame.bars) else None
        ema = _finite_at(frame.indicators.ema20, index)
        atr = _finite_at(frame.indicators.atr14, index)
        features.append(_measure_bar(bar, previous, ema=ema, atr=atr))
    return features[:limit] if limit is not None else features


def _measure_bar(
    bar: KlineBar,
    previous: KlineBar | None,
    *,
    ema: float | None,
    atr: float | None,
) -> KlineGeometryFeature:
    high = float(bar.high)
    low = float(bar.low)
    open_ = float(bar.open)
    close = float(bar.close)
    full_range = high - low
    body = abs(close - open_)

    if full_range > 0:
        body_ratio = body / full_range
        upper_wick_ratio = (high - max(open_, close)) / full_range
        lower_wick_ratio = (min(open_, close) - low) / full_range
        close_position = (close - low) / full_range
    else:
        body_ratio = upper_wick_ratio = lower_wick_ratio = close_position = None

    close_minus_ema = close - ema if ema is not None else None
    return KlineGeometryFeature(
        seq=int(bar.seq),
        candle_direction=(
            "bullish" if close > open_ else "bearish" if close < open_ else "flat"
        ),
        full_range=_round(full_range),
        body_size=_round(body),
        body_ratio=_round_optional(body_ratio),
        upper_wick_ratio=_round_optional(upper_wick_ratio),
        lower_wick_ratio=_round_optional(lower_wick_ratio),
        close_position=_round_optional(close_position),
        close_zone=classify_close_position_zone(close_position),
        atr14=_round_optional(atr),
        range_atr_ratio=_round_optional(full_range / atr if atr and atr > 0 else None),
        signed_body_atr=_round_optional(
            (close - open_) / atr if atr and atr > 0 else None
        ),
        close_progress_atr=_round_optional(
            (close - float(previous.close)) / atr
            if previous is not None and atr and atr > 0
            else None
        ),
        ema20=_round_optional(ema),
        close_minus_ema=_round_optional(close_minus_ema),
        close_minus_ema_atr=_round_optional(
            close_minus_ema / atr if close_minus_ema is not None and atr and atr > 0 else None
        ),
        relation_to_previous=_relation_to_previous(bar, previous),
        overlap_prev_ratio=_round_optional(_overlap_ratio(bar, previous)),
        high_delta_prev=_delta(high, previous.high if previous else None),
        low_delta_prev=_delta(low, previous.low if previous else None),
        close_delta_prev=_delta(close, previous.close if previous else None),
    )


def _relation_to_previous(bar: KlineBar, previous: KlineBar | None) -> str:
    if previous is None:
        return "none"
    if bar.high == previous.high and bar.low == previous.low:
        return "equal_range"
    if bar.high <= previous.high and bar.low >= previous.low:
        return "contained_by_previous"
    if bar.high >= previous.high and bar.low <= previous.low:
        return "contains_previous"
    if bar.low > previous.high:
        return "disjoint_above"
    if bar.high < previous.low:
        return "disjoint_below"
    return "partial_overlap"


def _overlap_ratio(bar: KlineBar, previous: KlineBar | None) -> float | None:
    if previous is None:
        return None
    overlap = max(0.0, min(bar.high, previous.high) - max(bar.low, previous.low))
    union = max(bar.high, previous.high) - min(bar.low, previous.low)
    return overlap / union if union > 0 else None


def _finite_at(values: tuple[float, ...], index: int) -> float | None:
    if index >= len(values):
        return None
    value = float(values[index])
    return value if math.isfinite(value) else None


def _delta(value: float, previous: float | None) -> float | None:
    return _round_optional(value - float(previous)) if previous is not None else None


def _round(value: float) -> float:
    return round(float(value), 6)


def _round_optional(value: float | None) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return _round(value)
