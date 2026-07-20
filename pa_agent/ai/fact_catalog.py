"""Single source of reproducible facts for PA and VPA analysis.

The catalog owns measurements and identities only. It never classifies a
market cycle, names a setup, assigns Wyckoff events, or recommends a trade.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Literal

from pa_agent.ai.kline_features import compute_kline_geometry_features
from pa_agent.data.base import KlineBar, KlineFrame

FactView = Literal["pa", "vpa"]
_WINDOWS = (5, 8, 10, 20, 40)
_ADJACENT_COMPARISON_COUNT = 5
_DEFAULT_DETAILED_BAR_LIMIT = 8


@dataclass(frozen=True)
class AnalysisFact:
    fact_id: str
    domain: str
    bar_range: str
    fact_type: str
    value: Any

    def as_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "domain": self.domain,
            "bar_range": self.bar_range,
            "fact_type": self.fact_type,
            "value": self.value,
        }


@dataclass(frozen=True)
class AnalysisFactCatalog:
    catalog_id: str
    snapshot_hash: str
    facts: tuple[AnalysisFact, ...]
    provenance: dict[str, Any]
    capabilities: dict[str, Any]
    vpa_catalog: dict[str, Any]

    def fact(self, fact_id: str) -> AnalysisFact:
        for item in self.facts:
            if item.fact_id == fact_id:
                return item
        raise KeyError(fact_id)

    def fact_ids(self, *, view: FactView = "pa") -> set[str]:
        domains = {"price", "indicator"} if view == "pa" else None
        return {
            item.fact_id
            for item in self.facts
            if domains is None or item.domain in domains
        }

    def view(self, view: FactView) -> dict[str, Any]:
        if view == "pa":
            facts = [
                item.as_dict()
                for item in self.facts
                if item.domain in {"price", "indicator"}
            ]
            return {
                "catalog_version": "1.0",
                "catalog_id": self.catalog_id,
                "snapshot_hash": self.snapshot_hash,
                "facts": facts,
            }
        if view == "vpa":
            return {
                "catalog_version": "1.0",
                "catalog_id": self.catalog_id,
                "snapshot_hash": self.snapshot_hash,
                "provenance": dict(self.provenance),
                "capabilities": dict(self.capabilities),
                "facts": [item.as_dict() for item in self.facts],
                "vpa_catalog": self.vpa_catalog,
            }
        raise ValueError(f"unsupported fact view: {view}")

    def compare_bar_to_range(
        self,
        bar_seq: int,
        older_seq: int,
        newer_seq: int,
        *,
        field: Literal["high", "low", "close"],
    ) -> str:
        bars = {
            int(item.value["seq"]): item.value
            for item in self.facts
            if item.fact_type == "bar_ohlc_geometry"
        }
        bar = bars.get(int(bar_seq))
        start = min(int(older_seq), int(newer_seq))
        end = max(int(older_seq), int(newer_seq))
        reference = [bars[seq] for seq in range(start, end + 1) if seq in bars]
        if bar is None or not reference:
            return "unavailable"
        value = float(bar[field])
        boundary = (
            max(float(item[field]) for item in reference)
            if field in {"high", "close"}
            else min(float(item[field]) for item in reference)
        )
        return _relation(value, boundary)


def build_analysis_fact_catalog(
    frame: KlineFrame,
    *,
    include_vpa: bool = False,
) -> AnalysisFactCatalog:
    """Build immutable PA facts, optionally attaching the independent VPA view."""
    bars = tuple(
        bar for bar in frame.bars if bool(getattr(bar, "closed", False)) and int(bar.seq) >= 1
    )
    geometry = {
        item.seq: item for item in compute_kline_geometry_features(frame)
    }
    facts: list[AnalysisFact] = []
    for bar in bars:
        feature = geometry.get(int(bar.seq))
        value: dict[str, Any] = {
            "seq": int(bar.seq),
            "open": _round(bar.open),
            "high": _round(bar.high),
            "low": _round(bar.low),
            "close": _round(bar.close),
            "candle_direction": _direction(bar),
            "closed": True,
        }
        if feature is not None:
            value.update(
                {
                    "range": _round(feature.full_range),
                    "body_ratio": _round_optional(feature.body_ratio),
                    "upper_wick_ratio": _round_optional(feature.upper_wick_ratio),
                    "lower_wick_ratio": _round_optional(feature.lower_wick_ratio),
                    "close_position": _round_optional(feature.close_position),
                    "close_zone": feature.close_zone,
                    "range_atr_ratio": _round_optional(feature.range_atr_ratio),
                    "relation_to_previous": feature.relation_to_previous,
                }
            )
        facts.append(
            AnalysisFact(
                fact_id=f"PA.BAR.K{bar.seq}",
                domain="price",
                bar_range=f"K{bar.seq}",
                fact_type="bar_ohlc_geometry",
                value=value,
            )
        )

    for newer, older in pairwise(bars[: _ADJACENT_COMPARISON_COUNT + 1]):
        facts.append(
            AnalysisFact(
                fact_id=f"PA.PAIR.K{newer.seq}.K{older.seq}",
                domain="price",
                bar_range=f"K{newer.seq} vs K{older.seq}",
                fact_type="adjacent_bar_comparison",
                value={
                    "newer_bar": f"K{newer.seq}",
                    "older_bar": f"K{older.seq}",
                    "newer_high_vs_older_high": _relation(
                        float(newer.high), float(older.high)
                    ),
                    "newer_low_vs_older_low": _relation(
                        float(newer.low), float(older.low)
                    ),
                    "newer_close_vs_older_close": _relation(
                        float(newer.close), float(older.close)
                    ),
                },
            )
        )

    for size in _window_sizes(len(bars)):
        window = bars[:size]
        facts.append(
            AnalysisFact(
                fact_id=f"PA.WINDOW.K{size}",
                domain="price",
                bar_range=f"K{size}-K1" if size > 1 else "K1",
                fact_type="rolling_price_window",
                value=_window_facts(window),
            )
        )

    for requested in (5, 10, 20):
        prior = bars[1 : 1 + requested]
        if not bars or not prior:
            continue
        k1 = bars[0]
        prior_high = max(float(item.high) for item in prior)
        prior_low = min(float(item.low) for item in prior)
        facts.append(
            AnalysisFact(
                fact_id=f"PA.COMPARE.K1.PRIOR{requested}",
                domain="price",
                bar_range=f"K{prior[-1].seq}-K1",
                fact_type="bar_to_prior_window",
                value={
                    "reference_bar_count": len(prior),
                    "prior_high": _round(prior_high),
                    "prior_low": _round(prior_low),
                    "high_relation": _relation(float(k1.high), prior_high),
                    "low_relation": _relation(float(k1.low), prior_low),
                    "close_vs_high_relation": _relation(float(k1.close), prior_high),
                    "close_vs_low_relation": _relation(float(k1.close), prior_low),
                },
            )
        )

    snapshot_hash = _price_snapshot_hash(frame, bars)
    catalog_id = f"analysis-facts-{snapshot_hash[:16]}"
    if include_vpa:
        from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog

        vpa_catalog = build_vpa_evidence_catalog(frame)
    else:
        vpa_catalog = {}
    provenance = dict(vpa_catalog.get("provenance") or {})
    capabilities = dict(vpa_catalog.get("capabilities") or {})
    return AnalysisFactCatalog(
        catalog_id=catalog_id,
        snapshot_hash=snapshot_hash,
        facts=tuple(facts),
        provenance=provenance,
        capabilities=capabilities,
        vpa_catalog=vpa_catalog,
    )


def render_pa_fact_catalog(
    catalog: AnalysisFactCatalog,
    *,
    detailed_bar_limit: int = _DEFAULT_DETAILED_BAR_LIMIT,
) -> str:
    """Render a compact price-only catalog for the PA prompt."""
    detail_limit = max(0, int(detailed_bar_limit))
    lines = [
        "## ANALYSIS_FACT_CATALOG",
        f"catalog_id: {catalog.catalog_id}",
        "Scope: deterministic closed-bar price and indicator facts only.",
        "Use fact IDs as evidence. Do not recalculate counts, runs, extrema, or comparisons.",
        (
            f"Detailed PA.BAR facts are limited to K1-K{detail_limit}; "
            "longer context remains available through PA.WINDOW facts and the K-line table."
        ),
        "Evidence first, interpretation second; program facts do not decide cycle, Setup, or timing.",
        "",
        "fact_id | bar_range | canonical_value",
    ]
    for item in catalog.facts:
        if item.domain not in {"price", "indicator"}:
            continue
        if item.fact_type == "bar_ohlc_geometry":
            value = item.value
            if int(value.get("seq") or 0) > detail_limit:
                continue
            compact = {
                key: value.get(key)
                for key in (
                    "candle_direction",
                    "relation_to_previous",
                    "range_atr_ratio",
                    "close_position",
                    "close_zone",
                )
            }
        else:
            compact = item.value
        lines.append(
            f"{item.fact_id} | {item.bar_range} | "
            + json.dumps(compact, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
    return "\n".join(lines)


def _window_sizes(bar_count: int) -> tuple[int, ...]:
    sizes = {min(value, bar_count) for value in _WINDOWS if bar_count > 0}
    return tuple(sorted(value for value in sizes if value > 0))


def _window_facts(bars: tuple[KlineBar, ...]) -> dict[str, Any]:
    directions = [_direction(bar) for bar in bars]
    highs = [float(bar.high) for bar in bars]
    lows = [float(bar.low) for bar in bars]
    high = max(highs)
    low = min(lows)
    return {
        "bar_count": len(bars),
        "direction_counts": {
            label: directions.count(label) for label in ("bullish", "bearish", "flat")
        },
        "direction_sequence_newest_first": directions,
        "max_consecutive": {
            label: _max_run(directions, label)
            for label in ("bullish", "bearish", "flat")
        },
        "high": _round(high),
        "high_bars": [f"K{bar.seq}" for bar in bars if float(bar.high) == high],
        "low": _round(low),
        "low_bars": [f"K{bar.seq}" for bar in bars if float(bar.low) == low],
        "latest_close_vs_window_high": _relation(float(bars[0].close), high),
        "latest_close_vs_window_low": _relation(float(bars[0].close), low),
    }


def _max_run(values: list[str], expected: str) -> int:
    best = 0
    current = 0
    for value in values:
        current = current + 1 if value == expected else 0
        best = max(best, current)
    return best


def _direction(bar: KlineBar) -> str:
    if float(bar.close) > float(bar.open):
        return "bullish"
    if float(bar.close) < float(bar.open):
        return "bearish"
    return "flat"


def _relation(left: float, right: float) -> str:
    if left > right:
        return "above"
    if left < right:
        return "below"
    return "equal"


def _price_snapshot_hash(frame: KlineFrame, bars: tuple[KlineBar, ...]) -> str:
    payload = {
        "symbol": frame.symbol,
        "timeframe": frame.timeframe,
        "bars": [
            {
                "seq": bar.seq,
                "ts_open": bar.ts_open,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "closed": bar.closed,
            }
            for bar in bars
        ],
        "ema20": [_finite_or_none(value) for value in frame.indicators.ema20],
        "atr14": [_finite_or_none(value) for value in frame.indicators.atr14],
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _round(value: float | int) -> float:
    return round(float(value), 6)


def _round_optional(value: float | None) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return _round(value)


def _finite_or_none(value: float | int) -> float | None:
    number = float(value)
    return number if math.isfinite(number) else None
