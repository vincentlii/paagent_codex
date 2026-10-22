"""Bounded Demo projection. Dashboard never opens the execution ledger."""

from __future__ import annotations

import math
import json
from decimal import Decimal, InvalidOperation

from pa_agent.execution.demo_contracts import DemoIntent, DemoOrderRequest
from pa_agent.operations.demo_plan_view import plan_view, order_ids

NUMERIC_FIELDS = frozenset(
    "equity available_balance unrealized_pnl margin_used planned_stop_loss reserved_margin "
    "entry stop tp1 quantity filled exited_quantity remaining_quantity average_fill_price notional target_notional pos upl imr margin lever fillPx fillSz fee net_pnl fees funding external_flows pnl".split()
)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _select(value, keys):
    if not isinstance(value, dict):
        return {}
    result = {
        k: (v[:2400] if isinstance(v, str) else v)
        for k, v in value.items()
        if k in keys
        and (v is None or isinstance(v, (str, int, float, bool)))
        and not (isinstance(v, float) and not math.isfinite(v))
    }
    for key in result.keys() & NUMERIC_FIELDS:
        try:
            parsed = Decimal(str(result[key]))
            if (
                isinstance(result[key], bool)
                or not parsed.is_finite()
                or abs(parsed) > Decimal("1e30")
            ):
                raise ValueError("invalid_number")
        except (InvalidOperation, ValueError):
            result[key] = None
    return result


def safe_demo_projection(value):
    if (
        not isinstance(value, dict)
        or value.get("environment") != "okx_demo_v1"
        or value.get("schema_version") != 1
    ):
        return {
            "schema_version": 1,
            "environment": "okx_demo_v1",
            "account_label": "OKX 模拟盘",
            "state": "unavailable",
        }
    result = _select(
        value,
        (
            "schema_version",
            "environment",
            "account_label",
            "generated_at_ms",
            "exchange_observed_at_ms",
            "reconciled_through_ms",
            "ledger_start_ms",
            "state",
            "reason",
            "total_decisions",
            "total_orders",
            "total_active_trades",
            "outside_orders",
        ),
    )
    result["account"] = _select(
        value.get("account"),
        (
            "equity",
            "available_balance",
            "unrealized_pnl",
            "margin_used",
            "planned_stop_loss",
            "reserved_margin",
        ),
    )
    result["coverage"] = {
        k: v
        for k, v in _mapping(value.get("coverage")).items()
        if k in {"account", "orders", "algos", "positions", "fills", "bills", "contracts", "ledger"}
        and isinstance(v, bool)
    }
    row_fields = {
        "orders": (
            "task_id",
            "run_id",
            "symbol",
            "side",
            "entry",
            "stop",
            "tp1",
            "quantity",
            "filled",
            "exited_quantity",
            "remaining_quantity",
            "average_fill_price",
            "notional",
            "target_notional",
            "limit_validity_hours",
            "execution_settings_generation",
            "take_profit_mode",
            "requested_reward_risk_ratio",
            "actual_reward_risk_ratio",
            "expires_at_ms",
            "entry_state",
            "protection",
            "trade_state",
            "reason",
            "created_at_ms",
            "display_status",
            "pnl",
            "sync_required",
            "valuation_at_ms",
            "source_provider", "source_venue", "source_symbol", "source_instrument_type",
            "timeframe", "pipeline", "market_digest", "cycle", "direction", "gate", "review_status",
        ),
        "decisions": ("task_id", "run_id", "symbol", "disposition", "reason", "created_at_ms"),
        "positions": ("instId", "pos", "upl", "imr", "margin", "mgnMode", "lever"),
        "fills": (
            "instId",
            "ordId",
            "tradeId",
            "side",
            "fillPx",
            "fillSz",
            "fee",
            "feeCcy",
            "ts",
            "fillTime",
        ),
    }
    for key, fields in row_fields.items():
        raw = value.get(key)
        result[key] = (
            [_select(row, fields) for row in raw[: 150 if key != "positions" else 3]]
            if isinstance(raw, list)
            else []
        )
    from pa_agent.operations.trading import REVIEW_FIELDS
    raw_orders = value.get("orders")
    for raw, clean in zip(raw_orders if isinstance(raw_orders, list) else [], result["orders"]):
        clean["review"] = _select(_mapping(raw).get("review"), REVIEW_FIELDS)
    raw_rules = value.get("price_rules")
    result["price_rules"] = (
        [_select(row, ("inst_id", "tick")) for row in raw_rules[:3]]
        if isinstance(raw_rules, list) else []
    )
    result["periods"] = {}
    for key in ("all", "7d", "30d"):
        raw = _mapping(value.get("periods")).get(key, {})
        result["periods"][key] = _select(
            raw, ("net_pnl", "fees", "funding", "external_flows", "scope")
        )
        curve = raw.get("curve") if isinstance(raw, dict) else None
        result["periods"][key]["curve"] = (
            [_select(row, ("bar_ts", "pnl")) for row in curve[-180:]]
            if isinstance(curve, list)
            else []
        )
    return result


