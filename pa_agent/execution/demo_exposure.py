"""Recompute per-plan quantities from durable exchange facts; no account allocation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal(0)
TERMINAL = {"filled", "canceled", "rejected", "order_failed"}


@dataclass(frozen=True)
class PlanExposure:
    entered: Decimal = ZERO
    exited: Decimal = ZERO
    remaining: Decimal = ZERO
    reserved_exit: Decimal = ZERO
    protected: Decimal = ZERO
    uncertain: bool = False
    # Pending exchange facts have finite exit bounds; contradictory facts do not.
    inconsistent: bool = False


def _quantity(value):
    result = Decimal(value)
    if not result.is_finite() or result < 0:
        raise ValueError("invalid_plan_quantity")
    return result


def plan_exposure(entry: dict | None, legs: list[dict], children: list[dict]) -> PlanExposure:
    entered = exited = reserved = protected = ZERO
    uncertain = False
    inconsistent = False
    seen = {}
    try:
        if entry:
            entered = _quantity(json.loads(entry["response"]).get("accFillSz", "0"))
        for leg in legs:
            if leg["leg"] == "entry":
                continue
            quantity = _quantity(json.loads(leg["params"])["quantity"])
            response = json.loads(leg["response"])
            status = leg["status"]
            rows = (
                [
                    json.loads(c["body"]) | {"ordId": c["order_id"], "state": c["state"]}
                    for c in children
                    if c["parent_client_id"] == leg["client_id"]
                ]
                if leg["leg"] == "oco"
                else [response]
            )
            leg_filled = ZERO
            own_seen = set()
            complete = bool(rows)
            for row in rows:
                oid = row.get("ordId")
                fill = _quantity(row["accFillSz"]) if "accFillSz" in row else ZERO
                complete &= bool(oid and "accFillSz" in row and row.get("state") in TERMINAL)
                if oid:
                    if oid in own_seen:
                        continue
                    own_seen.add(oid)
                    leg_filled += fill
                    if oid in seen:
                        if seen[oid] != (leg["client_id"], fill):
                            inconsistent = True
                    else:
                        seen[oid] = (leg["client_id"], fill)
                        exited += fill
            if leg_filled > quantity:
                inconsistent = True
            if leg["leg"] == "oco":
                resolved = (
                    status in {"canceled", "rejected", "order_failed"} and (not rows or complete)
                ) or (status == "effective" and complete)
            else:
                resolved = status in {"rejected", "order_failed"} or (
                    status in TERMINAL and complete
                )
            if not resolved:
                reserved += quantity - leg_filled
                if status == "live" and leg["leg"] == "oco":
                    protected += quantity - leg_filled
                else:
                    uncertain = True
        remaining = entered - exited
        inconsistent |= (
            remaining < 0 or reserved < 0 or reserved > remaining or protected > remaining
        )
        return PlanExposure(
            entered,
            exited,
            remaining,
            reserved,
            protected,
            bool(uncertain or inconsistent),
            bool(inconsistent),
        )
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return PlanExposure(entered, exited, entered - exited, reserved, protected, True, True)
