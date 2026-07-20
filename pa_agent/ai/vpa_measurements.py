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
from datetime import UTC, datetime
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pa_agent.data.base import KlineBar, KlineFrame
from pa_agent.data.datetime_ts import ts_open_to_ms
from pa_agent.data.snapshot_bundle import seasonality_context_hash


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
    if facts.get("volume_comparison_available") is True:
        return facts.get("current_vs_prior_volume_relation") in _AVAILABLE_RELATIONS
    return any(
        facts.get(field) in _AVAILABLE_RELATIONS
        for field in (
            "volume_vs_baseline_relation",
            "volume_vs_previous_relation",
            "volume_vs_seasonality_relation",
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


def _percentile_rank(value: float, sample: list[float]) -> float | None:
    if not sample:
        return None
    return 100.0 * sum(item <= value for item in sample) / len(sample)


def _baseline_volumes(volumes: list[float], index: int, lookback: int) -> list[float]:
    """Return only older closed bars, preserving causal measurement semantics."""
    return [value for value in volumes[index + 1 : index + 1 + lookback] if value > 0]


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
        "time_bucket": f"{moment.hour:02d}:00",
        "day_of_week": moment.strftime("%A"),
        "timezone_basis": timezone_basis,
    }


def _seasonality_samples(frame: KlineFrame) -> dict[str, list[float]]:
    """Read optional same-slot historical samples supplied by a data adapter.

    Supported keys are ``"<weekday>|<HH:00>"`` and ``"<HH:00>"``.  The
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
        usable = [number for item in values if (number := _finite(item)) and number > 0]
        if usable:
            out[str(key)] = usable
    return out


def _seasonality_reference(
    context: dict[str, str],
    samples: dict[str, list[float]],
    *,
    min_samples: int,
) -> tuple[float | None, int]:
    day = context.get("day_of_week", "unknown")
    bucket = context.get("time_bucket", "unknown")
    for key in (f"{day}|{bucket}", bucket):
        values = samples.get(key, [])
        if len(values) >= min_samples:
            return median(values), len(values)
    return None, 0


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
    volumes: list[float],
) -> dict[str, Any] | None:
    """Measure a contiguous recent sequence without calling it a market leg."""
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
    positive_volumes = [value for value in sequence_volumes if value > 0]
    volume_complete = len(positive_volumes) == len(sequence_volumes)
    total_volume = sum(positive_volumes) if volume_complete else None
    total_spread_atr = sum(
        (max(0.0, float(bar.high) - float(bar.low)) / atr)
        for index, bar in bars_with_indices
        if (atr := _atr_at(frame, index)) is not None and atr > 0
    )
    close_progress = (
        (float(newest.close) - float(oldest.close)) / atr_reference
        if atr_reference and atr_reference > 0
        else None
    )
    return {
        "bar_range": f"K{oldest.seq}-K{newest.seq}",
        "total_volume": _round(total_volume, 2),
        "volume_complete": volume_complete,
        "positive_volume_count": len(positive_volumes),
        "net_close_progress_atr": _round(close_progress),
        "net_close_progress_direction": _sign_label(close_progress),
        "cumulative_spread_atr": _round(total_spread_atr),
        "bar_count": len(bars_with_indices),
    }


def _sequence_comparisons(
    frame: KlineFrame,
    bars: list[KlineBar],
    volumes: list[float],
    *,
    recent_limit: int,
) -> list[dict[str, Any]]:
    """Return a single adjacent-window comparison when enough bars exist.

    This is intentionally a neutral data primitive.  The LLM decides whether
    the two sequences are comparable price legs in the current PA context.
    """
    window_size = 3
    needed = window_size * 2
    if min(len(bars), recent_limit) < needed:
        return []
    current = _sequence_stats(
        frame,
        [(index, bars[index]) for index in range(0, window_size)],
        volumes,
    )
    prior = _sequence_stats(
        frame,
        [(index, bars[index]) for index in range(window_size, needed)],
        volumes,
    )
    if current is None or prior is None:
        return []
    prior_volume = _finite(prior.get("total_volume"))
    current_volume = _finite(current.get("total_volume"))
    volume_ratio = (
        current_volume / prior_volume
        if current_volume is not None and prior_volume is not None and prior_volume > 0
        else None
    )
    current_progress = _finite(current.get("net_close_progress_atr"))
    prior_progress = _finite(prior.get("net_close_progress_atr"))
    current_spread = _finite(current.get("cumulative_spread_atr"))
    prior_spread = _finite(prior.get("cumulative_spread_atr"))
    volume_comparison_available = bool(
        current.get("volume_complete") and prior.get("volume_complete")
    )
    return [
        {
            "bar_range": f"{prior['bar_range'].split('-', 1)[0]}-{current['bar_range'].split('-', 1)[1]}",
            "facts": {
                "current_window": current["bar_range"],
                "prior_window": prior["bar_range"],
                "volume_comparison_available": volume_comparison_available,
                "current_positive_volume_count": current["positive_volume_count"],
                "prior_positive_volume_count": prior["positive_volume_count"],
                "current_total_volume": current["total_volume"],
                "prior_total_volume": prior["total_volume"],
                "current_vs_prior_volume_ratio": _round(volume_ratio),
                "current_vs_prior_volume_relation": _comparison_label(
                    current_volume,
                    prior_volume,
                ),
                "current_net_close_progress_atr": current["net_close_progress_atr"],
                "prior_net_close_progress_atr": prior["net_close_progress_atr"],
                "current_progress_direction": current["net_close_progress_direction"],
                "prior_progress_direction": prior["net_close_progress_direction"],
                "current_vs_prior_abs_progress_relation": _comparison_label(
                    abs(current_progress) if current_progress is not None else None,
                    abs(prior_progress) if prior_progress is not None else None,
                ),
                "current_cumulative_spread_atr": current["cumulative_spread_atr"],
                "prior_cumulative_spread_atr": prior["cumulative_spread_atr"],
                "current_vs_prior_spread_relation": _comparison_label(
                    current_spread,
                    prior_spread,
                ),
            },
        }
    ]


def compute_vpa_measurements(
    frame: KlineFrame,
    *,
    recent_limit: int = 8,
    baseline_lookback: int = 20,
    minimum_baseline_samples: int = 8,
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
    volumes = [_finite(getattr(bar, "volume", None)) or 0.0 for bar in bars]
    usable_volumes = [volume for volume in volumes if volume > 0]
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
    }
    samples = _seasonality_samples(frame)
    try:
        min_seasonality_samples = max(3, int(_metadata_value(frame, "seasonality_min_samples", 8)))
    except (TypeError, ValueError):
        min_seasonality_samples = 8

    if len(bars) < 3 or len(usable_volumes) < 3:
        return {
            "status": "unavailable",
            "reason": "insufficient_positive_volume",
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
                "bar_count": 0,
                "positive_volume_count": 0,
                "complete": False,
            },
            "seasonality_context": {
                "market_session": market_session,
                "seasonality_available": False,
                "timezone_basis": timezone_basis,
                "comparison": "history_not_provided",
            },
            "recent_bars": [],
            "sequence_comparisons": [],
        }

    status = "available" if volume_kind in {"real", "tick", "proxy"} else "limited"
    recent: list[dict[str, Any]] = []
    limit = max(1, min(int(recent_limit or 1), len(bars)))
    for index, bar in enumerate(bars[:limit]):
        volume = volumes[index]
        volume_available = volume > 0
        baseline = _baseline_volumes(volumes, index, normalized_lookback)
        baseline_sample_count = len(baseline)
        baseline_ready = baseline_sample_count >= baseline_min_samples
        base_median = median(baseline) if baseline_ready else None
        relative_volume = volume / base_median if volume_available and base_median else None
        volume_percentile = (
            _percentile_rank(volume, baseline) if volume_available and baseline_ready else None
        )

        spread = max(0.0, float(bar.high) - float(bar.low))
        atr = _atr_at(frame, index)
        spread_atr = spread / atr if atr and atr > 0 else None
        close_position = None
        if spread > 0:
            close_position = max(0.0, min(1.0, (float(bar.close) - float(bar.low)) / spread))
        body_atr = (float(bar.close) - float(bar.open)) / atr if atr and atr > 0 else None
        older_close = float(bars[index + 1].close) if index + 1 < len(bars) else None
        close_to_close_progress = (
            (float(bar.close) - older_close) / atr
            if older_close is not None and atr and atr > 0
            else None
        )
        time_context = _time_context(
            bar.ts_open,
            timezone_basis=timezone_basis,
            market_session=market_session,
        )
        seasonal_median, seasonal_count = _seasonality_reference(
            time_context,
            samples,
            min_samples=min_seasonality_samples,
        )
        seasonal_rvol = volume / seasonal_median if volume_available and seasonal_median else None
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
                    older_volume if older_volume and older_volume > 0 else None,
                ),
                "volume_percentile": _round(volume_percentile, 1),
                "seasonality_relative_volume": _round(seasonal_rvol),
                "volume_vs_seasonality_relation": _comparison_label(
                    volume if volume_available else None,
                    seasonal_median,
                ),
                "seasonality_sample_count": seasonal_count,
                "spread_atr": _round(spread_atr),
                "close_position": _round(close_position),
                "body_atr": _round(body_atr),
                "directional_progress_atr": _round(close_to_close_progress),
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
    positive_recent_volume_count = sum(1 for item in recent if item.get("volume_available"))
    volume_coverage = {
        "bar_count": len(recent),
        "positive_volume_count": positive_recent_volume_count,
        "complete": positive_recent_volume_count == len(recent),
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
        recent and recent[0]["seasonality_sample_count"] >= min_seasonality_samples
    )
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
            "comparison": (
                "same_slot_historical_median" if seasonality_available else "history_not_provided"
            ),
            "sample_key": (
                f"{newest_context['day_of_week']}|{newest_context['time_bucket']}"
                if seasonality_available
                else None
            ),
            "minimum_samples": min_seasonality_samples,
            "reason": (
                "same_slot_history_supplied_by_data_layer"
                if seasonality_available
                else "a_frozen_snapshot_is_not_a_same_slot_historical_sample"
            ),
        },
        "recent_bars": recent,
        "sequence_comparisons": _sequence_comparisons(
            frame,
            bars,
            volumes,
            recent_limit=limit,
        ),
    }


def _snapshot_hash(frame: KlineFrame) -> str:
    """Return a deterministic raw-snapshot identity, ignoring local run time."""
    provenance = {
        key: _metadata_text(frame, key, "")
        for key in ("source", "venue", "volume_kind", "volume_quality", "timezone_basis")
    }
    seasonal_hash = seasonality_context_hash(frame.data_metadata)
    if seasonal_hash is not None:
        provenance["seasonality_context_hash"] = seasonal_hash
    payload = {
        "symbol": frame.symbol,
        "timeframe": frame.timeframe,
        "provenance": provenance,
        "bars": [
            {
                "seq": bar.seq,
                "ts_open": bar.ts_open,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "closed": bar.closed,
            }
            for bar in frame.bars
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
    recent_limit: int = 8,
    baseline_lookback: int = 20,
    minimum_baseline_samples: int = 8,
) -> dict[str, Any]:
    """Build the immutable, program-owned VPA evidence available to the LLM."""
    measurements = compute_vpa_measurements(
        frame,
        recent_limit=recent_limit,
        baseline_lookback=baseline_lookback,
        minimum_baseline_samples=minimum_baseline_samples,
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
    for index, comparison in enumerate(measurements.get("sequence_comparisons", []), start=1):
        evidence.append(
            {
                "evidence_id": f"vpa-c{index:03d}",
                "source": (
                    "volume"
                    if comparison.get("facts", {}).get("volume_comparison_available")
                    else "price"
                ),
                "bar_range": comparison["bar_range"],
                "facts": comparison["facts"],
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
        "catalog_version": "1.3",
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
                "method": "older_closed_positive_bars_median",
            },
            "seasonality": measurements["seasonality_context"],
            "sequence_comparison": {
                "method": "adjacent_three_bar_windows_without_event_labels",
                "available": bool(measurements.get("sequence_comparisons")),
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
        "percentile | seasonality_rvol/relation/n | spread_atr | close_position | body_atr | "
        "close_to_close_progress_atr/relation | "
        "prior_5(high,low,high_delta/high_relation,low_delta/low_relation) | "
        "prior_20(high,low,high_delta/high_relation,low_delta/low_relation) | time_bucket | day"
    )
    for item in catalog["evidence"]:
        facts = item["facts"]
        if item["evidence_id"].startswith("vpa-c"):
            lines.append(
                f"{item['evidence_id']} | {item['source']} | {item['bar_range']} | "
                "sequence_comparison | "
                f"current={facts.get('current_window')} vs prior={facts.get('prior_window')}; "
                f"volume_available={facts.get('volume_comparison_available')}; "
                f"volume_ratio={facts.get('current_vs_prior_volume_ratio')}; "
                f"volume_relation={facts.get('current_vs_prior_volume_relation')}; "
                f"progress_atr={facts.get('current_net_close_progress_atr')}/"
                f"{facts.get('current_progress_direction')} vs "
                f"{facts.get('prior_net_close_progress_atr')}/"
                f"{facts.get('prior_progress_direction')}; "
                f"abs_progress_relation={facts.get('current_vs_prior_abs_progress_relation')}; "
                f"spread_atr={facts.get('current_cumulative_spread_atr')} vs "
                f"{facts.get('prior_cumulative_spread_atr')}; "
                f"spread_relation={facts.get('current_vs_prior_spread_relation')}"
            )
            continue
        clock = item["time_context"]
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
            f"{facts.get('seasonality_sample_count')} | {facts.get('spread_atr')} | "
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
