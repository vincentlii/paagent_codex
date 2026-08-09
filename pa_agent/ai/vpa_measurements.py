"""Program-computed Wyckoff VPA measurement facts.

The module deliberately measures reproducible facts.  It never labels a
Wyckoff event, infers participant intent, or creates a trade decision.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pa_agent.ai.kline_features import (
    KlineGeometryFeature,
    compute_kline_geometry_features,
)
from pa_agent.data.bar_close_wait import timeframe_to_seconds
from pa_agent.data.base import KlineBar, KlineFrame
from pa_agent.data.datetime_ts import ts_open_to_ms
from pa_agent.data.snapshot_bundle import seasonality_context_hash
from pa_agent.data.volume_context import resolve_volume_observation


@dataclass(frozen=True)
class VpaMeasurementSpec:
    """Versioned measurement policy; values select data, never market meaning."""

    version: str = "2.1"
    recent_limit: int = 8
    baseline_lookback: int = 20
    minimum_baseline_samples: int = 8
    pa_window_sizes: tuple[int, ...] = (5, 8, 10, 20, 40)


DEFAULT_VPA_MEASUREMENT_SPEC = VpaMeasurementSpec()


def _finite(value: float | int | None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def _comparison_label(value: float | None, reference: float | None) -> str:
    """Return an exact numeric relation without assigning market meaning."""
    left = _finite(value)
    right = _finite(reference)
    if left is None or right is None:
        return "unavailable"
    if left > right:
        return "above"
    if left < right:
        return "below"
    return "equal"


_AVAILABLE_RELATIONS = frozenset({"above", "below", "equal"})


def evidence_has_relative_volume_comparison(item: Mapping[str, Any] | None) -> bool:
    """Return whether one evidence item contains a usable volume comparator.

    A raw volume value is not an information increment by itself. VPA grounding
    requires a causal baseline, previous-bar, seasonality, or sequence comparison
    whose relation was actually computed by the program.
    """
    if not isinstance(item, Mapping) or str(item.get("source") or "") != "volume":
        return False
    facts = item.get("facts")
    if not isinstance(facts, Mapping):
        return False
    return any(
        facts.get(field) in _AVAILABLE_RELATIONS
        for field in (
            "volume_vs_baseline_relation",
            "volume_vs_previous_relation",
            "volume_vs_seasonality_relation",
            "current_volume_vs_causal_baseline_relation",
            "current_vs_prior_causal_rvol_relation",
        )
    )


def _sign_label(value: float | None) -> str:
    number = _finite(value)
    if number is None:
        return "unavailable"
    if number > 0:
        return "positive"
    if number < 0:
        return "negative"
    return "flat"


def net_close_progress_direction_relation(facts: Mapping[str, Any]) -> str:
    """Compare two program-computed sequence directions without market labels."""
    current = str(facts.get("current_progress_direction") or "unavailable").lower()
    prior = str(facts.get("prior_progress_direction") or "unavailable").lower()
    directional = {"positive", "negative"}
    if current not in directional or prior not in directional:
        return "flat_or_unavailable"
    return "same" if current == prior else "opposite"


def _percentile_rank(value: float, sample: list[float]) -> float | None:
    if not sample:
        return None
    return 100.0 * sum(item <= value for item in sample) / len(sample)


def _baseline_volumes(
    volumes: list[float | None],
    index: int,
    lookback: int,
) -> list[float]:
    """Return only older closed bars, preserving causal measurement semantics."""
    return [
        value
        for value in volumes[index + 1 : index + 1 + lookback]
        if value is not None and value >= 0
    ]


def _metadata_value(frame: KlineFrame, key: str, default: Any = None) -> Any:
    metadata = getattr(frame, "data_metadata", {}) or {}
    if not isinstance(metadata, Mapping):
        return default
    return metadata.get(key, default)


def _metadata_text(frame: KlineFrame, key: str, default: str) -> str:
    value = _metadata_value(frame, key, default)
    return str(value or default)


def _time_context(
    ts_open: float | int,
    *,
    timezone_basis: str,
    market_session: str,
) -> dict[str, str]:
    """Return a factual clock context without guessing an asset's session."""
    try:
        moment = datetime.fromtimestamp(float(ts_open_to_ms(ts_open)) / 1000, tz=UTC)
        zone = UTC if timezone_basis.upper() == "UTC" else ZoneInfo(timezone_basis)
        moment = moment.astimezone(zone)
    except (
        OSError,
        OverflowError,
        TypeError,
        ValueError,
        ZoneInfoNotFoundError,
    ):
        return {
            "market_session": market_session or "unclassified",
            "time_bucket": "unknown",
            "day_of_week": "unknown",
            "timezone_basis": "invalid",
        }
    return {
        "market_session": market_session or "unclassified",
        "time_bucket": f"{moment.hour:02d}:{moment.minute:02d}",
        "day_of_week": moment.strftime("%A"),
        "timezone_basis": timezone_basis,
    }


def _seasonality_samples(frame: KlineFrame) -> dict[str, list[float]]:
    """Read optional same-slot historical samples supplied by a data adapter.

    Supported keys are ``"<weekday>|<HH:MM>"`` and ``"<HH:MM>"``.  The
    adapter owns collection and timezone alignment; a frozen analysis snapshot
    alone is never treated as a valid same-slot historical sample.
    """
    raw = _metadata_value(frame, "seasonality_samples", {})
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, list[float]] = {}
    for key, values in raw.items():
        if not isinstance(values, (list, tuple)):
            continue
        usable = [
            number
            for item in values
            if (number := _finite(item)) is not None and number >= 0
        ]
        if usable:
            out[str(key)] = usable
    return out


