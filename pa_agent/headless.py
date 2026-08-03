"""Command-line entry point for unattended Linux and server deployments."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pa_agent.config.paths import RuntimePaths, resolve_runtime_paths
from pa_agent.config.runtime import RuntimeConfigurationError, load_runtime_settings
from pa_agent.config.settings import Settings, provider_api_key_configured

logger = logging.getLogger(__name__)


def _required_prompt_files(settings: Settings) -> set[str]:
    from pa_agent.ai.prompt_assembler import (
        VPA_KNOWLEDGE_MODULES,
        stage1_prompt_txt_files,
        stage2_prompt_txt_files,
    )
    from pa_agent.config.vpa import vpa_is_enabled

    required = set(stage1_prompt_txt_files())
    required.update(stage2_prompt_txt_files(load_full_strategy_library=True))
    if vpa_is_enabled(settings.prompt):
        required.update(VPA_KNOWLEDGE_MODULES.values())
    return required


def validate_headless_configuration(
    settings: Settings,
    paths: RuntimePaths,
    *,
    platform_name: str | None = None,
    source_factory: Callable[[Any], Any] | None = None,
) -> list[str]:
    """Return every deterministic deployment error without opening the network."""
    errors: list[str] = []
    platform = (platform_name or sys.platform).lower()
    if not provider_api_key_configured(settings):
        errors.append("AI provider API key is not configured")
    if not settings.provider.model.strip():
        errors.append("AI provider model is empty")
    if not settings.provider.base_url.strip():
        errors.append("AI provider base URL is empty")
    assets = [asset for asset in settings.monitoring.assets if asset.enabled]
    if not settings.monitoring.enabled:
        errors.append("headless monitoring is disabled")
    if not assets:
        errors.append("at least one enabled monitoring asset is required")

    if not paths.prompt_dir.is_dir():
        errors.append(f"prompt directory is unavailable: {paths.prompt_dir}")
    else:
        for filename in sorted(_required_prompt_files(settings)):
            path = paths.prompt_dir / filename
            try:
                if not path.is_file() or not path.read_text(encoding="utf-8").strip():
                    errors.append(f"prompt file is missing or empty: {filename}")
            except (OSError, UnicodeError) as exc:
                errors.append(f"prompt file is unreadable: {filename}: {exc}")

    factory = source_factory
    if factory is None:
        from pa_agent.data.factory import create_data_source

        def factory(asset):
            return create_data_source(asset.source, settings=settings)
    for asset in assets:
        if asset.source == "mt5" and not platform.startswith("win"):
            errors.append("MT5 data source is unavailable on Linux; use TradingView")
            continue
        try:
            source = factory(asset)
            supported = getattr(source, "supported_timeframes", None)
            if not callable(supported):
                raise TypeError("source does not expose supported_timeframes()")
            if asset.timeframe not in set(supported()):
                errors.append(
                    f"unsupported timeframe for {asset.source}: "
                    f"{asset.symbol} {asset.timeframe}"
                )
        except Exception as exc:
            errors.append(
                f"cannot initialize data source for {asset.symbol} "
                f"{asset.timeframe}: {exc}"
            )

    writable_dirs = {
        paths.config_dir,
        paths.logs_dir,
        paths.records_pending_dir,
        paths.analysis_audit_db_path.parent,
        paths.experience_dir,
    }
    if settings.paper_trading.enabled:
        from pa_agent.data.factory import NATIVE_PERPETUAL_KINDS

        writable_dirs.add(paths.paper_trading_db_path.parent)
        unsupported = [asset for asset in assets if asset.source not in NATIVE_PERPETUAL_KINDS]
        if unsupported:
            errors.append(
                "paper trading requires a native source with provider-declared "
                "instrument constraints; currently supported: "
                + ", ".join(sorted(NATIVE_PERPETUAL_KINDS))
            )
    for directory in sorted(writable_dirs, key=str):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".pa-agent-write-check"
            probe.write_text("ok", encoding="ascii")
            probe.unlink()
        except OSError as exc:
            errors.append(f"runtime directory is not writable: {directory}: {exc}")
    return errors


def build_headless_monitor(settings: Settings, paths: RuntimePaths):
    """Wire the production scheduler without importing any Qt component."""
    from pa_agent.data.factory import create_data_source
    from pa_agent.execution.coordinator import PaperExecutionCoordinator
    from pa_agent.monitoring.analysis_runner import IndependentAnalysisHandler
    from pa_agent.monitoring.audit import SchedulerAuditStore
    from pa_agent.monitoring.headless import HeadlessMonitor
    from pa_agent.monitoring.production import ProductionOrchestratorFactory
    from pa_agent.monitoring.service import MultiAssetAnalysisService
    from pa_agent.monitoring.task_factory import analysis_runtime_fingerprint
    from pa_agent.records.pending_writer import PendingWriter

    pending_writer = PendingWriter(
        pending_dir=paths.records_pending_dir,
        api_key=settings.provider.api_key,
        counterfactual_horizon_bars=settings.general.counterfactual_horizon_bars,
    )
    orchestrator_factory = ProductionOrchestratorFactory(
        settings=settings,
        pending_writer=pending_writer,
        prompt_dir=paths.prompt_dir,
        experience_dir=paths.experience_dir,
        logger=logging.getLogger("pa_agent"),
    )
    handler = IndependentAnalysisHandler(orchestrator_factory)
    scheduler_audit = SchedulerAuditStore(paths.monitoring_audit_db_path)
    paper_execution = PaperExecutionCoordinator(
        paths.paper_trading_db_path,
        settings.paper_trading,
    )
    if paper_execution.enabled:
        try:
            paper_execution.reconcile_official(
                scheduler_audit.list_successful_jobs(),
                paths.records_pending_dir,
            )
        except Exception:
            logging.getLogger("pa_agent").exception(
                "Paper execution reconciliation failed; analysis startup continues"
            )

    def service_factory(on_completion):
        monitoring = settings.monitoring
        return MultiAssetAnalysisService(
            handler,
            max_concurrency=monitoring.max_concurrency,
            max_pending_jobs=monitoring.max_pending_jobs,
            max_pending_per_asset=monitoring.max_pending_per_asset,
            dedupe_history=monitoring.dedupe_history,
            audit_store=scheduler_audit,
            on_completion=on_completion,
        )

    return HeadlessMonitor(
        settings=settings,
        paths=paths,
        scheduler_audit=scheduler_audit,
        source_factory=lambda asset: create_data_source(
            asset.source,
            settings=settings,
        ),
        service_factory=service_factory,
        analysis_fingerprint=analysis_runtime_fingerprint(
            project_root=paths.project_root,
            prompt_dir=paths.prompt_dir,
        ),
        paper_execution=paper_execution,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PA Agent headless multi-asset monitor")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="validate deployment without network calls")
    mode.add_argument("--once", action="store_true", help="analyze the latest closed snapshot once")
    parser.add_argument("--settings", type=Path, help="settings JSON path")
    parser.add_argument("--state-dir", type=Path, help="writable runtime state directory")
    parser.add_argument("--prompt-dir", type=Path, help="read-only prompt asset directory")
    return parser


def _paths_from_args(args: argparse.Namespace) -> RuntimePaths:
    env = dict(os.environ)
    if args.state_dir is not None:
        env["PA_AGENT_HOME"] = str(args.state_dir)
    if args.prompt_dir is not None:
        env["PA_AGENT_PROMPT_DIR"] = str(args.prompt_dir)
    if args.settings is not None:
        env["PA_AGENT_SETTINGS_PATH"] = str(args.settings)
    project_root = Path(__file__).resolve().parent.parent
    return resolve_runtime_paths(project_root=project_root, environ=env)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    paths = _paths_from_args(args)
    try:
        loaded = load_runtime_settings(paths.settings_json_path)
    except RuntimeConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    settings = loaded.settings
    if args.once:
        settings.monitoring.run_on_startup = True
    errors = validate_headless_configuration(settings, paths)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if args.check:
        print(
            "Headless configuration OK: "
            f"assets={len([a for a in settings.monitoring.assets if a.enabled])}, "
            f"api_key_source={loaded.api_key_source}, state={paths.state_root}"
        )
        return 0

    from pa_agent.util.logging import configure_logging

    configure_logging(
        api_key=settings.provider.api_key,
        log_file_path=paths.log_file_path,
    )
    monitor = build_headless_monitor(settings, paths)

    def request_stop(_signum, _frame):
        logger.info("Shutdown signal received")
        monitor.request_stop()

    previous_handlers = {}
    for signal_name in ("SIGINT", "SIGTERM"):
        value = getattr(signal, signal_name, None)
        if value is not None:
            previous_handlers[value] = signal.signal(value, request_stop)
    try:
        if args.once:
            monitor.poll_once()
            idle = monitor.wait_idle(settings.monitoring.analysis_timeout_seconds)
            return int(not (idle and monitor.latest_snapshots_successful()))
        monitor.run_forever()
        return 0
    except KeyboardInterrupt:
        monitor.request_stop()
        return 130
    except Exception:
        logger.exception("Headless runtime failed")
        return 1
    finally:
        monitor.shutdown()
        for sig, previous in previous_handlers.items():
            signal.signal(sig, previous)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
