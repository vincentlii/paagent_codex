"""Core data types and DataSource abstract base class."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pa_agent.data.contracts import ResolvedInstrumentIdentity

# ── KlineBar ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class KlineBar:
    """A single OHLCV bar with sequence number and closed flag."""
    seq: int           # 1 = newest closed bar, N = oldest; 0 = forming bar (not counted)
    ts_open: float     # Unix timestamp in milliseconds (UTC) of bar open
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float = 0.0   # turnover amount (成交额); 0 when unavailable
    pct_chg: float | None = None  # daily change % from API when available
    closed: bool = True   # False for the currently-forming bar


def normalize_kline_bar(bar: KlineBar) -> KlineBar:
    """Normalize timestamp units without changing provider OHLC facts."""
    from pa_agent.data.datetime_ts import ts_open_to_ms

    ts_ms = ts_open_to_ms(bar.ts_open)
    if ts_ms == bar.ts_open:
        return bar
    return KlineBar(
        seq=bar.seq,
        ts_open=ts_ms,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        amount=getattr(bar, "amount", 0.0),
        pct_chg=getattr(bar, "pct_chg", None),
        closed=bar.closed,
    )


# ── IndicatorBundle ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class IndicatorBundle:
    """Per-bar indicator values aligned to a KlineFrame's bars list."""
    ema20: tuple[float, ...]   # len == len(bars); nan for warm-up period
    atr14: tuple[float, ...]   # len == len(bars); nan for warm-up period


# ── KlineFrame ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class KlineFrame:
    """Immutable snapshot of N bars plus computed indicators.

    bars[0] is the newest closed bar (seq=1, closed=True).
    bars[-1] is the oldest bar (seq=N, closed=True).
    snapshot_ts_local_ms is the local machine time when the snapshot was taken.
    """
    symbol: str
    timeframe: str
    bars: tuple[KlineBar, ...]
    indicators: IndicatorBundle
    snapshot_ts_local_ms: int   # milliseconds since epoch, local time
    # Source facts are optional because historical records and tests predate VPA.
    # Values must come from the data provider, never be inferred by the LLM.
    data_metadata: dict[str, Any] = field(default_factory=dict)


# ── DataSource ABC ────────────────────────────────────────────────────────────

class DataSourceError(Exception):
    """Base class for data source errors."""


class DataSourceTransientError(DataSourceError):
    """Transient (retryable) error from a data source."""


class DataSource(ABC):
    """Abstract interface for K-line data providers.

    Implementations: TradingViewSource (active), MT5Source (stub).
    """

    @abstractmethod
    def connect(self) -> None:
        """Establish connection / authenticate."""

    @abstractmethod
    def disconnect(self) -> None:
        """Tear down connection cleanly."""

    @abstractmethod
    def list_symbols(self) -> list[str]:
        """Return available symbol names."""

    @abstractmethod
    def supported_timeframes(self) -> list[str]:
        """Return supported timeframe strings, e.g. ['1m','5m','1h','1d']."""

    @abstractmethod
    def subscribe(self, symbol: str, timeframe: str) -> None:
        """Subscribe to live updates for *symbol* at *timeframe*."""

    @abstractmethod
    def unsubscribe(self) -> None:
        """Cancel the current subscription."""

    @abstractmethod
    def latest_snapshot(self, n: int) -> list[KlineBar]:
        """Return the *n* most recent bars (index 0 = newest, including forming bar).

        Raises DataSourceTransientError on recoverable network issues.
        """

    def fetch_snapshot_for(
        self,
        symbol: str,
        timeframe: str,
        n: int,
    ) -> list[KlineBar]:
        """Fetch an isolated symbol/timeframe snapshot without changing subscription.

        Active chart subscriptions must remain stable while background context is
        fetched.  Providers that can safely do this override the method; the
        default keeps legacy adapters compatible and degrades the optional HTF
        feature instead of mutating their live subscription.
        """
        raise DataSourceTransientError(
            f"{type(self).__name__} does not support isolated timeframe snapshots"
        )

    def current_identity(
        self,
        requested_symbol: str | None = None,
    ) -> ResolvedInstrumentIdentity | None:
        """Return the identity attached to the latest successful snapshot.

        Legacy adapters expose a conservative identity from their active symbol.
        Providers with symbol/venue discovery must override this method.
        """
        symbol = str(
            requested_symbol
            or getattr(self, "_symbol", "")
            or ""
        ).strip()
        if not symbol:
            return None
        provider = type(self).__name__.removesuffix("Source").lower().lstrip("_")
        return ResolvedInstrumentIdentity(
            provider=provider,
            requested_symbol=symbol,
            resolved_symbol=symbol,
        )

    def fetch_snapshot_for_identity(
        self,
        identity: ResolvedInstrumentIdentity,
        timeframe: str,
        n: int,
    ) -> list[KlineBar]:
        """Fetch another timeframe for an already resolved instrument.

        The default is suitable for providers whose symbol uniquely identifies
        the instrument. Discovery-based providers must pin their resolved venue
        and symbol instead of probing again.
        """
        current = self.current_identity(identity.requested_symbol)
        if current is not None and current.provider != identity.provider:
            raise DataSourceTransientError(
                "resolved identity provider does not match active data source"
            )
        return self.fetch_snapshot_for(identity.resolved_symbol, timeframe, n)
