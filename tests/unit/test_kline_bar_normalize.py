from __future__ import annotations

from pa_agent.data.base import KlineBar, normalize_kline_bar


def test_normalize_kline_bar_only_converts_timestamp_units() -> None:
    bar = KlineBar(1, 1_700_000_000, 10, 12, 9, 11, 100)

    normalized = normalize_kline_bar(bar)

    assert normalized.ts_open == 1_700_000_000_000
    assert (normalized.open, normalized.high, normalized.low, normalized.close) == (10, 12, 9, 11)
    assert normalized.volume == 100


def test_normalize_does_not_silently_repair_invalid_ohlc() -> None:
    bar = KlineBar(1, 1_700_000_000_000, 10, 8, 9, 11, 100)

    normalized = normalize_kline_bar(bar)

    assert normalized.high == 8
    assert normalized.low == 9
