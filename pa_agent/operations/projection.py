"""Read-only operational projections produced by the trusted headless process."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from pa_agent.execution.reporting import build_paper_report
from pa_agent.experiments.backend_aabb import BACKEND_AABB_PROTOCOL_VERSION
from pa_agent.operations.trading import build_trading_projection

RECENT_RUN_FIELDS = (
    "run_id",
    "timestamp_local_ms",
    "symbol",
    "timeframe",
    "analysis_pipeline",
    "run_status",
    "error_type",
    "stage1_cycle_position",
    "stage1_direction",
    "stage1_gate_result",
    "order_type",
    "order_direction",
    "trade_confidence",
    "terminal_outcome",
    "vpa_data_status",
    "vpa_background_relation",
    "vpa_timing_relation",
    "vpa_effect",
    "analysis_backend",
    "requested_model",
    "effective_model",
    "requested_reasoning_effort",
    "effective_reasoning_effort",
    "total_latency_ms",
    "total_tokens",
    "initial_context_tokens",
    "retrieval_context_tokens",
    "evidence_call_count",
    "evidence_denied_count",
    "evidence_latency_ms",
    "repair_count",
    "outcome",
    "first_pass_clean",
    "metric_statuses",
    "analysis_to_persist_ms",
    "decision_kind",
)


def read_recent_runs(db_path: Path, *, limit: int = 30) -> list[dict[str, Any]]:
    """Read the review index in SQLite read-only mode without schema migration."""
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        return []
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        connection.row_factory = sqlite3.Row
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'analysis_runs'"
        ).fetchone()
        if table is None:
            return []
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(analysis_runs)").fetchall()
        }
        selected = [field for field in RECENT_RUN_FIELDS if field in columns]
        if "review_context_json" in columns:
            selected.append("review_context_json")
        if "timestamp_local_ms" not in selected:
            return []
        rows = connection.execute(
            f"SELECT {', '.join(selected)} FROM analysis_runs "
            "ORDER BY timestamp_local_ms DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        projected = [
            {field: row[field] if field in selected else None for field in RECENT_RUN_FIELDS}
            for row in rows
        ]
        _enrich_realized_outcomes(connection, projected)
        _enrich_call_summaries(connection, projected)
        for row, run in zip(rows, projected):
            _enrich_operational_quality(
                run, row["review_context_json"] if "review_context_json" in selected else None,
            )
        return projected
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return []
    finally:
        if "connection" in locals():
            connection.close()


def _enrich_call_summaries(
    connection: sqlite3.Connection,
    runs: list[dict[str, Any]],
) -> None:
    """Add bounded call-ledger summaries without loading raw model content."""
    if not runs:
        return
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'analysis_calls'"
    ).fetchone()
    if table is None:
        return
    available = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(analysis_calls)").fetchall()
    }
    wanted = (
        "run_id",
        "analysis_backend",
        "model",
        "requested_model",
        "effective_model",
        "requested_reasoning_effort",
        "effective_reasoning_effort",
        "latency_ms",
        "total_tokens",
        "initial_context_tokens",
        "retrieval_context_tokens",
        "evidence_call_count",
        "evidence_denied_count",
        "evidence_latency_ms",
        "attempt",
        "created_at_ms",
    )
    selected = [field for field in wanted if field in available]
    if "run_id" not in selected:
        return
    run_ids = [str(run["run_id"]) for run in runs if run.get("run_id")]
    if not run_ids:
        return
    placeholders = ",".join("?" for _ in run_ids)
    order = " ORDER BY created_at_ms, attempt" if "created_at_ms" in selected else ""
    rows = connection.execute(
        f"SELECT {', '.join(selected)} FROM analysis_calls "
        f"WHERE run_id IN ({placeholders}){order}",
        tuple(run_ids),
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(str(row["run_id"]), []).append(row)

    def unique_values(items: list[sqlite3.Row], *keys: str) -> str | None:
        values: list[str] = []
        for item in items:
            value = next(
                (
                    str(item[key]).strip()
                    for key in keys
                    if key in selected and item[key] is not None and str(item[key]).strip()
                ),
                "",
            )
            if value and value not in values:
                values.append(value)
        return " → ".join(values) or None

    for run in runs:
        calls = grouped.get(str(run.get("run_id")), [])
        if not calls:
            run["outcome"] = run.get("outcome") or run.get("terminal_outcome")
            continue
        run["analysis_backend"] = unique_values(calls, "analysis_backend")
        run["requested_model"] = unique_values(calls, "requested_model", "model")
        run["effective_model"] = unique_values(calls, "effective_model")
        run["requested_reasoning_effort"] = unique_values(
            calls, "requested_reasoning_effort"
        )
        run["effective_reasoning_effort"] = unique_values(
            calls, "effective_reasoning_effort"
        )
        latencies = [item["latency_ms"] for item in calls] if "latency_ms" in selected else []
        run["total_latency_ms"] = (
            sum(float(value) for value in latencies)
            if latencies and all(value is not None for value in latencies) else None
        )
        for key in (
            "total_tokens",
            "initial_context_tokens",
            "retrieval_context_tokens",
            "evidence_call_count",
            "evidence_denied_count",
            "evidence_latency_ms",
        ):
            # Legacy ledger columns default to zero even when not reported.
            # Only versioned metric coverage below can establish a known total.
            run[key] = None
        run["repair_count"] = sum(
            1
            for item in calls
            if "attempt" in selected
            and isinstance(item["attempt"], int)
            and item["attempt"] > 1
        )
        run["outcome"] = run.get("outcome") or run.get("terminal_outcome")


def _enrich_operational_quality(run: dict[str, Any], review_json: str | None) -> None:
    """Expose only canonical derived quality/measurements, never review prose."""
    run["metric_statuses"] = {}
    order_type = str(run.get("order_type") or "")
    run["decision_kind"] = (
        "no_trade" if order_type in {"不下单", "no_trade"} else
        "trade_candidate" if order_type in {"市价单", "限价单", "突破单", "market", "limit", "breakout"}
        else "unknown"
    )
    try:
        review = json.loads(review_json) if review_json else {}
        quality = review.get("operational_quality") or {}
        failure = review.get("runtime_failure") or {}
        if failure.get("schema_version") == 1:
            from pa_agent.ai.provider_failures import runtime_failure_summary
            safe_failure = runtime_failure_summary({"type": "provider_error",
                "failure_kind": failure.get("failure_kind"), "stage": failure.get("stage"),
                "retryable": failure.get("retryable"), "attempts": [{
                    "worker_diagnostic": failure.get("diagnostic"),
                    "model_call_started": failure.get("model_call_started"),
                }]})
            run["failure_label"] = safe_failure.get("label")
            run["failure_stage"] = safe_failure.get("stage")
        first_pass = (quality.get("first_pass") or {}).get("clean")
        metrics = quality.get("metrics") or {}
    except (TypeError, ValueError, AttributeError):
        first_pass, metrics = None, {}
    run["first_pass_clean"] = first_pass if type(first_pass) is bool else (
        False if run.get("run_status") in {"failed", "partial", "degraded"}
        or (run.get("repair_count") or 0) > 0 else None
    )
    if metrics.get("protocol_version") != "analysis_metrics_v1":
        return
    calls = int(metrics.get("calls") or 0)
    values = metrics.get("values") or {}
    for key in (
        "total_tokens", "initial_context_tokens", "retrieval_context_tokens",
        "evidence_call_count", "evidence_denied_count", "evidence_latency_ms",
    ):
        value = values.get(key) or {}
        actual = int(value.get("actual_count") or 0)
        estimated = int(value.get("estimated_count") or 0)
        if key == "total_tokens" and metrics.get("prior_attempt_usage_known") is False:
            run[key], status = None, "unknown"
        elif calls > 0 and value.get("complete_actual") and value.get("actual_sum") is not None:
            run[key], status = value["actual_sum"], "actual"
        elif calls > 0 and actual + estimated == calls and estimated > 0 and value.get("estimated_sum") is not None:
            run[key] = (value.get("actual_sum") or 0) + (value.get("estimated_sum") or 0)
            status = "estimated"
        else:
            run[key], status = None, "unknown"
        run["metric_statuses"][key] = status
    host_wall = metrics.get("analysis_to_persist_ms") or {}
    if host_wall.get("status") == "actual":
        run["analysis_to_persist_ms"] = host_wall.get("value")


def _enrich_realized_outcomes(
    connection: sqlite3.Connection,
    runs: list[dict[str, Any]],
) -> None:
    """Prefer the separately labelled realized outcome when it exists."""
    if not runs:
        return
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'analysis_outcomes'"
    ).fetchone()
    if table is None:
        return
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(analysis_outcomes)").fetchall()
    }
    if not {"run_id", "outcome"}.issubset(columns):
        return
    run_ids = [str(run["run_id"]) for run in runs if run.get("run_id")]
    if not run_ids:
        return
    placeholders = ",".join("?" for _ in run_ids)
    rows = connection.execute(
        f"SELECT run_id, outcome FROM analysis_outcomes "
        f"WHERE run_id IN ({placeholders})",
        tuple(run_ids),
    ).fetchall()
    outcomes = {str(row["run_id"]): row["outcome"] for row in rows}
    for run in runs:
        run["outcome"] = outcomes.get(str(run.get("run_id"))) or run.get(
            "terminal_outcome"
        )


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def summarize_recent_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize operational quality and cost without claiming trading edge."""
    grouped: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for run in runs:
        backend = str(run.get("analysis_backend") or "unknown")
        pipeline = run.get("analysis_pipeline")
        grouped.setdefault((backend, str(pipeline) if pipeline else None), []).append(run)
    summaries: list[dict[str, Any]] = []
    for (backend, pipeline), items in sorted(
        grouped.items(), key=lambda entry: (entry[0][0], entry[0][1] or "")
    ):
        latencies = [
            float(item["total_latency_ms"])
            for item in items
            if item.get("total_latency_ms") is not None
        ]
        total_tokens = [
            float(item["total_tokens"])
            for item in items
            if item.get("total_tokens") is not None
        ]
        initial_tokens = [
            float(item["initial_context_tokens"])
            for item in items
            if item.get("initial_context_tokens") is not None
        ]
        retrieval_tokens = [
            float(item["retrieval_context_tokens"])
            for item in items
            if item.get("retrieval_context_tokens") is not None
        ]
        evidence_calls = (
            sum(int(item["evidence_call_count"]) for item in items)
            if all(item.get("evidence_call_count") is not None for item in items) else None
        )
        evidence_denied = (
            sum(int(item["evidence_denied_count"]) for item in items)
            if all(item.get("evidence_denied_count") is not None for item in items) else None
        )
        completed = sum(item.get("run_status") == "completed" for item in items)
        first_pass = [item.get("first_pass_clean") for item in items
                      if type(item.get("first_pass_clean")) is bool]

        def mean(values: list[float]) -> float | None:
            return sum(values) / len(values) if values else None

        summaries.append(
            {
                "backend": backend,
                "analysis_pipeline": pipeline,
                "run_count": len(items),
                "operational_success_rate": completed / len(items),
                "first_pass_rate": sum(first_pass) / len(first_pass) if first_pass else None,
                "first_pass_known_count": len(first_pass),
                "latency_known_count": len(latencies),
                "tokens_known_count": len(total_tokens),
                "latency_p50_ms": _nearest_rank(latencies, 0.50),
                "latency_p95_ms": _nearest_rank(latencies, 0.95),
                "mean_total_tokens": mean(total_tokens),
                "mean_initial_context_tokens": mean(initial_tokens),
                "mean_retrieval_context_tokens": mean(retrieval_tokens),
                "evidence_call_count": evidence_calls,
                "evidence_denied_count": evidence_denied,
            }
        )
    return summaries


