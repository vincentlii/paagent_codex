from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pa_agent.ai.kline_features import compute_kline_geometry_features
from pa_agent.ai.vpa_measurements import compute_vpa_measurements
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.data.snapshot import build_analysis_frame
from pa_agent.data.volume_context import (
    merge_bar_history,
    normalize_provider_volume,
    seasonality_history_target_bars,
    snapshot_fetch_count,
)


def _bar(
    seq: int,
    moment: datetime,
    *,
    volume: float,
    volume_state: str | None = None,
    open_: float = 100.0,
    close: float = 101.0,
) -> KlineBar:
    return KlineBar(
        seq=seq,
        ts_open=int(moment.timestamp() * 1000),
        open=open_,
        high=max(open_, close) + 1.0,
        low=min(open_, close) - 1.0,
        close=close,
        volume=volume,
        volume_state=volume_state,
        closed=True,
    )


def test_intraday_history_target_is_decoupled_from_indicator_warmup() -> None:
    assert seasonality_history_target_bars("15m") >= 8 * 24 * 4
    assert seasonality_history_target_bars("1h") >= 8 * 24
    assert seasonality_history_target_bars("1d") == 0
    assert seasonality_history_target_bars("1w") == 0


def test_provider_volume_normalization_preserves_missing_and_zero_semantics() -> None:
    assert normalize_provider_volume(None, zero_is_observed=True) == (0.0, "missing")
    assert normalize_provider_volume(float("nan"), zero_is_observed=True) == (
        0.0,
        "missing",
    )
    assert normalize_provider_volume(0, zero_is_observed=True) == (
        0.0,
        "zero_observed",
    )
    assert normalize_provider_volume(0, zero_is_observed=False) == (
        0.0,
        "legacy_unknown",
    )


def test_volume_state_extension_preserves_legacy_positional_bar_arguments() -> None:
    bar = KlineBar(1, 1, 1, 2, 0, 1, 10, 20, 3.5, False)

    assert bar.amount == 20
    assert bar.pct_chg == 3.5
    assert bar.closed is False
    assert bar.volume_state is None


def test_vpa_fetch_backfill_is_opt_in_and_incremental_history_prefers_current() -> None:
    off_count = snapshot_fetch_count(
        20,
        timeframe="15m",
        indicator_warmup_bars=50,
        vpa_enabled=False,
    )
    on_count = snapshot_fetch_count(
        20,
        timeframe="15m",
        indicator_warmup_bars=50,
        vpa_enabled=True,
    )
    assert off_count == 75
    assert on_count >= 20 + 8 * 24 * 4

    latest = datetime(2026, 7, 20, 10, 15, tzinfo=UTC)
    cached = [
        _bar(1, latest - timedelta(minutes=15), volume=100),
        _bar(2, latest - timedelta(minutes=30), volume=90),
    ]
    current = [
        _bar(1, latest, volume=120),
        _bar(2, latest - timedelta(minutes=15), volume=110),
    ]
    merged = merge_bar_history(current, cached, limit=3)
    assert [bar.volume for bar in merged] == [120, 110, 90]


def test_analysis_frame_builds_exact_intraday_slots_from_causal_history() -> None:
    latest = datetime(2026, 7, 20, 10, 15, tzinfo=UTC)
    analysis = [
        _bar(index + 1, latest - timedelta(minutes=15 * index), volume=200.0) for index in range(3)
    ]
    history: list[KlineBar] = []
    for day in range(1, 9):
        base = latest - timedelta(days=day)
        history.append(_bar(1, base.replace(hour=10, minute=15), volume=100.0))
        history.append(_bar(1, base.replace(hour=10, minute=0), volume=1000.0))
    raw = sorted([*analysis, *history], key=lambda item: item.ts_open, reverse=True)

    frame = build_analysis_frame(
        raw,
        3,
        "TEST",
        "15m",
        now_ms=int((latest + timedelta(hours=1)).timestamp() * 1000),
        data_metadata={
            "source": "fixture",
            "timezone_basis": "UTC",
            "volume_kind": "real",
            "volume_quality": "direct",
        },
    )

    assert frame is not None
    measured = compute_vpa_measurements(frame)
    newest = measured["recent_bars"][0]
    assert newest["seasonality_relative_volume"] == 2.0
    assert measured["seasonality_context"]["sample_key"] == "10:15"
    assert measured["seasonality_context"]["fallback_level"] == "all_days_slot"
    assert measured["seasonality_context"]["history_is_causal"] is True
    assert frame.data_metadata["seasonality_history_end_ts_open"] < frame.bars[-1].ts_open


