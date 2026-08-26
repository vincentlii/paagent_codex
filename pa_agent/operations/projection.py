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
        return [
            {field: row[field] if field in selected else None for field in RECENT_RUN_FIELDS}
            for row in rows
        ]
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return []
    finally:
        if "connection" in locals():
            connection.close()


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
