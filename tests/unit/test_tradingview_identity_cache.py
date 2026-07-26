from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd

from pa_agent.data.tradingview import TradingViewSource


def _frame(ts_ms: int) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [datetime.fromtimestamp(ts_ms / 1000, tz=UTC)]
    )
    index.name = "datetime"
    return pd.DataFrame(
        {
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
            "volume": [10.0],
        },
        index=index,
    )


def test_auto_venue_is_pinned_for_the_subscription(monkeypatch) -> None:
    source = TradingViewSource()
    source._tv = SimpleNamespace()
    source.subscribe("XAUUSD", "15m")
    df = _frame(1_700_000_000_000)
    probes: list[str] = []
    direct: list[tuple[str, str]] = []

    monkeypatch.setattr(
        source,
        "_fetch_tv_auto_probe",
        lambda **_kwargs: (probes.append("probe") or df, "OANDA", "XAUUSD"),
    )
    monkeypatch.setattr(
        source,
        "_fetch_hist_with_retry",
        lambda **kwargs: (
            direct.append((kwargs["exchange"], kwargs["symbol"])) or df
        ),
    )
    monkeypatch.setattr(
        "tvDatafeed.Interval",
        SimpleNamespace(in_15_minute=object()),
    )

    source.latest_snapshot(1)
    source._snapshot_cache.clear()
    source.latest_snapshot(1)

    assert probes == ["probe"]
    assert direct == [("OANDA", "XAUUSD")]
    assert source.resolved_exchange == "OANDA"


def test_same_resolved_snapshot_is_served_from_short_cache(monkeypatch) -> None:
    source = TradingViewSource()
    source._tv = SimpleNamespace()
    source.subscribe("XAUUSD", "15m")
    source._resolved_exchange = "OANDA"
    source._resolved_symbol = "XAUUSD"
    df = _frame(1_700_000_000_000)
    calls: list[int] = []
    monkeypatch.setattr(
        source,
        "_fetch_hist_with_retry",
        lambda **_kwargs: (calls.append(1) or df),
    )
    monkeypatch.setattr(
        "tvDatafeed.Interval",
        SimpleNamespace(in_15_minute=object()),
    )

    first = source.latest_snapshot(1)
    second = source.latest_snapshot(1)

    assert first == second
    assert len(calls) == 1


def test_expired_tradingview_bar_is_never_reclassified_as_forming(
    monkeypatch,
) -> None:
    source = TradingViewSource()
    source._tv = SimpleNamespace()
    source.subscribe("XAUUSD", "15m")
    source._resolved_exchange = "OANDA"
    source._resolved_symbol = "XAUUSD"
    df = _frame(1_700_000_000_000)
    monkeypatch.setattr(source, "_fetch_hist_with_retry", lambda **_kwargs: df)
    monkeypatch.setattr(
        "tvDatafeed.Interval",
        SimpleNamespace(in_15_minute=object()),
    )
    monkeypatch.setattr("pa_agent.data.tradingview.time.time", lambda: 1_800_000_000.0)

    bars = source.latest_snapshot(1)

    assert bars[0].closed is True