def _seasonality_reference(
    context: dict[str, str],
    samples: dict[str, list[float]],
    *,
    min_samples: int,
) -> tuple[float | None, int, str | None, str | None]:
    day = context.get("day_of_week", "unknown")
    bucket = context.get("time_bucket", "unknown")
    best_count = 0
    best_key: str | None = None
    best_fallback: str | None = None
    for key, fallback_level in (
        (f"{day}|{bucket}", "weekday_slot"),
        (bucket, "all_days_slot"),
    ):
        values = samples.get(key, [])
        if len(values) >= min_samples:
            return median(values), len(values), key, fallback_level
        if len(values) > best_count:
            best_count = len(values)
            best_key = key
            best_fallback = fallback_level
    return None, best_count, best_key, best_fallback


def _seasonality_boundary(frame: KlineFrame) -> tuple[bool, str]:
    """Verify that adapter-supplied samples are exact-slot and strictly older."""
    duration = timeframe_to_seconds(frame.timeframe)
    if duration is None or duration >= 86_400:
        return False, "intraday_seasonality_not_applicable"
    if _metadata_text(frame, "seasonality_slot_scheme", "") != "local_clock_exact":
        return False, "exact_slot_history_not_provided"
    if _metadata_value(frame, "seasonality_history_is_causal", False) is not True:
        return False, "seasonality_history_not_verified_causal"
    history_end = _finite(_metadata_value(frame, "seasonality_history_end_ts_open"))
    if history_end is None:
        return False, "seasonality_history_not_provided"
    if not frame.bars:
        return False, "analysis_window_empty"
    oldest_analysis = float(ts_open_to_ms(frame.bars[-1].ts_open))
    if history_end >= oldest_analysis:
        return False, "seasonality_history_overlaps_analysis_window"
    return True, "same_slot_history_supplied_by_data_layer"


def _atr_at(frame: KlineFrame, index: int) -> float | None:
    values = getattr(getattr(frame, "indicators", None), "atr14", ()) or ()
    return _finite(values[index]) if index < len(values) else None


def _prior_window_extremes(
    bars: list[KlineBar],
    index: int,
    lookback: int,
) -> tuple[float | None, float | None, int]:
    """Return high/low of older bars only; no event or breakout label."""
    prior = bars[index + 1 : index + 1 + lookback]
    if not prior:
        return None, None, 0
    return (
        max(float(item.high) for item in prior),
        min(float(item.low) for item in prior),
        len(prior),
    )


def _sequence_stats(
    frame: KlineFrame,
    bars_with_indices: list[tuple[int, KlineBar]],
    volumes: list[float | None],
    geometry: list[KlineGeometryFeature],
    *,
    baseline_lookback: int,
    baseline_min_samples: int,
) -> dict[str, Any] | None:
    """Measure a contiguous PA-selected window without assigning a VPA label."""
    if len(bars_with_indices) < 2:
        return None
    _newest_index, newest = bars_with_indices[0]
    _oldest_index, oldest = bars_with_indices[-1]
    atr_values = [
        atr
        for index, _bar in bars_with_indices
        if (atr := _atr_at(frame, index)) is not None and atr > 0
    ]
    atr_reference = median(atr_values) if atr_values else None
    sequence_volumes = [volumes[index] for index, _bar in bars_with_indices]
    observed_volumes = [value for value in sequence_volumes if value is not None]
    positive_volumes = [value for value in observed_volumes if value > 0]
    volume_complete = len(observed_volumes) == len(sequence_volumes)
    total_volume = sum(observed_volumes) if volume_complete else None
    range_atr_values = [
        geometry[index].range_atr_ratio
        for index, _bar in bars_with_indices
        if geometry[index].range_atr_ratio is not None
    ]
    range_atr_complete = len(range_atr_values) == len(bars_with_indices)
    total_spread_atr = sum(range_atr_values) if range_atr_complete else None
    close_progress = (
        (float(newest.close) - float(oldest.close)) / atr_reference
        if atr_reference and atr_reference > 0
        else None
    )
    path_steps = [
        geometry[index].close_progress_atr
        for index, _bar in bars_with_indices[:-1]
    ]
    path_complete = all(value is not None for value in path_steps)
    path_length = (
        sum(abs(float(value)) for value in path_steps if value is not None)
        if path_steps and path_complete
        else None
    )
    efficiency = (
        abs(close_progress) / path_length
        if close_progress is not None and path_length is not None and path_length > 0
        else None
    )
    overlaps = [
        geometry[index].overlap_prev_ratio
        for index, _bar in bars_with_indices[:-1]
        if geometry[index].overlap_prev_ratio is not None
    ]
    mean_overlap = sum(overlaps) / len(overlaps) if overlaps else None
    causal_rvol: list[float] = []
    for index, _bar in bars_with_indices:
        value = volumes[index]
        baseline = _baseline_volumes(volumes, index, baseline_lookback)
        if (
            value is None
            or len(baseline) < baseline_min_samples
            or (baseline_median := median(baseline)) <= 0
        ):
            continue
        causal_rvol.append(value / baseline_median)
    median_causal_rvol = median(causal_rvol) if causal_rvol else None
    return {
        "bar_range": f"K{oldest.seq}-K{newest.seq}",
        "total_volume": _round(total_volume, 2),
        "volume_complete": volume_complete,
        "observed_volume_count": len(observed_volumes),
        "positive_volume_count": len(positive_volumes),
        "net_close_progress_atr": _round(close_progress),
        "net_close_progress_direction": _sign_label(close_progress),
        "cumulative_spread_atr": _round(total_spread_atr),
        "close_path_length_atr": _round(path_length),
        "net_to_path_efficiency": _round(efficiency),
        "mean_adjacent_overlap_ratio": _round(mean_overlap),
        "median_causal_relative_volume": _round(median_causal_rvol),
        "causal_relative_volume_sample_count": len(causal_rvol),
        "volume_vs_causal_baseline_relation": _comparison_label(
            median_causal_rvol,
            1.0,
        ),
        "bar_count": len(bars_with_indices),
    }


