from __future__ import annotations

from dataclasses import replace

import pytest

from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.data.validation import validate_analysis_frame


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

