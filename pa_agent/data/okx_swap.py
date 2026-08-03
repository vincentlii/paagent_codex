"""Public OKX linear USDT perpetual market data."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pa_agent.data.base import DataSource, DataSourceTransientError, KlineBar
from pa_agent.data.contracts import ResolvedInstrumentIdentity
from pa_agent.data.instrument import InstrumentSpec

_BASE_URL = "https://openapi.okx.com"
_TIMEFRAMES = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1H", "2h": "2H", "4h": "4H", "6h": "6Hutc", "12h": "12Hutc",
    "1d": "1Dutc", "2d": "2Dutc", "3d": "3Dutc", "1w": "1Wutc",
    "1M": "1Mutc",
}


class OkxSwapSource(DataSource):
    """Read public OKX candles and exchange-declared contract constraints."""

    def __init__(
        self,
        *,
        http: Any | None = None,
        base_url: str = _BASE_URL,
        timeout_s: float = 10.0,
    ) -> None:
        if http is None:
            from curl_cffi import requests

            http = requests.Session()
        self._http = http
        self._base_url = str(base_url).rstrip("/")
        self._timeout_s = max(0.1, float(timeout_s))
        self._connected = False
        self._symbol = ""
        self._timeframe = ""
        self._specs: dict[str, InstrumentSpec] = {}

    def connect(self) -> None:
        data = self._data(
            "/api/v5/public/instruments",
            params={"instType": "SWAP"},
        )
        specs = {}
        for raw in data:
            spec = _instrument_spec(raw)
            if spec is not None:
                specs[spec.symbol] = spec
        if not specs:
            raise DataSourceTransientError("OKX returned no live linear USDT swaps")
        self._specs = specs
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        close = getattr(self._http, "close", None)
        if callable(close):
            close()

    def list_symbols(self) -> list[str]:
        return sorted(self._specs)

    def supported_timeframes(self) -> list[str]:
        return list(_TIMEFRAMES)

    def subscribe(self, symbol: str, timeframe: str) -> None:
        self._require_connected()
        normalized_symbol = str(symbol or "").strip().upper()
        normalized_timeframe = str(timeframe or "").strip()
        if normalized_timeframe not in _TIMEFRAMES:
            raise DataSourceTransientError(f"OKX does not support timeframe {timeframe}")
        if normalized_symbol not in self._specs:
            raise DataSourceTransientError(
                f"OKX swap {normalized_symbol} is not available for trading"
            )
        self._symbol = normalized_symbol
        self._timeframe = normalized_timeframe

    def unsubscribe(self) -> None:
        self._symbol = ""
        self._timeframe = ""

    def latest_snapshot(self, n: int) -> list[KlineBar]:
        self._require_subscription()
        return self._fetch(self._symbol, self._timeframe, n)

    def fetch_snapshot_for(self, symbol: str, timeframe: str, n: int) -> list[KlineBar]:
        self._require_connected()
        normalized = str(symbol or "").strip().upper()
        if normalized not in self._specs:
            raise DataSourceTransientError(f"OKX swap {normalized} is not available for trading")
        if timeframe not in _TIMEFRAMES:
            raise DataSourceTransientError(f"OKX does not support timeframe {timeframe}")
        return self._fetch(normalized, timeframe, n)

    def fetch_snapshot_for_identity(
        self,
        identity: ResolvedInstrumentIdentity,
        timeframe: str,
        n: int,
    ) -> list[KlineBar]:
        current = self.current_identity(identity.requested_symbol)
        if current is None or not current.same_instrument(identity):
            raise DataSourceTransientError("resolved identity does not match OKX swap")
        return self.fetch_snapshot_for(identity.resolved_symbol, timeframe, n)

    def current_identity(
        self,
        requested_symbol: str | None = None,
    ) -> ResolvedInstrumentIdentity | None:
        symbol = self._symbol or str(requested_symbol or "").strip().upper()
        spec = self._specs.get(symbol)
        if spec is None:
            return None
        return ResolvedInstrumentIdentity(
            provider="okx_swap",
            requested_symbol=str(requested_symbol or symbol),
            resolved_symbol=symbol,
            venue="OKX",
            market="derivatives",
            instrument_type=spec.instrument_type,
            timezone_basis="UTC",
            volume_kind="real",
            volume_quality="exchange",
        )

    def current_instrument_spec(self) -> InstrumentSpec:
        self._require_subscription()
        return self._specs[self._symbol]

    def _fetch(self, symbol: str, timeframe: str, n: int) -> list[KlineBar]:
        target = max(1, int(n))
        rows: dict[int, list[Any]] = {}
        after: int | None = None
        while len(rows) < target:
            limit = min(300, target - len(rows))
            params = {"instId": symbol, "bar": _TIMEFRAMES[timeframe], "limit": str(limit)}
            if after is not None:
                params["after"] = str(after)
            page = self._data("/api/v5/market/candles", params=params)
            before = len(rows)
            for raw in page:
                if not isinstance(raw, list) or len(raw) < 9:
                    raise DataSourceTransientError("OKX returned a malformed candle")
                try:
                    rows[int(raw[0])] = raw
                except (TypeError, ValueError) as exc:
                    raise DataSourceTransientError("OKX returned an invalid candle timestamp") from exc
            if len(rows) == before or len(page) < limit:
                break
            after = min(rows)
        parsed: list[KlineBar] = []
        for timestamp in sorted(rows, reverse=True)[:target]:
            raw = rows[timestamp]
            try:
                parsed.append(KlineBar(
                    seq=len(parsed) + 1,
                    ts_open=float(timestamp),
                    open=float(raw[1]), high=float(raw[2]), low=float(raw[3]),
                    close=float(raw[4]), volume=float(raw[6]), amount=float(raw[7]),
                    closed=str(raw[8]) == "1", volume_state="observed",
                ))
            except (TypeError, ValueError) as exc:
                raise DataSourceTransientError("OKX returned non-numeric candle data") from exc
        return parsed

    def _data(self, path: str, *, params: dict[str, Any] | None = None) -> list[Any]:
        payload = self._get(path, params=params)
        if not isinstance(payload, Mapping) or str(payload.get("code")) != "0":
            detail = payload.get("msg") if isinstance(payload, Mapping) else "invalid response"
            raise DataSourceTransientError(f"OKX API error: {detail}")
        data = payload.get("data")
        if not isinstance(data, list):
            raise DataSourceTransientError("OKX response has no data array")
        return data

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self._http.get(
                self._base_url + path, params=params, timeout=self._timeout_s,
            )
        except Exception as exc:
            raise DataSourceTransientError(f"OKX request failed: {exc}") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        try:
            payload = response.json()
        except Exception as exc:
            raise DataSourceTransientError("OKX returned invalid JSON") from exc
        if status != 200:
            raise DataSourceTransientError(f"OKX HTTP {status}")
        return payload

    def _require_connected(self) -> None:
        if not self._connected:
            raise DataSourceTransientError("OKX source is not connected")

    def _require_subscription(self) -> None:
        self._require_connected()
        if not self._symbol or not self._timeframe:
            raise DataSourceTransientError("OKX source is not subscribed")


def _instrument_spec(raw: object) -> InstrumentSpec | None:
    if not isinstance(raw, Mapping):
        return None
    if str(raw.get("state")) != "live" or str(raw.get("ctType")) != "linear":
        return None
    if str(raw.get("settleCcy")).upper() != "USDT":
        return None
    try:
        ct_multiplier = float(raw["ctVal"]) * float(raw.get("ctMult") or 1.0)
        return InstrumentSpec(
            provider="okx_swap", venue="OKX", symbol=str(raw["instId"]),
            instrument_type="linear_swap", tick_size=float(raw["tickSz"]),
            quantity_step=float(raw["lotSz"]), minimum_quantity=float(raw["minSz"]),
            maximum_quantity=float(raw["maxLmtSz"]), minimum_notional=0.0,
            contract_multiplier=ct_multiplier,
        )
    except (KeyError, TypeError, ValueError):
        return None
