"""Run one offline PA/VPA A/A/B/B experiment from a saved market snapshot."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pa_agent.config.paths import (  # noqa: E402
    EXPERIENCE_DIR,
    PAPER_TRADING_DB_PATH,
    PROMPT_DIR,
    RECORDS_PENDING_DIR,
    SETTINGS_JSON_PATH,
)
from pa_agent.config.settings import load_settings  # noqa: E402
from pa_agent.demo.record_loader import load_analysis_record  # noqa: E402
from pa_agent.execution.coordinator import PaperExecutionCoordinator  # noqa: E402
from pa_agent.execution.plans import instrument_spec_from_record  # noqa: E402
from pa_agent.experiments.executor import OrchestratorArmExecutor  # noqa: E402
from pa_agent.experiments.pa_vpa_aabb import (  # noqa: E402
    PaVpaAabbRunner,
    freeze_experiment_input,
)
from pa_agent.experiments.replay import frozen_input_from_record  # noqa: E402
from pa_agent.experiments.store import AnalysisExperimentStore  # noqa: E402
from pa_agent.records.audit_store import AnalysisAuditStore  # noqa: E402
from pa_agent.records.pending_writer import PendingWriter  # noqa: E402
from pa_agent.util.threading import CancelToken  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute four independent A1/A2/B1/B2 analyses over one frozen record",
    )
    parser.add_argument("source_record", type=Path)
    parser.add_argument("--settings", type=Path, default=SETTINGS_JSON_PATH)
    parser.add_argument("--pending-dir", type=Path, default=RECORDS_PENDING_DIR)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--paper-db", type=Path, default=PAPER_TRADING_DB_PATH)
    args = parser.parse_args()

    source_path = args.source_record.resolve()
    settings = load_settings(args.settings)
    source = load_analysis_record(source_path)
    frame, htf_context, catalog = frozen_input_from_record(source)
    frozen = freeze_experiment_input(
        frame,
        htf_context,
        settings,
        catalog=catalog,
    )

    db_path = args.db or args.pending_dir.parent / "audit" / "analysis.sqlite3"
    audit_store = AnalysisAuditStore(
        db_path,
        simulation_horizon_bars=settings.general.counterfactual_horizon_bars,
    )
    writer = PendingWriter(
        pending_dir=args.pending_dir,
        api_key=settings.provider.api_key,
        audit_store=audit_store,
        counterfactual_horizon_bars=settings.general.counterfactual_horizon_bars,
    )
    executor = OrchestratorArmExecutor(
        settings=settings,
        prompt_dir=PROMPT_DIR,
        experience_dir=EXPERIENCE_DIR,
        logger=logging.getLogger("pa_agent.experiments"),
    )
    result = PaVpaAabbRunner(
        execute_arm=executor,
        pending_writer=writer,
        experiment_store=AnalysisExperimentStore(db_path),
    ).run(
        frozen,
        CancelToken(),
        source_run_id=source.meta.run_id,
        source_record_path=str(source_path),
    )
    paper_actions = []
    paper_error = None
    if settings.paper_trading.enabled and result.status == "completed":
        spec = instrument_spec_from_record(source)
        if spec is None:
            paper_error = "source record has no provider-declared instrument spec"
        else:
            try:
                actions = PaperExecutionCoordinator(
                    args.paper_db,
                    settings.paper_trading,
                ).record_aabb(result, frozen, spec)
                paper_actions = [action.__dict__ for action in actions]
            except (OSError, sqlite3.Error, ValueError) as exc:
                paper_error = str(exc)
    print(
        json.dumps(
            {
                "experiment_id": result.experiment_id,
                "status": result.status,
                "records": len(result.records),
                "protocol": result.protocol_report,
                "external_comparison": result.external_comparison,
                "db_path": str(db_path),
                "paper_actions": paper_actions,
                "paper_error": paper_error,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.status == "completed" and paper_error is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
