"""TradingView data source using tvdatafeed."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from pa_agent.data.base import (
    DataSource,
    DataSourceTransientError,
    KlineBar,
    normalize_kline_bar,
)
from pa_agent.data.contracts import ResolvedInstrumentIdentity
from pa_agent.data.datetime_ts import datetime_to_ts_ms, naive_local_to_utc
from pa_agent.data.market_defaults import (
    TV_CRYPTO_EXCHANGES,
    is_likely_crypto_symbol,
    is_tv_exchange_auto,
    resolve_tv_fetch_pair,
    tv_auto_probe_plan,
)
from pa_agent.data.tradingview_errors import format_tradingview_fetch_error
from pa_agent.data.tv_symbol_lookup import TvSymbolNotFoundError
from pa_agent.data.volume_context import normalize_provider_volume

logger = logging.getLogger(__name__)

# One attempt per fetch cycle. Each tvDatafeed get_hist() that times out
# blocks for up to _TV_WS_TIMEOUT_S, so retrying here multiplies the worst-case
# wait the user sees on a slow/blocked connection. The RefreshLoop already does
# its own exponential backoff + retry across ticks, so a per-call retry only
# stacks latency without adding resilience.
_TV_FETCH_RETRIES = 1
_TV_FETCH_RETRY_SLEEP_S = 0.5

# Override tvDatafeed's hardcoded 15s WebSocket timeout. Once the socket leak
# (see _close_tv_socket) is fixed, healthy fetches complete in 1-3s, so this
# only bounds the worst case on a stalled connection.
_TV_WS_TIMEOUT_S = 10.0

# Name-mangled attribute tvDatafeed uses internally for its socket timeout.
_TV_WS_TIMEOUT_ATTR = "_TvDatafeed__ws_timeout"
_TV_SNAPSHOT_CACHE_TTL_S = 2.0

# Map our timeframe strings to tvDatafeed Interval enum names
_TF_MAP: dict[str, str] = {
    "1m":  "in_1_minute",
    "3m":  "in_3_minute",
    "5m":  "in_5_minute",
    "15m": "in_15_minute",
    "30m": "in_30_minute",
    "45m": "in_45_minute",
    "1h":  "in_1_hour",
    "2h":  "in_2_hour",
    "3h":  "in_3_hour",
    "4h":  "in_4_hour",
    "1d":  "in_daily",
    "1w":  "in_weekly",
    "1M":  "in_monthly",
}

# Forex / spot gold and China A-share (tvDatafeed exchange ids)
TV_EXCHANGE_PRESETS: tuple[str, ...] = (
    "OANDA",
    "PEPPERSTONE",
    "FOREXCOM",
    "FX",
    "TVC",
    "CAPITALCOM",
    "SSE",
    "SZSE",
    "HKEX",
    "SP",
    "NYSE",
    "NASDAQ",
    "CBOT",
    "CME_MINI",
    "",
)


class TradingViewSource(DataSource):
    """Live K-line data from TradingView via tvdatafeed."""

    def __init__(self, username: str = "", password: str = "") -> None:
        self._username = username
        self._password = password
        self._tv = None          # tvDatafeed instance
        self._connected: bool = False
        self._symbol: str = ""
        self._timeframe: str = ""
        self._exchange: str = ""
        self._resolved_exchange: str = ""
        self._resolved_symbol: str = ""
        self._snapshot_cache: dict[
            tuple[str, str, str], tuple[float, tuple[KlineBar, ...]]
        ] = {}
        # Mutex: tvDatafeed is NOT thread-safe — its get_hist() creates a
        # WebSocket and stores it on self.ws; concurrent calls clobber the
        # same socket and cause C++ segfaults.
        self._snapshot_lock = threading.Lock()
        # Callback for status updates during auto-probe: fn(symbol, exchange, label)
        self.on_probe_status = None

    @property
    def exchange(self) -> str:
        return self._exchange

    @property
    def resolved_exchange(self) -> str:
        """Venue that supplied the most recent successful snapshot."""
        return self._resolved_exchange

    @property
    def resolved_symbol(self) -> str:
        """Provider symbol that supplied the most recent successful snapshot."""
        return self._resolved_symbol

    def current_identity(
        self,
        requested_symbol: str | None = None,
    ) -> ResolvedInstrumentIdentity | None:
        requested = str(requested_symbol or self._symbol or "").strip()
        resolved = str(self._resolved_symbol or requested).strip()
        if not requested or not resolved or not self._resolved_exchange:
            return None
        return ResolvedInstrumentIdentity(
            provider="tradingview",
            requested_symbol=requested,
            resolved_symbol=resolved,
            venue=self._resolved_exchange,
            market=(
                "crypto"
                if self._resolved_exchange in TV_CRYPTO_EXCHANGES
                or is_likely_crypto_symbol(resolved)
                else ""
            ),
            timezone_basis="UTC",
            volume_kind="unknown",
            volume_quality="unknown",
        )

    def set_exchange(self, exchange: str) -> None:
        """Set TradingView exchange id (e.g. ``BINANCE``); empty = auto-detect."""
        self._exchange = (exchange or "").strip().upper()
        self._resolved_exchange = ""
        self._resolved_symbol = ""
        self._snapshot_cache.clear()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def connect(self) -> None:
        try:
            from tvDatafeed import TvDatafeed  # type: ignore[import]
            if self._username and self._password:
                self._tv = TvDatafeed(self._username, self._password)
            else:
                self._tv = TvDatafeed()  # anonymous
            # Bound tvDatafeed's hardcoded 15s WebSocket timeout so a stalled
            # connection fails faster instead of freezing the UI.
            try:
                setattr(self._tv, _TV_WS_TIMEOUT_ATTR, _TV_WS_TIMEOUT_S)
            except Exception:  # noqa: BLE001
                logger.debug("Could not override tvDatafeed ws timeout", exc_info=True)
            self._connected = True
            logger.info("TradingViewSource connected (anonymous=%s)", not self._username)
        except Exception as exc:
            self._connected = False
            raise DataSourceTransientError(
                f"TradingView 连接失败：{exc}（若未安装请执行 "
                "pip install git+https://github.com/rongardF/tvdatafeed.git）"
            ) from exc

    def disconnect(self) -> None:
        self._close_tv_socket()
        self._tv = None
        self._connected = False
        self._snapshot_cache.clear()
        logger.info("TradingViewSource disconnected")

    def _close_tv_socket(self) -> None:
        """Close the live tvDatafeed WebSocket, if any.

        tvDatafeed 2.x opens a brand-new socket on *every* ``get_hist()`` call
        and never closes the previous one — a leak that piles up half-open
        connections and trips TradingView's rate limiting. Closing the socket
        after each fetch fixes the leak, and closing it mid-flight is also the
        only way to abort a ``recv()`` that is blocked waiting on a stalled
        connection (e.g. when the user switches symbol/timeframe).

        Safe to call from another thread: ``socket.close()`` will raise inside
        the blocked ``recv()``, which tvDatafeed catches and turns into an
        empty result.
        """
        tv = self._tv
        if tv is None:
            return
        ws = getattr(tv, "ws", None)
        if ws is None:
            return
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            logger.debug("tvDatafeed socket close failed", exc_info=True)
        finally:
            try:
                tv.ws = None
            except Exception:  # noqa: BLE001
                pass

    # ── Discovery ─────────────────────────────────────────────────────────────

    def list_symbols(self) -> list[str]:
        return [
            "XAUUSD",
            "GOLD",
            "600519",
            "000001",
            "1810",
            "700",
            "小米集团",
            "腾讯控股",
            "EURUSD",
            "GBPUSD",
        ]

    def supported_timeframes(self) -> list[str]:
        return list(_TF_MAP.keys())

    # ── Subscription ──────────────────────────────────────────────────────────

    def subscribe(self, symbol: str, timeframe: str) -> None:
        if timeframe not in _TF_MAP:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}. Use one of {list(_TF_MAP)}")
        self._timeframe = timeframe
        self._symbol = symbol.strip()
        self._resolved_exchange = ""
        self._resolved_symbol = ""
        self._snapshot_cache.clear()
        # Abort any in-flight get_hist() blocked on a stalled connection so the
        # new symbol/timeframe takes effect immediately instead of waiting out
        # the previous request's timeout. Closing the socket raises inside the
        # worker thread's recv(); the next fetch transparently reconnects.
        self._close_tv_socket()
        logger.info(
            "TradingViewSource subscribed: %s %s exchange=%s",
            self._symbol,
            timeframe,
            self._exchange or "(auto)",
        )

    def unsubscribe(self) -> None:
        self._symbol = ""
        self._timeframe = ""
        self._resolved_exchange = ""
        self._resolved_symbol = ""
        self._snapshot_cache.clear()
        logger.info("TradingViewSource unsubscribed")

    # ── Data fetch ────────────────────────────────────────────────────────────

    def _fetch_hist_with_retry(
        self,
        *,
        symbol: str,
        exchange: str,
        interval: object,
        n_bars: int,
    ):
        """Call tvDatafeed get_hist with retries (timeouts / empty are common)."""
        logger.debug(
            "TradingView get_hist: symbol=%s, exchange=%s, interval=%s, n_bars=%d",
            symbol, exchange, interval, n_bars,
        )
        last_exc: BaseException | None = None
        for attempt in range(1, _TV_FETCH_RETRIES + 1):
            try:
                df = self._tv.get_hist(
                    symbol=symbol,
                    exchange=exchange,
                    interval=interval,
                    n_bars=n_bars,
                )
                if df is not None and not df.empty:
                    return df
                logger.warning(
                    "TradingView get_hist attempt %s/%s returned empty data: symbol=%s, exchange=%s, interval=%s",
                    attempt, _TV_FETCH_RETRIES, symbol, exchange, interval,
                )
                last_exc = None
            except Exception as exc:
                last_exc = exc
                logger.debug(
                    "TradingView get_hist attempt %s/%s failed: %s",
                    attempt,
                    _TV_FETCH_RETRIES,
                    exc,
                )
            finally:
                # tvDatafeed leaks the WebSocket it opens on every get_hist()
                # call. Close it here so half-open sockets don't accumulate and
                # trip TradingView rate limiting; the next call reconnects.
                self._close_tv_socket()
            if attempt < _TV_FETCH_RETRIES:
                time.sleep(_TV_FETCH_RETRY_SLEEP_S)
        if last_exc is not None:
            raise last_exc
        return None

    def _fetch_tv_auto_probe(
        self,
        *,
        symbol: str,
        plan: list[tuple[str, str]],
        interval: object,
        n_bars: int,
    ) -> tuple[object, str, str]:
        """Try each (exchange, symbol) in *plan* until one returns bars."""
        if not plan:
            raise DataSourceTransientError(
                f"TradingView 无法识别品种「{symbol}」；"
                "请用 A 股 6 位代码、港股代码（如 1810）、"
                "指数代码（如 SPX、NDX、VIX）、"
                "外汇/黄金代码或已支持的股票名称"
            )
        last_exc: BaseException | None = None
        tried: list[str] = []
        for exchange, code in plan:
            label = f"{exchange}:{code}"
            tried.append(label)
            # Notify GUI about current probe attempt
            if self.on_probe_status is not None:
                try:
                    self.on_probe_status(symbol, exchange, label)
                except Exception:  # noqa: BLE001
                    pass
            try:
                df = self._fetch_hist_with_retry(
                    symbol=code,
                    exchange=exchange,
                    interval=interval,
                    n_bars=n_bars,
                )
            except Exception as exc:
                last_exc = exc
                logger.info("TradingView auto probe %s failed: %s", label, exc)
                continue
            if df is not None and not df.empty:
                logger.info(
                    "TradingView auto probe picked %s (tried %s)",
                    label,
                    ", ".join(tried),
                )
                return df, exchange, code
        if last_exc is not None:
            raise last_exc
        raise DataSourceTransientError(
            f"TradingView 自动探测失败（{symbol}）：已尝试 {', '.join(tried)} 均无 K 线"
        )

    def latest_snapshot(self, n: int) -> list[KlineBar]:
        """Return *n* bars newest-first; bars[0] is the forming (unclosed) bar.

        Thread-safety: serialized via ``_snapshot_lock`` because
        ``TvDatafeed.get_hist()`` is NOT thread-safe — it writes to
        ``self.ws`` on each call, and concurrent access clobbers the
        WebSocket, causing C++ segfaults.
        """
        with self._snapshot_lock:
            return self._latest_snapshot_inner(n)

    def fetch_snapshot_for(self, symbol: str, timeframe: str, n: int) -> list[KlineBar]:
        """Fetch background bars without changing the visible subscription."""
        if timeframe not in _TF_MAP:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}. Use one of {list(_TF_MAP)}")
        with self._snapshot_lock:
            return self._snapshot_for_inner(
                symbol=symbol.strip(),
                timeframe=timeframe,
                n=n,
            )

    def fetch_snapshot_for_identity(
        self,
        identity: ResolvedInstrumentIdentity,
        timeframe: str,
        n: int,
    ) -> list[KlineBar]:
        """Fetch an HTF snapshot without re-running venue auto discovery."""
        if identity.provider != "tradingview":
            raise DataSourceTransientError(
                "TradingView identity provider must be 'tradingview'"
            )
        if not identity.venue or not identity.resolved_symbol:
            raise DataSourceTransientError(
                "TradingView identity must include resolved venue and symbol"
            )
        if timeframe not in _TF_MAP:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}. Use one of {list(_TF_MAP)}")
        with self._snapshot_lock:
            return self._snapshot_for_inner(
                symbol=identity.requested_symbol,
                timeframe=timeframe,
                n=n,
                pinned_identity=identity,
            )

    def _latest_snapshot_inner(self, n: int) -> list[KlineBar]:
        """Actual snapshot logic — caller holds ``_snapshot_lock``."""
        return self._snapshot_for_inner(
            symbol=self._symbol,
            timeframe=self._timeframe,
            n=n,
        )

    def _snapshot_for_inner(
        self,
        *,
        symbol: str,
        timeframe: str,
        n: int,
        pinned_identity: ResolvedInstrumentIdentity | None = None,
    ) -> list[KlineBar]:
        """Fetch a specified snapshot while caller holds ``_snapshot_lock``."""
        if self._tv is None:
            raise DataSourceTransientError("TradingView 未连接，请先选择数据来源 TradingView")
        if not symbol or not timeframe:
            raise DataSourceTransientError("TradingView 未订阅品种/周期")

        user_symbol = symbol
        req_exchange = self._exchange
        subscription_identity_is_resolved = bool(
            pinned_identity is None
            and is_tv_exchange_auto(req_exchange)
            and self._resolved_exchange
            and self._resolved_symbol
            and user_symbol.strip() == self._symbol.strip()
        )
        if pinned_identity is not None:
            exchange = pinned_identity.venue
            fetch_symbol = pinned_identity.resolved_symbol
            auto_probe = False
            probe_plan: list[tuple[str, str]] = []
        elif subscription_identity_is_resolved:
            exchange = self._resolved_exchange
            fetch_symbol = self._resolved_symbol
            auto_probe = False
            probe_plan = []
        else:
            exchange = req_exchange or ""
            fetch_symbol = user_symbol
            auto_probe = is_tv_exchange_auto(req_exchange)
            probe_plan = tv_auto_probe_plan(user_symbol) if auto_probe else []
            if not auto_probe:
                try:
                    exchange, fetch_symbol = resolve_tv_fetch_pair(
                        req_exchange, user_symbol
                    )
                except TvSymbolNotFoundError as exc:
                    raise DataSourceTransientError(str(exc)) from exc
        cache_key = (
            str(exchange or "").upper(),
            str(fetch_symbol or "").strip(),
            timeframe,
        )
        if not auto_probe:
            cached = self._snapshot_cache.get(cache_key)
            if cached is not None:
                cached_at, cached_bars = cached
                if (
                    time.monotonic() - cached_at <= _TV_SNAPSHOT_CACHE_TTL_S
                    and len(cached_bars) >= n
                ):
                    return list(cached_bars[:n])
        try:
            from tvDatafeed import Interval  # type: ignore[import]
            interval = getattr(Interval, _TF_MAP[timeframe])
            if pinned_identity is not None:
                df = self._fetch_hist_with_retry(
                    symbol=fetch_symbol,
                    exchange=exchange,
                    interval=interval,
                    n_bars=n + 1,
                )
            elif auto_probe and probe_plan:
                df, exchange, fetch_symbol = self._fetch_tv_auto_probe(
                    symbol=user_symbol,
                    plan=probe_plan,
                    interval=interval,
                    n_bars=n + 1,
                )
            else:
                df = self._fetch_hist_with_retry(
                    symbol=fetch_symbol,
                    exchange=exchange,
                    interval=interval,
                    n_bars=n + 1,
                )
        except DataSourceTransientError:
            raise
        except Exception as exc:
            msg = format_tradingview_fetch_error(
                user_symbol, exchange or req_exchange or "自动", cause=exc,
            )
            logger.warning("TradingView fetch failed: %s", exc)
            raise DataSourceTransientError(msg) from exc

        if df is None or df.empty:
            msg = format_tradingview_fetch_error(
                user_symbol, exchange or req_exchange or "自动", empty_data=True,
            )
            logger.debug(
                "TradingView empty data for %s exchange=%s",
                user_symbol,
                exchange or req_exchange or "(auto)",
            )
            raise DataSourceTransientError(msg)

        self._resolved_exchange = str(exchange or req_exchange or "").upper()
        self._resolved_symbol = str(fetch_symbol or user_symbol).strip()

        df = df.iloc[::-1].reset_index()

        bars: list[KlineBar] = []
        for i, row in enumerate(df.itertuples(index=False)):
            ts_ms = _row_ts_ms(row)
            volume, volume_state = normalize_provider_volume(
                getattr(row, "volume", None),
                # TradingView can expose zero for unavailable volume; the
                # feed does not distinguish that from a true zero print.
                zero_is_observed=False,
            )
            bar = KlineBar(
                seq=i + 1,
                ts_open=ts_ms,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=volume,
                volume_state=volume_state,
                closed=True,
            )
            if i == 0:
                # Check the newest bar against its absolute UTC close instant. Use a
                # probe marked open so the helper cannot trust the provider flag.
                from pa_agent.data.bar_close_wait import is_bar_still_forming

                probe_bar = KlineBar(
                    seq=bar.seq,
                    ts_open=bar.ts_open,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    volume_state=bar.volume_state,
                    closed=False,
                )
                still_forming = is_bar_still_forming(
                    probe_bar,
                    timeframe,
                    now_ms=int(time.time() * 1000),
                    symbol=user_symbol,
                )
                bar = KlineBar(
                    seq=bar.seq,
                    ts_open=bar.ts_open,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    volume_state=bar.volume_state,
                    closed=not still_forming,
                )
            bars.append(normalize_kline_bar(bar))
            if len(bars) >= n:
                break

        resolved_cache_key = (
            self._resolved_exchange,
            self._resolved_symbol,
            timeframe,
        )
        self._snapshot_cache[resolved_cache_key] = (
            time.monotonic(),
            tuple(bars),
        )
        return bars


def _row_ts_ms(row) -> int:
    """Extract bar open time in milliseconds from a tvDatafeed DataFrame row."""
    value = getattr(row, "datetime", None)
    if isinstance(value, datetime) and value.tzinfo is None:
        # tvDatafeed builds its index with datetime.fromtimestamp(), so naive
        # values are host-local wall time rather than UTC wall time.
        value = naive_local_to_utc(value)
    return datetime_to_ts_ms(value)
