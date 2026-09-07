from __future__ import annotations

from dataclasses import dataclass

import pytest

from pa_agent.data.bitget_usdt_futures import BitgetUsdtFuturesSource


@dataclass
class _Response:
    payload: object
    status_code: int = 200

    def json(self):
        return self.payload


class _Http:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None, float]] = []
        self.routes = {
            "/api/v3/market/instruments": _Response({
                "code": "00000",
                "data": [{
                    "symbol": "XAUUSDT",
                    "type": "perpetual",
                    "status": "online",
                    "symbolType": "metal",
                    "priceMultiplier": "0.01",
                    "quantityMultiplier": "0.001",
                    "minOrderQty": "0.001",
                    "maxOrderQty": "1000",
                    "minOrderAmount": "5",
                }],
            }),
            "/api/v3/market/candles": _Response({
                "code": "00000",
                "data": [
                    ["1700003600000", "2005", "2012", "2001", "2008", "4.5", "9036"],
                    ["1700000000000", "2000", "2010", "1995", "2005", "12.5", "25062.5"],
                ],
            }),
            "/api/v2/public/time": _Response({
                "code": "00000",
                "data": {"serverTime": "1700003700000"},
            }),
        }

    def get(self, url, *, params=None, timeout):
        path = url.removeprefix("https://api.bitget.com")
        self.calls.append((path, params, timeout))
        return self.routes[path]

    def close(self):
        pass


def test_bitget_source_uses_server_clock_for_closed_bars() -> None:
    http = _Http()
    source = BitgetUsdtFuturesSource(http=http, timeout_s=3.0)

    source.connect()
    source.subscribe("xauusdt", "1h")
    bars = source.latest_snapshot(2)

    identity = source.current_identity()
    spec = source.current_instrument_spec()
    assert identity is not None
    assert identity.key == (
        "bitget_usdt_futures", "bitget", "xauusdt", "usdt_perpetual"
    )
    assert identity.volume_kind == "real"
    assert spec.tick_size == pytest.approx(0.01)
    assert spec.quantity_step == pytest.approx(0.001)
    assert spec.contract_multiplier == pytest.approx(1.0)
    assert [bar.ts_open for bar in bars] == [1_700_003_600_000, 1_700_000_000_000]
    assert bars[0].closed is False
    assert bars[1].closed is True
    assert bars[1].volume == pytest.approx(12.5)
    assert bars[1].amount == pytest.approx(25062.5)
    assert http.calls[-1][1] == {
        "category": "USDT-FUTURES",
        "symbol": "XAUUSDT",
        "interval": "1H",
        "type": "market",
        "limit": "2",
    }


def test_bitget_source_uses_history_endpoint_after_current_page() -> None:
    class _PagedHttp(_Http):
        def get(self, url, *, params=None, timeout):
            path = url.removeprefix("https://api.bitget.com")
            if path not in {
                "/api/v3/market/candles",
                "/api/v3/market/history-candles",
            }:
                return super().get(url, params=params, timeout=timeout)
            self.calls.append((path, params, timeout))
            if path.endswith("/candles") and "history" not in path:
                rows = [
                    [str(2_000_000 - index), "1", "2", "0.5", "1.5", "1", "1.5"]
                    for index in range(100)
                ]
            else:
                rows = [["1999900", "1", "2", "0.5", "1.5", "1", "1.5"]]
            return _Response({"code": "00000", "data": rows})

    http = _PagedHttp()
    source = BitgetUsdtFuturesSource(http=http)
    source.connect()
    source.subscribe("XAUUSDT", "1h")

    bars = source.latest_snapshot(101)

    assert len(bars) == 101
    history = next(call for call in http.calls if "history-candles" in call[0])
    assert history[1]["endTime"] == "1999900"
    assert history[1]["limit"] == "1"


def test_bitget_crossing_close_keeps_partial_candle_unconfirmed():
    class CrossingHttp(_Http):
        delivered = False

        def get(self, url, *, params=None, timeout):
            if url.endswith("/time"):
                return _Response({"code": "00000", "data": {
                    "serverTime": 1_700_007_200_001 if self.delivered else 1_700_007_199_999,
                }})
            result = super().get(url, params=params, timeout=timeout)
            if url.endswith("/candles"):
                self.delivered = True
            return result

    source = BitgetUsdtFuturesSource(http=CrossingHttp())
    source.connect()
    source.subscribe("XAUUSDT", "1h")
    assert source.latest_snapshot(2)[0].closed is False