def test_daily_timeframe_does_not_claim_intraday_seasonality() -> None:
    latest = datetime(2026, 7, 20, tzinfo=UTC)
    raw = [
        _bar(index + 1, latest - timedelta(days=index), volume=100.0 + index) for index in range(20)
    ]
    frame = build_analysis_frame(
        raw,
        3,
        "TEST",
        "1d",
        now_ms=int((latest + timedelta(days=1)).timestamp() * 1000),
    )

    assert frame is not None
    measured = compute_vpa_measurements(frame)
    assert measured["seasonality_context"]["seasonality_available"] is False
    assert measured["seasonality_context"]["reason"] == "intraday_seasonality_not_applicable"


def test_volume_zero_state_is_distinct_from_legacy_unknown_zero() -> None:
    latest = datetime(2026, 7, 20, 10, 15, tzinfo=UTC)
    bars = (
        _bar(1, latest, volume=0.0, volume_state="zero_observed"),
        _bar(2, latest - timedelta(minutes=15), volume=100.0, volume_state="observed"),
        _bar(3, latest - timedelta(minutes=30), volume=100.0, volume_state="observed"),
        _bar(4, latest - timedelta(minutes=45), volume=100.0, volume_state="observed"),
    )
    frame = KlineFrame(
        symbol="TEST",
        timeframe="15m",
        bars=bars,
        indicators=IndicatorBundle(ema20=(100.0,) * 4, atr14=(2.0,) * 4),
        snapshot_ts_local_ms=1,
        data_metadata={"volume_kind": "real", "volume_quality": "direct"},
    )
    observed_zero = compute_vpa_measurements(
        frame,
        baseline_lookback=3,
        minimum_baseline_samples=3,
    )["recent_bars"][0]
    legacy_frame = KlineFrame(
        symbol=frame.symbol,
        timeframe=frame.timeframe,
        bars=(_bar(1, latest, volume=0.0), *bars[1:]),
        indicators=frame.indicators,
        snapshot_ts_local_ms=1,
        data_metadata=frame.data_metadata,
    )
    legacy_zero = compute_vpa_measurements(
        legacy_frame,
        baseline_lookback=3,
        minimum_baseline_samples=3,
    )["recent_bars"][0]

    assert observed_zero["volume_state"] == "zero_observed"
    assert observed_zero["volume_available"] is True
    assert observed_zero["relative_volume"] == 0.0
    assert legacy_zero["volume_state"] == "legacy_unknown"
    assert legacy_zero["volume_available"] is False
    assert legacy_zero["relative_volume"] is None


def test_vpa_reuses_canonical_geometry_values() -> None:
    latest = datetime(2026, 7, 20, 10, 15, tzinfo=UTC)
    bars = (
        _bar(1, latest, volume=120.0, open_=100.0, close=101.0),
        _bar(2, latest - timedelta(minutes=15), volume=100.0, open_=99.0, close=100.0),
        _bar(3, latest - timedelta(minutes=30), volume=90.0, open_=100.0, close=99.0),
    )
    frame = KlineFrame(
        symbol="TEST",
        timeframe="15m",
        bars=bars,
        indicators=IndicatorBundle(ema20=(100.0,) * 3, atr14=(2.0,) * 3),
        snapshot_ts_local_ms=1,
        data_metadata={"volume_kind": "real", "volume_quality": "direct"},
    )

    geometry = compute_kline_geometry_features(frame)[0]
    measured = compute_vpa_measurements(frame)["recent_bars"][0]

    assert measured["spread_atr"] == geometry.range_atr_ratio
    assert measured["close_position"] == geometry.close_position
    assert measured["body_atr"] == geometry.signed_body_atr
    assert measured["directional_progress_atr"] == geometry.close_progress_atr