def _pa_window_slices(
    frame: KlineFrame,
    bars: list[KlineBar],
    volumes: list[float | None],
    geometry: list[KlineGeometryFeature],
    *,
    window_sizes: tuple[int, ...],
    baseline_lookback: int,
    baseline_min_samples: int,
) -> list[dict[str, Any]]:
    """Build neutral slices for PA fact windows; no window is interpreted here."""
    comparisons: list[dict[str, Any]] = []
    for window_size in window_sizes:
        if window_size < 2 or len(bars) < window_size:
            continue
        current = _sequence_stats(
            frame,
            [(index, bars[index]) for index in range(window_size)],
            volumes,
            geometry,
            baseline_lookback=baseline_lookback,
            baseline_min_samples=baseline_min_samples,
        )
        prior = None
        if len(bars) >= window_size * 2:
            prior = _sequence_stats(
                frame,
                [
                    (index, bars[index])
                    for index in range(window_size, window_size * 2)
                ],
                volumes,
                geometry,
                baseline_lookback=baseline_lookback,
                baseline_min_samples=baseline_min_samples,
            )
        if current is None:
            continue
        current_volume = _finite(current.get("total_volume"))
        prior_volume = _finite(prior.get("total_volume")) if prior else None
        current_progress = _finite(current.get("net_close_progress_atr"))
        prior_progress = _finite(prior.get("net_close_progress_atr")) if prior else None
        current_spread = _finite(current.get("cumulative_spread_atr"))
        prior_spread = _finite(prior.get("cumulative_spread_atr")) if prior else None
        current_causal_rvol = _finite(
            current.get("median_causal_relative_volume")
        )
        prior_causal_rvol = (
            _finite(prior.get("median_causal_relative_volume")) if prior else None
        )
        volume_comparison_available = bool(
            prior
            and current.get("volume_complete")
            and prior.get("volume_complete")
        )
        facts = {
            "source_fact_id": f"PA.WINDOW.K{window_size}",
            "current_window": current["bar_range"],
            "prior_window": prior["bar_range"] if prior else None,
            "current_observed_volume_count": current["observed_volume_count"],
            "prior_observed_volume_count": (
                prior["observed_volume_count"] if prior else 0
            ),
            "current_total_volume": current["total_volume"],
            "prior_total_volume": prior["total_volume"] if prior else None,
            "volume_comparison_available": volume_comparison_available,
            "current_vs_prior_volume_ratio": _round(
                current_volume / prior_volume
                if volume_comparison_available
                and current_volume is not None
                and prior_volume is not None
                and prior_volume > 0
                else None
            ),
            "current_vs_prior_volume_relation": _comparison_label(
                current_volume if volume_comparison_available else None,
                prior_volume if volume_comparison_available else None,
            ),
            "current_median_causal_relative_volume": current[
                "median_causal_relative_volume"
            ],
            "current_causal_relative_volume_sample_count": current[
                "causal_relative_volume_sample_count"
            ],
            "current_volume_vs_causal_baseline_relation": current[
                "volume_vs_causal_baseline_relation"
            ],
            "prior_median_causal_relative_volume": (
                prior["median_causal_relative_volume"] if prior else None
            ),
            "prior_volume_vs_causal_baseline_relation": (
                prior["volume_vs_causal_baseline_relation"] if prior else "unavailable"
            ),
            "current_vs_prior_causal_rvol_ratio": _round(
                current_causal_rvol / prior_causal_rvol
                if current_causal_rvol is not None
                and prior_causal_rvol is not None
                and prior_causal_rvol > 0
                else None
            ),
            "current_vs_prior_causal_rvol_relation": _comparison_label(
                current_causal_rvol,
                prior_causal_rvol,
            ),
            "current_net_close_progress_atr": current["net_close_progress_atr"],
            "prior_net_close_progress_atr": (
                prior["net_close_progress_atr"] if prior else None
            ),
            "current_progress_direction": current["net_close_progress_direction"],
            "prior_progress_direction": (
                prior["net_close_progress_direction"] if prior else "unavailable"
            ),
            "current_vs_prior_abs_progress_relation": _comparison_label(
                abs(current_progress) if current_progress is not None and prior else None,
                abs(prior_progress) if prior_progress is not None else None,
            ),
            "current_cumulative_spread_atr": current["cumulative_spread_atr"],
            "prior_cumulative_spread_atr": (
                prior["cumulative_spread_atr"] if prior else None
            ),
            "current_vs_prior_spread_relation": _comparison_label(
                current_spread if prior else None,
                prior_spread,
            ),
            "current_close_path_length_atr": current["close_path_length_atr"],
            "current_net_to_path_efficiency": current["net_to_path_efficiency"],
            "current_mean_adjacent_overlap_ratio": current[
                "mean_adjacent_overlap_ratio"
            ],
        }
        comparisons.append(
            {
                "window_size": window_size,
                "bar_range": (
                    f"{prior['bar_range'].split('-', 1)[0]}-K1"
                    if prior
                    else current["bar_range"]
                ),
                "facts": facts,
            }
        )
    return comparisons