def read_backend_experiment_summary(db_path: Path) -> dict[str, Any]:
    """Read a bounded Backend A/A/B/B status summary without migrating the DB."""
    empty = {
        "available": False,
        "protocol_version": BACKEND_AABB_PROTOCOL_VERSION,
        "experiments": 0,
        "completed_experiments": 0,
        "valid_experiments": 0,
        "unique_snapshots": 0,
        "cohort_count": 0,
        "assigned_arms": 0,
        "persisted_arms": 0,
        "failed_arms": 0,
        "arm_coverage": None,
    }
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        return empty
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        experiment_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'analysis_experiments'"
        ).fetchone()
        arm_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'experiment_arms'"
        ).fetchone()
        if experiment_table is None or arm_table is None:
            return empty
        experiment_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(analysis_experiments)"
            ).fetchall()
        }
        arm_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(experiment_arms)").fetchall()
        }
        required_experiment = {
            "experiment_id",
            "protocol_version",
            "status",
            "market_snapshot_hash",
            "cohort_id",
            "protocol_valid",
        }
        required_arm = {"experiment_id", "persisted", "run_status"}
        if not required_experiment.issubset(experiment_columns) or not required_arm.issubset(
            arm_columns
        ):
            return empty
        experiment_row = connection.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN protocol_valid = 1 THEN 1 ELSE 0 END), "
            "COUNT(DISTINCT market_snapshot_hash), COUNT(DISTINCT cohort_id) "
            "FROM analysis_experiments WHERE protocol_version = ?",
            (BACKEND_AABB_PROTOCOL_VERSION,),
        ).fetchone()
        arm_row = connection.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN a.persisted = 1 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN a.run_status = 'failed' THEN 1 ELSE 0 END) "
            "FROM experiment_arms a JOIN analysis_experiments e "
            "ON e.experiment_id = a.experiment_id WHERE e.protocol_version = ?",
            (BACKEND_AABB_PROTOCOL_VERSION,),
        ).fetchone()
        experiments = int(experiment_row[0] or 0)
        assigned = int(arm_row[0] or 0)
        persisted = int(arm_row[1] or 0)
        return {
            "available": True,
            "protocol_version": BACKEND_AABB_PROTOCOL_VERSION,
            "experiments": experiments,
            "completed_experiments": int(experiment_row[1] or 0),
            "valid_experiments": int(experiment_row[2] or 0),
            "unique_snapshots": int(experiment_row[3] or 0),
            "cohort_count": int(experiment_row[4] or 0),
            "assigned_arms": assigned,
            "persisted_arms": persisted,
            "failed_arms": int(arm_row[2] or 0),
            "arm_coverage": round(persisted / assigned, 4) if assigned else None,
        }
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return empty
    finally:
        if "connection" in locals():
            connection.close()


