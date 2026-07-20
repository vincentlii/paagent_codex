from __future__ import annotations

import sys
import types
from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from pa_agent.data.base import DataSourceTransientError
from pa_agent.data.contracts import ResolvedInstrumentIdentity
from pa_agent.data.snapshot_bundle import compact_snapshot_context
from pa_agent.data.tradingview import TradingViewSource
from tests.integration.conftest import make_frame


def _tv_frame() -> pd.DataFrame:
    count = 80
    return pd.DataFrame(
        {
            "open": [100.0 + index for index in range(count)],
            "high": [102.0 + index for index in range(count)],
            "low": [99.0 + index for index in range(count)],
            "close": [101.0 + index for index in range(count)],
            "volume": [1_000.0 + index for index in range(count)],
        },
        index=pd.date_range(
            "2025-01-01",
            periods=count,
            freq="15min",
            name="datetime",
        ),
    )


def _install_interval_module(monkeypatch) -> None:
    module = types.ModuleType("tvDatafeed")
    module.Interval = SimpleNamespace(
        in_15_minute=object(),
        in_1_hour=object(),
    )
    monkeypatch.setitem(sys.modules, "tvDatafeed", module)


def test_tradingview_htf_fetch_reuses_resolved_venue_and_symbol(monkeypatch) -> None:
    _install_interval_module(monkeypatch)
    source = TradingViewSource()
    calls: list[tuple[str, str]] = []
    frame = _tv_frame()

    def get_hist(*, symbol, exchange, interval, n_bars):
        del interval, n_bars
        calls.append((exchange, symbol))
        return frame.copy() if exchange == "PEPPERSTONE" else pd.DataFrame()

    source._tv = SimpleNamespace(get_hist=get_hist, ws=None)
    source._connected = True
    source.subscribe("XAUUSD", "15m")

    source.latest_snapshot(20)
    identity = source.current_identity()

    assert identity is not None
    assert identity.venue == "PEPPERSTONE"
    assert identity.resolved_symbol == "XAUUSD"
    calls.clear()

    source.fetch_snapshot_for_identity(identity, "1h", 20)

    assert calls == [("PEPPERSTONE", "XAUUSD")]
    assert source.current_identity() == identity


def test_tradingview_rejects_identity_from_another_provider() -> None:
    source = TradingViewSource()
    identity = ResolvedInstrumentIdentity(
        provider="mt5",
        requested_symbol="XAUUSD",
        resolved_symbol="XAUUSD",
    )

    with pytest.raises(DataSourceTransientError, match="provider"):
        source.fetch_snapshot_for_identity(identity, "1h", 20)


def test_identity_round_trip_and_comparison_are_case_insensitive() -> None:
    identity = ResolvedInstrumentIdentity(
        provider="TradingView",
        requested_symbol="xauusd",
        resolved_symbol="XAUUSD",
        venue="pepperstone",
    )
    restored = ResolvedInstrumentIdentity.from_value(identity.to_metadata())

    assert restored == identity
    assert restored is not None
    assert restored.same_instrument(
        ResolvedInstrumentIdentity(
            provider="tradingview",
            requested_symbol="XAUUSD",
            resolved_symbol="xauusd",
            venue="PEPPERSTONE",
        )
    )


def test_compact_snapshot_context_keeps_identity_without_seasonality_payload() -> None:
    identity = ResolvedInstrumentIdentity(
        provider="tradingview",
        requested_symbol="XAUUSD",
        resolved_symbol="XAUUSD",
        venue="OANDA",
    )
    frame = replace(
        make_frame(),
        data_metadata={
            **identity.to_metadata(),
            "seasonality_history_bars": 50,
            "seasonality_samples": {"22:00": [1.0] * 50},
        },
    )

    context = compact_snapshot_context(frame)

    assert context["source_identity"] == identity.to_dict()
    assert context["seasonality_history_bars"] == 50
    assert "seasonality_samples" not in context
