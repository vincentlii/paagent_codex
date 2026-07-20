from __future__ import annotations

from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.util.price_tick import (
    format_breakout_price_hint,
    infer_display_increment_from_frame,
)


def _frame() -> KlineFrame:
    bars = (
        KlineBar(1, 2000, 1.23, 1.24, 1.22, 1.235, 1),
        KlineBar(2, 1000, 1.22, 1.23, 1.21, 1.22, 1),
    )
    return KlineFrame(
        symbol="TEST",
        timeframe="1m",
        bars=bars,
        indicators=IndicatorBundle(ema20=(1.22, 1.21), atr14=(0.01, 0.01)),
        snapshot_ts_local_ms=3000,
    )


def test_display_increment_reflects_snapshot_decimals() -> None:
    assert infer_display_increment_from_frame(_frame()) == 0.001


def test_hint_does_not_claim_exchange_tick_or_reprice_order() -> None:
    text = format_breakout_price_hint(_frame())

    assert "不等同于交易场所的真实 tick size" in text
    assert "严格大于" in text and "严格低于" in text
    assert "不会替你改写订单" in text


def test_empty_frame_has_no_precision_hint() -> None:
    assert infer_display_increment_from_frame(None) is None
    assert format_breakout_price_hint(None) == ""
