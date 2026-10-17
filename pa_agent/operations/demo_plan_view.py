"""Read-only per-plan value from owned fills, never the shared position basis."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

TERMINAL = {"filled", "canceled", "rejected", "order_failed"}


def order_ids(legs, children):
    return {
        str(value)
        for value in [
            *(json.loads(leg["response"]).get("ordId") for leg in legs),
            *(child["order_id"] for child in children),
        ]
        if value
    }


def _number(value, *, positive=False):
    result = Decimal(str(value))
    if not result.is_finite() or (result <= 0 if positive else result < 0):
        raise ValueError("invalid_plan_value")
    return result


def plan_view(intent, request, row, legs, children, fills, quote, now_ms):
    entered = _number(row["filled"])
    status = "open" if entered > 0 else "pending"
    result = dict(display_status=status, pnl=None, sync_required=True, valuation_at_ms=None)
    try:
        entry = next(leg for leg in legs if leg["leg"] == "entry")
        observation = json.loads(entry["response"])
        if (
            entry["status"] == row["entry_state"] == "rejected"
            and entered == 0
            and not observation.get("ordId")
        ):
            if fills or children or any(leg["leg"] != "entry" for leg in legs):
                return result
            return dict(
                display_status="cancelled", pnl="0", sync_required=False, valuation_at_ms=None
            )
        entry_id = observation["ordId"]
        entered = _number(observation["accFillSz"])
        if entry["status"] == "observation_unknown" or entered != _number(row["filled"]):
            return result
        exits = {}
        for leg in legs:
            if leg["leg"] == "entry":
                continue
            observations = (
                [
                    (c["order_id"], c["state"], json.loads(c["body"]))
                    for c in children
                    if c["parent_client_id"] == leg["client_id"]
                ]
                if leg["leg"] == "oco"
                else [
                    (
                        json.loads(leg["response"]).get("ordId"),
                        leg["status"],
                        json.loads(leg["response"]),
                    )
                ]
            )
            if leg["status"] not in {
                "live",
                "effective",
                "filled",
                "canceled",
                "rejected",
                "order_failed",
            }:
                return result
            if leg["status"] == "effective" and not observations:
                return result
            for oid, state, body in observations:
                if not oid or "accFillSz" not in body or state not in TERMINAL:
                    return result
                quantity = _number(body["accFillSz"])
                if oid == entry_id or (oid in exits and exits[oid] != quantity):
                    return result
                exits[oid] = quantity
        remaining = entered - sum(exits.values(), Decimal(0))
        if remaining < 0:
            return result
        if remaining > 0:
            result["display_status"] = "open"
        elif row["entry_state"] in TERMINAL:
            result["display_status"] = "closed" if entered > 0 else "cancelled"
        quantities, amounts, fees, seen = {}, {}, Decimal(0), {}
        for fill in fills:
            oid = fill.get("ordId")
            if fill.get("instId") != intent.inst_id or oid not in {entry_id, *exits}:
                continue
            key = (fill["instId"], fill["tradeId"])
            if key in seen:
                if seen[key] != fill:
                    return result
                continue
            seen[key] = fill
            expected_side = (
                intent.side if oid == entry_id else ("sell" if intent.side == "buy" else "buy")
            )
            if fill.get("side") != expected_side or fill.get("feeCcy") != "USDT":
                return result
            quantity = _number(fill["fillSz"], positive=True)
            quantities[oid] = quantities.get(oid, Decimal(0)) + quantity
            amounts[oid] = amounts.get(oid, Decimal(0)) + quantity * _number(
                fill["fillPx"], positive=True
            )
            fee = Decimal(fill["fee"])
            if not fee.is_finite():
                return result
            fees += fee
        if any(
            quantities.get(oid, Decimal(0)) != q for oid, q in {entry_id: entered, **exits}.items()
        ):
            result["display_status"] = None
            return result
        if entered == 0 and not exits:
            result.update(pnl="0", sync_required=False)
            return result
        value = sum((amounts.get(oid, Decimal(0)) for oid in exits), Decimal(0)) - amounts[entry_id]
        if remaining:
            timestamp = int(quote["ts"])
            if not 0 <= now_ms - timestamp <= 30000:
                return result
            mark = _number(quote["bidPx" if intent.side == "buy" else "askPx"], positive=True)
            value += remaining * mark
            result["valuation_at_ms"] = timestamp
        multiplier = request.notional / (request.px * request.sz)
        pnl = (value if intent.side == "buy" else -value) * multiplier + fees
        result.update(pnl=str(pnl), sync_required=False)
        return result
    except (KeyError, ValueError, TypeError, StopIteration, InvalidOperation, ArithmeticError):
        return result
