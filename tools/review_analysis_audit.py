"""Review the derived PA/VPA audit index without touching source JSON records."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pa_agent.config.paths import RECORDS_PENDING_DIR, SETTINGS_JSON_PATH  # noqa: E402
from pa_agent.config.settings import load_settings  # noqa: E402
from pa_agent.records.audit_store import AnalysisAuditStore  # noqa: E402
from pa_agent.records.quality_gate import (  # noqa: E402
    QualityThresholds,
    evaluate_analysis_quality,
)
from pa_agent.records.schema import AnalysisRecord  # noqa: E402


def _store(path: str | None, horizon_bars: int | None = None) -> AnalysisAuditStore:
    db_path = Path(path) if path else RECORDS_PENDING_DIR.parent / "audit" / "analysis.sqlite3"
    horizon = horizon_bars
    if horizon is None:
        horizon = load_settings(SETTINGS_JSON_PATH).general.counterfactual_horizon_bars
    return AnalysisAuditStore(db_path, simulation_horizon_bars=horizon)


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description="PA Agent analysis audit review")
    parser.add_argument("--db", help="optional analysis.sqlite3 path")
    parser.add_argument(
        "--simulation-horizon-bars",
        type=int,
        help="override configured closed-OHLC replay horizon when rebuilding",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary = subparsers.add_parser("summary", help="show paired VPA frequency guard report")
    counterfactual = subparsers.add_parser(
        "counterfactual",
        help="show matched closed-OHLC A/B outcome report (not live PnL)",
    )
    listing = subparsers.add_parser("list", help="list compact indexed runs")
    quality = subparsers.add_parser(
        "quality",
        help="apply strict quality/SLO gates to completed full-prompt A/B records",
    )
    label = subparsers.add_parser("label", help="attach one manually reviewed outcome")
    rebuild = subparsers.add_parser("rebuild", help="rebuild the derived index from records/pending JSON")
    for command in (summary, counterfactual, listing, quality):
        command.add_argument("--symbol")
        command.add_argument("--timeframe")
    summary.add_argument("--min-snapshots", type=int, default=50)
    summary.add_argument("--max-relative-decline", type=float, default=0.15)
    counterfactual.add_argument("--min-snapshots", type=int, default=30)
    listing.add_argument("--limit", type=int, default=100)
    quality.add_argument("--records-dir", default=str(RECORDS_PENDING_DIR))
    quality.add_argument("--limit", type=int, default=200)
    quality.add_argument("--min-runs", type=int, default=20)
    quality.add_argument("--max-call-p50-ms", type=float, default=100_000)
    quality.add_argument("--max-call-p95-ms", type=float, default=150_000)
    quality.add_argument("--max-run-p50-ms", type=float, default=220_000)
    quality.add_argument("--max-run-p95-ms", type=float, default=300_000)
    quality.add_argument("--max-run-p50-tokens", type=int, default=180_000)
    quality.add_argument("--max-run-p95-tokens", type=int, default=250_000)
    quality.add_argument("--max-vpa-latency-ratio-p95", type=float, default=2.2)
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
    store = _store(args.db, args.simulation_horizon_bars)
    if args.command == "summary":
        _print(
            store.vpa_frequency_guard_report(
                symbol=args.symbol,
                timeframe=args.timeframe,
                min_unique_snapshots=args.min_snapshots,
                max_relative_trade_rate_decline=args.max_relative_decline,
            )
        )
        return 0
    if args.command == "counterfactual":
        _print(
            store.counterfactual_performance_report(
                symbol=args.symbol,
                timeframe=args.timeframe,
                min_decisive_snapshots=args.min_snapshots,
            )
        )
        return 0
    if args.command == "list":
        _print(store.list_runs(symbol=args.symbol, timeframe=args.timeframe, limit=args.limit))
        return 0
    if args.command == "quality":
        records: list[AnalysisRecord] = []
        skipped = 0
        paths = sorted(Path(args.records_dir).glob("*.json"), reverse=True)
        for path in paths:
            if len(records) >= max(1, int(args.limit)):
                break
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                raw.pop("_partial_reason", None)
                record = AnalysisRecord.model_validate(raw)
            except (OSError, ValueError, TypeError):
                skipped += 1
                continue
            sidecar = record.vpa_sidecar if isinstance(record.vpa_sidecar, dict) else {}
            paired = sidecar.get("paired_execution") if isinstance(sidecar, dict) else {}
            if not isinstance(paired, dict) or paired.get("experiment_type") != "full_prompt_ab":
                continue
            if args.symbol and record.meta.symbol != args.symbol:
                continue
            if args.timeframe and record.meta.timeframe != args.timeframe:
                continue
            records.append(record)
        thresholds = QualityThresholds(
            min_completed_runs=args.min_runs,
            max_call_p50_latency_ms=args.max_call_p50_ms,
            max_call_p95_latency_ms=args.max_call_p95_ms,
            max_run_p50_latency_ms=args.max_run_p50_ms,
            max_run_p95_latency_ms=args.max_run_p95_ms,
            max_run_p50_tokens=args.max_run_p50_tokens,
            max_run_p95_tokens=args.max_run_p95_tokens,
            max_vpa_to_baseline_latency_ratio_p95=args.max_vpa_latency_ratio_p95,
        )
        report = evaluate_analysis_quality(records, thresholds=thresholds)
        report["record_load"] = {"loaded": len(records), "skipped_invalid": skipped}
        _print(report)
        if report["status"] == "pass":
            return 0
        return 2 if report["status"] == "insufficient_sample" else 1
    if args.command == "label":
        result = store.record_outcome(
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
    result = store.rebuild_from_records(Path(args.records_dir))
    _print({"indexed": result.indexed, "skipped": result.skipped})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
