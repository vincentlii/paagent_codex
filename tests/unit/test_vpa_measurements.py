from __future__ import annotations

from dataclasses import replace
from datetime import timedelta, timezone

from pa_agent.ai.vpa_measurements import (
    build_vpa_evidence_catalog,
    compute_vpa_measurements,
    net_close_progress_direction_relation,
    render_vpa_measurements,
)
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame


def _frame(volumes: list[float]) -> KlineFrame:
    bars = tuple(
        KlineBar(
            seq=i + 1,
            ts_open=float(1_700_000_000 - i * 60),
            open=100.0 + i,
            high=102.0 + i,
            low=99.0 + i,
            close=101.5 + i,
            volume=vol,
            closed=True,
        )
        for i, vol in enumerate(volumes)
    )
    return KlineFrame(
        symbol="XAUUSD",
        timeframe="15m",
        bars=bars,
        indicators=IndicatorBundle(
            ema20=tuple(100.0 for _ in bars),
            atr14=tuple(3.0 for _ in bars),
        ),
        snapshot_ts_local_ms=1,
    )


def test_vpa_measurements_render_facts_without_trade_conclusions() -> None:
    frame = _frame([220, 180, 120, 90, 80, 100, 110, 95, 105, 115, 125, 130])

    data = compute_vpa_measurements(frame)
    catalog = build_vpa_evidence_catalog(frame)
    text = render_vpa_measurements(frame)

    assert data["status"] == "limited"
    assert data["provenance"]["volume_kind"] == "unknown"
    assert data["recent_bars"][0]["seq"] == 1
    assert "VPA_PROGRAM_EVIDENCE_CATALOG" in text
    assert catalog["catalog_id"].startswith("vpa-catalog-")
    assert catalog["data_status"] == "limited"
    assert catalog["evidence"][0]["evidence_id"] == "vpa-e001"
    assert catalog["evidence"][0]["source"] == "volume"
    first_facts = catalog["evidence"][0]["facts"]
    assert first_facts["open"] == 100.0
    assert first_facts["close"] == 101.5
    assert first_facts["candle_direction"] == "bullish"
    assert first_facts["volume_vs_previous_ratio"] == 1.222
    assert first_facts["volume_vs_previous_relation"] == "above"
    assert first_facts["close_vs_previous_relation"] == "below"
    assert first_facts["prior_5_low"] == 100.0
    assert first_facts["low_minus_prior_5_low"] == -1.0
    assert first_facts["high_vs_prior_5_high"] == "below"
    assert first_facts["low_vs_prior_5_low"] == "below"
    assert "rvol" in text
    assert "percentile" in text
    assert "spread_atr" in text
    assert "close_position" in text
    assert "volume_provenance" in text
    assert "seasonality" in text
    assert "OHLC" in text
    assert "prior_5" in text
    assert "volume_relation=above" in text
    assert "above" in text
    assert data["seasonality_context"]["seasonality_available"] is False
    assert data["seasonality_context"]["comparison"] == "history_not_provided"

    forbidden = (
        "absorption",
        "exhaustion",
        "confirmed_breakout",
        "spring",
        "UTAD",
        "SOS",
        "LPS",
    )
    assert all(term not in text for term in forbidden)


def test_vpa_measurements_unavailable_when_volume_is_missing() -> None:
    frame = _frame([0, 0, 0, 0])

    data = compute_vpa_measurements(frame)
    text = render_vpa_measurements(frame)

    assert data["status"] == "unavailable"
    assert data["recent_bars"] == []
    assert "VPA_PROGRAM_EVIDENCE_CATALOG" in text
    assert "unavailable" in text


def test_non_positive_bar_volume_is_not_treated_as_low_activity() -> None:
    frame = _frame([0, 180, 120, 90, 80, 100, 110, 95])

    data = compute_vpa_measurements(frame)
    catalog = build_vpa_evidence_catalog(frame)

    newest = data["recent_bars"][0]
    assert newest["volume"] is None
    assert newest["volume_available"] is False
    assert newest["relative_volume"] is None
    assert newest["volume_vs_previous_relation"] == "unavailable"
    assert data["status"] == "limited"
    assert data["volume_coverage"] == {
        "bar_count": 8,
        "positive_volume_count": 7,
        "complete": False,
    }
    assert catalog["evidence"][0]["source"] == "price"
    assert catalog["comparators"]["volume_coverage"]["complete"] is False
    sequence = next(item for item in catalog["evidence"] if item["evidence_id"] == "vpa-c001")
    assert sequence["source"] == "price"
    assert sequence["facts"]["volume_comparison_available"] is False
    assert sequence["facts"]["current_total_volume"] is None


def test_vpa_baseline_never_falls_back_to_newer_bars() -> None:
    data = compute_vpa_measurements(_frame([100, 100, 100, 100, 100]))

    # K3 has only K4 and K5 behind it, so the causal baseline is insufficient.
    assert data["recent_bars"][2]["baseline_median_volume"] is None
    assert data["recent_bars"][2]["relative_volume"] is None


