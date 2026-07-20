from __future__ import annotations

import json
from dataclasses import replace

from pa_agent.ai.fact_catalog import (
    build_analysis_fact_catalog,
    render_pa_fact_catalog,
)
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame


def make_mixed_frame() -> KlineFrame:
    rows = (
        (4081.785, 4088.795, 4075.825, 4087.520, 26246.0),
        (4075.100, 4086.485, 4072.270, 4081.665, 19332.0),
        (4076.335, 4083.700, 4071.000, 4075.150, 27575.0),
        (4093.190, 4095.255, 4068.985, 4076.225, 20235.0),
        (4030.180, 4104.050, 4029.375, 4093.120, 39199.0),
        (4026.080, 4031.035, 4023.330, 4030.350, 10886.0),
        (4031.110, 4035.990, 4025.295, 4026.085, 8085.0),
        (4028.080, 4031.915, 4025.820, 4031.185, 6763.0),
    )
    bars = tuple(
        KlineBar(
            seq=index,
            ts_open=1_784_035_800_000 - (index - 1) * 900_000,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            closed=True,
        )
        for index, (open_, high, low, close, volume) in enumerate(rows, start=1)
    )
    return KlineFrame(
        symbol="XAUUSD",
        timeframe="15m",
        bars=bars,
        indicators=IndicatorBundle(
            ema20=tuple(4050.0 + index for index in range(len(bars))),
            atr14=tuple(12.5 for _ in bars),
        ),
        snapshot_ts_local_ms=1_784_036_219_741,
        data_metadata={
            "source": "tradingview",
            "venue": "OANDA",
            "volume_kind": "unknown",
            "volume_quality": "unknown",
            "timezone_basis": "UTC",
        },
    )


def test_catalog_recomputes_direction_counts_runs_and_extrema() -> None:
    catalog = build_analysis_fact_catalog(make_mixed_frame())

    five = catalog.fact("PA.WINDOW.K5").value
    eight = catalog.fact("PA.WINDOW.K8").value

    assert five["direction_counts"] == {"bullish": 3, "bearish": 2, "flat": 0}
    assert eight["direction_counts"] == {"bullish": 5, "bearish": 3, "flat": 0}
    assert eight["max_consecutive"] == {"bullish": 2, "bearish": 2, "flat": 0}
    assert five["high"] == 4104.05
    assert five["high_bars"] == ["K5"]


def test_catalog_exposes_exact_bounded_comparisons() -> None:
    catalog = build_analysis_fact_catalog(make_mixed_frame())

    assert catalog.fact("PA.COMPARE.K1.PRIOR5").value["high_relation"] == "below"
    assert catalog.fact("PA.BAR.K1").value["close_zone"] == "upper_quartile"
    assert catalog.compare_bar_to_range(2, 4, 3, field="high") == "below"


def test_catalog_exposes_recent_adjacent_bar_comparisons() -> None:
    catalog = build_analysis_fact_catalog(make_mixed_frame())

    pair = catalog.fact("PA.PAIR.K3.K4")

    assert pair.fact_type == "adjacent_bar_comparison"
    assert pair.bar_range == "K3 vs K4"
    assert pair.value == {
        "newer_bar": "K3",
        "older_bar": "K4",
        "newer_high_vs_older_high": "below",
        "newer_low_vs_older_low": "above",
        "newer_close_vs_older_close": "below",
    }


def test_rendered_catalog_keeps_recent_bar_facts_without_repeating_distant_bars() -> None:
    catalog = build_analysis_fact_catalog(make_mixed_frame())

    rendered = render_pa_fact_catalog(catalog, detailed_bar_limit=5)

    assert "PA.BAR.K5" in rendered
    assert "PA.BAR.K6" not in rendered
    assert "PA.WINDOW.K8" in rendered


def test_price_fact_relations_are_independent_of_symbol_timeframe_and_scale() -> None:
    base = make_mixed_frame()
    scaled = replace(
        base,
        symbol="EURUSD",
        timeframe="1h",
        bars=tuple(
            replace(
                bar,
                open=bar.open / 4000.0,
                high=bar.high / 4000.0,
                low=bar.low / 4000.0,
                close=bar.close / 4000.0,
            )
            for bar in base.bars
        ),
        indicators=replace(
            base.indicators,
            ema20=tuple(value / 4000.0 for value in base.indicators.ema20),
            atr14=tuple(value / 4000.0 for value in base.indicators.atr14),
        ),
    )

    base_catalog = build_analysis_fact_catalog(base)
    scaled_catalog = build_analysis_fact_catalog(scaled)

    assert (
        base_catalog.fact("PA.PAIR.K3.K4").value
        == scaled_catalog.fact("PA.PAIR.K3.K4").value
    )
    assert (
        base_catalog.fact("PA.BAR.K1").value["close_zone"]
        == scaled_catalog.fact("PA.BAR.K1").value["close_zone"]
    )


def test_pa_view_is_price_only_while_vpa_view_has_provenance_capabilities() -> None:
    catalog = build_analysis_fact_catalog(make_mixed_frame(), include_vpa=True)

    pa_payload = json.dumps(catalog.view("pa"), sort_keys=True).lower()
    vpa_view = catalog.view("vpa")

    assert "volume" not in pa_payload
    assert vpa_view["provenance"]["volume_kind"] == "unknown"
    assert vpa_view["capabilities"]["actor_attribution"] is False
    assert vpa_view["capabilities"]["interpretation_ceiling"] == "relative_activity_only"


def test_catalog_identity_is_stable_for_same_closed_price_snapshot() -> None:
    frame = make_mixed_frame()

    first = build_analysis_fact_catalog(frame)
    second = build_analysis_fact_catalog(frame)

    assert first.catalog_id == second.catalog_id
    assert first.snapshot_hash == second.snapshot_hash


def test_analysis_catalog_reuses_vpa_capabilities_without_rederiving_them() -> None:
    frame = make_mixed_frame()
    sparse = replace(
        frame,
        bars=tuple(
            replace(bar, volume=volume)
            for bar, volume in zip(
                frame.bars,
                (100.0, 0.0, 100.0, 0.0, 100.0, 0.0, 0.0, 0.0),
                strict=True,
            )
        ),
    )

    catalog = build_analysis_fact_catalog(sparse, include_vpa=True)

    assert catalog.capabilities == catalog.vpa_catalog["capabilities"]
    assert catalog.capabilities["relative_activity_comparison"] is False


def test_pa_fact_catalog_default_does_not_construct_vpa_state() -> None:
    catalog = build_analysis_fact_catalog(make_mixed_frame())

    assert catalog.provenance == {}
    assert catalog.capabilities == {}
    assert catalog.vpa_catalog == {}
