"""Causal volume metadata used by VPA measurement.

This module owns only provider facts, availability, and clock-aligned history.
It never assigns Wyckoff labels or trading meaning.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pa_agent.data.bar_close_wait import timeframe_to_seconds
from pa_agent.data.datetime_ts import ts_open_to_ms

VolumeState = Literal[
    "observed",
    "zero_observed",
    "missing",
    "unsupported",
    "legacy_unknown",
]

_INTRADAY_SAMPLE_PERIODS = 8
_MAX_HISTORY_BARS = 2_500


@dataclass(frozen=True)
class VolumeObservation:
    value: float | None
    state: VolumeState

    @property
    def available(self) -> bool:
        return self.state in {"observed", "zero_observed"}


def normalize_provider_volume(
    raw_value: object,
    *,
    zero_is_observed: bool,
) -> tuple[float, VolumeState]:
    """Normalize a provider field while preserving its availability semantics."""
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return 0.0, "missing"
    if not math.isfinite(value) or value < 0:
        return 0.0, "missing"
    if value > 0:
        return value, "observed"
    if zero_is_observed:
        return 0.0, "zero_observed"
    return 0.0, "legacy_unknown"


def resolve_volume_observation(bar: object) -> VolumeObservation:
    """Resolve one bar without treating a legacy zero as observed volume."""
    raw_state = str(getattr(bar, "volume_state", None) or "").strip().lower()
    try:
        value = float(getattr(bar, "volume", None))
    except (TypeError, ValueError):
        value = math.nan
    if raw_state == "unsupported":
        return VolumeObservation(None, "unsupported")
    if raw_state == "missing" or not math.isfinite(value) or value < 0:
        return VolumeObservation(None, "missing")
    if value > 0:
        return VolumeObservation(value, "observed")
    if value == 0 and raw_state in {"observed", "zero_observed"}:
        return VolumeObservation(0.0, "zero_observed")
    return VolumeObservation(None, "legacy_unknown")


def seasonality_history_target_bars(timeframe: str) -> int:
    """Return a bounded initial backfill target for exact intraday slots."""
    duration = timeframe_to_seconds(timeframe)
    if duration is None or duration >= 86_400:
        return 0
    bars_per_day = max(1, math.ceil(86_400 / duration))
    return min(_MAX_HISTORY_BARS, bars_per_day * _INTRADAY_SAMPLE_PERIODS)


def snapshot_fetch_count(
    analysis_bars: int,
    *,
    timeframe: str,
    indicator_warmup_bars: int,
    vpa_enabled: bool,
    guard_bars: int = 5,
) -> int:
    """Return the fetch size; VPA-off preserves the existing request size."""
    base = max(1, int(analysis_bars)) + max(0, int(indicator_warmup_bars))
    base += max(0, int(guard_bars))
    if not vpa_enabled:
        return base
    seasonality = seasonality_history_target_bars(timeframe)
    return max(base, max(1, int(analysis_bars)) + seasonality + max(0, int(guard_bars)))


def merge_bar_history(
    current_bars: list[Any],
    cached_bars: list[Any],
    *,
    limit: int,
) -> list[Any]:
    """Merge same-source snapshots by open time, preferring current values."""
    merged: dict[int, Any] = {}
    for bar in cached_bars:
        try:
            merged[int(ts_open_to_ms(bar.ts_open))] = bar
        except (TypeError, ValueError, OverflowError):
            continue
    for bar in current_bars:
        try:
            merged[int(ts_open_to_ms(bar.ts_open))] = bar
        except (TypeError, ValueError, OverflowError):
            continue
    ordered = [merged[key] for key in sorted(merged, reverse=True)]
    return ordered[: max(1, int(limit))]


def build_seasonality_metadata(
    history_bars: list[Any],
    *,
    timeframe: str,
    timezone_basis: str,
    oldest_analysis_ts_open: float | int,
) -> dict[str, Any]:
    """Build exact-slot samples strictly older than the analysis window."""
    duration = timeframe_to_seconds(timeframe)
    if duration is None or duration >= 86_400:
        return _empty_seasonality("not_applicable", causal=True)
    try:
        zone = UTC if timezone_basis.upper() == "UTC" else ZoneInfo(timezone_basis)
    except (ZoneInfoNotFoundError, ValueError):
        return _empty_seasonality("invalid_timezone", causal=False)

    cutoff = float(ts_open_to_ms(oldest_analysis_ts_open))
    grouped: dict[str, list[float]] = defaultdict(list)
    used_timestamps: list[float] = []
    for bar in history_bars:
        try:
            timestamp = float(ts_open_to_ms(bar.ts_open))
            moment = datetime.fromtimestamp(timestamp / 1000, tz=UTC).astimezone(zone)
        except (OSError, OverflowError, TypeError, ValueError):
            continue
        if timestamp >= cutoff:
            continue
        observation = resolve_volume_observation(bar)
        if not observation.available or observation.value is None:
            continue
        slot = f"{moment.hour:02d}:{moment.minute:02d}"
        weekday = moment.strftime("%A")
        grouped[slot].append(observation.value)
        grouped[f"{weekday}|{slot}"].append(observation.value)
        used_timestamps.append(timestamp)

    return {
        "seasonality_samples": dict(grouped),
        "seasonality_slot_scheme": "local_clock_exact",
        "seasonality_history_bars": len(used_timestamps),
        "seasonality_history_start_ts_open": min(used_timestamps, default=None),
        "seasonality_history_end_ts_open": max(used_timestamps, default=None),
        "seasonality_history_is_causal": bool(not used_timestamps or max(used_timestamps) < cutoff),
    }


def _empty_seasonality(slot_scheme: str, *, causal: bool) -> dict[str, Any]:
    return {
        "seasonality_samples": {},
        "seasonality_slot_scheme": slot_scheme,
        "seasonality_history_bars": 0,
        "seasonality_history_start_ts_open": None,
        "seasonality_history_end_ts_open": None,
        "seasonality_history_is_causal": causal,
    }
