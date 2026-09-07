"""Public Binance USD-M Futures market-data adapter."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pa_agent.data.base import DataSource, DataSourceTransientError, KlineBar
from pa_agent.data.contracts import ResolvedInstrumentIdentity
from pa_agent.data.instrument import InstrumentSpec

_BASE_URL = "https://fapi.binance.com"
_TIMEFRAMES = (
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h",
    "12h", "1d", "3d", "1w", "1M",
)


class BinanceUsdmSource(DataSource):
    """Read closed and forming USD-M futures bars without API credentials."""

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
        payload = self._get("/fapi/v1/exchangeInfo")
        symbols = payload.get("symbols") if isinstance(payload, Mapping) else None
        if not isinstance(symbols, list):
            raise DataSourceTransientError("Binance exchangeInfo has no symbols")
        specs: dict[str, InstrumentSpec] = {}
        for raw in symbols:
            spec = _instrument_spec(raw)
            if spec is not None:
                specs[spec.symbol] = spec
        if not specs:
            raise DataSourceTransientError("Binance returned no trading USD-M contracts")
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
            raise DataSourceTransientError(
                f"Binance USD-M does not support timeframe {timeframe}"
            )
        if normalized_symbol not in self._specs:
            raise DataSourceTransientError(
                f"Binance USD-M contract {normalized_symbol} is not available for trading"
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
                f"Binance USD-M contract {normalized} is not available for trading"
            )
        if timeframe not in _TIMEFRAMES:
            raise DataSourceTransientError(
                f"Binance USD-M does not support timeframe {timeframe}"
            )
        return self._fetch(normalized, timeframe, n)

    def fetch_snapshot_for_identity(
        self,
        identity: ResolvedInstrumentIdentity,
        timeframe: str,
        n: int,
    ) -> list[KlineBar]:
        current = self.current_identity(identity.requested_symbol)
        if current is None or not current.same_instrument(identity):
            raise DataSourceTransientError(
                "resolved identity does not match Binance USD-M contract"
            )
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
            provider="binance_usdm",
            requested_symbol=str(requested_symbol or symbol),
            resolved_symbol=symbol,
            venue="BINANCE",
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
        limit = max(1, min(int(n), 1500))
        # Freeze the observation boundary before fetching mutable candle data.
        # A request crossing a close cannot certify its earlier partial payload.
        clock = self._get("/fapi/v1/time")
        try:
            server_time = int(clock["serverTime"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DataSourceTransientError("Binance server time is invalid") from exc
        payload = self._get(
            "/fapi/v1/klines",
            params={"symbol": symbol, "interval": timeframe, "limit": limit},
        )
        if not isinstance(payload, list):
            raise DataSourceTransientError("Binance kline response is not an array")
        chronological: list[KlineBar] = []
        for raw in payload:
            if not isinstance(raw, list) or len(raw) < 8:
                raise DataSourceTransientError("Binance returned a malformed kline")
            try:
                chronological.append(
                    KlineBar(
                        seq=0,
                        ts_open=float(raw[0]),
                        open=float(raw[1]),
                        high=float(raw[2]),
                        low=float(raw[3]),
                        close=float(raw[4]),
                        volume=float(raw[5]),
                        amount=float(raw[7]),
                        closed=int(raw[6]) < server_time,
                        volume_state="observed",
                    )
                )
            except (TypeError, ValueError) as exc:
                raise DataSourceTransientError("Binance returned non-numeric kline data") from exc
        newest_first = list(reversed(chronological))
        return [KlineBar(**{**bar.__dict__, "seq": index}) for index, bar in enumerate(newest_first, 1)]

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self._http.get(
                self._base_url + path,
                params=params,
                timeout=self._timeout_s,
            )
        except Exception as exc:
            raise DataSourceTransientError(f"Binance request failed: {exc}") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        try:
            payload = response.json()
        except Exception as exc:
            raise DataSourceTransientError("Binance returned invalid JSON") from exc
        if status != 200:
            detail = payload.get("msg") if isinstance(payload, Mapping) else "HTTP error"
            raise DataSourceTransientError(f"Binance HTTP {status}: {detail}")
        return payload

    def _require_connected(self) -> None:
        if not self._connected:
            raise DataSourceTransientError("Binance USD-M source is not connected")

    def _require_subscription(self) -> None:
        self._require_connected()
        if not self._symbol or not self._timeframe:
            raise DataSourceTransientError("Binance USD-M source is not subscribed")


def _instrument_spec(raw: object) -> InstrumentSpec | None:
    if not isinstance(raw, Mapping) or str(raw.get("status") or "") != "TRADING":
        return None
    symbol = str(raw.get("symbol") or "").strip().upper()
    if not symbol or str(raw.get("marginAsset") or "").upper() != "USDT":
        return None
    filters = {
        str(item.get("filterType") or ""): item
        for item in raw.get("filters", [])
        if isinstance(item, Mapping)
    }
    price = filters.get("PRICE_FILTER", {})
    lot = filters.get("LOT_SIZE", {})
    notional = filters.get("MIN_NOTIONAL", {})
    try:
        return InstrumentSpec(
            provider="binance_usdm",
            venue="BINANCE",
            symbol=symbol,
            instrument_type=str(raw.get("contractType") or "unknown").lower(),
            tick_size=float(price["tickSize"]),
            quantity_step=float(lot["stepSize"]),
            minimum_quantity=float(lot["minQty"]),
            maximum_quantity=float(lot["maxQty"]),
            minimum_notional=float(notional.get("notional") or 0.0),
        )
    except (KeyError, TypeError, ValueError):
        return None
