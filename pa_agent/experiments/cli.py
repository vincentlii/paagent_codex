"""Command-line entry points for isolated PA/VPA A/A/B/B research."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from pa_agent.config.paths import RuntimePaths, resolve_runtime_paths
from pa_agent.config.settings import load_settings
from pa_agent.experiments.backend_service import BackendAabbExperimentService
from pa_agent.experiments.continuous import ContinuousAabbMonitor, ContinuousAabbPolicy
from pa_agent.experiments.service import AabbExperimentService, AabbServiceResult
from pa_agent.experiments.store import AnalysisExperimentStore
from pa_agent.util.threading import CancelToken


def _path_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--prompt-dir", type=Path)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--pending-dir", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--paper-db", type=Path)


def _runtime_paths(args: argparse.Namespace) -> RuntimePaths:
    environ = dict(os.environ)
    if args.state_dir is not None:
        environ["PA_AGENT_HOME"] = str(args.state_dir.resolve())
    if args.prompt_dir is not None:
        environ["PA_AGENT_PROMPT_DIR"] = str(args.prompt_dir.resolve())
    return resolve_runtime_paths(environ=environ)


def _service(args: argparse.Namespace) -> tuple[AabbExperimentService, RuntimePaths]:
    paths = _runtime_paths(args)
    settings_path = (args.settings or paths.settings_json_path).resolve()
    settings = load_settings(settings_path)
    service = AabbExperimentService(
        settings=settings,
        prompt_dir=paths.prompt_dir,
        experience_dir=paths.experience_dir,
        pending_dir=(args.pending_dir or paths.records_pending_dir).resolve(),
        db_path=(args.db or paths.analysis_audit_db_path).resolve(),
        paper_db_path=(args.paper_db or paths.paper_trading_db_path).resolve(),
        logger=logging.getLogger("pa_agent.experiments"),
    )
    return service, paths


def _result_payload(result: AabbServiceResult, *, db_path: Path) -> dict:
    return {
        "experiment_id": result.experiment_id,
        "status": result.status,
        "records": len(result.records),
        "protocol": result.protocol_report,
        "external_comparison": result.external_comparison,
        "db_path": str(db_path),
        "paper_actions": [asdict(action) for action in result.paper_actions],
        "error": result.error,
        "retryable": result.retryable,
        "reused_experiment": result.reused_experiment,
    }


def single_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Execute four independent A1/A2/B1/B2 analyses over one frozen record",
    )
    parser.add_argument("source_record", type=Path)
    _path_options(parser)
    args = parser.parse_args(argv)
    service, paths = _service(args)
    prepared = service.prepare_source(args.source_record)
    result = service.run(prepared, CancelToken())
    db_path = (args.db or paths.analysis_audit_db_path).resolve()
    print(json.dumps(_result_payload(result, db_path=db_path), ensure_ascii=False, indent=2))
    return int(
        not (
            result.status == "completed"
            and bool(result.protocol_report.get("valid"))
            and result.error is None
        )
    )


def backend_main(argv: Sequence[str] | None = None) -> int:
    """Run the full Direct-versus-Codex A/A/B/B protocol over one saved record."""
    parser = argparse.ArgumentParser(
        description="Compare complete DeepSeek Direct and Codex PA+VPA backends",
    )
    parser.add_argument("source_record", type=Path)
    _path_options(parser)
    args = parser.parse_args(argv)
    paths = _runtime_paths(args)
    settings = load_settings((args.settings or paths.settings_json_path).resolve())
    settings.prompt.vpa_mode = "shadow"
    settings.prompt.vpa_enabled = True
    settings.general.higher_timeframe_enabled = True
    from pa_agent.agent_runtime.production import build_codex_experiment_backend_factory

    codex_factory, codex_runtime = build_codex_experiment_backend_factory(
        state_dir=paths.state_root,
        prompt_dir=paths.prompt_dir,
    )
    db_path = (args.db or paths.analysis_audit_db_path).resolve()
    service = BackendAabbExperimentService(
        settings=settings,
        prompt_dir=paths.prompt_dir,
        experience_dir=paths.experience_dir,
        pending_dir=(args.pending_dir or paths.records_pending_dir).resolve(),
        db_path=db_path,
        codex_backend_factory=codex_factory,
        codex_runtime_config=codex_runtime,
        logger=logging.getLogger("pa_agent.experiments.backend"),
    )
    result = service.run(service.prepare_source(args.source_record), CancelToken())
    print(
        json.dumps(
            {
                "experiment_id": result.experiment_id,
                "status": result.status,
                "records": len(result.records),
                "protocol": result.protocol_report,
                "external_comparison": result.external_comparison,
                "db_path": str(db_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return int(result.status != "completed" or not result.protocol_report.get("valid"))


def continuous_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Continuously sample eligible production Shadow records for A/A/B/B research",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="process one bounded batch and exit")
    mode.add_argument("--forever", action="store_true", help="poll until interrupted")
    _path_options(parser)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--max-records-per-cycle", type=int, default=1)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--retry-backoff-seconds", type=float, default=300.0)
    parser.add_argument("--lease-seconds", type=float, default=7200.0)
    args = parser.parse_args(argv)
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")

    service, paths = _service(args)
    records_dir = (args.pending_dir or paths.records_pending_dir).resolve()
    db_path = (args.db or paths.analysis_audit_db_path).resolve()
    monitor = ContinuousAabbMonitor(
        records_dir=records_dir,
        prepare_source=lambda path: service.prepare_source(
            path,
            production_shadow_only=True,
        ),
        execute_source=lambda source, token: service.run(
            source,
            token,
            reuse_completed=True,
        ),
        experiment_store=AnalysisExperimentStore(db_path),
        policy=ContinuousAabbPolicy(
            max_records_per_cycle=args.max_records_per_cycle,
            max_attempts=args.max_attempts,
            retry_backoff_seconds=args.retry_backoff_seconds,
            lease_seconds=args.lease_seconds,
        ),
    )
    cancel = CancelToken()

    def run_cycle() -> tuple:
        outcomes = monitor.run_cycle(cancel)
        print(
            json.dumps(
                {
                    "status": "cycle_completed",
                    "records_dir": str(records_dir),
                    "outcomes": [asdict(outcome) for outcome in outcomes],
                },
                ensure_ascii=False,
                default=str,
            ),
            flush=True,
        )
        return outcomes

    if args.once:
        outcomes = run_cycle()
        return int(any(outcome.status not in {"completed"} for outcome in outcomes))

    previous_handlers: dict[int, object] = {}

    def request_stop(_signum, _frame) -> None:
        cancel.set()

    for signal_value in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[signal_value] = signal.signal(signal_value, request_stop)
        except (OSError, ValueError):
            continue
    try:
        while not cancel.is_set():
            run_cycle()
            cancel.wait(args.poll_seconds)
    finally:
        for signal_value, handler in previous_handlers.items():
            signal.signal(signal_value, handler)
    return 0
