"""Persisted market frames remain replayable without a desktop application."""

from pa_agent.records.replay import frame_from_record_klines


def test_frame_from_record_klines() -> None:
    bars = [
        {
            "seq": index + 1,
            "ts_open": 1_700_000_000_000 - index * 3_600_000,
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "volume": 10.0,
            "closed": True,
        }
        for index in range(20)
    ]
    frame = frame_from_record_klines(
        bars, symbol="BTCUSDT", timeframe="1h", snapshot_ts_local_ms=1_700_003_600_000
    )

    assert frame.symbol == "BTCUSDT" and frame.timeframe == "1h"
    assert frame.bars[0].seq == 1
    assert frame.bars[0].ts_open == bars[0]["ts_open"]
    assert len(frame.indicators.ema20) == len(frame.bars) == 20