def compute_vpa_measurements(
    frame: KlineFrame,
    *,
    recent_limit: int = DEFAULT_VPA_MEASUREMENT_SPEC.recent_limit,
    baseline_lookback: int = DEFAULT_VPA_MEASUREMENT_SPEC.baseline_lookback,
    minimum_baseline_samples: int = (
        DEFAULT_VPA_MEASUREMENT_SPEC.minimum_baseline_samples
    ),
    pa_window_sizes: tuple[int, ...] = DEFAULT_VPA_MEASUREMENT_SPEC.pa_window_sizes,
) -> dict[str, Any]:
    """Return VPA measurement facts for the newest closed bars.

    Relative-volume comparisons use only older bars from the frozen analysis
    snapshot.  Optional seasonality values require same-slot historical samples
    collected by the data layer; without them the context remains unavailable.
    """
    bars = list(frame.bars or [])
    try:
        normalized_lookback = max(1, int(baseline_lookback))
    except (TypeError, ValueError):
        normalized_lookback = 20
    try:
        requested_minimum = int(minimum_baseline_samples)
    except (TypeError, ValueError):
        requested_minimum = 8
    baseline_min_samples = min(
        normalized_lookback,
        max(3, requested_minimum) if normalized_lookback >= 3 else normalized_lookback,
    )
    volume_observations = [resolve_volume_observation(bar) for bar in bars]
    volumes = [
        observation.value if observation.available else None
        for observation in volume_observations
    ]
    observed_volumes = [volume for volume in volumes if volume is not None]
    positive_volumes = [volume for volume in observed_volumes if volume > 0]
    volume_kind = _metadata_text(frame, "volume_kind", "unknown")
    volume_quality = _metadata_text(frame, "volume_quality", "unknown")
    timezone_basis = _metadata_text(frame, "timezone_basis", "UTC")
    market_session = _metadata_text(frame, "market_session", "unclassified")
    provenance = {
        "source": _metadata_text(frame, "source", "unknown"),
        "venue": _metadata_text(frame, "venue", ""),
        "volume_kind": volume_kind,
        "volume_quality": volume_quality,
        "timezone_basis": timezone_basis,
        "volume_provenance": dict(
            _metadata_value(frame, "volume_provenance", {})
            if isinstance(_metadata_value(frame, "volume_provenance", {}), Mapping)
            else {}
        ),
    }
    samples = _seasonality_samples(frame)
    seasonality_boundary_valid, seasonality_reason = _seasonality_boundary(frame)
    try:
        min_seasonality_samples = max(3, int(_metadata_value(frame, "seasonality_min_samples", 8)))
    except (TypeError, ValueError):
        min_seasonality_samples = 8

    if len(bars) < 3 or len(observed_volumes) < 3:
        return {
            "status": "unavailable",
            "reason": "insufficient_observed_volume",
            "provenance": provenance,
            "baseline_lookback": normalized_lookback,
            "baseline_min_samples": baseline_min_samples,
            "baseline_coverage": {
                "bar_count": 0,
                "usable_bar_count": 0,
                "minimum_samples": baseline_min_samples,
                "complete": False,
            },
            "volume_coverage": {
                "bar_count": len(bars),
                "observed_volume_count": len(observed_volumes),
                "positive_volume_count": len(positive_volumes),
                "complete": False,
            },
            "seasonality_context": {
                "market_session": market_session,
                "seasonality_available": False,
                "timezone_basis": timezone_basis,
                "comparison": (
                    "not_applicable"
                    if seasonality_reason == "intraday_seasonality_not_applicable"
                    else (
                        "current_volume_unavailable"
                        if seasonality_boundary_valid
                        else "history_not_provided"
                    )
                ),
                "sample_key": None,
                "fallback_level": None,
                "history_is_causal": bool(
                    _metadata_value(frame, "seasonality_history_is_causal", False)
                ),
                "reason": (
                    "current_volume_unavailable"
                    if seasonality_boundary_valid
                    else seasonality_reason
                ),
            },
            "recent_bars": [],
            "measurement_slices": [],
        }

    status = "available" if volume_kind in {"real", "tick", "proxy"} else "limited"
    recent: list[dict[str, Any]] = []
    limit = max(1, min(int(recent_limit or 1), len(bars)))
    geometry = compute_kline_geometry_features(frame)
    for index, bar in enumerate(bars[:limit]):
        volume = volumes[index]
        observation = volume_observations[index]
        volume_available = observation.available and volume is not None
        baseline = _baseline_volumes(volumes, index, normalized_lookback)
        baseline_sample_count = len(baseline)
        baseline_ready = baseline_sample_count >= baseline_min_samples
        base_median = median(baseline) if baseline_ready else None
        relative_volume = (
            volume / base_median
            if volume_available and base_median is not None and base_median > 0
            else None
        )
        volume_percentile = (
            _percentile_rank(volume, baseline) if volume_available and baseline_ready else None
        )

        bar_geometry = geometry[index]
        older_close = float(bars[index + 1].close) if index + 1 < len(bars) else None
        time_context = _time_context(
            bar.ts_open,
            timezone_basis=timezone_basis,
            market_session=market_session,
        )
        seasonal_median: float | None = None
        seasonal_count = 0
        seasonal_key: str | None = None
        seasonal_fallback: str | None = None
        if seasonality_boundary_valid:
            (
                seasonal_median,
                seasonal_count,
                seasonal_key,
                seasonal_fallback,
            ) = _seasonality_reference(
                time_context,
                samples,
                min_samples=min_seasonality_samples,
            )
        seasonal_rvol = (
            volume / seasonal_median
            if volume_available and seasonal_median is not None and seasonal_median > 0
            else None
        )
        older_volume = volumes[index + 1] if index + 1 < len(volumes) else None
        volume_vs_previous = (
            volume / older_volume
            if volume_available and older_volume is not None and older_volume > 0
            else None
        )
        prior_5_high, prior_5_low, prior_5_count = _prior_window_extremes(bars, index, 5)
        prior_20_high, prior_20_low, prior_20_count = _prior_window_extremes(bars, index, 20)

        recent.append(
            {
                "seq": int(bar.seq),
                "open": _round(float(bar.open), 6),
                "high": _round(float(bar.high), 6),
                "low": _round(float(bar.low), 6),
                "close": _round(float(bar.close), 6),
                "candle_direction": (
                    "bullish"
                    if float(bar.close) > float(bar.open)
                    else "bearish" if float(bar.close) < float(bar.open) else "flat"
                ),
                "volume": _round(volume, 2) if volume_available else None,
                "volume_state": observation.state,
                "volume_available": volume_available,
                "baseline_sample_count": baseline_sample_count,
                "baseline_min_samples": baseline_min_samples,
                "baseline_median_volume": _round(base_median, 2),
                "relative_volume": _round(relative_volume),
                "volume_vs_baseline_relation": _comparison_label(
                    volume if volume_available else None,
                    base_median,
                ),
                "volume_vs_previous_ratio": _round(volume_vs_previous),
                "volume_vs_previous_relation": _comparison_label(
                    volume if volume_available else None,
                    older_volume,
                ),
                "volume_percentile": _round(volume_percentile, 1),
                "seasonality_relative_volume": _round(seasonal_rvol),
                "volume_vs_seasonality_relation": _comparison_label(
                    volume if volume_available else None,
                    seasonal_median,
                ),
                "seasonality_sample_count": seasonal_count,
                "seasonality_sample_key": seasonal_key,
                "seasonality_fallback_level": seasonal_fallback,
                "spread_atr": bar_geometry.range_atr_ratio,
                "close_position": bar_geometry.close_position,
                "body_atr": bar_geometry.signed_body_atr,
                "directional_progress_atr": bar_geometry.close_progress_atr,
                "close_vs_previous_relation": _comparison_label(
                    float(bar.close),
                    older_close,
                ),
                "prior_5_count": prior_5_count,
                "prior_5_high": _round(prior_5_high, 6),
                "prior_5_low": _round(prior_5_low, 6),
                "high_minus_prior_5_high": _round(
                    float(bar.high) - prior_5_high if prior_5_high is not None else None,
                    6,
                ),
                "high_vs_prior_5_high": _comparison_label(
                    float(bar.high),
                    prior_5_high,
                ),
                "low_minus_prior_5_low": _round(
                    float(bar.low) - prior_5_low if prior_5_low is not None else None,
                    6,
                ),
                "low_vs_prior_5_low": _comparison_label(
                    float(bar.low),
                    prior_5_low,
                ),
                "prior_20_count": prior_20_count,
                "prior_20_high": _round(prior_20_high, 6),
                "prior_20_low": _round(prior_20_low, 6),
                "high_minus_prior_20_high": _round(
                    float(bar.high) - prior_20_high if prior_20_high is not None else None,
                    6,
                ),
                "high_vs_prior_20_high": _comparison_label(
                    float(bar.high),
                    prior_20_high,
                ),
                "low_minus_prior_20_low": _round(
                    float(bar.low) - prior_20_low if prior_20_low is not None else None,
                    6,
                ),
                "low_vs_prior_20_low": _comparison_label(
                    float(bar.low),
                    prior_20_low,
                ),
                "time_context": time_context,
            }
        )

    newest_context = (
        recent[0]["time_context"]
        if recent
        else {
            "time_bucket": "unknown",
            "day_of_week": "unknown",
        }
    )
    observed_recent_volume_count = sum(
        1 for item in recent if item.get("volume_available")
    )
    positive_recent_volume_count = sum(
        1
        for item in recent
        if item.get("volume_available") and (_finite(item.get("volume")) or 0) > 0
    )
    volume_coverage = {
        "bar_count": len(recent),
        "observed_volume_count": observed_recent_volume_count,
        "positive_volume_count": positive_recent_volume_count,
        "complete": observed_recent_volume_count == len(recent),
    }
    usable_baseline_count = sum(
        1 for item in recent if item.get("baseline_sample_count", 0) >= baseline_min_samples
    )
    baseline_coverage = {
        "bar_count": len(recent),
        "usable_bar_count": usable_baseline_count,
        "minimum_samples": baseline_min_samples,
        "complete": usable_baseline_count == len(recent),
    }
    if not volume_coverage["complete"] or not baseline_coverage["complete"]:
        status = "limited"
    seasonality_available = bool(
        seasonality_boundary_valid
        and recent
        and recent[0]["seasonality_sample_count"] >= min_seasonality_samples
    )
    sample_key = recent[0].get("seasonality_sample_key") if recent else None
    sample_count = int(recent[0].get("seasonality_sample_count") or 0) if recent else 0
    fallback_level = (
        recent[0].get("seasonality_fallback_level") if recent else None
    )
    if seasonality_available:
        seasonality_comparison = "same_slot_historical_median"
        resolved_seasonality_reason = "same_slot_reference_available"
    elif seasonality_reason == "intraday_seasonality_not_applicable":
        seasonality_comparison = "not_applicable"
        resolved_seasonality_reason = seasonality_reason
    elif seasonality_boundary_valid:
        seasonality_comparison = "insufficient_same_slot_samples"
        resolved_seasonality_reason = "insufficient_same_slot_samples"
    else:
        seasonality_comparison = "history_not_provided"
        resolved_seasonality_reason = seasonality_reason
    return {
        "status": status,
        "provenance": provenance,
        "baseline_lookback": normalized_lookback,
        "baseline_min_samples": baseline_min_samples,
        "baseline_coverage": baseline_coverage,
        "recent_limit": limit,
        "volume_coverage": volume_coverage,
        "seasonality_context": {
            "market_session": market_session,
            "time_bucket": newest_context["time_bucket"],
            "day_of_week": newest_context["day_of_week"],
            "seasonality_available": seasonality_available,
            "timezone_basis": timezone_basis,
            "comparison": seasonality_comparison,
            "sample_key": sample_key if sample_count else None,
            "fallback_level": fallback_level if sample_count else None,
            "minimum_samples": min_seasonality_samples,
            "history_is_causal": bool(
                _metadata_value(frame, "seasonality_history_is_causal", False)
            ),
            "reason": resolved_seasonality_reason,
        },
        "recent_bars": recent,
        "measurement_slices": _pa_window_slices(
            frame,
            bars,
            volumes,
            geometry,
            window_sizes=tuple(sorted(set(pa_window_sizes))),
            baseline_lookback=normalized_lookback,
            baseline_min_samples=baseline_min_samples,
        ),
    }


