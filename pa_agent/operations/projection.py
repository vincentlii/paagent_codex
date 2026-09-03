"""Read-only operational projections produced by the trusted headless process."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from pa_agent.execution.reporting import build_paper_report

RECENT_RUN_FIELDS = (
    "run_id",
    "timestamp_local_ms",
    "symbol",
    "timeframe",
    "run_status",
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
    "repair_count",
    "outcome",
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
        run["effective_model"] = unique_values(calls, "effective_model", "model")
        run["requested_reasoning_effort"] = unique_values(
            calls, "requested_reasoning_effort"
        )
        run["effective_reasoning_effort"] = unique_values(
            calls, "effective_reasoning_effort"
        )
        run["total_latency_ms"] = sum(
            float(item["latency_ms"])
            for item in calls
            if "latency_ms" in selected and item["latency_ms"] is not None
        )
        run["repair_count"] = sum(
            1
            for item in calls
            if "attempt" in selected
            and isinstance(item["attempt"], int)
            and item["attempt"] > 1
        )
        run["outcome"] = run.get("outcome") or run.get("terminal_outcome")


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


def build_review_projection(
    analysis_db_path: Path,
    paper_db_path: Path,
) -> dict[str, Any]:
    """Build the persisted dashboard review block without mutating either ledger."""
    paper = build_paper_report(paper_db_path)
    paper.pop("database", None)
    return {
        "recent_runs": read_recent_runs(analysis_db_path),
        "paper": paper,
    }
