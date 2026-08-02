"""Unit tests for settings load/save round-trip (task 2.4)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from pa_agent.config.settings import (
    GeneralSettings,
    MonitoringAssetSettings,
    MonitoringSettings,
    Settings,
    load_settings,
    save_settings,
)


def test_defaults(tmp_path):
    """load_settings on a missing file returns defaults and creates the file."""
    p = tmp_path / "settings.json"
    s = load_settings(p)
    assert s.provider.model == "deepseek-v4-flash"
    assert s.provider.base_url == "https://api.deepseek.com"
    assert s.provider.thinking is True
    assert s.provider.reasoning_effort == "high"
    assert s.provider.context_window == 2_000_000
    assert s.general.analysis_bar_count == 100
    assert s.general.last_symbol == "XAUUSD"
    assert s.general.last_timeframe == "15m"
    assert s.general.decision_stance == "balanced"
    assert s.general.decision_flow_auto_play is True
    assert s.general.auto_resume_chart_after_analysis is False
    assert s.general.counterfactual_horizon_bars == 20
    assert s.monitoring.enabled is False
    assert s.monitoring.max_concurrency == 2
    assert s.monitoring.max_pending_jobs == 100
    assert s.paper_trading.enabled is False
    assert s.paper_trading.risk_fraction == 0.01
    assert s.paper_trading.max_portfolio_risk_fraction == 0.03
    assert s.paper_trading.max_leverage == 3.0
    assert p.exists(), "defaults should be written to disk"


def test_analysis_bar_count_cannot_be_below_preflight_minimum():
    with pytest.raises(ValidationError):
        GeneralSettings(analysis_bar_count=19)


def test_monitoring_queue_must_cover_worker_concurrency():
    with pytest.raises(ValidationError):
        MonitoringSettings(max_concurrency=4, max_pending_jobs=3)


def test_monitoring_assets_are_canonical_and_minute_month_remain_distinct():
    minute = MonitoringAssetSettings(
        source="TRADINGVIEW",
        exchange="oanda",
        symbol=" xauusd ",
        timeframe="1m",
    )
    monthly = MonitoringAssetSettings(
        source="tradingview",
        exchange="OANDA",
        symbol="XAUUSD",
        timeframe="1M",
    )

    assert minute.source == "tradingview"
    assert minute.exchange == "OANDA"
    assert minute.symbol == "XAUUSD"
    assert minute.timeframe == "1m"
    assert monthly.timeframe == "1M"


def test_monitoring_rejects_duplicate_or_ambiguous_asset_subscriptions():
    duplicate = {
        "source": "tradingview",
        "exchange": "OANDA",
        "symbol": "XAUUSD",
        "timeframe": "1h",
    }
    with pytest.raises(ValidationError, match="duplicate monitoring asset"):
        MonitoringSettings(assets=[duplicate, dict(duplicate)])

    with pytest.raises(ValidationError, match="ambiguous monitoring asset"):
        MonitoringSettings(
            assets=[
                {**duplicate, "exchange": ""},
                {**duplicate, "exchange": "OANDA"},
            ]
        )


def test_round_trip(tmp_path):
    """save → load preserves all fields."""
    p = tmp_path / "settings.json"
    original = Settings()
    original.provider.api_key = "sk-test-1234"
    original.general.last_symbol = "BTCUSDT"
    save_settings(original, p)
    loaded = load_settings(p)
    assert loaded.provider.api_key == "sk-test-1234"
    # Crypto symbols migrate to gold defaults on load
    assert loaded.general.last_symbol == "XAUUSD"
    assert loaded.provider.model == original.provider.model


def test_api_key_is_encrypted_on_disk_and_round_trips(tmp_path):
    """The saved JSON contains ciphertext, never the plaintext API key."""
    p = tmp_path / "settings.json"
    s = Settings()
    s.provider.api_key = "sk-super-secret-key"
    save_settings(s, p)
    raw = p.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert "sk-super-secret-key" not in raw
    assert data["provider"]["api_key"] == ""
    assert data["provider"]["api_key_encrypted"]
    assert (tmp_path / "secret.key").is_file()
    assert load_settings(p).provider.api_key == "sk-super-secret-key"


def test_legacy_plaintext_api_key_is_migrated_to_ciphertext(tmp_path):
    p = tmp_path / "settings.json"
    data = Settings().model_dump()
    data["provider"]["api_key"] = "legacy-plaintext-key"
    p.write_text(json.dumps(data), encoding="utf-8")

    loaded = load_settings(p)

    raw = p.read_text(encoding="utf-8")
    assert loaded.provider.api_key == "legacy-plaintext-key"
    assert "legacy-plaintext-key" not in raw
    assert json.loads(raw)["provider"]["api_key_encrypted"]


def test_missing_encryption_key_does_not_invent_or_expose_a_secret(tmp_path):
    p = tmp_path / "settings.json"
    settings = Settings()
    settings.provider.api_key = "temporary-key"
    save_settings(settings, p)
    (tmp_path / "secret.key").unlink()

    loaded = load_settings(p)

    assert loaded.provider.api_key == ""
    assert "temporary-key" not in p.read_text(encoding="utf-8")


def test_corrupt_json_returns_defaults(tmp_path):
    """Corrupt settings.json falls back to defaults without raising."""
    p = tmp_path / "settings.json"
    p.write_text("{not valid json", encoding="utf-8")
    s = load_settings(p)
    assert s.provider.model == "deepseek-v4-flash"


def test_missing_api_key_leaves_api_key_blank(tmp_path):
    """If api_key is absent, api_key stays empty string."""
    p = tmp_path / "settings.json"
    data = Settings().model_dump()
    data["provider"].pop("api_key", None)
    data["provider"].pop("api_key_encrypted", None)
    p.write_text(json.dumps(data), encoding="utf-8")
    s = load_settings(p)
    assert s.provider.api_key == ""


def test_feishu_round_trip(tmp_path):
    """save → load preserves feishu settings."""
    p = tmp_path / "settings.json"
    original = Settings()
    original.feishu.webhook_url = "https://example.com/hook"
    original.feishu.secret = "sec"
    original.feishu.app_id = "cli_test"
    save_settings(original, p)
    loaded = load_settings(p)
    assert loaded.feishu.webhook_url == "https://example.com/hook"
    assert loaded.feishu.secret == "sec"
    assert loaded.feishu.app_id == "cli_test"


def test_pushplus_round_trip(tmp_path):
    """save → load preserves pushplus settings."""
    p = tmp_path / "settings.json"
    original = Settings()
    original.pushplus.token = "pp-test-token"
    original.pushplus.enabled = False
    save_settings(original, p)
    loaded = load_settings(p)
    assert loaded.pushplus.token == "pp-test-token"
    assert loaded.pushplus.enabled is False


def test_tushare_round_trip(tmp_path):
    """save → load preserves tushare token."""
    p = tmp_path / "settings.json"
    original = Settings()
    original.tushare.token = "ts-test-token"
    save_settings(original, p)
    loaded = load_settings(p)
    assert loaded.tushare.token == "ts-test-token"


def test_pushplus_auto_disabled_when_enabled_without_token(tmp_path):
    """load_settings disables pushplus when enabled but token empty."""
    p = tmp_path / "settings.json"
    p.write_text(
        '{"pushplus": {"enabled": true, "token": ""}}',
        encoding="utf-8",
    )
    with patch.dict("os.environ", {}, clear=True):
        loaded = load_settings(p)
    assert loaded.pushplus.enabled is False
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["pushplus"]["enabled"] is False


def test_migrate_legacy_feishu_json(tmp_path):
    """Legacy config/feishu.json is merged into settings.json on load."""
    p = tmp_path / "settings.json"
    legacy = tmp_path / "feishu.json"
    save_settings(Settings(), p)
    legacy.write_text(
        json.dumps(
            {
                "enabled": True,
                "webhook_url": "https://example.com/legacy-hook",
                "secret": "legacy-secret",
                "app_id": "cli_legacy",
                "app_secret": "legacy-app-secret",
                "notify_on_order_only": True,
            }
        ),
        encoding="utf-8",
    )
    loaded = load_settings(p)
    assert loaded.feishu.webhook_url == "https://example.com/legacy-hook"
    assert loaded.feishu.secret == "legacy-secret"
    assert loaded.feishu.app_id == "cli_legacy"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["feishu"]["webhook_url"] == "https://example.com/legacy-hook"
