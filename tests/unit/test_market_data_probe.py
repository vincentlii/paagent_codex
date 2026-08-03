from __future__ import annotations

from pa_agent.config.settings import MonitoringSettings, Settings
from pa_agent.data.base import KlineBar
from pa_agent.data.contracts import ResolvedInstrumentIdentity
from pa_agent.data.instrument import InstrumentSpec
from pa_agent.headless import probe_market_data


class _Source:
    def __init__(self, provider: str, *, fail: bool = False) -> None:
        self.provider = provider
        self.fail = fail
        self.disconnected = False
        self.symbol = ""

    def connect(self) -> None:
        if self.fail:
            raise OSError("venue unavailable")

    def disconnect(self) -> None:
        self.disconnected = True

    def subscribe(self, symbol: str, timeframe: str) -> None:
        self.symbol = symbol

    def latest_snapshot(self, n: int):
        return [
            KlineBar(1, 2000, 2000, 2010, 1990, 2005, 12, 24060, closed=True),
            KlineBar(2, 1000, 1995, 2005, 1990, 2000, 10, 20000, closed=True),
        ][:n]

    def supported_timeframes(self):
        return ["1h", "4h"]

    def fetch_snapshot_for_identity(self, identity, timeframe: str, n: int):
        assert identity.resolved_symbol == self.symbol
        assert timeframe == "4h"
        return self.latest_snapshot(n)

    def current_identity(self, requested_symbol=None):
        venue = self.provider.split("_")[0].upper()
        return ResolvedInstrumentIdentity(
            provider=self.provider,
            requested_symbol=requested_symbol or self.symbol,
            resolved_symbol=self.symbol,
            venue=venue,
            market="derivatives",
            instrument_type="linear_perpetual",
            timezone_basis="UTC",
            volume_kind="real",
            volume_quality="exchange",
        )

    def current_instrument_spec(self):
        identity = self.current_identity()
        return InstrumentSpec(
            provider=self.provider,
            venue=identity.venue,
            symbol=self.symbol,
            instrument_type=identity.instrument_type,
            tick_size=0.01,
            quantity_step=0.01,
            minimum_quantity=0.01,
            maximum_quantity=1000,
            minimum_notional=5,
        )


def test_market_data_probe_isolates_assets_and_reports_native_facts() -> None:
    settings = Settings()
    settings.paper_trading.enabled = True
    settings.monitoring = MonitoringSettings(
        enabled=True,
        assets=[
            {"source": "binance_usdm", "symbol": "XAUUSDT", "timeframe": "1h"},
            {"source": "okx_swap", "symbol": "XAU-USDT-SWAP", "timeframe": "1h"},
        ],
    )
    sources = {
        "binance_usdm": _Source("binance_usdm"),
        "okx_swap": _Source("okx_swap", fail=True),
    }

    results = probe_market_data(
        settings,
        source_factory=lambda asset: sources[asset.source],
        sample_bars=2,
    )

    assert [result.success for result in results] == [True, False]
    assert results[0].closed_bars == 2
    assert results[0].higher_timeframe == "4h"
    assert results[0].higher_closed_bars == 2
    assert results[0].identity["resolved_symbol"] == "XAUUSDT"
    assert results[0].instrument_spec["tick_size"] == 0.01
    assert "venue unavailable" in results[1].error
    assert all(source.disconnected for source in sources.values())