def test_vpa_sparse_causal_baseline_is_disclosed_and_not_scored() -> None:
    frame = _frame([100, 100, 0, 100, 0, 100, 0, 0, 0, 0, 0, 0])
    data = compute_vpa_measurements(frame)
    catalog = build_vpa_evidence_catalog(frame)
    text = render_vpa_measurements(catalog=catalog)

    newest = data["recent_bars"][0]
    assert newest["baseline_sample_count"] == 3
    assert newest["baseline_min_samples"] == 8
    assert newest["baseline_median_volume"] is None
    assert newest["relative_volume"] is None
    assert newest["volume_percentile"] is None
    assert newest["volume_vs_baseline_relation"] == "unavailable"
    assert data["baseline_coverage"]["complete"] is False
    assert data["status"] == "limited"
    catalog_facts = catalog["evidence"][0]["facts"]
    assert catalog_facts["baseline_sample_count"] == 3
    assert catalog_facts["baseline_min_samples"] == 8
    assert catalog["comparators"]["volume_baseline"]["coverage"]["complete"] is False
    assert "causal_baseline_coverage: usable=0/8" in text
    assert "None/None/unavailable/3/8" in text


def test_vpa_snapshot_identity_ignores_local_observation_time() -> None:
    first = build_vpa_evidence_catalog(_frame([120] * 12))
    second_frame = _frame([120] * 12)
    second_frame = KlineFrame(
        symbol=second_frame.symbol,
        timeframe=second_frame.timeframe,
        bars=second_frame.bars,
        indicators=second_frame.indicators,
        snapshot_ts_local_ms=999_999,
    )
    second = build_vpa_evidence_catalog(second_frame)

    assert first["snapshot_hash"] == second["snapshot_hash"]
    assert first["catalog_id"] == second["catalog_id"]


def test_vpa_catalog_identity_includes_injected_measurement_context() -> None:
    frame = _frame([120] * 12)
    asia = build_vpa_evidence_catalog(replace(frame, data_metadata={"market_session": "Asia"}))
    london = build_vpa_evidence_catalog(replace(frame, data_metadata={"market_session": "London"}))

    assert asia["snapshot_hash"] == london["snapshot_hash"]
    assert asia["catalog_id"] != london["catalog_id"]


def test_vpa_snapshot_identity_covers_raw_seasonality_history() -> None:
    frame = _frame([120] * 12)
    first = build_vpa_evidence_catalog(
        replace(
            frame,
            data_metadata={
                "timezone_basis": "UTC",
                "seasonality_history_bars": 8,
                "seasonality_samples": {"00:00": [100.0] * 8},
            },
        )
    )
    second = build_vpa_evidence_catalog(
        replace(
            frame,
            data_metadata={
                "timezone_basis": "UTC",
                "seasonality_history_bars": 8,
                "seasonality_samples": {"00:00": [200.0] * 8},
            },
        )
    )

    assert first["snapshot_hash"] != second["snapshot_hash"]


def test_vpa_uses_close_to_close_progress_and_sequence_comparison() -> None:
    data = compute_vpa_measurements(_frame([100, 120, 140, 80, 90, 110, 100]))

    # The fixture's K1 close is 1.0 below K2 close and ATR is 3.0.
    assert data["recent_bars"][0]["directional_progress_atr"] == -0.333
    assert data["recent_bars"][0]["body_atr"] == 0.5
    comparison = data["sequence_comparisons"][0]
    assert comparison["bar_range"] == "K6-K1"
    assert comparison["facts"]["current_window"] == "K3-K1"
    assert comparison["facts"]["prior_window"] == "K6-K4"
    assert comparison["facts"]["current_vs_prior_volume_relation"] == "above"
    assert comparison["facts"]["current_progress_direction"] == "negative"
    assert net_close_progress_direction_relation(comparison["facts"]) == "same"
    assert "net_progress_direction_relation=same" in render_vpa_measurements(
        _frame([100, 120, 140, 80, 90, 110, 100])
    )


def test_vpa_sequence_direction_relation_is_exact_and_non_semantic() -> None:
    assert net_close_progress_direction_relation(
        {
            "current_progress_direction": "positive",
            "prior_progress_direction": "negative",
        }
    ) == "opposite"
    assert net_close_progress_direction_relation(
        {
            "current_progress_direction": "flat",
            "prior_progress_direction": "negative",
        }
    ) == "flat_or_unavailable"


def test_vpa_seasonality_requires_adapter_supplied_same_slot_history() -> None:
    frame = _frame([150] * 12)
    frame = KlineFrame(
        symbol=frame.symbol,
        timeframe=frame.timeframe,
        bars=frame.bars,
        indicators=frame.indicators,
        snapshot_ts_local_ms=frame.snapshot_ts_local_ms,
        data_metadata={
            "seasonality_samples": {"Tuesday|22:00": [100, 100, 100, 100, 100]},
            "seasonality_min_samples": 5,
        },
    )
    data = compute_vpa_measurements(frame)

    assert data["seasonality_context"]["seasonality_available"] is True
    assert data["recent_bars"][0]["seasonality_relative_volume"] == 1.5


def test_time_bucket_is_converted_to_declared_timezone(monkeypatch) -> None:
    import pa_agent.ai.vpa_measurements as measurements

    monkeypatch.setattr(
        measurements,
        "ZoneInfo",
        lambda _name: timezone(timedelta(hours=8)),
    )

    context = measurements._time_context(
        1_700_000_000_000,
        timezone_basis="Asia/Shanghai",
        market_session="",
    )

    assert context["time_bucket"] == "06:00"
    assert context["timezone_basis"] == "Asia/Shanghai"
