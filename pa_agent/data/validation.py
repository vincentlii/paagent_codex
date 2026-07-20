"""Deterministic validation for analysis-ready K-line frames."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from pa_agent.config.settings import MIN_ANALYSIS_BARS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameValidationResult:
    ok: bool
    reason: str
    failed_check: str | None


def validate_analysis_frame(
    frame: Any,
    *,
    minimum_bars: int = MIN_ANALYSIS_BARS,
) -> FrameValidationResult:
    """Validate reproducible input requirements without market interpretation."""
    try:
        return _validate_analysis_frame(frame, minimum_bars=max(1, int(minimum_bars)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("validate_analysis_frame failed unexpectedly: %s", exc)
        return FrameValidationResult(
            ok=False,
            reason=f"数据校验时发生异常：{exc}",
            failed_check="frame_validation_exception",
        )


def _validate_analysis_frame(frame: Any, *, minimum_bars: int) -> FrameValidationResult:
    bars = getattr(frame, "bars", None) if frame is not None else None
    if not bars:
        return _failed("K线序列为空，无法分析。", "bars_empty")

    expected_seqs = list(range(1, len(bars) + 1))
    actual_seqs: list[int] = []
    timestamps: list[float] = []
    for bar in bars:
        try:
            seq = int(bar.seq)
            timestamp = float(bar.ts_open)
            open_ = float(bar.open)
            high = float(bar.high)
            low = float(bar.low)
            close = float(bar.close)
            volume = float(bar.volume)
        except (AttributeError, TypeError, ValueError):
            return _failed("存在K线字段缺失或非数值。", "bar_field_invalid")
        if not all(
            math.isfinite(value)
            for value in (timestamp, open_, high, low, close, volume)
        ):
            return _failed("存在K线字段含 NaN/Inf。", "bar_value_non_finite")
        if high < low or high < max(open_, close) or low > min(open_, close):
            return _failed(f"K{seq} OHLC 几何关系不合法。", "bar_ohlc_invalid")
        if volume < 0:
            return _failed(f"K{seq} 成交量为负数。", "bar_volume_negative")
        if not bool(getattr(bar, "closed", True)):
            return _failed(
                f"K{seq} 尚未收盘；分析窗口只能包含已收盘K线。",
                "forming_bar_in_frame",
            )
        actual_seqs.append(seq)
        timestamps.append(timestamp)

    if actual_seqs != expected_seqs:
        return _failed(
            "K线必须按 K1 最新、序号连续递增排列。",
            "bar_sequence_invalid",
        )
    if any(newer <= older for newer, older in zip(timestamps, timestamps[1:])):
        return _failed(
            "K线时间戳未按最新到最旧严格递减排列。",
            "bar_timestamp_order_invalid",
        )
    if len(bars) < minimum_bars:
        return _failed(
            f"已收盘K线数量 {len(bars)} 根不足 {minimum_bars} 根。",
            "bar_count_insufficient",
        )

    indicators = getattr(frame, "indicators", None)
    if indicators is None:
        return _failed("缺少 EMA20/ATR14 指标序列。", "indicators_missing")
    ema20 = getattr(indicators, "ema20", ())
    atr14 = getattr(indicators, "atr14", ())
    if len(ema20) != len(bars) or len(atr14) != len(bars):
        return _failed("EMA20/ATR14 与 K线数量未对齐。", "indicator_length_mismatch")
    if not _finite_at_zero(ema20) or not _finite_positive_at_zero(atr14):
        return _failed(
            "最新已收盘K线缺少有效 EMA20 或正数 ATR14。",
            "latest_indicators_invalid",
        )
    return FrameValidationResult(ok=True, reason="", failed_check=None)


def _failed(reason: str, check: str) -> FrameValidationResult:
    return FrameValidationResult(ok=False, reason=reason, failed_check=check)


def _finite_at_zero(values: Any) -> bool:
    try:
        return bool(values) and math.isfinite(float(values[0]))
    except (TypeError, ValueError, IndexError):
        return False


def _finite_positive_at_zero(values: Any) -> bool:
    try:
        value = float(values[0])
    except (TypeError, ValueError, IndexError):
        return False
    return math.isfinite(value) and value > 0
