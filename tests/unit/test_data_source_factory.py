"""Tests for data source factory and settings."""
from __future__ import annotations

from pa_agent.config.settings import GeneralSettings
from pa_agent.data.binance_usdm import BinanceUsdmSource
from pa_agent.data.eastmoney_source import EastMoneySource
from pa_agent.data.factory import (
    DATA_SOURCE_CHOICES,
    create_data_source,
    default_symbol_for_kind,
    default_tradingview_exchange,
    normalize_data_source_kind,
    volume_metadata_for_source,
)
from pa_agent.data.mt5 import MT5Source
from pa_agent.data.tradingview import TradingViewSource
from pa_agent.data.tushare_source import TushareSource


def test_normalize_data_source_kind_defaults_unknown():
    assert normalize_data_source_kind("invalid") == "tradingview"
    assert normalize_data_source_kind(None) == "tradingview"


def test_normalize_data_source_kind_hidden_sources():
    assert normalize_data_source_kind("akshare") == "akshare"
    assert normalize_data_source_kind("eastmoney") == "eastmoney"
    assert normalize_data_source_kind("tushare") == "tushare"
    assert normalize_data_source_kind("yfinance") == "yfinance"


def test_eastmoney_not_in_ui_choices():
    ui_kinds = {k for k, _ in DATA_SOURCE_CHOICES}
    assert "eastmoney" not in ui_kinds
    assert "akshare" not in ui_kinds


def test_tushare_not_in_ui_choices():
    ui_kinds = {k for k, _ in DATA_SOURCE_CHOICES}
    assert "tushare" not in ui_kinds


def test_create_data_source_returns_expected_types():
    from pa_agent.data.bitget_usdt_futures import BitgetUsdtFuturesSource
    from pa_agent.data.okx_swap import OkxSwapSource

    assert isinstance(create_data_source("mt5"), MT5Source)
    assert isinstance(create_data_source("tradingview"), TradingViewSource)
    assert isinstance(create_data_source("eastmoney"), EastMoneySource)
    assert isinstance(create_data_source("tushare"), TushareSource)
    assert isinstance(create_data_source("binance_usdm"), BinanceUsdmSource)
    assert isinstance(create_data_source("okx_swap"), OkxSwapSource)
    assert isinstance(create_data_source("bitget_usdt_futures"), BitgetUsdtFuturesSource)


def test_default_symbols_per_kind():
    assert default_symbol_for_kind("mt5") == "XAUUSDm"
    assert default_symbol_for_kind("tradingview") == "XAUUSD"
    assert default_symbol_for_kind("eastmoney") == "000001"
    assert default_symbol_for_kind("tushare") == "000001"
    assert default_symbol_for_kind("binance_usdm") == "XAUUSDT"
    assert default_symbol_for_kind("okx_swap") == "XAU-USDT-SWAP"
    assert default_symbol_for_kind("bitget_usdt_futures") == "XAUUSDT"


def test_binance_usdm_is_an_explicit_ui_source_with_exchange_volume() -> None:
    assert "binance_usdm" in {kind for kind, _ in DATA_SOURCE_CHOICES}
    metadata = volume_metadata_for_source("binance_usdm")
    assert metadata["volume_kind"] == "real"
    assert metadata["volume_quality"] == "exchange"


def test_native_perpetual_sources_are_explicit_exchange_volume_sources() -> None:
    ui_kinds = {kind for kind, _ in DATA_SOURCE_CHOICES}

    for kind in ("okx_swap", "bitget_usdt_futures"):
        assert normalize_data_source_kind(kind) == kind
        assert kind in ui_kinds
        metadata = volume_metadata_for_source(kind)
        assert metadata["volume_kind"] == "real"
        assert metadata["volume_quality"] == "exchange"


def test_native_perpetual_volume_provenance_matches_base_asset_fields() -> None:
    expected_fields = {
        "binance_usdm": "volume",
        "okx_swap": "volCcy",
        "bitget_usdt_futures": "baseVolume",
    }

    for kind, field in expected_fields.items():
        provenance = volume_metadata_for_source(kind)["volume_provenance"]
        assert provenance["field"] == field
        assert provenance["basis"] == "exchange_base_asset_volume"


def test_default_tradingview_exchange_is_auto():
    assert default_tradingview_exchange() == ""


def test_volume_metadata_marks_mt5_as_tick_proxy() -> None:
    metadata = volume_metadata_for_source("mt5")

    assert metadata["volume_kind"] == "tick"
    assert metadata["volume_quality"] == "proxy"


def test_volume_metadata_keeps_tradingview_semantics_unknown() -> None:
    source = TradingViewSource()
    source.set_exchange("OANDA")
    metadata = volume_metadata_for_source("tradingview", source)

    assert metadata["venue"] == "OANDA"
    assert metadata["volume_kind"] == "unknown"


def test_volume_metadata_prefers_resolved_tradingview_venue() -> None:
    source = TradingViewSource()
    source.set_exchange("")
    source._resolved_exchange = "FOREXCOM"

    metadata = volume_metadata_for_source("tradingview", source)

    assert metadata["venue"] == "FOREXCOM"


def test_volume_metadata_matches_adapter_zero_and_field_semantics() -> None:
    expected_fields = {
        "eastmoney": "volume",
        "akshare": "volume",
        "tushare": "vol",
        "yfinance": "Volume",
    }

    for source_kind, field in expected_fields.items():
        provenance = volume_metadata_for_source(source_kind)["volume_provenance"]
        assert provenance["field"] == field
        assert provenance["zero_semantics"] == "observed_zero"


def test_general_settings_last_data_source_default():
    g = GeneralSettings()
    assert g.last_data_source == "tradingview"
