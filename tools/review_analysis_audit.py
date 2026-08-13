"""Review production analyses and offline PA/VPA A/A/B/B experiments."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pa_agent.config.paths import RECORDS_PENDING_DIR, SETTINGS_JSON_PATH  # noqa: E402
from pa_agent.config.settings import load_settings  # noqa: E402
from pa_agent.experiments.store import AnalysisExperimentStore  # noqa: E402
from pa_agent.records.audit_store import AnalysisAuditStore  # noqa: E402
from pa_agent.records.codec import load_record  # noqa: E402
from pa_agent.records.quality_gate import (  # noqa: E402
    QualityThresholds,
    default_quality_thresholds,
    evaluate_analysis_quality,
)
from pa_agent.records.schema import AnalysisRecord  # noqa: E402


def _db_path(path: str | None) -> Path:
    return Path(path) if path else RECORDS_PENDING_DIR.parent / "audit" / "analysis.sqlite3"


def _audit_store(path: Path, horizon_bars: int | None) -> AnalysisAuditStore:
    horizon = horizon_bars
    if horizon is None:
        horizon = load_settings(SETTINGS_JSON_PATH).general.counterfactual_horizon_bars
    return AnalysisAuditStore(path, simulation_horizon_bars=horizon)


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _load_completed_experiment_records(
    store: AnalysisExperimentStore,
    *,
    symbol: str | None,
    timeframe: str | None,
    limit_experiments: int,
    cohort_id: str | None = None,
) -> tuple[list[AnalysisRecord], dict[str, object]]:
    records: list[AnalysisRecord] = []
    invalid_json = 0
    missing_record = 0
    manifests = store.list_experiments(
        symbol=symbol,
        timeframe=timeframe,
        limit=max(1, int(limit_experiments)),
    )
    completed_all = [
        item
        for item in manifests
        if item.get("status") == "completed" and bool(item.get("protocol_valid"))
    ]
    completed_cohorts = list(
        dict.fromkeys(
            str(item.get("cohort_id") or "") for item in completed_all if item.get("cohort_id")
        )
    )
    selected_cohort = str(cohort_id or "").strip() or (
        completed_cohorts[0] if completed_cohorts else None
    )
    completed = [item for item in completed_all if item.get("cohort_id") == selected_cohort]
    for item in completed:
        experiment = store.get_experiment(str(item["experiment_id"]))
        if experiment is None:
            missing_record += 4
            continue
        for arm in experiment["arms"]:
            raw_path = str(arm.get("record_path") or "")
            path = Path(raw_path) if raw_path else None
            if path is None or not path.is_file():
                missing_record += 1
                continue
            try:
                records.append(load_record(path).record)
            except (OSError, ValueError, TypeError):
                invalid_json += 1
    return records, {
        "manifests_scanned": len(manifests),
        "completed_experiments_all_cohorts": len(completed_all),
        "completed_experiments": len(completed),
        "selected_cohort_id": selected_cohort,
        "available_completed_cohort_ids": completed_cohorts,
        "skipped_other_cohort_experiments": len(completed_all) - len(completed),
        "records_loaded": len(records),
        "missing_records": missing_record,
        "invalid_records": invalid_json,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PA Agent analysis and experiment review")
    parser.add_argument("--db", help="optional analysis.sqlite3 path")
    parser.add_argument(
        "--simulation-horizon-bars",
        type=int,
        help="override configured closed-OHLC replay horizon when rebuilding",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary = subparsers.add_parser(
        "summary",
        help="show offline A/A/B/B trade-frequency report",
    )
    counterfactual = subparsers.add_parser(
        "counterfactual",
        help="show matched closed-OHLC A/A/B/B outcomes (not live PnL)",
    )
    listing = subparsers.add_parser("list", help="list compact production and arm runs")
    experiments = subparsers.add_parser("experiments", help="list experiment manifests")
    quality = subparsers.add_parser(
        "quality",
        help="apply protocol, grounding, repeatability, and performance gates",
    )
    label = subparsers.add_parser("label", help="attach one manually reviewed outcome")
    rebuild = subparsers.add_parser(
        "rebuild",
        help="rebuild the derived run index from records/pending JSON",
    )
    for command in (summary, counterfactual, listing, experiments, quality):
        command.add_argument("--symbol")
        command.add_argument("--timeframe")
    for command in (summary, counterfactual, experiments, quality):
        command.add_argument(
            "--cohort-id",
            help="restrict review to one provider/settings/analysis cohort",
        )
    summary.add_argument("--min-snapshots", type=int, default=50)
    summary.add_argument("--max-relative-decline", type=float, default=0.15)
    counterfactual.add_argument("--min-snapshots", type=int, default=30)
    counterfactual.add_argument("--cost-per-trade-r", type=float, default=0.0)
    listing.add_argument("--limit", type=int, default=100)
    experiments.add_argument("--limit", type=int, default=100)
    quality.add_argument("--limit-experiments", type=int, default=200)
    quality.add_argument("--min-snapshots", type=int)
    quality.add_argument("--min-repeatability-snapshots", type=int)
    quality.add_argument("--max-call-p50-ms", type=float)
    quality.add_argument("--max-call-p95-ms", type=float)
    quality.add_argument("--max-run-p50-ms", type=float)
    quality.add_argument("--max-run-p95-ms", type=float)
    quality.add_argument("--max-run-p50-tokens", type=int)
    quality.add_argument("--max-run-p95-tokens", type=int)
    quality.add_argument("--max-b-latency-ratio-p95", type=float)
    quality.add_argument("--max-b-token-ratio-p95", type=float)
    quality.add_argument("--max-a-disagreement-rate", type=float)
    quality.add_argument("--max-b-disagreement-rate", type=float)
    label.add_argument("run_id")
    label.add_argument("--entry-status", required=True)
    label.add_argument("--outcome", required=True)
    label.add_argument("--horizon-bars", type=int)
    label.add_argument("--realized-r", type=float)
    label.add_argument("--mfe-r", type=float)
    label.add_argument("--mae-r", type=float)
    label.add_argument("--notes", default="")
    rebuild.add_argument("--records-dir", default=str(RECORDS_PENDING_DIR))

    args = parser.parse_args()
    path = _db_path(args.db)
    audit_store = _audit_store(path, args.simulation_horizon_bars)
    experiment_store = AnalysisExperimentStore(path)
    if args.command == "summary":
        _print(
            experiment_store.frequency_report(
                symbol=args.symbol,
                timeframe=args.timeframe,
                cohort_id=args.cohort_id,
                min_unique_snapshots=args.min_snapshots,
                max_relative_trade_rate_decline=args.max_relative_decline,
            )
        )
        return 0
    if args.command == "counterfactual":
        _print(
            experiment_store.counterfactual_report(
                symbol=args.symbol,
                timeframe=args.timeframe,
                cohort_id=args.cohort_id,
                min_decisive_snapshots=args.min_snapshots,
                cost_per_trade_r=args.cost_per_trade_r,
            )
        )
        return 0
    if args.command == "list":
        _print(
            audit_store.list_runs(
                symbol=args.symbol,
                timeframe=args.timeframe,
                limit=args.limit,
            )
        )
        return 0
    if args.command == "experiments":
        _print(
            experiment_store.list_experiments(
                symbol=args.symbol,
                timeframe=args.timeframe,
                limit=args.limit,
            )
        )
        return 0
    if args.command == "quality":
        records, load_report = _load_completed_experiment_records(
            experiment_store,
            symbol=args.symbol,
            timeframe=args.timeframe,
            limit_experiments=args.limit_experiments,
            cohort_id=args.cohort_id,
        )
        threshold_values = asdict(default_quality_thresholds())
        overrides = {
            "min_unique_snapshots": args.min_snapshots,
            "min_repeatability_snapshots": args.min_repeatability_snapshots,
            "max_call_p50_latency_ms": args.max_call_p50_ms,
            "max_call_p95_latency_ms": args.max_call_p95_ms,
            "max_run_p50_latency_ms": args.max_run_p50_ms,
            "max_run_p95_latency_ms": args.max_run_p95_ms,
            "max_run_p50_tokens": args.max_run_p50_tokens,
            "max_run_p95_tokens": args.max_run_p95_tokens,
            "max_b_to_a_latency_ratio_p95": args.max_b_latency_ratio_p95,
            "max_b_to_a_token_ratio_p95": args.max_b_token_ratio_p95,
            "max_a_disagreement_rate": args.max_a_disagreement_rate,
            "max_b_disagreement_rate": args.max_b_disagreement_rate,
        }
        threshold_values.update(
            {key: value for key, value in overrides.items() if value is not None}
        )
        thresholds = QualityThresholds(**threshold_values)
        report = evaluate_analysis_quality(records, thresholds=thresholds)
        report["record_load"] = load_report
        if load_report["missing_records"] or load_report["invalid_records"]:
            report["status"] = "fail"
            report["quality_gate_passed"] = False
        _print(report)
        if report["status"] == "pass":
            return 0
        return 2 if report["status"] == "insufficient_sample" else 1
    if args.command == "label":
        result = audit_store.record_outcome(
            args.run_id,
            entry_status=args.entry_status,
            outcome=args.outcome,
            horizon_bars=args.horizon_bars,
            realized_return_r=args.realized_r,
            mfe_r=args.mfe_r,
            mae_r=args.mae_r,
            notes=args.notes,
        )
        _print({"ok": result.ok, "error": result.error})
        return 0 if result.ok else 1
    result = audit_store.rebuild_from_records(Path(args.records_dir))
    _print({"indexed": result.indexed, "skipped": result.skipped})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
