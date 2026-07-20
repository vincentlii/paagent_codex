"""Construct :class:`DataSource` implementations by kind id."""
from __future__ import annotations

from typing import Any, Literal

from pa_agent.data.base import DataSource
from pa_agent.data.market_defaults import (
    A_SHARE_DEFAULT_SYMBOL,
    GOLD_MT5_SYMBOL,
    GOLD_TV_SYMBOL,
)

DataSourceKind = Literal[
    "mt5",
    "tradingview",
    "akshare",
    "eastmoney",
    "tushare",
    "yfinance",
]

# UI-visible sources only — ``eastmoney`` is config/programmatic, not listed here.
DATA_SOURCE_CHOICES: tuple[tuple[DataSourceKind, str], ...] = (
    ("tradingview", "TradingView"),
    ("mt5", "MT5"),
)

_HIDDEN_KINDS: frozenset[DataSourceKind] = frozenset(
    {"akshare", "eastmoney", "tushare", "yfinance"}
)

_DEFAULT_SYMBOLS: dict[DataSourceKind, str] = {
    "mt5": GOLD_MT5_SYMBOL,
    "tradingview": GOLD_TV_SYMBOL,
    "akshare": A_SHARE_DEFAULT_SYMBOL,
    "eastmoney": A_SHARE_DEFAULT_SYMBOL,
    "tushare": A_SHARE_DEFAULT_SYMBOL,
    "yfinance": "GC=F",
}


def default_tradingview_exchange() -> str:
    """Empty string = UI «（自动）» — probe all TV preset venues."""
    return ""


def normalize_data_source_kind(kind: str | None) -> DataSourceKind:
    """Return a supported data-source kind, defaulting to MT5."""
    supported = {k for k, _ in DATA_SOURCE_CHOICES} | _HIDDEN_KINDS
    if kind in supported:
        return kind  # type: ignore[return-value]
    return "tradingview"


def data_source_label(kind: str | None) -> str:
    """Human-readable label for *kind*."""
    normalized = normalize_data_source_kind(kind)
    for key, label in DATA_SOURCE_CHOICES:
        if key == normalized:
            return label
    if normalized == "eastmoney":
        return "东方财富"
    if normalized == "tushare":
        return "Tushare(A股)"
    if normalized == "akshare":
        return "AkShare"
    if normalized == "yfinance":
        return "YFinance"
    return "MT5"


def volume_metadata_for_source(kind: str | None, source: object | None = None) -> dict[str, Any]:
    """Return only provider-known volume provenance for VPA measurement.

    TradingView volume semantics vary by resolved venue and instrument, so it
    deliberately remains ``unknown``.  MT5 exposes tick volume unless a future
    adapter explicitly records real exchange volume.
    """
    normalized = normalize_data_source_kind(kind)
    metadata = {
        "source": normalized,
        "venue": "",
        "volume_kind": "unknown",
        "volume_quality": "unknown",
        "timezone_basis": "UTC",
    }
    if normalized == "mt5":
        metadata.update(volume_kind="tick", volume_quality="proxy")
    elif normalized == "tradingview":
        # Auto-probe may resolve a venue different from the user request.  Keep
        # both concepts separate: provenance needs the resolved venue, while
        # ``exchange`` remains the requested subscription setting.
        metadata["venue"] = str(
            getattr(source, "resolved_exchange", "")
            or getattr(source, "exchange", "")
            or ""
        ).upper()
    return metadata


def default_symbol_for_kind(kind: str | None) -> str:
    return _DEFAULT_SYMBOLS[normalize_data_source_kind(kind)]


def create_data_source(kind: str | None) -> DataSource:
    """Instantiate a fresh data source for *kind* (not connected)."""
    normalized = normalize_data_source_kind(kind)
    if normalized == "tradingview":
        from pa_agent.data.tradingview import TradingViewSource

        return TradingViewSource()
    if normalized == "eastmoney":
        from pa_agent.data.eastmoney_source import EastMoneySource

        return EastMoneySource()
    if normalized == "tushare":
        from pa_agent.config.paths import SETTINGS_JSON_PATH
        from pa_agent.config.settings import load_settings
        from pa_agent.data.tushare_source import TushareSource

        return TushareSource(settings=load_settings(SETTINGS_JSON_PATH))
    if normalized == "akshare":
        from pa_agent.data.akshare_source import AkShareSource

        return AkShareSource()
    if normalized == "yfinance":
        from pa_agent.data.yfinance_source import YFinanceSource

        return YFinanceSource()
    from pa_agent.data.mt5 import MT5Source

    return MT5Source()
