from __future__ import annotations

import json
from pathlib import Path

from pa_agent.config.settings import Settings


def test_vps_example_enables_three_native_xau_paper_feeds_without_secrets() -> None:
    root = Path(__file__).resolve().parents[2]
    raw = json.loads((root / "config" / "settings.vps.example.json").read_text("utf-8"))
    settings = Settings.model_validate(raw)

    assert settings.provider.api_key == ""
    assert settings.monitoring.enabled is True
    assert settings.prompt.vpa_mode == "shadow"
    assert settings.paper_trading.enabled is True
    assert {
        (asset.source, asset.symbol, asset.timeframe)
        for asset in settings.monitoring.assets
        if asset.enabled
    } == {
        ("binance_usdm", "XAUUSDT", "1h"),
        ("okx_swap", "XAU-USDT-SWAP", "1h"),
        ("bitget_usdt_futures", "XAUUSDT", "1h"),
    }


def test_vps_environment_and_systemd_assets_are_headless_and_non_live() -> None:
    root = Path(__file__).resolve().parents[2]
    environment = (root / "environment-vps.yml").read_text("utf-8")
    service = (root / "deploy" / "systemd" / "pa-agent.service").read_text("utf-8")
    dashboard_service = (
        root / "deploy" / "systemd" / "pa-agent-dashboard.service"
    ).read_text("utf-8")
    installer = (root / "deploy" / "install_vps.sh").read_text("utf-8")
    ci = (root / ".github" / "workflows" / "ci.yml").read_text("utf-8")
    attributes = (root / ".gitattributes").read_text("utf-8")

    assert "name: pa-agent" in environment
    assert "- python=3.11" in environment
    assert "uv==0.11.19" in environment
    assert "ExecStartPre=" in service and "--check" in service
    assert "ExecStart=" in service and "pa-agent-headless" in service
    assert "--probe-data" not in service
    assert "LoadCredential=pa_agent_api_key:" in service
    assert "pa-agent-dashboard" in dashboard_service
    assert "--host 127.0.0.1" in dashboard_service
    assert "LoadCredential=pa_agent_dashboard_token:" in dashboard_service
    assert "pa_agent_api_key" not in dashboard_service
    assert "User=pa-agent-dashboard" in dashboard_service
    assert "PA_AGENT_OPERATION_DIR=/var/lib/pa-agent-control" in dashboard_service
    assert "ReadWritePaths=/var/lib/pa-agent-control/control" in dashboard_service
    assert "ReadOnlyPaths=/var/lib/pa-agent-control/status" in dashboard_service
    assert "ReadWritePaths=/var/lib/pa-agent\n" not in dashboard_service
    assert "NoNewPrivileges=true" in dashboard_service
    assert "systemctl enable" not in installer
    assert '"$ENV_UV" export' in installer
    assert "--frozen" in installer
    assert "--require-hashes" in installer
    assert 'trap \'rm -f "$LOCK_REQUIREMENTS"\' EXIT' in installer
    assert "YOUR_API_KEY" not in installer
    assert "pa-agent-dashboard.service" in installer
    assert "CONTROL_GROUP=pa-agent-control" in installer
    assert "/var/lib/pa-agent-control/status" in installer
    assert "/var/lib/pa-agent-control/control" in installer
    assert '-m 2750 /var/lib/pa-agent-control' in installer
    assert 'cd "$APP_DIR"' in installer
    assert "${APP_DIR:-" not in installer
    assert "${STATE_DIR:-" not in installer
    assert "Validate frozen VPS profile" in ci
    assert "uv export --frozen --extra vps --no-dev --no-emit-project" in ci
    assert "uv pip check" in ci
    assert "python -m pip check" not in ci
    assert "*.sh text eol=lf" in attributes
