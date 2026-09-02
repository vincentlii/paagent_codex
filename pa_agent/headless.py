"""Command-line entry point for unattended Linux and server deployments."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import signal
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pa_agent.config.paths import RuntimePaths, resolve_runtime_paths
from pa_agent.config.runtime import RuntimeConfigurationError, load_runtime_settings
from pa_agent.config.settings import Settings, provider_api_key_configured

logger = logging.getLogger(__name__)


def analysis_batch_timeout_seconds(monitoring: Any, *, accepted_jobs: int) -> float:
    """Bound a run-once wait by per-job timeout and scheduler concurrency waves."""
    jobs = max(0, int(accepted_jobs))
    if jobs == 0:
        return 0.0
    concurrency = max(1, int(monitoring.max_concurrency))
    waves = math.ceil(jobs / concurrency)
    return float(monitoring.analysis_timeout_seconds) * waves


@dataclass(frozen=True)
class MarketDataProbeResult:
    source: str
    symbol: str
    timeframe: str
    success: bool
    closed_bars: int = 0
    newest_closed_ts: int | None = None
    higher_timeframe: str = ""
    higher_closed_bars: int = 0
    identity: dict[str, Any] = field(default_factory=dict)
    instrument_spec: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def probe_market_data(
    settings: Settings,
    *,
    source_factory: Callable[[Any], Any] | None = None,
    sample_bars: int = 3,
    config_dir: Path | None = None,
) -> list[MarketDataProbeResult]:
    """Probe native market facts without invoking an AI provider."""
    from pa_agent.data.factory import create_data_source, resolved_identity_for_source
    from pa_agent.data.higher_timeframe import resolve_higher_timeframe

    factory = source_factory or (
        lambda asset: create_data_source(
            asset.source,
            settings=settings,
            config_dir=config_dir,
        )
    )
    results: list[MarketDataProbeResult] = []
    for asset in settings.monitoring.assets:
        if not asset.enabled:
            continue
        source = None
        error = ""
        closed = []
        identity = None
        spec = None
        higher_timeframe = ""
        higher_closed = []
        try:
            source = factory(asset)
            source.connect()
            source.subscribe(asset.symbol, asset.timeframe)
            bars = list(source.latest_snapshot(max(2, int(sample_bars))))
            closed = _validated_probe_bars(bars, label="source")
            identity = resolved_identity_for_source(
                asset.source, source, symbol=asset.symbol,
            )
            if identity is None or identity.provider != asset.source:
                raise ValueError("source did not resolve an exact instrument identity")
            if settings.general.higher_timeframe_enabled:
                higher_timeframe = str(resolve_higher_timeframe(
                    asset.timeframe,
                    settings.general.higher_timeframe_override,
                ) or "")
                if higher_timeframe:
                    if higher_timeframe not in set(source.supported_timeframes()):
                        raise ValueError(
                            f"source does not support higher timeframe {higher_timeframe}"
                        )
                    higher_bars = source.fetch_snapshot_for_identity(
                        identity,
                        higher_timeframe,
                        max(2, int(sample_bars)),
                    )
                    higher_closed = _validated_probe_bars(
                        list(higher_bars),
                        label="higher timeframe source",
                    )
            current_spec = getattr(source, "current_instrument_spec", None)
            if callable(current_spec):
                spec = current_spec()
            if settings.paper_trading.enabled:
                if spec is None:
                    raise ValueError("paper trading requires an instrument spec")
                if identity.key != (
                    spec.provider.casefold(), spec.venue.casefold(),
                    spec.symbol.casefold(), spec.instrument_type.casefold(),
                ):
                    raise ValueError("instrument spec does not match resolved identity")
        except Exception as exc:
            error = str(exc) or type(exc).__name__
        finally:
            if source is not None:
                try:
                    source.disconnect()
                except Exception as exc:
                    if not error:
                        error = f"disconnect failed: {exc}"
        results.append(MarketDataProbeResult(
            source=asset.source,
            symbol=asset.symbol,
            timeframe=asset.timeframe,
            success=not error,
            closed_bars=len(closed),
            newest_closed_ts=(
                max(int(bar.ts_open) for bar in closed) if closed else None
            ),
            higher_timeframe=higher_timeframe,
            higher_closed_bars=len(higher_closed),
            identity=(identity.to_dict() if identity is not None else {}),
            instrument_spec=(
                spec.to_dict()
                if spec is not None and callable(getattr(spec, "to_dict", None))
                else {}
            ),
            error=error,
        ))
    return results


def _validated_probe_bars(bars: list[Any], *, label: str) -> list[Any]:
    closed = [bar for bar in bars if bool(getattr(bar, "closed", False))]
    if not closed:
        raise ValueError(f"{label} returned no closed bars")
    for bar in closed:
        values = (bar.ts_open, bar.open, bar.high, bar.low, bar.close, bar.volume)
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError(f"{label} returned non-finite bar facts")
        if not float(bar.low) <= min(float(bar.open), float(bar.close)):
            raise ValueError(f"{label} returned OHLC below bar low")
        if not float(bar.high) >= max(float(bar.open), float(bar.close)):
            raise ValueError(f"{label} returned OHLC above bar high")
        if float(bar.volume) < 0:
            raise ValueError(f"{label} returned negative volume")
    return closed


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
            return create_data_source(
                asset.source,
                settings=settings,
                config_dir=paths.config_dir,
            )
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
    from pa_agent.agent_runtime.production import (
        CodexProductionConfig,
        validate_codex_production_config,
    )

    codex_config = CodexProductionConfig.from_environment(state_dir=paths.state_root)
    errors.extend(
        validate_codex_production_config(codex_config, prompt_dir=paths.prompt_dir)
    )
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
    from pa_agent.agent_runtime.production import (
        CodexProductionConfig,
        build_codex_backend_factory,
    )

    pending_writer = PendingWriter(
        pending_dir=paths.records_pending_dir,
        api_key=settings.provider.api_key,
        counterfactual_horizon_bars=settings.general.counterfactual_horizon_bars,
    )
    codex_config = CodexProductionConfig.from_environment(state_dir=paths.state_root)
    codex_backend_factory = build_codex_backend_factory(
        state_dir=paths.state_root,
        prompt_dir=paths.prompt_dir,
    )
    orchestrator_factory = ProductionOrchestratorFactory(
        settings=settings,
        pending_writer=pending_writer,
        prompt_dir=paths.prompt_dir,
        experience_dir=paths.experience_dir,
        logger=logging.getLogger("pa_agent"),
        codex_backend_factory=codex_backend_factory,
    )
    handler = IndependentAnalysisHandler(orchestrator_factory)
    scheduler_audit = SchedulerAuditStore(paths.monitoring_audit_db_path)
    paper_execution = PaperExecutionCoordinator(
        paths.paper_trading_db_path,
        settings.paper_trading,
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
            config_dir=paths.config_dir,
        ),
        service_factory=service_factory,
        analysis_fingerprint=analysis_runtime_fingerprint(
            project_root=paths.project_root,
            prompt_dir=paths.prompt_dir,
        ),
        paper_execution=paper_execution,
        backend_switcher=orchestrator_factory.activate_backend_mode,
        profile_applier=orchestrator_factory.apply_analysis_profile,
        backend_runtime_identity=(
            codex_config.public_runtime_identity()
            if codex_config.enabled
            else None
        ),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PA Agent headless multi-asset monitor")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="validate deployment without network calls")
    mode.add_argument(
        "--probe-data",
        action="store_true",
        help="probe configured market data without calling the AI provider",
    )
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
    if args.probe_data:
        results = probe_market_data(settings, config_dir=paths.config_dir)
        print(json.dumps(
            [result.to_dict() for result in results],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return 0 if results and all(result.success for result in results) else 1

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
            cycle = monitor.poll_once()
            idle = monitor.wait_idle(
                analysis_batch_timeout_seconds(
                    settings.monitoring,
                    accepted_jobs=cycle.accepted,
                )
            )
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