def bind_demo_order_reviews(demo, analysis_db, monitoring_db):
    """Host-only historical join, bound to the exact source task, not a recent list."""
    from pa_agent.execution.demo_contracts import MAPPINGS
    from pa_agent.operations.trading import _reader, _review, _uri
    import sqlite3

    result = safe_demo_projection(demo)
    orders = result.get("orders", [])
    for order in orders:
        order.update(review={}, review_status="missing")
    ids = list({o.get("task_id") for o in orders if o.get("task_id")})
    if not ids:
        return result
    try:
        with _reader(analysis_db) as db:
            db.execute("ATTACH DATABASE ? AS monitoring", (_uri(monitoring_db),))
            rows = db.execute(
                "SELECT a.run_id,a.symbol,a.timeframe,a.analysis_pipeline,a.source_provider,a.venue,"
                "a.resolved_symbol,a.instrument_type,a.stage1_cycle_position,a.stage1_direction,"
                "a.stage1_gate_result,CASE WHEN length(a.review_context_json)<=1000000 "
                "THEN a.review_context_json END AS review_json,m.task_id,m.snapshot_hash,"
                "m.source AS task_source,m.venue AS task_venue,m.symbol AS task_symbol,"
                "m.timeframe AS task_timeframe FROM analysis_runs a JOIN monitoring.monitoring_jobs m "
                "ON a.run_id=m.run_id WHERE m.task_id IN (" + ",".join("?" for _ in ids) + ")", ids,
            ).fetchall()
        by_task = {r["task_id"]: r for r in rows}
        for order in orders:
            row = by_task.get(order.get("task_id"))
            if row is None:
                continue
            checks = {
                "run_id": row["run_id"], "source_provider": row["source_provider"],
                "source_venue": row["venue"], "source_symbol": row["symbol"],
                "source_instrument_type": row["instrument_type"], "timeframe": row["timeframe"],
                "pipeline": row["analysis_pipeline"], "market_digest": row["snapshot_hash"],
            }
            valid = (
                all(order.get(k) == v and v for k, v in checks.items())
                and row["source_provider"] == row["task_source"] == "binance_usdm"
                and row["venue"] == row["task_venue"] == "BINANCE"
                and row["symbol"] == row["resolved_symbol"] == row["task_symbol"]
                and row["timeframe"] == row["task_timeframe"] == "1h"
                and row["analysis_pipeline"] == "pa_vpa_joint"
                and row["instrument_type"] in {"perpetual", "tradifi_perpetual"}
                and MAPPINGS.get(row["symbol"]) == order.get("symbol")
            )
            if not valid:
                order["review_status"] = "identity_mismatch"
                continue
            review = _review(row["review_json"])
            order.update(review=review, review_status="available" if any(review.values()) else "missing",
                         cycle=row["stage1_cycle_position"], direction=row["stage1_direction"],
                         gate=row["stage1_gate_result"])
    except (OSError, sqlite3.Error, TypeError, ValueError):
        for order in orders:
            order.update(review={}, review_status="unavailable")
    return result


