"""Construct :class:`DataSource` implementations by kind id."""
from __future__ import annotations

from typing import Any, Literal

from pa_agent.data.base import DataSource
from pa_agent.data.contracts import ResolvedInstrumentIdentity
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
    "binance_usdm",
    "okx_swap",
    "bitget_usdt_futures",
]

# UI-visible sources only — ``eastmoney`` is config/programmatic, not listed here.
DATA_SOURCE_CHOICES: tuple[tuple[DataSourceKind, str], ...] = (
    ("tradingview", "TradingView"),
    ("mt5", "MT5"),
    ("binance_usdm", "Binance USD-M"),
    ("okx_swap", "OKX Swap"),
    ("bitget_usdt_futures", "Bitget USDT Futures"),
)

NATIVE_PERPETUAL_KINDS: frozenset[DataSourceKind] = frozenset({
    "binance_usdm",
    "okx_swap",
    "bitget_usdt_futures",
})

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
    "binance_usdm": "XAUUSDT",
    "okx_swap": "XAU-USDT-SWAP",
    "bitget_usdt_futures": "XAUUSDT",
}


def default_tradingview_exchange() -> str:
    """Empty string means automatic TradingView venue probing."""
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
    if normalized == "binance_usdm":
        return "Binance USD-M"
    if normalized == "okx_swap":
        return "OKX Swap"
    if normalized == "bitget_usdt_futures":
        return "Bitget USDT Futures"
    return "MT5"


def resolved_identity_for_source(
    kind: str | None,
    source: object | None,
    *,
    symbol: str = "",
) -> ResolvedInstrumentIdentity | None:
    """Read the identity attached to the latest successful provider fetch."""
    normalized = normalize_data_source_kind(kind)
    current_identity = getattr(source, "current_identity", None)
    identity = None
    if callable(current_identity):
        try:
            identity = current_identity(symbol or None)
        except (TypeError, ValueError):
            identity = None
    if isinstance(identity, ResolvedInstrumentIdentity):
        if identity.provider == normalized:
            return identity
        return ResolvedInstrumentIdentity(
            provider=normalized,
            requested_symbol=identity.requested_symbol,
            resolved_symbol=identity.resolved_symbol,
            venue=identity.venue,
            market=identity.market,
            instrument_type=identity.instrument_type,
            timezone_basis=identity.timezone_basis,
            volume_kind=identity.volume_kind,
            volume_quality=identity.volume_quality,
        )

    requested = str(
        symbol
        or getattr(source, "_symbol", "")
        or ""
    ).strip()
    if not requested:
        return None
    venue = ""
    if normalized == "tradingview":
        venue = str(
            getattr(source, "resolved_exchange", "")
            or getattr(source, "exchange", "")
            or ""
        ).upper()
    return ResolvedInstrumentIdentity(
        provider=normalized,
        requested_symbol=requested,
        resolved_symbol=str(
            getattr(source, "resolved_symbol", "")
            or requested
        ),
        venue=venue,
        timezone_basis="UTC",
        volume_kind="tick" if normalized == "mt5" else "unknown",
        volume_quality="proxy" if normalized == "mt5" else "unknown",
    )


