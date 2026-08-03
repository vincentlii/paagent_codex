from __future__ import annotations

from dataclasses import dataclass

import pytest

from pa_agent.data.okx_swap import OkxSwapSource


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
            "/api/v5/public/instruments": _Response({
                "code": "0",
                "data": [{
                    "instId": "XAU-USDT-SWAP",
                    "instType": "SWAP",
                    "state": "live",
                    "ctType": "linear",
                    "ctVal": "0.01",
                    "ctMult": "1",
                    "settleCcy": "USDT",
                    "tickSz": "0.1",
                    "lotSz": "1",
                    "minSz": "1",
                    "maxLmtSz": "100000",
                }],
            }),
            "/api/v5/market/candles": _Response({
                "code": "0",
                "data": [
                    ["1700003600000", "2005", "2012", "2001", "2008", "450", "4.5", "9036", "0"],
                    ["1700000000000", "2000", "2010", "1995", "2005", "1250", "12.5", "25062.5", "1"],
                ],
            }),
        }

    def get(self, url, *, params=None, timeout):
        path = url.removeprefix("https://openapi.okx.com")
        self.calls.append((path, params, timeout))
        return self.routes[path]

    def close(self):
        pass


def test_okx_source_resolves_linear_contract_multiplier_and_closed_bars() -> None:
    http = _Http()
    source = OkxSwapSource(http=http, timeout_s=3.0)

    source.connect()
    source.subscribe("xau-usdt-swap", "1h")
    bars = source.latest_snapshot(2)

    identity = source.current_identity()
    spec = source.current_instrument_spec()
    assert identity is not None
    assert identity.key == ("okx_swap", "okx", "xau-usdt-swap", "linear_swap")
    assert identity.volume_kind == "real"
    assert spec.tick_size == pytest.approx(0.1)
    assert spec.quantity_step == pytest.approx(1.0)
    assert spec.contract_multiplier == pytest.approx(0.01)
    assert [bar.ts_open for bar in bars] == [1_700_003_600_000, 1_700_000_000_000]
    assert bars[0].closed is False
    assert bars[1].closed is True
    assert bars[1].volume == pytest.approx(12.5)
    assert bars[1].amount == pytest.approx(25062.5)
    assert http.calls[-1][1] == {
        "instId": "XAU-USDT-SWAP",
        "bar": "1H",
        "limit": "2",
    }


def test_okx_source_paginates_history_without_duplicate_timestamps() -> None:
    class _PagedHttp(_Http):
        def get(self, url, *, params=None, timeout):
            path = url.removeprefix("https://openapi.okx.com")
            if path != "/api/v5/market/candles":
                return super().get(url, params=params, timeout=timeout)
            self.calls.append((path, params, timeout))
            if "after" not in params:
                rows = [
                    [str(2_000_000 - index), "1", "2", "0.5", "1.5", "1", "1", "1.5", "1"]
                    for index in range(300)
                ]
            else:
                rows = [["1999700", "1", "2", "0.5", "1.5", "1", "1", "1.5", "1"]]
            return _Response({"code": "0", "data": rows})

    http = _PagedHttp()
    source = OkxSwapSource(http=http)
    source.connect()
    source.subscribe("XAU-USDT-SWAP", "1h")

    bars = source.latest_snapshot(301)

    assert len(bars) == 301
    candle_calls = [call for call in http.calls if call[0].endswith("/candles")]
    assert candle_calls[1][1]["after"] == "1999701"
    assert candle_calls[1][1]["limit"] == "1"
