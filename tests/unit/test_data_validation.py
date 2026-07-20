from __future__ import annotations

from dataclasses import replace

import pytest

from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.data.contracts import ResolvedInstrumentIdentity
from pa_agent.data.validation import (
    assess_analysis_frame,
    validate_analysis_frame,
    validate_snapshot_identities,
)


def _frame(count: int = 20) -> KlineFrame:
    bars = tuple(
        KlineBar(
            seq=index + 1,
            ts_open=1_700_000_000_000 - index * 60_000,
            open=100 + index,
            high=102 + index,
            low=99 + index,
            close=101 + index,
            volume=1000 + index,
            closed=True,
        )
        for index in range(count)
    )
    return KlineFrame(
        symbol="TEST",
        timeframe="1m",
        bars=bars,
        indicators=IndicatorBundle(
            ema20=tuple(100.0 for _ in bars),
            atr14=tuple(2.0 for _ in bars),
        ),
        snapshot_ts_local_ms=1_700_000_060_000,
    )


def test_valid_frame_passes_without_mutation() -> None:
    frame = _frame()
    before = frame

    result = validate_analysis_frame(frame)

    assert result.ok is True
    assert result.failed_check is None
    assert frame == before


@pytest.mark.parametrize(
    ("mutate", "failed_check"),
    [
        (lambda frame: replace(frame, bars=()), "bars_empty"),
        (lambda frame: _frame(19), "bar_count_insufficient"),
        (
            lambda frame: replace(
                frame,
                bars=(replace(frame.bars[0], seq=2), *frame.bars[1:]),
            ),
            "bar_sequence_invalid",
        ),
        (
            lambda frame: replace(
                frame,
                bars=(
                    frame.bars[0],
                    replace(frame.bars[1], ts_open=frame.bars[0].ts_open),
                    *frame.bars[2:],
                ),
            ),
            "bar_timestamp_order_invalid",
        ),
        (
            lambda frame: replace(
                frame,
                bars=(replace(frame.bars[0], closed=False), *frame.bars[1:]),
            ),
            "forming_bar_in_frame",
        ),
        (
            lambda frame: replace(
                frame,
                bars=(replace(frame.bars[0], high=98), *frame.bars[1:]),
            ),
            "bar_ohlc_invalid",
        ),
        (
            lambda frame: replace(
                frame,
                bars=(replace(frame.bars[0], volume=-1), *frame.bars[1:]),
            ),
            "bar_volume_negative",
        ),
        (
            lambda frame: replace(
                frame,
                indicators=IndicatorBundle(ema20=(100.0,), atr14=(2.0,)),
            ),
            "indicator_length_mismatch",
        ),
        (
            lambda frame: replace(
                frame,
                indicators=IndicatorBundle(
                    ema20=(float("nan"), *frame.indicators.ema20[1:]),
                    atr14=frame.indicators.atr14,
                ),
            ),
            "latest_indicators_invalid",
        ),
    ],
)
def test_invalid_frame_is_rejected_by_exact_check(mutate, failed_check: str) -> None:
    result = validate_analysis_frame(mutate(_frame()))

    assert result.ok is False
    assert result.failed_check == failed_check


def test_duplicate_timestamp_has_specific_quality_issue() -> None:
    frame = _frame()
    duplicate = replace(
        frame,
        bars=(
            frame.bars[0],
            replace(frame.bars[1], ts_open=frame.bars[0].ts_open),
            *frame.bars[2:],
        ),
    )

    report = assess_analysis_frame(duplicate, now_ms=frame.snapshot_ts_local_ms)

    assert report.ok is False
    assert report.failed_check == "bar_timestamp_duplicate"


def test_future_bar_is_hard_failure() -> None:
    frame = _frame()
    future = replace(
        frame,
        bars=(
            replace(frame.bars[0], ts_open=frame.snapshot_ts_local_ms + 120_000),
            *frame.bars[1:],
        ),
    )

    report = assess_analysis_frame(future, now_ms=frame.snapshot_ts_local_ms)

    assert report.ok is False
    assert any(issue.code == "bar_timestamp_in_future" for issue in report.hard_errors)


def test_staleness_is_hard_only_when_market_is_known_open_or_continuous() -> None:
    frame = _frame()
    stale_now = frame.snapshot_ts_local_ms + 10 * 60_000

    unknown = assess_analysis_frame(frame, now_ms=stale_now)
    continuous = assess_analysis_frame(
        replace(frame, data_metadata={"continuous_market": True}),
        now_ms=stale_now,
    )

    assert unknown.ok is True
    assert any(issue.code == "snapshot_stale" for issue in unknown.warnings)
    assert continuous.ok is False
    assert any(issue.code == "snapshot_stale" for issue in continuous.hard_errors)


def test_cross_timeframe_identity_drift_is_rejected() -> None:
    current_identity = ResolvedInstrumentIdentity(
        provider="tradingview",
        requested_symbol="XAUUSD",
        resolved_symbol="XAUUSD",
        venue="PEPPERSTONE",
    )
    other_identity = ResolvedInstrumentIdentity(
        provider="tradingview",
        requested_symbol="XAUUSD",
        resolved_symbol="XAUUSD",
        venue="OANDA",
    )
    current = replace(_frame(), data_metadata=current_identity.to_metadata())
    higher = replace(
        _frame(),
        timeframe="1h",
        data_metadata=other_identity.to_metadata(),
    )

    report = validate_snapshot_identities(
        current,
        higher,
        expected_identity=current_identity,
        now_ms=current.snapshot_ts_local_ms,
    )

    assert report.ok is False
    assert any(
        issue.code == "cross_timeframe_source_identity_drift"
        for issue in report.hard_errors
    )
