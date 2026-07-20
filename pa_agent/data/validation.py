"""Deterministic validation for analysis-ready K-line snapshots."""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

from pa_agent.config.settings import MIN_ANALYSIS_BARS
from pa_agent.data.bar_close_wait import timeframe_to_seconds
from pa_agent.data.contracts import (
    DataQualityIssue,
    DataQualityReport,
    ResolvedInstrumentIdentity,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameValidationResult:
    """Compatibility view over the richer :class:`DataQualityReport`."""

    ok: bool
    reason: str
    failed_check: str | None
    report: DataQualityReport = field(default_factory=DataQualityReport)


def validate_analysis_frame(
    frame: Any,
    *,
    minimum_bars: int = MIN_ANALYSIS_BARS,
    now_ms: int | None = None,
    expected_identity: ResolvedInstrumentIdentity | dict[str, Any] | None = None,
) -> FrameValidationResult:
    """Validate reproducible input requirements without market interpretation."""
    try:
        report = assess_analysis_frame(
            frame,
            minimum_bars=max(1, int(minimum_bars)),
            now_ms=now_ms,
            expected_identity=expected_identity,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("validate_analysis_frame failed unexpectedly: %s", exc)
        report = DataQualityReport(
            (
                DataQualityIssue(
                    code="frame_validation_exception",
                    severity="hard_error",
                    message=f"数据校验时发生异常：{exc}",
                ),
            )
        )
    return FrameValidationResult(
        ok=report.ok,
        reason=report.reason,
        failed_check=_legacy_failed_check(report.failed_check),
        report=report,
    )


def assess_analysis_frame(
    frame: Any,
    *,
    minimum_bars: int = MIN_ANALYSIS_BARS,
    now_ms: int | None = None,
    expected_identity: ResolvedInstrumentIdentity | dict[str, Any] | None = None,
) -> DataQualityReport:
    issues: list[DataQualityIssue] = []
    timeframe = str(getattr(frame, "timeframe", "") or "")
    bars = getattr(frame, "bars", None) if frame is not None else None
    if not bars:
        return _hard("bars_empty", "K线序列为空，无法分析。", timeframe)

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
            return _hard(
                "bar_field_invalid",
                "存在K线字段缺失或非数值。",
                timeframe,
            )
        if not all(
            math.isfinite(value)
            for value in (timestamp, open_, high, low, close, volume)
        ):
            return _hard(
                "bar_value_non_finite",
                "存在K线字段含 NaN/Inf。",
                timeframe,
                seq,
            )
        if high < low or high < max(open_, close) or low > min(open_, close):
            return _hard(
                "bar_ohlc_invalid",
                f"K{seq} OHLC 几何关系不合法。",
                timeframe,
                seq,
            )
        if volume < 0:
            return _hard(
                "bar_volume_negative",
                f"K{seq} 成交量为负数。",
                timeframe,
                seq,
            )
        if not bool(getattr(bar, "closed", True)):
            return _hard(
                "forming_bar_in_frame",
                f"K{seq} 尚未收盘；分析窗口只能包含已收盘K线。",
                timeframe,
                seq,
            )
        actual_seqs.append(seq)
        timestamps.append(timestamp)

    if actual_seqs != expected_seqs:
        return _hard(
            "bar_sequence_invalid",
            "K线必须按 K1 最新、序号连续递增排列。",
            timeframe,
        )
    duplicate_positions = [
        index + 1
        for index, (newer, older) in enumerate(
            zip(timestamps, timestamps[1:], strict=False)
        )
        if newer == older
    ]
    if duplicate_positions:
        return _hard(
            "bar_timestamp_duplicate",
            "K线包含重复时间戳。",
            timeframe,
            *tuple(duplicate_positions),
            details={"duplicate_pairs": duplicate_positions},
        )
    if any(newer < older for newer, older in zip(timestamps, timestamps[1:], strict=False)):
        return _hard(
            "bar_timestamp_order_invalid",
            "K线时间戳未按最新到最旧严格递减排列。",
            timeframe,
        )
    if len(bars) < minimum_bars:
        return _hard(
            "bar_count_insufficient",
            f"已收盘K线数量 {len(bars)} 根不足 {minimum_bars} 根。",
            timeframe,
        )

    indicators = getattr(frame, "indicators", None)
    if indicators is None:
        return _hard(
            "indicators_missing",
            "缺少 EMA20/ATR14 指标序列。",
            timeframe,
        )
    ema20 = getattr(indicators, "ema20", ())
    atr14 = getattr(indicators, "atr14", ())
    if len(ema20) != len(bars) or len(atr14) != len(bars):
        return _hard(
            "indicator_length_mismatch",
            "EMA20/ATR14 与 K线数量未对齐。",
            timeframe,
        )
    if not _finite_at_zero(ema20) or not _finite_positive_at_zero(atr14):
        return _hard(
            "latest_indicators_invalid",
            "最新已收盘K线缺少有效 EMA20 或正数 ATR14。",
            timeframe,
        )

    identity = ResolvedInstrumentIdentity.from_value(
        getattr(frame, "data_metadata", {}) or {}
    )
    expected = ResolvedInstrumentIdentity.from_value(expected_identity)
    if expected is not None:
        if identity is None:
            issues.append(
                DataQualityIssue(
                    code="source_identity_missing",
                    severity="hard_error",
                    message="快照缺少已解析的数据源身份。",
                    timeframe=timeframe,
                )
            )
        elif not identity.same_instrument(expected):
            issues.append(
                DataQualityIssue(
                    code="source_identity_mismatch",
                    severity="hard_error",
                    message="快照来源与本次分析冻结的数据源身份不一致。",
                    timeframe=timeframe,
                    details={
                        "expected": expected.to_dict(),
                        "actual": identity.to_dict(),
                    },
                )
            )
    elif identity is None:
        issues.append(
            DataQualityIssue(
                code="source_identity_unavailable",
                severity="warning",
                message="历史或测试快照未提供数据源身份，无法验证跨周期同源性。",
                timeframe=timeframe,
            )
        )

    issues.extend(
        _time_series_issues(
            frame,
            timestamps,
            now_ms=now_ms,
        )
    )
    return DataQualityReport(tuple(issues))


def validate_snapshot_identities(
    current_frame: Any,
    higher_frame: Any | None,
    *,
    expected_identity: ResolvedInstrumentIdentity | dict[str, Any] | None = None,
    now_ms: int | None = None,
) -> DataQualityReport:
    """Validate both frames and reject cross-timeframe provider drift."""
    expected = ResolvedInstrumentIdentity.from_value(expected_identity)
    current_report = assess_analysis_frame(
        current_frame,
        now_ms=now_ms,
        expected_identity=expected,
    )
    if higher_frame is None:
        return current_report
    higher_report = assess_analysis_frame(
        higher_frame,
        minimum_bars=20,
        now_ms=now_ms,
        expected_identity=expected,
    )
    current_identity = ResolvedInstrumentIdentity.from_value(
        getattr(current_frame, "data_metadata", {}) or {}
    )
    higher_identity = ResolvedInstrumentIdentity.from_value(
        getattr(higher_frame, "data_metadata", {}) or {}
    )
    identity_report = DataQualityReport()
    if (
        current_identity is not None
        and higher_identity is not None
        and not current_identity.same_instrument(higher_identity)
    ):
        identity_report = DataQualityReport(
            (
                DataQualityIssue(
                    code="cross_timeframe_source_identity_drift",
                    severity="hard_error",
                    message="主周期与高周期解析到了不同的数据源品种或交易场所。",
                    details={
                        "current": current_identity.to_dict(),
                        "higher": higher_identity.to_dict(),
                    },
                ),
            )
        )
    return current_report.merged(higher_report, identity_report)


def _time_series_issues(
    frame: Any,
    timestamps: list[float],
    *,
    now_ms: int | None,
) -> list[DataQualityIssue]:
    timeframe = str(getattr(frame, "timeframe", "") or "")
    duration_s = timeframe_to_seconds(timeframe)
    if duration_s is None or not timestamps:
        return []
    duration_ms = duration_s * 1000
    metadata = getattr(frame, "data_metadata", {}) or {}
    metadata = metadata if isinstance(metadata, dict) else {}
    identity = ResolvedInstrumentIdentity.from_value(metadata)
    reference_now = int(now_ms if now_ms is not None else time.time() * 1000)
    tolerance_ms = max(
        1_000,
        int(metadata.get("clock_skew_tolerance_ms", min(60_000, duration_ms * 0.1))),
    )
    issues: list[DataQualityIssue] = []

    future = [
        index + 1
        for index, timestamp in enumerate(timestamps)
        if timestamp > reference_now + tolerance_ms
    ]
    if future:
        issues.append(
            DataQualityIssue(
                code="bar_timestamp_in_future",
                severity="hard_error",
                message="快照包含超出时钟容差的未来 K 线。",
                timeframe=timeframe,
                bar_seqs=tuple(future),
                details={
                    "now_ms": reference_now,
                    "clock_skew_tolerance_ms": tolerance_ms,
                },
            )
        )

    latest_close_ms = int(timestamps[0]) + duration_ms
    strict_intervals = bool(
        identity is not None
        or metadata.get("strict_interval_validation") is True
    )
    strict_close_time = bool(
        metadata.get("strict_close_time") is True
        or metadata.get("continuous_market") is True
    )
    if latest_close_ms > reference_now + tolerance_ms:
        issues.append(
            DataQualityIssue(
                code="closed_bar_not_elapsed",
                severity="hard_error" if strict_close_time else "warning",
                message=(
                    "标记为已收盘的 K1 尚未到达理论收盘时间。"
                    if strict_close_time
                    else "历史/测试快照的 K1 收盘标记无法用可靠来源时钟复核。"
                ),
                timeframe=timeframe,
                bar_seqs=(1,),
                details={
                    "bar_close_ms": latest_close_ms,
                    "now_ms": reference_now,
                },
            )
        )

    missing_intervals = 0
    irregular_pairs: list[tuple[int, int]] = []
    for index, (newer, older) in enumerate(
        zip(timestamps, timestamps[1:], strict=False),
        start=1,
    ):
        delta = newer - older
        if delta < duration_ms - tolerance_ms:
            irregular_pairs.append((index, index + 1))
            continue
        quotient = delta / duration_ms
        nearest = round(quotient)
        if nearest < 1 or abs(quotient - nearest) > tolerance_ms / duration_ms:
            irregular_pairs.append((index, index + 1))
        elif nearest > 1:
            missing_intervals += nearest - 1
    if irregular_pairs:
        issues.append(
            DataQualityIssue(
                code="bar_interval_irregular",
                severity="hard_error" if strict_intervals else "warning",
                message=(
                    "K线时间间隔与声明周期不一致。"
                    if strict_intervals
                    else "历史/测试快照缺少来源身份且周期不规则，保留为警告。"
                ),
                timeframe=timeframe,
                details={"pairs": irregular_pairs},
            )
        )
    if missing_intervals:
        continuous = metadata.get("continuous_market")
        severity = "hard_error" if continuous is True else "warning"
        issues.append(
            DataQualityIssue(
                code="bar_intervals_missing",
                severity=severity,
                message=(
                    "连续市场快照存在缺失 K 线。"
                    if severity == "hard_error"
                    else "快照存在跨时段缺口；市场日历未知，保留为警告。"
                ),
                timeframe=timeframe,
                details={"missing_intervals": missing_intervals},
            )
        )

    newest_close_ms = latest_close_ms
    stale_intervals = max(
        1,
        int(metadata.get("max_staleness_intervals", 3)),
    )
    stale_by_ms = reference_now - newest_close_ms
    if stale_by_ms > stale_intervals * duration_ms:
        market_open = metadata.get("market_open")
        continuous = metadata.get("continuous_market")
        severity = "hard_error" if market_open is True or continuous is True else "warning"
        issues.append(
            DataQualityIssue(
                code="snapshot_stale",
                severity=severity,
                message=(
                    "市场应处于连续/开盘状态，但最新已收盘 K 线已过期。"
                    if severity == "hard_error"
                    else "最新 K 线较旧；市场时段状态未知，保留为警告。"
                ),
                timeframe=timeframe,
                bar_seqs=(1,),
                details={
                    "stale_by_ms": stale_by_ms,
                    "max_staleness_intervals": stale_intervals,
                },
            )
        )
    return issues


def _hard(
    code: str,
    message: str,
    timeframe: str,
    *bar_seqs: int,
    details: dict[str, Any] | None = None,
) -> DataQualityReport:
    return DataQualityReport(
        (
            DataQualityIssue(
                code=code,
                severity="hard_error",
                message=message,
                timeframe=timeframe,
                bar_seqs=tuple(bar_seqs),
                details=dict(details or {}),
            ),
        )
    )


def _legacy_failed_check(code: str | None) -> str | None:
    if code == "bar_timestamp_duplicate":
        return "bar_timestamp_order_invalid"
    return code


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
