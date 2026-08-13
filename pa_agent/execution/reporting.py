"""Read-only summaries for deterministic paper execution ledgers."""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def build_paper_report(db_path: Path) -> dict[str, Any]:
    """Return a descriptive ledger report without changing the database."""
    path = Path(db_path).expanduser().resolve()
    empty: dict[str, Any] = {
        "database": str(path),
        "available": False,
        "accounts": [],
        "decisions": {"total": 0, "by_status": {}, "by_received_path": {}},
        "research": {
            "cohorts": 0,
            "complete_aabb_cohorts": 0,
            "unit_of_analysis": "paired_market_snapshot",
            "descriptive_only": True,
        },
        "simulation": {
            "current_version": "paper_ohlc_v2",
            "by_version": [],
        },
    }
    if not path.is_file():
        return empty
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        return {**empty, "error": str(exc)}
    connection.row_factory = sqlite3.Row
    try:
        accounts = _account_rows(connection)
        decisions = _decision_summary(connection)
        research = _research_summary(connection)
        simulation = _simulation_summary(connection)
    except sqlite3.Error as exc:
        return {**empty, "error": str(exc)}
    finally:
        connection.close()
    return {
        "database": str(path),
        "available": True,
        "accounts": accounts,
        "decisions": decisions,
        "research": research,
        "simulation": simulation,
    }


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone() is not None


def _account_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(connection, "paper_accounts"):
        return []
    has_orders = _table_exists(connection, "paper_orders")
    if not has_orders:
        rows = connection.execute(
            "SELECT account_id, equity, initial_equity FROM paper_accounts "
            "ORDER BY CASE WHEN account_id = 'pa-official' THEN 0 ELSE 1 END, account_id"
        ).fetchall()
        return [{
            "account_id": row["account_id"],
            "equity": float(row["equity"]),
            "initial_equity": float(row["initial_equity"]),
            "net_change": float(row["equity"]) - float(row["initial_equity"]),
            "orders": 0,
            "pending": 0,
            "open": 0,
            "closed": 0,
            "cancelled": 0,
            "ambiguous": 0,
            "realized_pnl": 0.0,
            "fees": 0.0,
        } for row in rows]
    rows = connection.execute("""
        SELECT a.account_id, a.equity, a.initial_equity,
               COUNT(o.order_id) AS orders,
               SUM(CASE WHEN o.status = 'pending' THEN 1 ELSE 0 END) AS pending,
               SUM(CASE WHEN o.status = 'open' THEN 1 ELSE 0 END) AS open_count,
               SUM(CASE WHEN o.status = 'closed' THEN 1 ELSE 0 END) AS closed,
               SUM(CASE WHEN o.status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
               SUM(CASE WHEN o.status = 'ambiguous' THEN 1 ELSE 0 END) AS ambiguous,
               COALESCE(SUM(o.realized_pnl), 0) AS realized_pnl,
               COALESCE(SUM(o.fees), 0) AS fees
        FROM paper_accounts AS a
        LEFT JOIN paper_orders AS o ON o.account_id = a.account_id
        GROUP BY a.account_id, a.equity, a.initial_equity
        ORDER BY CASE WHEN a.account_id = 'pa-official' THEN 0 ELSE 1 END, a.account_id
    """).fetchall()
    return [{
        "account_id": row["account_id"],
        "equity": float(row["equity"]),
        "initial_equity": float(row["initial_equity"]),
        "net_change": float(row["equity"]) - float(row["initial_equity"]),
        "orders": int(row["orders"]),
        "pending": int(row["pending"]),
        "open": int(row["open_count"]),
        "closed": int(row["closed"]),
        "cancelled": int(row["cancelled"]),
        "ambiguous": int(row["ambiguous"]),
        "realized_pnl": float(row["realized_pnl"]),
        "fees": float(row["fees"]),
    } for row in rows]