def build_review_projection(
    analysis_db_path: Path,
    paper_db_path: Path,
    *,
    monitoring_db_path: Path | None = None,
    codex_circuit_path: Path | None = None,
    codex_worker_status_path: Path | None = None,
) -> dict[str, Any]:
    """Build the persisted dashboard review block without mutating either ledger."""
    paper = build_paper_report(paper_db_path)
    paper.pop("database", None)
    codex: dict[str, Any] = {}
    if codex_circuit_path is not None and Path(codex_circuit_path).is_file():
        try:
            raw = json.loads(Path(codex_circuit_path).read_text(encoding="utf-8"))
            codex = {
                key: raw[key]
                for key in (
                    "failures",
                    "opened_until",
                    "cooldown_level",
                    "probe_in_flight",
                    "auth_locked",
                )
                if key in raw
            }
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            codex = {"state_unreadable": True}
    if codex_worker_status_path is not None and Path(codex_worker_status_path).is_file():
        try:
            worker = json.loads(Path(codex_worker_status_path).read_text(encoding="utf-8"))
            codex.update(
                {
                    key: worker[key]
                    for key in (
                        "updated_at_epoch",
                        "service_state",
                        "auth_state",
                        "runtime_version",
                        "runtime_asset_digest",
                        "codex_binary_digest",
                    )
                    if key in worker
                }
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            codex["worker_status_unreadable"] = True
    recent_runs = read_recent_runs(analysis_db_path, limit=200)
    projection = {
        "recent_runs": recent_runs[:30],
        "analysis_summary": summarize_recent_runs(recent_runs),
        "backend_experiments": read_backend_experiment_summary(analysis_db_path),
        "paper": paper,
        "codex": codex,
    }
    if monitoring_db_path is not None:
        projection["trading"] = build_trading_projection(
            analysis_db_path, monitoring_db_path, paper_db_path,
        )
        projection["trading"]["execution_versions"] = {
            "paper_latency_v3": build_trading_projection(
                analysis_db_path, monitoring_db_path, paper_db_path,
                simulation_version="paper_latency_v3",
            ),
        }
        by_run = {item["run_id"]: item for item in projection["trading"]["analyses"]}
        for run in projection["recent_runs"]:
            disposition = by_run.get(run["run_id"], {})
            for key in ("paper_status", "paper_reason", "paper_order_id"):
                run[key] = disposition.get(key)
    return projection
