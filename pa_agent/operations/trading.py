"""Bounded, read-only trader projection. Never interprets or executes a trade."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pa_agent.operations.analysis_profile import MARKET_SYMBOLS

HOUR_MS = 3_600_000
ACCOUNT_ID = "pipeline-pa_vpa_joint"
PIPELINE = "pa_vpa_joint"
# A fixed reporting stratum, not whichever default the Broker uses in the future.
SIMULATION_VERSION = "paper_latency_v3"
ANALYSIS_FIELDS = (
    "tasks",
    "completed",
    "failed",
    "cancelled",
    "outstanding",
    "protocol_unknown",
    "joint_completed",
    "model_no_trade",
    "candidates",
    "decision_unknown",
    "last_analysis_at_ms",
    "mean_execution_ms",
)
PAPER_FIELDS = (
    "orders",
    "filled",
    "closed",
    "pnl_known",
    "wins",
    "losses",
    "breakeven",
    "pending",
    "open",
    "ambiguous",
    "cancelled_orders",
    "realized_pnl",
    "win_rate",
    "paper_submitted",
    "paper_no_trade",
    "paper_rejected",
)
ORDER_FIELDS = (
    "order_id",
    "run_id",
    "symbol",
    "instrument_type",
    "side",
    "order_type",
    "status",
    "entry_price",
    "fill_price",
    "stop_price",
    "take_profit_price",
    "quantity",
    "available_at_ms",
    "expires_at_ms",
    "fill_bar_ts",
    "exit_bar_ts",
    "exit_price",
    "exit_reason",
    "realized_pnl",
    "fees",
    "unrealized_pnl",
    "mark_status",
    "mark_close",
    "mark_closed_at_ms",
)
REVIEW_FIELDS = (
    "reasoning",
    "key_factors",
    "watch_points",
    "risk_assessment",
    "entry_rule",
    "invalidation_condition",
    "vpa_summary",
    "vpa_background",
    "vpa_timing",
    "vpa_observations",
    "vpa_confirmation",
    "vpa_data_status",
)
RUN_FIELDS = (
    "task_id",
    "run_id",
    "symbol",
    "created_at_ms",
    "completed_at_ms",
    "success",
    "status",
    "pipeline",
    "cycle",
    "direction",
    "gate",
    "order_type",
    "model",
    "confidence",
)
_PERPETUAL_TYPES = "('perpetual','tradifi_perpetual')"
_MARKET_FILTER = (
    f"provider = 'binance_usdm' AND venue = 'BINANCE' AND instrument_type IN {_PERPETUAL_TYPES} "
    "AND timeframe = '1h' AND symbol IN ('XAUUSDT','BTCUSDT','ETHUSDT')"
)
_ORDER_FILTER = f"account_id = '{ACCOUNT_ID}' AND arm = '{PIPELINE}' AND {_MARKET_FILTER}"
_TASK_FILTER = (
    "m.source = 'binance_usdm' AND m.venue = 'BINANCE' AND m.timeframe = '1h' "
    "AND m.symbol IN ('XAUUSDT','BTCUSDT','ETHUSDT') "
    "AND (m.first_submission_status = 'accepted' OR m.submission_status = 'accepted' "
    "OR m.completion_status IS NOT NULL "
    "OR EXISTS (SELECT 1 FROM monitoring_frozen_tasks f WHERE f.task_id = m.task_id))"
)
_KNOWN_PNL = "status = 'closed' AND realized_pnl BETWEEN -1e308 AND 1e308"
_PERIODS = {"all": None, "7d": 7, "30d": 30}


def _uri(path: Path) -> str:
    return Path(path).resolve().as_uri() + "?mode=ro"


@contextmanager
def _reader(path: Path) -> Iterator[sqlite3.Connection]:
    if not Path(path).is_file():
        raise OSError("ledger_unavailable")
    connection = sqlite3.connect(_uri(path), uri=True, timeout=0.25)
    connection.row_factory = sqlite3.Row
    deadline = time.monotonic() + 1.0
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def _finite(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, list):
        value = "\n".join(item for item in value[:12] if isinstance(item, str))
    return value[:2400] if isinstance(value, str) else None


def _review(raw: Any) -> dict[str, Any]:
    try:
        context = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}
    if not isinstance(context, dict):
        return {}
    decision = context.get("decision_review")
    decision = decision if isinstance(decision, dict) else {}
    result = {key: _text(decision.get(key)) for key in REVIEW_FIELDS[:6]}
    vpa = context.get("vpa_evidence")
    vpa = vpa if isinstance(vpa, dict) else {}
    result.update(
        {
            "vpa_summary": _text(vpa.get("summary")),
            "vpa_background": _text(vpa.get("background_relation")),
            "vpa_timing": _text(vpa.get("entry_timing_relation")),
            "vpa_data_status": _text(vpa.get("data_status")),
        }
    )
    observations = vpa.get("observations")
    lines = []
    for item in observations[:8] if isinstance(observations, list) else []:
        if not isinstance(item, dict):
            continue
        refs = item.get("evidence_ids") or []
        refs = refs if isinstance(refs, list) else []
        text = _text(item.get("interpretation"))
        if text:
            automatic = item.get("auto_bound_evidence_ids")
            auto_refs = (
                ", ".join(ref for ref in automatic[:8] if isinstance(ref, str))
                if isinstance(automatic, list)
                else ""
            )
            lines.append(
                f"{item.get('bar_range', '')} [{item.get('epistemic_status', 'unknown')}] "
                f"{', '.join(ref for ref in refs[:8] if isinstance(ref, str))}"
                f"{f' [自动绑定: {auto_refs}]' if auto_refs else ''}: {text}"
            )
    result["vpa_observations"] = _text(lines)
    confirmation = vpa.get("next_confirmation")
    if isinstance(confirmation, dict):
        result["vpa_confirmation"] = _text(confirmation.get("trigger"))
    return result


def _analysis(
    connection: sqlite3.Connection, periods: dict[str, Any], now_ms: int
) -> list[dict[str, Any]]:
    join = (
        " FROM monitoring_jobs m LEFT JOIN analysis.analysis_runs r ON r.run_id = m.run_id "
        "AND r.symbol = m.symbol AND r.timeframe = m.timeframe AND r.source_provider = m.source "
        f"AND r.venue = m.venue AND r.resolved_symbol = m.symbol AND r.instrument_type IN {_PERPETUAL_TYPES} "
    )
    for period in periods.values():
        rows = connection.execute(
            "SELECT m.symbol, COUNT(*) AS tasks, "
            "SUM(m.completion_status = 'completed' AND m.analysis_success = 1) AS completed, "
            "SUM(m.completion_status IS NOT NULL AND m.completion_status != 'cancelled' "
            "AND COALESCE(m.analysis_success,0) != 1) AS failed, "
            "SUM(m.completion_status = 'cancelled') AS cancelled, "
            "SUM(m.completion_status IS NULL) AS outstanding, "
            "SUM(r.analysis_pipeline IS NULL) AS protocol_unknown, "
            "SUM(m.analysis_success = 1 AND r.analysis_pipeline = :pipeline) AS joint_completed, "
            "SUM(m.analysis_success = 1 AND r.analysis_pipeline = :pipeline "
            "AND r.order_type IN ('不下单','no_trade')) AS model_no_trade, "
            "SUM(m.analysis_success = 1 AND r.analysis_pipeline = :pipeline "
            "AND r.order_type IN ('市价单','限价单','突破单','market','limit','breakout')) AS candidates, "
            "MAX(m.completed_at_ms) AS last_analysis_at_ms, "
            "AVG(CASE WHEN m.analysis_success = 1 THEN m.execution_latency_ms END) AS mean_execution_ms "
            + join
            + f"WHERE {_TASK_FILTER} AND m.task_created_at_ms BETWEEN :start AND :now GROUP BY m.symbol",
            {"pipeline": PIPELINE, "start": period["start_ms"], "now": now_ms},
        ).fetchall()
        by_symbol = {row["symbol"]: row for row in rows}
        for asset in period["assets"]:
            row = by_symbol.get(asset["symbol"])
            for key in ANALYSIS_FIELDS[:-2]:
                asset[key] = (
                    int(row[key] or 0) if row is not None and key != "decision_unknown" else 0
                )
            asset["decision_unknown"] = (
                asset["joint_completed"] - asset["candidates"] - asset["model_no_trade"]
            )
            asset["last_analysis_at_ms"] = row["last_analysis_at_ms"] if row else None
            asset["mean_execution_ms"] = _finite(row["mean_execution_ms"]) if row else None
    rows = connection.execute(
        "SELECT m.task_id, m.run_id, m.symbol, m.task_created_at_ms AS created_at_ms, "
        "m.completed_at_ms, m.analysis_success AS success, m.analysis_status AS status, "
        "r.analysis_pipeline AS pipeline, r.stage1_cycle_position AS cycle, "
        "r.stage1_direction AS direction, r.stage1_gate_result AS gate, r.order_type, "
        "r.model_name AS model, r.trade_confidence AS confidence, "
        "CASE WHEN length(r.review_context_json) <= 1000000 THEN r.review_context_json END AS review_json "
        + join
        + f"WHERE {_TASK_FILTER} AND m.task_created_at_ms <= ? "
        "ORDER BY m.task_created_at_ms DESC LIMIT 30",
        (now_ms,),
    ).fetchall()
    return [
        {**{key: row[key] for key in RUN_FIELDS}, "review": _review(row["review_json"])}
        for row in rows
    ]


def _mark(connection: sqlite3.Connection, order: sqlite3.Row, now_ms: int) -> dict[str, Any]:
    empty = {
        "unrealized_pnl": None,
        "mark_status": "not_open",
        "mark_close": None,
        "mark_closed_at_ms": None,
    }
    if order["status"] != "open":
        return empty
    fill = _finite(order["fill_price"])
    if fill is None or order["fill_bar_ts"] is None or order["side"] not in {"long", "short"}:
        return {**empty, "mark_status": "missing_fill"}
    bar = connection.execute(
        "SELECT ts_open, close FROM paper_market_bars WHERE provider = ? AND venue = ? "
        "AND instrument_type = ? AND symbol = ? AND timeframe = ? AND ts_open BETWEEN ? AND ? "
        "ORDER BY ts_open DESC LIMIT 1",
        (
            *(
                order[key]
                for key in ("provider", "venue", "instrument_type", "symbol", "timeframe")
            ),
            order["fill_bar_ts"],
            now_ms - HOUR_MS,
        ),
    ).fetchone()
    if bar is None or _finite(bar["close"]) is None:
        return {**empty, "mark_status": "missing_price"}
    closed_at = int(bar["ts_open"]) + HOUR_MS
    if order["evaluated_through_ts"] is None or int(order["evaluated_through_ts"]) < int(
        bar["ts_open"]
    ):
        return {
            **empty,
            "mark_status": "execution_lag",
            "mark_close": float(bar["close"]),
            "mark_closed_at_ms": closed_at,
        }
    stale = now_ms - closed_at > 2 * HOUR_MS
    return {
        "mark_status": "stale" if stale else "closed_bar",
        "mark_close": float(bar["close"]),
        "mark_closed_at_ms": closed_at,
        "unrealized_pnl": None
        if stale
        else _finite(
            (1 if order["side"] == "long" else -1)
            * (float(bar["close"]) - fill)
            * float(order["quantity"])
            * float(order["contract_multiplier"])
        ),
    }


def _paper(
    connection: sqlite3.Connection, periods: dict[str, Any], now_ms: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    version = SIMULATION_VERSION
    scoped = f"{_ORDER_FILTER} AND simulation_version = :version"
    for period in periods.values():
        params = {"version": version, "start": period["start_ms"], "now": now_ms}
        rows = connection.execute(
            "SELECT symbol, "
            "SUM(COALESCE(available_at_ms,created_bar_ts) >= :start) AS orders, "
            "SUM(COALESCE(available_at_ms,created_bar_ts) >= :start AND fill_price IS NOT NULL "
            "AND fill_bar_ts IS NOT NULL AND status IN ('open','closed','ambiguous')) AS filled, "
            "SUM(status = 'pending') AS pending, SUM(status = 'open') AS open, "
            "SUM(status = 'ambiguous') AS ambiguous, "
            "SUM(status = 'cancelled' AND COALESCE(available_at_ms,created_bar_ts) >= :start) AS cancelled_orders, "
            "SUM(status = 'closed' AND exit_bar_ts BETWEEN :start AND :now) AS closed, "
            f"SUM({_KNOWN_PNL} AND exit_bar_ts BETWEEN :start AND :now) AS pnl_known, "
            f"SUM({_KNOWN_PNL} AND exit_bar_ts BETWEEN :start AND :now AND realized_pnl > 0) AS wins, "
            f"SUM({_KNOWN_PNL} AND exit_bar_ts BETWEEN :start AND :now AND realized_pnl < 0) AS losses, "
            f"SUM({_KNOWN_PNL} AND exit_bar_ts BETWEEN :start AND :now AND realized_pnl = 0) AS breakeven, "
            f"SUM(CASE WHEN {_KNOWN_PNL} AND exit_bar_ts BETWEEN :start AND :now THEN realized_pnl ELSE 0 END) AS realized_pnl "
            f"FROM paper_orders WHERE {scoped} AND COALESCE(available_at_ms,created_bar_ts) <= :now GROUP BY symbol",
            params,
        ).fetchall()
        by_symbol = {row["symbol"]: row for row in rows}
        for asset in period["assets"]:
            row = by_symbol.get(asset["symbol"])
            for key in PAPER_FIELDS[:-3]:
                if key != "win_rate":
                    asset[key] = (row[key] or 0) if row else 0
            asset["win_rate"] = asset["wins"] / asset["pnl_known"] if asset["pnl_known"] else None
            asset["realized_pnl"] = _finite(asset["realized_pnl"])
        # Same exit-bar bucket is aggregated before accumulation: no invented intrabar ordering.
        period["curve"] = [
            dict(row)
            for row in reversed(
                connection.execute(
                    "SELECT bar_ts, SUM(net) OVER (ORDER BY bar_ts) AS pnl FROM ("
                    "SELECT (exit_bar_ts / 86400000) * 86400000 AS bar_ts, SUM(realized_pnl) AS net "
                    f"FROM paper_orders WHERE {scoped} AND {_KNOWN_PNL} "
                    "AND exit_bar_ts BETWEEN :start AND :now GROUP BY bar_ts) ORDER BY bar_ts DESC LIMIT 180",
                    params,
                ).fetchall()
            )
        ]
    account = connection.execute(
        "SELECT equity, initial_equity FROM paper_accounts WHERE account_id = ?", (ACCOUNT_ID,)
    ).fetchone()
    totals = connection.execute(
        f"SELECT COUNT(*) AS total, SUM({scoped}) AS scoped FROM paper_orders WHERE account_id = :account",
        {"account": ACCOUNT_ID, "version": version},
    ).fetchone()
    active = connection.execute(
        "SELECT status, SUM(ABS(COALESCE(fill_price,entry_price)-stop_price)*quantity*contract_multiplier) AS risk "
        "FROM paper_orders WHERE account_id = ? AND status IN ('open','pending') GROUP BY status",
        (ACCOUNT_ID,),
    ).fetchall()
    risks = {row["status"]: _finite(row["risk"]) for row in active}
    opens = connection.execute(
        f"SELECT * FROM paper_orders WHERE {scoped} AND status = 'open'", {"version": version}
    ).fetchall()
    marks = {row["order_id"]: _mark(connection, row, now_ms) for row in opens}
    known = [
        mark["unrealized_pnl"] for mark in marks.values() if mark["unrealized_pnl"] is not None
    ]
    account_view = {
        "settled_balance": _finite(account["equity"]) if account else None,
        "initial_balance": _finite(account["initial_equity"]) if account else None,
        "outside_scope_orders": int(totals["total"] or 0) - int(totals["scoped"] or 0),
        "open_risk": risks.get("open", 0.0),
        "pending_risk": risks.get("pending", 0.0),
        "open_count": len(opens),
        "marks_known": len(known),
        "unrealized_pnl": _finite(sum(known)) if len(known) == len(opens) else None,
    }
    rows = connection.execute(
        "SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol,status "
        "ORDER BY COALESCE(available_at_ms,created_bar_ts) DESC,order_id) AS rank "
        f"FROM paper_orders WHERE {scoped} AND COALESCE(available_at_ms,created_bar_ts) <= :now) "
        "WHERE rank <= 10 ORDER BY COALESCE(available_at_ms,created_bar_ts) DESC,order_id",
        {"version": version, "now": now_ms},
    ).fetchall()
    orders = []
    for row in rows:
        stored = dict(row)
        order = {key: stored.get(key) for key in ORDER_FIELDS}
        order.update(
            marks.get(row["order_id"], {"unrealized_pnl": None, "mark_status": "not_open"})
        )
        if row["status"] != "closed":
            order["realized_pnl"] = None
        orders.append(order)
    return account_view, orders


def build_trading_projection(
    analysis_db: Path, monitoring_db: Path, paper_db: Path, *, now_ms: int | None = None
) -> dict[str, Any]:
    """Aggregate full ledgers; bound journals/prose and SQL work, not total counts."""
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    periods = {
        key: {
            "start_ms": now_ms - days * 24 * HOUR_MS if days else 0,
            "assets": [
                {"symbol": symbol, **dict.fromkeys(ANALYSIS_FIELDS + PAPER_FIELDS)}
                for symbol in MARKET_SYMBOLS
            ],
            "curve": [],
        }
        for key, days in _PERIODS.items()
    }
    result: dict[str, Any] = {
        "schema_version": 1,
        "generated_at_ms": now_ms,
        "scope": {
            "account_id": ACCOUNT_ID,
            "pipeline": PIPELINE,
            "simulation_version": SIMULATION_VERSION,
            "provider": "binance_usdm",
            "venue": "BINANCE",
            "timeframe": "1h",
            "instrument_type": "perpetual / tradifi_perpetual",
        },
        "analysis_available": False,
        "paper_available": False,
        "decisions_available": False,
        "periods": periods,
        "account": {},
        "orders": [],
        "analyses": [],
    }
    try:
        with _reader(monitoring_db) as connection:
            connection.execute("ATTACH DATABASE ? AS analysis", (_uri(analysis_db),))
            result["analyses"] = _analysis(connection, periods, now_ms)
        result["analysis_available"] = True
    except (OSError, sqlite3.Error, TypeError, ValueError):
        for period in periods.values():
            for asset in period["assets"]:
                asset.update(dict.fromkeys(ANALYSIS_FIELDS))
    try:
        with _reader(paper_db) as connection:
            result["account"], result["orders"] = _paper(connection, periods, now_ms)
        result["paper_available"] = True
    except (OSError, sqlite3.Error, TypeError, ValueError):
        for period in periods.values():
            period["curve"] = []
            for asset in period["assets"]:
                asset.update(dict.fromkeys(PAPER_FIELDS))
    if result["paper_available"] and result["analysis_available"]:
        try:
            with _reader(paper_db) as connection:
                connection.execute("ATTACH DATABASE ? AS monitoring", (_uri(monitoring_db),))
                for period in periods.values():
                    rows = connection.execute(
                        "SELECT m.symbol, SUM(d.status = 'submitted') AS paper_submitted, "
                        "SUM(d.status = 'no_trade') AS paper_no_trade, SUM(d.status = 'rejected') AS paper_rejected "
                        "FROM paper_decisions d JOIN monitoring.monitoring_jobs m ON m.task_id = d.dedupe_key "
                        "WHERE d.account_id = ? AND d.arm = ? AND m.source = 'binance_usdm' AND m.venue = 'BINANCE' "
                        "AND m.timeframe = '1h' AND m.task_created_at_ms BETWEEN ? AND ? GROUP BY m.symbol",
                        (ACCOUNT_ID, PIPELINE, period["start_ms"], now_ms),
                    ).fetchall()
                    by_symbol = {row["symbol"]: row for row in rows}
                    for asset in period["assets"]:
                        for key in PAPER_FIELDS[-3:]:
                            asset[key] = (
                                int(by_symbol[asset["symbol"]][key] or 0)
                                if asset["symbol"] in by_symbol
                                else 0
                            )
            result["decisions_available"] = True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            for period in periods.values():
                for asset in period["assets"]:
                    asset.update(dict.fromkeys(PAPER_FIELDS[-3:]))
    if result["orders"]:
        try:
            with _reader(analysis_db) as connection:
                ids = list({order["run_id"] for order in result["orders"]})
                marks = ",".join("?" for _ in ids)
                rows = connection.execute(
                    "SELECT run_id, symbol, timeframe, instrument_type, analysis_pipeline, stage1_cycle_position, "
                    "stage1_direction, stage1_gate_result, "
                    "CASE WHEN length(review_context_json) <= 1000000 THEN review_context_json END AS review_json "
                    f"FROM analysis_runs WHERE run_id IN ({marks}) AND source_provider = 'binance_usdm' "
                    f"AND venue = 'BINANCE' AND resolved_symbol = symbol AND instrument_type IN {_PERPETUAL_TYPES}",
                    ids,
                ).fetchall()
                by_run = {row["run_id"]: row for row in rows}
                for order in result["orders"]:
                    row = by_run.get(order["run_id"])
                    if (
                        row
                        and row["symbol"] == order["symbol"]
                        and row["instrument_type"] == order["instrument_type"]
                        and row["timeframe"] == "1h"
                        and row["analysis_pipeline"] == PIPELINE
                    ):
                        order["review"] = _review(row["review_json"])
                        order["cycle"] = row["stage1_cycle_position"]
                        order["direction"] = row["stage1_direction"]
                        order["gate"] = row["stage1_gate_result"]
        except (OSError, sqlite3.Error, TypeError, ValueError):
            pass  # Execution facts remain visible even if the separate review index is unavailable.
    return result


def safe_trading_projection(value: Any) -> dict[str, Any]:
    """Dashboard allowlist: no database paths, arbitrary JSON or raw model output."""
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return {}

    def select(item: Any, keys: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {}
        return {
            key: item[key][:2400] if isinstance(item[key], str) else item[key]
            for key in keys
            if key in item
            and (item[key] is None or isinstance(item[key], (str, int, float, bool)))
            and (not isinstance(item[key], float) or math.isfinite(item[key]))
        }

    def items(raw: Any, limit: int) -> list[dict[str, Any]]:
        return (
            [item for item in raw[:limit] if isinstance(item, dict)]
            if isinstance(raw, list)
            else []
        )

    result = select(
        value,
        (
            "schema_version",
            "generated_at_ms",
            "analysis_available",
            "paper_available",
            "decisions_available",
        ),
    )
    result["scope"] = select(
        value.get("scope"),
        (
            "account_id",
            "pipeline",
            "simulation_version",
            "provider",
            "venue",
            "timeframe",
            "instrument_type",
        ),
    )
    result["account"] = select(
        value.get("account"),
        (
            "settled_balance",
            "initial_balance",
            "outside_scope_orders",
            "open_risk",
            "pending_risk",
            "open_count",
            "marks_known",
            "unrealized_pnl",
        ),
    )
    result["periods"] = {}
    raw_periods = value.get("periods")
    for key in _PERIODS:
        period = raw_periods.get(key) if isinstance(raw_periods, dict) else None
        if isinstance(period, dict):
            result["periods"][key] = {
                **select(period, ("start_ms",)),
                "assets": [
                    select(item, ("symbol", *ANALYSIS_FIELDS, *PAPER_FIELDS))
                    for item in items(period.get("assets"), 3)
                ],
                "curve": [
                    select(item, ("bar_ts", "pnl")) for item in items(period.get("curve"), 180)
                ],
            }
    result["orders"] = [
        {
            **select(item, (*ORDER_FIELDS, "cycle", "direction", "gate")),
            "review": select(item.get("review"), REVIEW_FIELDS),
        }
        for item in items(value.get("orders"), 150)
    ]
    result["analyses"] = [
        {**select(item, RUN_FIELDS), "review": select(item.get("review"), REVIEW_FIELDS)}
        for item in items(value.get("analyses"), 30)
    ]
    return result