def _simulation_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "current_version": "paper_ohlc_v2",
        "by_version": [],
    }
    if not _table_exists(connection, "paper_orders"):
        return summary
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(paper_orders)").fetchall()
    }
    version_expression = (
        "COALESCE(NULLIF(simulation_version, ''), 'paper_ohlc_v1')"
        if "simulation_version" in columns
        else "'paper_ohlc_v1'"
    )
    rows = connection.execute(f"""
        SELECT {version_expression} AS simulation_version,
               COUNT(*) AS orders,
               SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed,
               SUM(CASE WHEN status = 'ambiguous' THEN 1 ELSE 0 END) AS ambiguous,
               COALESCE(SUM(realized_pnl), 0) AS realized_pnl,
               COALESCE(SUM(fees), 0) AS fees
        FROM paper_orders
        GROUP BY {version_expression}
        ORDER BY simulation_version
    """).fetchall()
    summary["by_version"] = [
        {
            "simulation_version": str(row["simulation_version"]),
            "orders": int(row["orders"]),
            "closed": int(row["closed"]),
            "ambiguous": int(row["ambiguous"]),
            "realized_pnl": float(row["realized_pnl"]),
            "fees": float(row["fees"]),
        }
        for row in rows
    ]
    return summary


def _group_counts(
    connection: sqlite3.Connection,
    column: str,
) -> dict[str, int]:
    allowed = {"status", "received_path", "treatment_received"}
    if column not in allowed:
        raise ValueError("unsupported report grouping")
    rows = connection.execute(
        f"SELECT {column} AS value, COUNT(*) AS count FROM paper_decisions "
        f"GROUP BY {column} ORDER BY {column}"
    ).fetchall()
    result = {}
    for row in rows:
        value = row["value"]
        if column == "treatment_received":
            key = "unassigned" if value is None else ("true" if int(value) else "false")
        else:
            key = str(value or "unspecified")
        result[key] = int(row["count"])
    return result


def _decision_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    if not _table_exists(connection, "paper_decisions"):
        return {"total": 0, "by_status": {}, "by_received_path": {}}
    total = int(connection.execute("SELECT COUNT(*) FROM paper_decisions").fetchone()[0])
    return {
        "total": total,
        "by_status": _group_counts(connection, "status"),
        "by_received_path": _group_counts(connection, "received_path"),
        "by_treatment_received": _group_counts(connection, "treatment_received"),
    }


def _research_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "cohorts": 0,
        "complete_aabb_cohorts": 0,
        "complete_aabb_snapshots": 0,
        "unit_of_analysis": "paired_market_snapshot",
        "descriptive_only": True,
    }
    if not _table_exists(connection, "paper_decisions"):
        return summary
    summary["cohorts"] = int(connection.execute(
        "SELECT COUNT(DISTINCT cohort_id) FROM paper_decisions "
        "WHERE cohort_id IS NOT NULL AND cohort_id != ''"
    ).fetchone()[0])
    complete_groups = """
        SELECT cohort_id, market_snapshot_hash FROM paper_decisions
            WHERE cohort_id IS NOT NULL AND cohort_id != ''
              AND market_snapshot_hash IS NOT NULL AND market_snapshot_hash != ''
            GROUP BY cohort_id, market_snapshot_hash
            HAVING COUNT(*) = 4
               AND COUNT(DISTINCT arm) = 4
               AND SUM(CASE WHEN arm IN ('A1', 'A2', 'B1', 'B2') THEN 1 ELSE 0 END) = 4
    """
    summary["complete_aabb_snapshots"] = int(connection.execute(
        f"SELECT COUNT(*) FROM ({complete_groups})"
    ).fetchone()[0])
    summary["complete_aabb_cohorts"] = int(connection.execute(
        f"SELECT COUNT(DISTINCT cohort_id) FROM ({complete_groups})"
    ).fetchone()[0])
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PA Agent paper ledger report")
    parser.add_argument("--db", type=Path, help="paper SQLite path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.db is None:
        from pa_agent.config.paths import resolve_runtime_paths

        db_path = resolve_runtime_paths().paper_trading_db_path
    else:
        db_path = args.db
    report = build_paper_report(db_path)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["available"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
