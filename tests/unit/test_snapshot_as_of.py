from pa_agent.data.base import KlineBar
from pa_agent.data.snapshot import build_analysis_frame


def _bar(ts, *, closed=True):
    return KlineBar(seq=1, ts_open=ts, open=10, high=12, low=9, close=11, volume=5, closed=closed)


def test_expired_unconfirmed_payload_is_not_relabelled_closed():
    bars = [_bar(1_700_000_060_000, closed=False), _bar(1_700_000_000_000)]
    frame = build_analysis_frame(bars, 1, "TEST", "1m", now_ms=1_700_000_180_000)
    assert frame.bars[0].ts_open == 1_700_000_000_000
    assert bars[0].closed is False


def test_provider_closed_flag_does_not_override_frozen_as_of():
    bars = [_bar(1_700_000_060_000), _bar(1_700_000_000_000)]
    frame = build_analysis_frame(bars, 1, "TEST", "1m", now_ms=1_700_000_090_000)
    assert frame.bars[0].ts_open == 1_700_000_000_000


def test_close_exactly_at_as_of_is_eligible():
    frame = build_analysis_frame([_bar(1_700_000_000_000)], 1, "TEST", "1m", now_ms=1_700_000_060_000)
    assert frame.bars[0].ts_open == 1_700_000_000_000
    assert frame.data_metadata["analysis_as_of_ms"] == 1_700_000_060_000
