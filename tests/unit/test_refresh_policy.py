"""Refresh policy guards for sources with per-request live sockets."""
from __future__ import annotations

from pa_agent.data.refresh_policy import effective_refresh_interval_ms


def test_tradingview_refresh_interval_is_rate_limited() -> None:
    assert effective_refresh_interval_ms("tradingview", 1000, timeframe="15m") == 3000
    assert effective_refresh_interval_ms("tradingview", 5000, timeframe="15m") == 5000


def test_http_refresh_interval_remains_rate_limited() -> None:
    assert effective_refresh_interval_ms("akshare", 1000, timeframe="1h") == 2500