def build_demo_projection(store, snapshot, *, now_ms):
    rows = store.projection_rows(now_ms=now_ms)
    orders, decisions = [], []
    stop_risk = Decimal(0)
    for row in rows["decisions"]:
        i = DemoIntent.model_validate_json(row["body"])
        decisions.append(
            dict(
                task_id=i.task_id,
                run_id=i.run_id,
                symbol=i.inst_id,
                disposition=i.disposition,
                reason=row["reason"],
                created_at_ms=i.generated_at_ms,
            )
        )
    for row in rows["intents"]:
        i = DemoIntent.model_validate_json(row["body"])
        if row["request"]:
            request = DemoOrderRequest.model_validate_json(row["request"])
            legs = store.legs(i.content_digest)
            children = store.triggered_orders(i.content_digest)
            entry = next((l for l in legs if l["leg"] == "entry"), None)
            average_fill = json.loads(entry["response"]).get("avgPx") if entry else None
            independent = i.schema_version == "okx_demo_intent_v2"
            exposure = store.plan_exposure(i.content_digest) if independent else None
            orders.append(
                dict(
                    task_id=i.task_id,
                    run_id=i.run_id,
                    symbol=i.inst_id,
                    source_provider=i.source_identity.get("provider"),
                    source_venue=i.source_identity.get("venue"),
                    source_symbol=i.source_identity.get("symbol"),
                    source_instrument_type=i.source_identity.get("instrument_type"),
                    timeframe=i.timeframe, pipeline=i.pipeline, market_digest=i.market_digest,
                    side=i.side,
                    entry=str(i.entry),
                    stop=str(i.stop),
                    tp1=str(i.tp1),
                    take_profit_mode="fixed_rr" if i.take_profit_policy else "pa",
                    requested_reward_risk_ratio=(
                        i.take_profit_policy.reward_risk_ratio if i.take_profit_policy else None
                    ),
                    actual_reward_risk_ratio=str(abs(i.tp1 - i.entry) / abs(i.entry - i.stop)),
                    quantity=str(request.sz),
                    filled=row["filled"],
                    exited_quantity=(
                        str(exposure.exited) if exposure and not exposure.uncertain else None
                    ),
                    remaining_quantity=(
                        str(exposure.remaining) if exposure and not exposure.uncertain else None
                    ),
                    average_fill_price=average_fill or None,
                    notional=str(request.notional),
                    target_notional=(
                        str(i.demo_execution_policy.notional_usdt)
                        if i.demo_execution_policy
                        else "500"
                    ),
                    limit_validity_hours=(
                        i.demo_execution_policy.limit_validity_hours
                        if i.demo_execution_policy
                        else None
                    ),
                    execution_settings_generation=(
                        i.demo_execution_policy.generation if i.demo_execution_policy else None
                    ),
                    expires_at_ms=i.expires_at_ms,
                    entry_state=row["entry_state"],
                    protection=row["protection"],
                    trade_state=row["trade_state"],
                    reason=row["reason"],
                    created_at_ms=i.generated_at_ms,
                    **plan_view(
                        i, request, row, legs, children,
                        store.order_fills(i.inst_id, order_ids(legs, children),
                                          client_id=entry["client_id"] if entry else None),
                        snapshot.quotes.get(i.inst_id, {}), now_ms,
                    ),
                )
            )
            if row["trade_state"] == "active":
                quantity = request.sz
                if exposure is not None:
                    if exposure.uncertain:
                        stop_risk = None
                        continue
                    quantity = exposure.remaining + (
                        max(Decimal(0), request.sz - exposure.entered)
                        if row["entry_state"] not in {"filled", "canceled", "rejected"}
                        else Decimal(0)
                    )
                if stop_risk is not None:
                    stop_risk += (
                        request.notional
                        * abs(request.px - request.stop)
                        / request.px
                        * quantity
                        / request.sz
                    )
    periods = {}
    for key, days in (("all", None), ("7d", 7), ("30d", 30)):
        start = max(
            rows.get("ledger_start_ms") or 0, 0 if days is None else now_ms - days * 86400000
        )
        pnl, fees, funding, flows = (Decimal(0) for _ in range(4))
        curve = []
        complete = snapshot.coverage.get("bills", False) and rows.get("ledger_complete", True)
        # Account trading bills are the sole monetary source. Fill fees are displayed,
        # never added again to bill fees or position.realizedPnl (which already includes costs).
        for bill in sorted(rows["bills"], key=lambda b: int(b.get("ts", "0"))):
            try:
                ts = int(bill.get("ts", "0"))
                if ts < start or ts > now_ms:
                    continue
                if bill.get("ccy", "USDT") != "USDT":
                    complete = False
                    continue
                kind = bill.get("type")
                if kind == "2":
                    if not bill.get("pnl") or not bill.get("fee"):
                        complete = False
                        continue
                    trade_pnl, fee = Decimal(bill["pnl"]), Decimal(bill["fee"])
                    pnl += trade_pnl + fee
                    fees += fee
                elif kind == "8":
                    payment = Decimal(bill["balChg"])
                    pnl += payment
                    funding += payment
                elif kind == "1":
                    flows += Decimal(bill["balChg"])
                else:
                    complete = False  # Unclassified bills are not silently called profit/transfer.
                if not all(x.is_finite() for x in (pnl, fees, funding, flows)):
                    complete = False
                    break
                curve.append({"bar_ts": ts, "pnl": str(pnl)})
            except (ValueError, InvalidOperation, KeyError):
                complete = False
        periods[key] = {
            "scope": "demo_account_bills",
            "net_pnl": str(pnl) if complete else None,
            "fees": str(fees) if complete else None,
            "funding": str(funding) if complete else None,
            "external_flows": str(flows) if complete else None,
            "curve": curve[-180:] if complete else [],
        }
    positions = list(snapshot.positions)

    def position_sum(key):
        try:
            values = [Decimal(p[key]) for p in positions if Decimal(p.get("pos", "0")) != 0]
            return str(sum(values, Decimal(0))) if all(v.is_finite() for v in values) else None
        except (KeyError, InvalidOperation):
            return None

    return safe_demo_projection(
        dict(
            schema_version=1,
            environment="okx_demo_v1",
            account_label="OKX 模拟盘",
            generated_at_ms=now_ms,
            exchange_observed_at_ms=snapshot.observed_at_ms,
            reconciled_through_ms=(
                snapshot.observed_at_ms if all(snapshot.coverage.values()) else None
            ),
            state="ready" if all(snapshot.coverage.values()) else "reconciliation_gap",
            coverage=dict(snapshot.coverage),
            price_rules=[{"inst_id": c.inst_id, "tick": str(c.tick)} for c in snapshot.contracts],
            account={
                "equity": str(snapshot.equity) if snapshot.equity is not None else None,
                "available_balance": (
                    str(snapshot.available) if snapshot.available is not None else None
                ),
                "unrealized_pnl": position_sum("upl"),
                "margin_used": position_sum("margin"),
                "planned_stop_loss": str(stop_risk) if stop_risk is not None else None,
                "reserved_margin": str(store.reserved_margin()),
            },
            orders=orders,
            decisions=decisions,
            positions=positions,
            fills=list(rows["fills"])[-150:],
            periods=periods,
            ledger_start_ms=rows.get("ledger_start_ms"),
            total_decisions=rows["total_decisions"],
            total_orders=rows.get("total_orders", len(orders)),
            total_active_trades=len(store.pending_intents()),
            outside_orders=len(snapshot.orders) + len(snapshot.algos),
        )
    )