def volume_metadata_for_source(
    kind: str | None,
    source: object | None = None,
    *,
    identity: ResolvedInstrumentIdentity | None = None,
    symbol: str = "",
) -> dict[str, Any]:
    """Return only provider-known volume provenance for VPA measurement.

    TradingView volume semantics vary by resolved venue and instrument, so it
    deliberately remains ``unknown``.  MT5 exposes tick volume unless a future
    adapter explicitly records real exchange volume.
    """
    normalized = normalize_data_source_kind(kind)
    resolved = identity or resolved_identity_for_source(
        normalized,
        source,
        symbol=symbol,
    )
    metadata: dict[str, Any] = {
        "source": normalized,
        "venue": "",
        "volume_kind": "unknown",
        "volume_quality": "unknown",
        "timezone_basis": "UTC",
        "volume_provenance": {
            "provider": normalized,
            "field": "volume",
            "basis": "provider_ohlcv_field",
            "zero_semantics": "provider_declared",
            "comparison_scope": "same_provider_same_instrument",
        },
    }
    if normalized == "mt5":
        metadata.update(volume_kind="tick", volume_quality="proxy")
        metadata["volume_provenance"].update(
            field="tick_volume",
            basis="quote_change_count",
            zero_semantics="observed_zero",
        )
    elif normalized == "tradingview":
        # Auto-probe may resolve a venue different from the user request.  Keep
        # both concepts separate: provenance needs the resolved venue, while
        # ``exchange`` remains the requested subscription setting.
        metadata["venue"] = str(
            getattr(source, "resolved_exchange", "")
            or getattr(source, "exchange", "")
            or ""
        ).upper()
        metadata["volume_provenance"].update(
            zero_semantics="ambiguous_zero_or_unavailable",
        )
    elif normalized in {"eastmoney", "akshare", "tushare", "yfinance"}:
        metadata["volume_provenance"].update(
            field=(
                "vol"
                if normalized == "tushare"
                else "Volume" if normalized == "yfinance" else "volume"
            ),
            zero_semantics="observed_zero",
        )
    elif normalized in NATIVE_PERPETUAL_KINDS:
        metadata.update(volume_kind="real", volume_quality="exchange")
        metadata["volume_provenance"].update(
            field="volume",
            basis="exchange_contract_volume",
            zero_semantics="observed_zero",
        )
    if resolved is not None:
        metadata.update(resolved.to_metadata())
    current_spec = getattr(source, "current_instrument_spec", None)
    if normalized in NATIVE_PERPETUAL_KINDS and callable(current_spec):
        try:
            spec = current_spec()
        except (AttributeError, TypeError, ValueError):
            spec = None
        if spec is not None and callable(getattr(spec, "to_dict", None)):
            metadata["instrument_spec"] = spec.to_dict()
    if type(source).__name__ in {
        "TradingViewSource", "MT5Source", "BinanceUsdmSource", "OkxSwapSource",
        "BitgetUsdtFuturesSource",
    }:
        metadata["strict_close_time"] = True
    return metadata


def default_symbol_for_kind(kind: str | None) -> str:
    return _DEFAULT_SYMBOLS[normalize_data_source_kind(kind)]


def create_data_source(kind: str | None, *, settings: Any | None = None) -> DataSource:
    """Instantiate a fresh data source for *kind* (not connected)."""
    normalized = normalize_data_source_kind(kind)
    if normalized == "tradingview":
        from pa_agent.data.tradingview import TradingViewSource

        return TradingViewSource()
    if normalized == "binance_usdm":
        from pa_agent.data.binance_usdm import BinanceUsdmSource

        return BinanceUsdmSource()
    if normalized == "okx_swap":
        from pa_agent.data.okx_swap import OkxSwapSource

        return OkxSwapSource()
    if normalized == "bitget_usdt_futures":
        from pa_agent.data.bitget_usdt_futures import BitgetUsdtFuturesSource

        return BitgetUsdtFuturesSource()
    if normalized == "eastmoney":
        from pa_agent.data.eastmoney_source import EastMoneySource

        return EastMoneySource()
    if normalized == "tushare":
        from pa_agent.data.tushare_source import TushareSource

        if settings is None:
            from pa_agent.config.paths import SETTINGS_JSON_PATH
            from pa_agent.config.settings import load_settings

            settings = load_settings(SETTINGS_JSON_PATH)
        return TushareSource(settings=settings)
    if normalized == "akshare":
        from pa_agent.data.akshare_source import AkShareSource

        return AkShareSource()
    if normalized == "yfinance":
        from pa_agent.data.yfinance_source import YFinanceSource

        return YFinanceSource()
    from pa_agent.data.mt5 import MT5Source

    return MT5Source()
