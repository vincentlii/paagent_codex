"""Refresh policy guards for sources with per-request live sockets."""
from __future__ import annotations

from pa_agent.data.refresh_policy import effective_refresh_interval_ms


def test_tradingview_refresh_interval_is_rate_limited() -> None:
    assert effective_refresh_interval_ms("tradingview", 1000, timeframe="1m") == 10_000
    assert effective_refresh_interval_ms("tradingview", 5000, timeframe="15m") == 30_000
    assert effective_refresh_interval_ms("tradingview", 5000, timeframe="1h") == 60_000
    assert effective_refresh_interval_ms("tradingview", 600_000, timeframe="1d") == 600_000


def test_http_refresh_interval_remains_rate_limited() -> None:
    assert effective_refresh_interval_ms("akshare", 1000, timeframe="1h") == 2500
