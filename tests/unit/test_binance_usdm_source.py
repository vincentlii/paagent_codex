from __future__ import annotations

from dataclasses import dataclass

import pytest

from pa_agent.data.base import DataSourceTransientError
from pa_agent.data.binance_usdm import BinanceUsdmSource


@dataclass
class _Response:
    payload: object
    status_code: int = 200
    headers: dict[str, str] | None = None

    def json(self):
        return self.payload


class _Http:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None, float]] = []
        self.routes = {
            "/fapi/v1/exchangeInfo": _Response(
                {
                    "symbols": [
                        {
                            "symbol": "XAUUSDT",
                            "status": "TRADING",
                            "contractType": "TRADIFI_PERPETUAL",
                            "baseAsset": "XAU",
                            "quoteAsset": "USDT",
                            "marginAsset": "USDT",
                            "filters": [
                                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                                {
                                    "filterType": "LOT_SIZE",
                                    "minQty": "0.001",
                                    "maxQty": "1000",
                                    "stepSize": "0.001",
                                },
                                {"filterType": "MIN_NOTIONAL", "notional": "5"},
                            ],
                        }
                    ]
                }
            ),
            "/fapi/v1/time": _Response({"serverTime": 1_700_003_700_000}),
            "/fapi/v1/klines": _Response(
                [
                    [
                        1_700_000_000_000,
                        "2000",
                        "2010",
                        "1995",
                        "2005",
                        "12.5",
                        1_700_003_599_999,
                        "25062.5",
                        100,
                        "7",
                        "14035",
                        "0",
                    ],
                    [
                        1_700_003_600_000,
                        "2005",
                        "2012",
                        "2001",
                        "2008",
                        "4.5",
                        1_700_007_199_999,
                        "9036",
                        20,
                        "2",
                        "4016",
                        "0",
                    ],
                ]
            ),
        }

    def get(self, url, *, params=None, timeout):
        path = url.removeprefix("https://fapi.binance.com")
        self.calls.append((path, params, timeout))
        return self.routes[path]

    def close(self):
        pass


def test_binance_source_resolves_tradifi_perpetual_and_closed_bars() -> None:
    http = _Http()
    source = BinanceUsdmSource(http=http, timeout_s=3.0)

    source.connect()
    source.subscribe("xauusdt", "1h")
    bars = source.latest_snapshot(2)

    identity = source.current_identity()
    spec = source.current_instrument_spec()
    assert identity is not None
    assert identity.key == (
        "binance_usdm",
        "binance",
        "xauusdt",
        "tradifi_perpetual",
    )
    assert identity.volume_kind == "real"
    assert spec.tick_size == pytest.approx(0.01)
    assert spec.quantity_step == pytest.approx(0.001)
    assert spec.minimum_notional == pytest.approx(5.0)
    assert [bar.ts_open for bar in bars] == [1_700_003_600_000, 1_700_000_000_000]
    assert bars[0].closed is False
    assert bars[1].closed is True
    assert bars[1].volume == pytest.approx(12.5)
    assert bars[1].amount == pytest.approx(25062.5)
    assert http.calls[-2][1] == {"symbol": "XAUUSDT", "interval": "1h", "limit": 2}


def test_binance_source_rejects_unknown_or_non_trading_contract() -> None:
    source = BinanceUsdmSource(http=_Http())
    source.connect()

    with pytest.raises(DataSourceTransientError, match="not available"):
        source.subscribe("MISSINGUSDT", "1h")


def test_binance_source_http_failure_is_retryable() -> None:
    http = _Http()
    http.routes["/fapi/v1/time"] = _Response(
        {"code": -1003, "msg": "Too many requests"},
        status_code=429,
        headers={"Retry-After": "2"},
    )
    source = BinanceUsdmSource(http=http)
    source.connect()
    source.subscribe("XAUUSDT", "1h")

    with pytest.raises(DataSourceTransientError, match="429"):
        source.latest_snapshot(2)