def compose_trading_projection(paper, demo, *, selected_backend, now_ms):
    from pa_agent.operations.trading import safe_trading_projection

    if selected_backend == "paper":
        return safe_trading_projection(dict(paper))
    clean = safe_demo_projection(demo)
    observed = clean.get("exchange_observed_at_ms")
    if clean.get("state") != "unavailable" and (
        not isinstance(observed, int) or not 0 <= now_ms - observed <= 30000
    ):
        clean["state"] = "stale"
    legacy = safe_trading_projection(dict(paper)) if paper.get("schema_version") == 1 else {}
    versions = {"paper_latency_v4": legacy, **legacy.get("execution_versions", {})}
    for version in versions.values():
        version["history_frozen"] = True
        account = version.get("account", {})
        for key in ("open_risk", "pending_risk", "unrealized_pnl", "marks_known"):
            account[key] = None
        for order in version.get("orders", []):
            if order.get("status") in {"open", "pending"}:
                order.update(
                    unrealized_pnl=None,
                    mark_close=None,
                    mark_closed_at_ms=None,
                    mark_status="history_frozen",
                )
    return {
        "schema_version": 2,
        "selected_backend": "okx_demo",
        "generated_at_ms": now_ms,
        "demo": clean,
        "paper_versions": versions,
        "analysis": {
            "source": "host_records",
            "available": legacy.get("analysis_available"),
            "analyses": legacy.get("analyses", []),
            "periods": legacy.get("periods", {}),
        },
    }


def safe_composed_projection(value):
    from pa_agent.operations.trading import safe_trading_projection

    raw_versions = value.get("paper_versions")
    versions = raw_versions if isinstance(raw_versions, dict) else {}
    raw = _mapping(versions.get("paper_latency_v4"))
    paper = safe_trading_projection(raw) if raw.get("schema_version") == 1 else {}
    if _mapping(versions.get("paper_latency_v3")).get("schema_version") == 1:
        paper["execution_versions"] = {
            "paper_latency_v3": safe_trading_projection(versions["paper_latency_v3"])
        }
    stamp = value.get("generated_at_ms")
    result = compose_trading_projection(
        paper,
        value.get("demo"),
        selected_backend="okx_demo",
        now_ms=stamp if type(stamp) is int else 0,
    )
    return result
