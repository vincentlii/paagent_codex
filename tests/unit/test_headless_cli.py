from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

from pa_agent.config.paths import resolve_runtime_paths
from pa_agent.config.settings import MonitoringSettings, Settings, save_settings
from pa_agent.headless import (
    analysis_batch_timeout_seconds,
    main,
    validate_headless_configuration,
)


def test_once_timeout_scales_by_bounded_concurrency_waves() -> None:
    monitoring = MonitoringSettings(
        max_concurrency=2,
        analysis_timeout_seconds=120,
    )

    assert analysis_batch_timeout_seconds(monitoring, accepted_jobs=0) == 0
    assert analysis_batch_timeout_seconds(monitoring, accepted_jobs=1) == 120
    assert analysis_batch_timeout_seconds(monitoring, accepted_jobs=5) == 360


def test_headless_import_does_not_load_qt() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import sys; import pa_agent.headless; "
                "print(any(name.startswith('PyQt6') for name in sys.modules))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


def test_headless_runtime_import_does_not_load_qt() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import sys; import pa_agent.monitoring.headless; "
                "print(any(name.startswith('PyQt6') for name in sys.modules))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


def test_aabb_research_cli_import_does_not_load_qt() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import sys; import pa_agent.experiments.cli; "
                "print(any(name.startswith('PyQt6') for name in sys.modules))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.stdout.strip() == "False"


def test_desktop_entry_module_can_be_imported_without_qt() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import sys; import pa_agent.main; "
                "print(any(name.startswith('PyQt6') for name in sys.modules))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


def test_packaging_exposes_headless_entry_and_keeps_qt_in_gui_extra() -> None:
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    core = config["project"]["dependencies"]
    gui = config["project"]["optional-dependencies"]["gui"]
    scripts = config["project"]["scripts"]

    assert not any(item.startswith(("PyQt6", "pyqtgraph")) for item in core)
    assert any(item.startswith("PyQt6") for item in gui)
    assert any(item.startswith("pyqtgraph") for item in gui)
    assert scripts["pa-agent-headless"] == "pa_agent.headless:main"
    assert scripts["pa-agent-paper-report"] == "pa_agent.execution.reporting:main"
    assert scripts["pa-agent-aabb"] == "pa_agent.experiments.cli:single_main"
    assert scripts["pa-agent-aabb-monitor"] == "pa_agent.experiments.cli:continuous_main"


def test_headless_check_reports_missing_assets_secret_and_prompts(tmp_path) -> None:
    settings = Settings()
    settings.provider.api_key = ""
    paths = resolve_runtime_paths(project_root=tmp_path, environ={})

    errors = validate_headless_configuration(
        settings,
        paths,
        platform_name="linux",
        source_factory=lambda _asset: object(),
    )

    assert any("API key" in error for error in errors)
    assert any("monitoring asset" in error for error in errors)
    assert any("prompt" in error.lower() for error in errors)


def test_headless_check_rejects_mt5_on_linux(tmp_path) -> None:
    settings = Settings()
    settings.provider.api_key = "secret"
    settings.monitoring = MonitoringSettings(
        enabled=True,
        assets=[
            {
                "source": "mt5",
                "symbol": "XAUUSD",
                "timeframe": "1h",
            }
        ],
    )
    prompt_dir = tmp_path / "prompt_engineering"
    prompt_dir.mkdir()
    paths = resolve_runtime_paths(project_root=tmp_path, environ={})

    errors = validate_headless_configuration(
        settings,
        paths,
        platform_name="linux",
        source_factory=lambda _asset: object(),
    )

    assert any("MT5" in error and "Linux" in error for error in errors)


def test_headless_check_smoke_uses_real_prompts_and_tradingview_adapter(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    root = Path(__file__).resolve().parents[2]
    settings = Settings()
    settings.provider.api_key = "test-only-key"
    settings.monitoring = MonitoringSettings(
        enabled=True,
        assets=[
            {
                "source": "tradingview",
                "exchange": "OANDA",
                "symbol": "XAUUSD",
                "timeframe": "15m",
            }
        ],
    )
    settings_path = tmp_path / "private" / "settings.json"
    save_settings(settings, settings_path)
    for name in (
        "PA_AGENT_API_KEY",
        "PA_AGENT_API_KEY_FILE",
        "CREDENTIALS_DIRECTORY",
    ):
        monkeypatch.delenv(name, raising=False)

    exit_code = main(
        [
            "--check",
            "--settings",
            str(settings_path),
            "--state-dir",
            str(tmp_path / "state"),
            "--prompt-dir",
            str(root / "prompt_engineering"),
        ]
    )

    assert exit_code == 0
    assert "Headless configuration OK" in capsys.readouterr().out


def test_headless_check_rejects_paper_execution_without_native_constraints(
    tmp_path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    settings = Settings()
    settings.provider.api_key = "test-key"
    settings.paper_trading.enabled = True
    settings.monitoring = MonitoringSettings(
        enabled=True,
        assets=[
            {
                "source": "tradingview",
                "exchange": "OANDA",
                "symbol": "XAUUSD",
                "timeframe": "1h",
            }
        ],
    )
    paths = resolve_runtime_paths(
        project_root=root,
        environ={"PA_AGENT_HOME": str(tmp_path / "state")},
    )

    errors = validate_headless_configuration(settings, paths, platform_name="linux")

    assert any("provider-declared instrument constraints" in error for error in errors)


def test_headless_check_accepts_three_native_xau_paper_sources(tmp_path) -> None:
    root = Path(__file__).resolve().parents[2]
    settings = Settings()
    settings.provider.api_key = "test-key"
    settings.paper_trading.enabled = True
    settings.monitoring = MonitoringSettings(
        enabled=True,
        assets=[
            {"source": "binance_usdm", "symbol": "XAUUSDT", "timeframe": "1h"},
            {"source": "okx_swap", "symbol": "XAU-USDT-SWAP", "timeframe": "1h"},
            {
                "source": "bitget_usdt_futures",
                "symbol": "XAUUSDT",
                "timeframe": "1h",
            },
        ],
    )
    paths = resolve_runtime_paths(
        project_root=root,
        environ={"PA_AGENT_HOME": str(tmp_path / "state")},
    )

    errors = validate_headless_configuration(settings, paths, platform_name="linux")

    assert not any("provider-declared instrument constraints" in error for error in errors)