def _snapshot_hash(frame: KlineFrame) -> str:
    """Return a deterministic raw-snapshot identity, ignoring local run time."""
    provenance = {
        key: _metadata_text(frame, key, "")
        for key in ("source", "venue", "volume_kind", "volume_quality", "timezone_basis")
    }
    volume_provenance = _metadata_value(frame, "volume_provenance", {})
    if isinstance(volume_provenance, Mapping) and volume_provenance:
        provenance["volume_provenance"] = dict(volume_provenance)
    seasonal_hash = seasonality_context_hash(frame.data_metadata)
    if seasonal_hash is not None:
        provenance["seasonality_context_hash"] = seasonal_hash
    payload = {
        "symbol": frame.symbol,
        "timeframe": frame.timeframe,
        "provenance": provenance,
        "bars": [_snapshot_bar_payload(bar) for bar in frame.bars],
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _snapshot_bar_payload(bar: KlineBar) -> dict[str, Any]:
    payload = {
        "seq": bar.seq,
        "ts_open": bar.ts_open,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "closed": bar.closed,
    }
    volume_state = getattr(bar, "volume_state", None)
    if volume_state is not None:
        payload["volume_state"] = volume_state
    return payload


def vpa_snapshot_hash(frame: KlineFrame) -> str:
    """Public immutable OHLCV/provenance identity used by experiment replay."""
    return _snapshot_hash(frame)


def vpa_catalog_id(catalog: dict[str, Any]) -> str:
    """Recompute a catalog identity from every field except ``catalog_id``."""
    core = {key: value for key, value in catalog.items() if key != "catalog_id"}
    serialized = json.dumps(
        core,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "vpa-catalog-" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def validate_frozen_vpa_catalog(frame: KlineFrame, catalog: object) -> dict[str, Any]:
    """Return a copied catalog only when its frame and content identities match."""
    if not isinstance(catalog, dict):
        raise ValueError("frozen VPA catalog must be an object")
    if str(catalog.get("snapshot_hash") or "") != vpa_snapshot_hash(frame):
        raise ValueError("frozen VPA catalog snapshot_hash does not match the frame")
    if str(catalog.get("catalog_id") or "") != vpa_catalog_id(catalog):
        raise ValueError("frozen VPA catalog_id does not match its contents")
    return copy.deepcopy(catalog)


def build_vpa_evidence_catalog(
    frame: KlineFrame,
    *,
    recent_limit: int = DEFAULT_VPA_MEASUREMENT_SPEC.recent_limit,
    baseline_lookback: int = DEFAULT_VPA_MEASUREMENT_SPEC.baseline_lookback,
    minimum_baseline_samples: int = (
        DEFAULT_VPA_MEASUREMENT_SPEC.minimum_baseline_samples
    ),
    pa_window_sizes: tuple[int, ...] = DEFAULT_VPA_MEASUREMENT_SPEC.pa_window_sizes,
) -> dict[str, Any]:
    """Build the immutable, program-owned VPA evidence available to the LLM."""
    measurements = compute_vpa_measurements(
        frame,
        recent_limit=recent_limit,
        baseline_lookback=baseline_lookback,
        minimum_baseline_samples=minimum_baseline_samples,
        pa_window_sizes=pa_window_sizes,
    )
    snapshot_hash = _snapshot_hash(frame)
    evidence: list[dict[str, Any]] = []
    for index, bar in enumerate(measurements.get("recent_bars", []), start=1):
        evidence.append(
            {
                "evidence_id": f"vpa-e{index:03d}",
                "source": "volume" if bar.get("volume_available") else "price",
                "bar_range": f"K{bar['seq']}",
                "facts": {
                    key: bar[key]
                    for key in (
                        "open",
                        "high",
                        "low",
                        "close",
                        "candle_direction",
                        "volume",
                        "volume_state",
                        "volume_available",
                        "baseline_sample_count",
                        "baseline_min_samples",
                        "baseline_median_volume",
                        "relative_volume",
                        "volume_vs_baseline_relation",
                        "volume_vs_previous_ratio",
                        "volume_vs_previous_relation",
                        "volume_percentile",
                        "seasonality_relative_volume",
                        "volume_vs_seasonality_relation",
                        "seasonality_sample_count",
                        "spread_atr",
                        "close_position",
                        "body_atr",
                        "directional_progress_atr",
                        "close_vs_previous_relation",
                        "prior_5_count",
                        "prior_5_high",
                        "prior_5_low",
                        "high_minus_prior_5_high",
                        "high_vs_prior_5_high",
                        "low_minus_prior_5_low",
                        "low_vs_prior_5_low",
                        "prior_20_count",
                        "prior_20_high",
                        "prior_20_low",
                        "high_minus_prior_20_high",
                        "high_vs_prior_20_high",
                        "low_minus_prior_20_low",
                        "low_vs_prior_20_low",
                    )
                },
                "time_context": bar["time_context"],
            }
        )
    for comparison in measurements.get("measurement_slices", []):
        window_size = int(comparison.get("window_size", 0) or 0)
        facts = comparison.get("facts", {})
        evidence.append(
            {
                "evidence_id": f"vpa-w{window_size:03d}",
                "source": (
                    "volume"
                    if evidence_has_relative_volume_comparison(
                        {"source": "volume", "facts": facts}
                    )
                    else "price"
                ),
                "bar_range": comparison["bar_range"],
                "facts": facts,
                "time_context": measurements["seasonality_context"],
            }
        )
    relative_activity_available = any(
        item.get("facts", {}).get(field) not in {None, "unavailable"}
        for item in evidence
        if item.get("evidence_id", "").startswith("vpa-e")
        for field in (
            "volume_vs_baseline_relation",
            "volume_vs_previous_relation",
            "volume_vs_seasonality_relation",
            "current_volume_vs_causal_baseline_relation",
        )
    )
    capabilities = {
        "relative_activity_comparison": relative_activity_available,
        "seasonality_adjustment": bool(
            measurements["seasonality_context"].get("seasonality_available")
        ),
        "actor_attribution": False,
        "aggressor_side_attribution": False,
        "institutional_intent_attribution": False,
        "interpretation_ceiling": (
            "price_volume_context_only"
            if str(measurements["provenance"].get("volume_kind") or "").lower() == "real"
            else "relative_activity_only"
        ),
    }
    catalog_core = {
        "catalog_version": "2.1",
        "measurement_spec": {
            "version": DEFAULT_VPA_MEASUREMENT_SPEC.version,
            "recent_limit": recent_limit,
            "baseline_lookback": baseline_lookback,
            "minimum_baseline_samples": minimum_baseline_samples,
            "pa_window_sizes": list(pa_window_sizes),
        },
        "snapshot_hash": snapshot_hash,
        "data_status": measurements["status"],
        "data_reason": measurements.get("reason"),
        "provenance": measurements["provenance"],
        "capabilities": capabilities,
        "comparators": {
            "volume_coverage": measurements.get("volume_coverage", {}),
            "volume_baseline": {
                "lookback": measurements.get("baseline_lookback"),
                "minimum_samples": measurements.get("baseline_min_samples"),
                "coverage": measurements.get("baseline_coverage", {}),
                "method": "older_closed_observed_bars_median",
            },
            "seasonality": measurements["seasonality_context"],
            "contextual_slices": {
                "method": "pa_window_aligned_neutral_measurements",
                "available_fact_ids": [
                    item.get("facts", {}).get("source_fact_id")
                    for item in evidence
                    if str(item.get("evidence_id") or "").startswith("vpa-w")
                ],
            },
        },
        "evidence": evidence,
    }
    return {
        **catalog_core,
        "catalog_id": vpa_catalog_id(catalog_core),
    }


def render_vpa_measurements(
    frame: KlineFrame | None = None,
    *,
    catalog: dict[str, Any] | None = None,
    selected_fact_refs: set[str] | None = None,
) -> str:
    """Render the factual VPA evidence catalog for prompt injection."""
    if catalog is None:
        if frame is None:
            raise ValueError("frame or catalog is required")
        catalog = build_vpa_evidence_catalog(frame)
    provenance = catalog.get("provenance", {})
    capabilities = catalog.get("capabilities", {})
    comparators = catalog.get("comparators", {})
    seasonality = comparators.get("seasonality", {})
    coverage = comparators.get("volume_coverage", {})
    baseline = comparators.get("volume_baseline", {})
    baseline_coverage = baseline.get("coverage", {})
    selected_windows = {
        str(fact_id).upper() for fact_id in (selected_fact_refs or set())
    }
    lines = [
        "## VPA_PROGRAM_EVIDENCE_CATALOG",
        "Scope: program-computed facts only. Interpret facts; do not invent evidence IDs or automatic trade conclusions.",
        f"catalog_id: {catalog['catalog_id']}",
        f"data_status: {catalog['data_status']}",
        "volume_provenance: "
        f"source={provenance.get('source', 'unknown')}; venue={provenance.get('venue', '') or 'unknown'}; "
        f"volume_kind={provenance.get('volume_kind', 'unknown')}; "
        f"quality={provenance.get('volume_quality', 'unknown')}",
        "inference_capabilities: "
        f"relative_activity={capabilities.get('relative_activity_comparison', False)}; "
        f"actor_attribution={capabilities.get('actor_attribution', False)}; "
        f"aggressor_side={capabilities.get('aggressor_side_attribution', False)}; "
        f"institutional_intent={capabilities.get('institutional_intent_attribution', False)}; "
        f"ceiling={capabilities.get('interpretation_ceiling', 'relative_activity_only')}",
        "volume_coverage: "
        f"observed={coverage.get('observed_volume_count', 0)}/"
        f"{coverage.get('bar_count', 0)}; "
        f"positive={coverage.get('positive_volume_count', 0)}/"
        f"{coverage.get('bar_count', 0)}; complete={coverage.get('complete', False)}",
        "causal_baseline_coverage: "
        f"usable={baseline_coverage.get('usable_bar_count', 0)}/"
        f"{baseline_coverage.get('bar_count', 0)}; "
        f"minimum_samples={baseline.get('minimum_samples')}; "
        f"lookback={baseline.get('lookback')}; "
        f"complete={baseline_coverage.get('complete', False)}",
        "seasonality: "
        f"available={seasonality.get('seasonality_available', False)}; "
        f"basis={seasonality.get('comparison', 'not_available')}; "
        f"timezone={seasonality.get('timezone_basis', 'UTC')}; "
        f"session={seasonality.get('market_session', 'unclassified')}",
    ]
    if catalog["data_status"] == "unavailable":
        lines.append(f"reason: {catalog.get('data_reason', 'unavailable')}")
        return "\n".join(lines)

    lines.append(
        "evidence_id | source | range | OHLC/direction | volume/available | "
        "baseline_median/rvol/relation/sample_count/minimum | volume_vs_prev_ratio/relation | "
        "percentile | seasonality_rvol/relation/n | comparator_alignment | spread_atr | close_position | body_atr | "
        "close_to_close_progress_atr/relation | "
        "prior_5(high,low,high_delta/high_relation,low_delta/low_relation) | "
        "prior_20(high,low,high_delta/high_relation,low_delta/low_relation) | time_bucket | day"
    )
    for item in catalog["evidence"]:
        facts = item["facts"]
        evidence_id = str(item.get("evidence_id") or "")
        if evidence_id.startswith("vpa-w"):
            if str(facts.get("source_fact_id") or "").upper() not in selected_windows:
                continue
            lines.append(
                f"{evidence_id} | {item['source']} | {item['bar_range']} | "
                f"pa_window_slice={facts.get('source_fact_id')} | "
                f"current={facts.get('current_window')} vs prior={facts.get('prior_window')}; "
                f"volume_available={facts.get('volume_comparison_available')}; "
                f"volume_ratio={facts.get('current_vs_prior_volume_ratio')}; "
                f"volume_relation={facts.get('current_vs_prior_volume_relation')}; "
                f"median_causal_rvol={facts.get('current_median_causal_relative_volume')}; "
                "causal_rvol_relation="
                f"{facts.get('current_volume_vs_causal_baseline_relation')}; "
                "causal_rvol_comparison="
                f"{facts.get('current_vs_prior_causal_rvol_ratio')}/"
                f"{facts.get('current_vs_prior_causal_rvol_relation')}; "
                f"progress_atr={facts.get('current_net_close_progress_atr')}/"
                f"{facts.get('current_progress_direction')} vs "
                f"{facts.get('prior_net_close_progress_atr')}/"
                f"{facts.get('prior_progress_direction')}; "
                "net_progress_direction_relation="
                f"{net_close_progress_direction_relation(facts)}; "
                f"abs_progress_relation={facts.get('current_vs_prior_abs_progress_relation')}; "
                f"spread_atr={facts.get('current_cumulative_spread_atr')} vs "
                f"{facts.get('prior_cumulative_spread_atr')}; "
                f"spread_relation={facts.get('current_vs_prior_spread_relation')}"
                f"; path_atr={facts.get('current_close_path_length_atr')}"
                f"; efficiency={facts.get('current_net_to_path_efficiency')}"
                f"; mean_overlap={facts.get('current_mean_adjacent_overlap_ratio')}"
            )
            continue
        if evidence_id.startswith("vpa-c"):
            # Frozen pre-2.0 catalogs remain replayable; new catalogs never
            # create this legacy fixed-window evidence.
            lines.append(
                f"{evidence_id} | {item['source']} | {item['bar_range']} | "
                "legacy_frozen_sequence_comparison | "
                f"current={facts.get('current_window')} vs prior={facts.get('prior_window')}; "
                f"volume_relation={facts.get('current_vs_prior_volume_relation')}; "
                f"progress_relation={facts.get('current_vs_prior_abs_progress_relation')}; "
                f"spread_relation={facts.get('current_vs_prior_spread_relation')}"
            )
            continue
        clock = item["time_context"]
        rolling_relation = str(facts.get("volume_vs_baseline_relation") or "")
        seasonal_relation = str(facts.get("volume_vs_seasonality_relation") or "")
        comparator_alignment = (
            "divergent"
            if {rolling_relation, seasonal_relation} == {"above", "below"}
            else (
                "aligned"
                if rolling_relation in {"above", "below", "equal"}
                and rolling_relation == seasonal_relation
                else "unavailable_or_mixed"
            )
        )
        lines.append(
            f"{item['evidence_id']} | {item['source']} | {item['bar_range']} | "
            f"{facts.get('open')}/{facts.get('high')}/{facts.get('low')}/{facts.get('close')}/"
            f"{facts.get('candle_direction')} | {facts.get('volume')}/"
            f"{facts.get('volume_available')} | {facts.get('baseline_median_volume')}/"
            f"{facts.get('relative_volume')}/{facts.get('volume_vs_baseline_relation')}/"
            f"{facts.get('baseline_sample_count')}/{facts.get('baseline_min_samples')} | "
            f"{facts.get('volume_vs_previous_ratio')}/"
            f"{facts.get('volume_vs_previous_relation')} | {facts.get('volume_percentile')} | "
            f"{facts.get('seasonality_relative_volume')}/"
            f"{facts.get('volume_vs_seasonality_relation')}/"
            f"{facts.get('seasonality_sample_count')} | {comparator_alignment} | "
            f"{facts.get('spread_atr')} | "
            f"{facts.get('close_position')} | {facts.get('body_atr')} | "
            f"{facts.get('directional_progress_atr')}/"
            f"{facts.get('close_vs_previous_relation')} | "
            f"{facts.get('prior_5_high')},{facts.get('prior_5_low')},"
            f"{facts.get('high_minus_prior_5_high')}/{facts.get('high_vs_prior_5_high')},"
            f"{facts.get('low_minus_prior_5_low')}/{facts.get('low_vs_prior_5_low')} | "
            f"{facts.get('prior_20_high')},{facts.get('prior_20_low')},"
            f"{facts.get('high_minus_prior_20_high')}/{facts.get('high_vs_prior_20_high')},"
            f"{facts.get('low_minus_prior_20_low')}/{facts.get('low_vs_prior_20_low')} | "
            f"{clock.get('time_bucket')} | "
            f"{clock.get('day_of_week')}"
        )
    return "\n".join(lines)


def pa_window_fact_refs(pa_hypotheses: Mapping[str, Any] | None) -> set[str]:
    """Collect PA-declared measurement windows without interpreting the setup."""
    if not isinstance(pa_hypotheses, Mapping):
        return set()
    sections: list[object] = [
        pa_hypotheses.get("primary"),
        pa_hypotheses.get("alternative"),
    ]
    candidates = pa_hypotheses.get("setup_candidates")
    if isinstance(candidates, list):
        sections.extend(candidates)
    result: set[str] = set()
    for section in sections:
        if not isinstance(section, Mapping):
            continue
        refs = section.get("fact_refs")
        if not isinstance(refs, list):
            continue
        for value in refs:
            fact_id = str(value).upper()
            if fact_id.startswith("PA.WINDOW.K") and fact_id[11:].isdigit():
                result.add(fact_id)
    return result


def prompt_vpa_evidence_ids(
    catalog: Mapping[str, Any],
    pa_hypotheses: Mapping[str, Any] | None,
) -> set[str]:
    """Return evidence IDs actually rendered to this VPA role invocation."""
    selected_windows = pa_window_fact_refs(pa_hypotheses)
    result: set[str] = set()
    evidence = catalog.get("evidence")
    if not isinstance(evidence, list):
        return result
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        evidence_id = str(item.get("evidence_id") or "")
        if not evidence_id:
            continue
        if evidence_id.startswith("vpa-w"):
            facts = item.get("facts")
            source_fact_id = (
                str(facts.get("source_fact_id") or "").upper()
                if isinstance(facts, Mapping)
                else ""
            )
            if source_fact_id not in selected_windows:
                continue
        result.add(evidence_id)
    return result
