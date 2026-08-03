"""Public Bitget USDT perpetual market data."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pa_agent.data.bar_close_wait import timeframe_to_seconds
from pa_agent.data.base import DataSource, DataSourceTransientError, KlineBar
from pa_agent.data.contracts import ResolvedInstrumentIdentity
from pa_agent.data.instrument import InstrumentSpec

_BASE_URL = "https://api.bitget.com"
_CATEGORY = "USDT-FUTURES"
_TIMEFRAMES = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1H", "4h": "4H", "6h": "6H", "12h": "12H", "1d": "1D",
}


class BitgetUsdtFuturesSource(DataSource):
    """Read Bitget V3 candles and exchange-declared contract constraints."""

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
            "/api/v3/market/instruments",
            params={"category": _CATEGORY},
        )
        specs = {}
        for raw in data:
            spec = _instrument_spec(raw)
            if spec is not None:
                specs[spec.symbol] = spec
        if not specs:
            raise DataSourceTransientError("Bitget returned no online USDT perpetuals")
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
            raise DataSourceTransientError(f"Bitget does not support timeframe {timeframe}")
        if normalized_symbol not in self._specs:
            raise DataSourceTransientError(
                f"Bitget contract {normalized_symbol} is not available for trading"
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
            raise DataSourceTransientError(
                f"Bitget contract {normalized} is not available for trading"
            )
        if timeframe not in _TIMEFRAMES:
            raise DataSourceTransientError(f"Bitget does not support timeframe {timeframe}")
        return self._fetch(normalized, timeframe, n)

    def fetch_snapshot_for_identity(
        self,
        identity: ResolvedInstrumentIdentity,
        timeframe: str,
        n: int,
    ) -> list[KlineBar]:
        current = self.current_identity(identity.requested_symbol)
        if current is None or not current.same_instrument(identity):
            raise DataSourceTransientError("resolved identity does not match Bitget contract")
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
            provider="bitget_usdt_futures",
            requested_symbol=str(requested_symbol or symbol),
            resolved_symbol=symbol,
            venue="BITGET",
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
        end_time: int | None = None
        while len(rows) < target:
            limit = min(100, target - len(rows))
            params = {
                "category": _CATEGORY, "symbol": symbol,
                "interval": _TIMEFRAMES[timeframe], "type": "market",
                "limit": str(limit),
            }
            path = "/api/v3/market/candles"
            if end_time is not None:
                path = "/api/v3/market/history-candles"
                params["endTime"] = str(end_time)
            page = self._data(path, params=params)
            before = len(rows)
            for raw in page:
                if not isinstance(raw, list) or len(raw) < 7:
                    raise DataSourceTransientError("Bitget returned a malformed candle")
                try:
                    rows[int(raw[0])] = raw
                except (TypeError, ValueError) as exc:
                    raise DataSourceTransientError(
                        "Bitget returned an invalid candle timestamp"
                    ) from exc
            if len(rows) == before or len(page) < limit:
                break
            end_time = min(rows) - 1
        clock = self._data("/api/v2/public/time")
        try:
            clock_item = clock[0] if isinstance(clock, list) else clock
            server_time = int(clock_item["serverTime"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DataSourceTransientError("Bitget server time is invalid") from exc
        duration = timeframe_to_seconds(timeframe)
        if duration is None:
            raise DataSourceTransientError(f"Bitget timeframe duration is unknown: {timeframe}")
        parsed: list[KlineBar] = []
        for timestamp in sorted(rows, reverse=True)[:target]:
            raw = rows[timestamp]
            try:
                parsed.append(KlineBar(
                    seq=len(parsed) + 1,
                    ts_open=float(timestamp),
                    open=float(raw[1]), high=float(raw[2]), low=float(raw[3]),
                    close=float(raw[4]), volume=float(raw[5]), amount=float(raw[6]),
                    closed=timestamp + duration * 1000 <= server_time,
                    volume_state="observed",
                ))
            except (TypeError, ValueError) as exc:
                raise DataSourceTransientError("Bitget returned non-numeric candle data") from exc
        return parsed

    def _data(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        payload = self._get(path, params=params)
        if not isinstance(payload, Mapping) or str(payload.get("code")) != "00000":
            detail = payload.get("msg") if isinstance(payload, Mapping) else "invalid response"
            raise DataSourceTransientError(f"Bitget API error: {detail}")
        data = payload.get("data")
        if not isinstance(data, (list, Mapping)):
            raise DataSourceTransientError("Bitget response has no data")
        return data

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self._http.get(
                self._base_url + path, params=params, timeout=self._timeout_s,
            )
        except Exception as exc:
            raise DataSourceTransientError(f"Bitget request failed: {exc}") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        try:
            payload = response.json()
        except Exception as exc:
            raise DataSourceTransientError("Bitget returned invalid JSON") from exc
        if status != 200:
            raise DataSourceTransientError(f"Bitget HTTP {status}")
        return payload

    def _require_connected(self) -> None:
        if not self._connected:
            raise DataSourceTransientError("Bitget source is not connected")

    def _require_subscription(self) -> None:
        self._require_connected()
        if not self._symbol or not self._timeframe:
            raise DataSourceTransientError("Bitget source is not subscribed")


def _instrument_spec(raw: object) -> InstrumentSpec | None:
    if not isinstance(raw, Mapping):
        return None
    if str(raw.get("status")) != "online" or str(raw.get("type")) != "perpetual":
        return None
    try:
        return InstrumentSpec(
            provider="bitget_usdt_futures", venue="BITGET", symbol=str(raw["symbol"]),
            instrument_type="usdt_perpetual", tick_size=float(raw["priceMultiplier"]),
            quantity_step=float(raw["quantityMultiplier"]),
            minimum_quantity=float(raw["minOrderQty"]),
            maximum_quantity=float(raw["maxOrderQty"]),
            minimum_notional=float(raw.get("minOrderAmount") or 0.0),
        )
    except (KeyError, TypeError, ValueError):
        return None

