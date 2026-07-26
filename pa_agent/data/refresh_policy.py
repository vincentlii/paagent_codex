"""Refresh interval, cache TTL, and zombie-loop timing for data sources."""
from __future__ import annotations

HTTP_POLL_SOURCES: frozenset[str] = frozenset({"eastmoney", "akshare", "tushare"})
LIVE_SOCKET_SOURCES: frozenset[str] = frozenset({"tradingview"})

HTTP_MIN_REFRESH_MS = 2500
LIVE_SOCKET_MIN_REFRESH_MS = 3000
TRADINGVIEW_MIN_REFRESH_BY_TIMEFRAME_MS: dict[str, int] = {
    "1m": 10_000,
    "3m": 15_000,
    "5m": 15_000,
    "15m": 30_000,
    "30m": 30_000,
    "45m": 30_000,
    "1h": 60_000,
    "2h": 60_000,
    "3h": 60_000,
    "4h": 60_000,
    "1d": 300_000,
    "1w": 300_000,
    "1M": 300_000,
}
HTTP_SNAPSHOT_CACHE_TTL_S = 8.0
HTTP_SNAPSHOT_CACHE_TTL_1D_S = 12.0
HTTP_ZOMBIE_JOIN_MS = 15_000
DEFAULT_ZOMBIE_JOIN_MS = 5000


def is_http_poll_source(kind: str) -> bool:
    return kind in HTTP_POLL_SOURCES


def effective_refresh_interval_ms(
    kind: str,
    user_ms: int,
    *,
    timeframe: str = "",
) -> int:
    """Clamp sources whose snapshots create a network request per refresh."""
    ms = max(500, int(user_ms or 1000))
    if kind in HTTP_POLL_SOURCES:
        ms = max(ms, HTTP_MIN_REFRESH_MS)
    if kind in LIVE_SOCKET_SOURCES:
        ms = max(
            ms,
            TRADINGVIEW_MIN_REFRESH_BY_TIMEFRAME_MS.get(
                timeframe,
                LIVE_SOCKET_MIN_REFRESH_MS,
            ),
        )
    if kind in HTTP_POLL_SOURCES and timeframe == "1d":
        ms = max(ms, 3000)
    return ms


def snapshot_cache_ttl_s(timeframe: str) -> float:
    if timeframe in ("1d", "1w", "1M"):
        return HTTP_SNAPSHOT_CACHE_TTL_1D_S
    if timeframe == "1m":
        return 4.0
    return HTTP_SNAPSHOT_CACHE_TTL_S


def zombie_join_timeout_ms(kind: str) -> int:
    if kind in HTTP_POLL_SOURCES:
        return HTTP_ZOMBIE_JOIN_MS
    return DEFAULT_ZOMBIE_JOIN_MS
