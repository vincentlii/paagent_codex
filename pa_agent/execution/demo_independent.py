"""v2 execution: independent plans, exact exits, one serial worker and ledger."""

from __future__ import annotations

import json
from decimal import Decimal

from pa_agent.execution.demo_contracts import DemoOrderRequest
from pa_agent.execution.demo_store import ENTRY_TERMINAL, LEG_TERMINAL
from pa_agent.execution.okx_demo import DemoRequestRejected, DemoRequestUnknown, DemoUnavailable


class IndependentPlanManager:
    def __init__(self, worker):
        self.worker = worker
        self.store = worker.store

    def accept_reason(self, intent, snapshot):
        if intent.disposition != "limit":
            return ""
        preceding = True
        for other in self.store.pending_intents():
            if other.content_digest == intent.content_digest:
                preceding = False
                continue
            if other.inst_id != intent.inst_id:
                continue
            if not preceding and not self.store.row(other.content_digest)["request"]:
                continue
            if other.schema_version != "okx_demo_intent_v2":
                return "legacy_plan_active"
            if other.disposition == "limit" and other.side != intent.side:
                return "opposite_plan_active"
        for p in snapshot.positions:
            if p.get("instId") == intent.inst_id and Decimal(p.get("pos", "0")):
                if (Decimal(p["pos"]) > 0) != (intent.side == "buy"):
                    return "opposite_position_active"
        return ""

    def _aligned(self, inst_id, snapshot, *, protecting=False):
        expected = Decimal(0)
        pending = Decimal(0)
        sides = set()
        for intent in self.store.pending_intents():
            if intent.inst_id != inst_id:
                continue
            row = self.store.row(intent.content_digest)
            if not row["request"]:
                continue
            if intent.schema_version != "okx_demo_intent_v2":
                return False
            exposure = self.store.plan_exposure(intent.content_digest)
            if exposure.inconsistent or (exposure.uncertain and not protecting):
                return False
            sides.add(intent.side)
            expected += exposure.remaining * (1 if intent.side == "buy" else -1)
            pending += exposure.reserved_exit
        actual = Decimal(0)
        for p in snapshot.positions:
            if p.get("instId") != inst_id:
                continue
            if p.get("posSide") != "net" or p.get("mgnMode") != "isolated":
                return False
            actual += Decimal(p.get("pos", "0"))
        if not snapshot.coverage.get("positions") or len(sides) > 1:
            return False
        if protecting:
            # Existing exits can consume only their own reserved budgets. This interval
            # permits protecting B while A's finite exit remains unknown, never new entry.
            sign = 1 if sides == {"buy"} else -1
            return abs(expected) - pending <= actual * sign <= abs(expected)
        return actual == expected

    def start(self, intent, snapshot):
        w = self.worker
        row = self.store.row(intent.content_digest)
        if row["request"]:
            return
        if w.now_ms() >= intent.fresh_until_ms:
            w._finish(intent, "decision_expired")
            return
        if intent.disposition != "limit":
            w._finish(intent, intent.source_reason if intent.disposition == "rejected" else "")
            return
        reason = self.accept_reason(intent, snapshot)
        if reason:
            w._finish(intent, reason)
            return
        if intent.inst_id not in w.config.independent_accepted_symbols:
            w._finish(intent, "independent_protection_not_live_accepted")
            return
        block = w._entry_block(snapshot)
        if block or not self._aligned(intent.inst_id, snapshot):
            w.error = block or "plan_position_reconciliation_gap"
            return
        w._submit_limit(intent, snapshot)

    def observe(self, intent):
        w = self.worker
        row = self.store.row(intent.content_digest)
        if not row["request"]:
            return
        for leg in self.store.legs(intent.content_digest):
            result = w._leg_query(intent, leg)
            if not result and leg["leg"] != "entry" and leg["status"] not in LEG_TERMINAL:
                # Cached live is not a fresh verification. Retain the entire exit
                # reservation, including across restart; never infer absence from GET.
                self.store.update_leg(leg["client_id"], "observation_unknown")
            if leg["leg"] == "entry" and result:
                self.store.update(
                    intent.content_digest,
                    entry_state=result.get("state", "unknown"),
                    filled=result.get("accFillSz", "0"),
                )
                latest = self.store.row(intent.content_digest)
                if latest["entry_state"] in ENTRY_TERMINAL and (
                    not latest["terminal_at_ms"]
                    or Decimal(latest["terminal_filled"]) != Decimal(latest["filled"])
                ):
                    self.store.update(
                        intent.content_digest,
                        terminal_at_ms=w.now_ms(),
                        terminal_filled=latest["filled"],
                    )
        w._exits_terminal(intent)

    @staticmethod
    def _verify(result, request, leg):
        params = json.loads(leg["params"])
        if (
            result.get("algoClOrdId") != leg["client_id"]
            or result.get("instId") != request.inst_id
            or result.get("side") != params["side"]
            or Decimal(result.get("sz", "0")) != Decimal(params["quantity"])
            or result.get("closeFraction") not in (None, "")
            or Decimal(result.get("slTriggerPx", "0")) != request.stop
            or Decimal(result.get("tpTriggerPx", "0")) != request.tp1
            or result.get("ordType") != "oco"
            or result.get("reduceOnly") != "true"
            or result.get("posSide") != "net"
            or result.get("slOrdPx") != "-1"
            or result.get("tpOrdPx") != "-1"
            or result.get("slTriggerPxType") != "last"
            or result.get("tpTriggerPxType") != "last"
        ):
            raise DemoUnavailable("protection_parameters_mismatch")

    def _protect(self, intent, request, quantity):
        w = self.worker
        legs = [l for l in self.store.legs(intent.content_digest) if l["leg"] == "oco"]
        params = dict(
            stop=str(request.stop),
            tp1=str(request.tp1),
            quantity=str(quantity),
            side="sell" if request.side == "buy" else "buy",
        )
        cid = self.store.prepare_leg(
            intent.content_digest,
            "oco",
            params,
            generation=max((l["generation"] for l in legs), default=0) + 1,
        )
        self.store.update_leg(cid, "submission_unknown")
        try:
            result = w.client.place_oco(
                inst_id=intent.inst_id,
                side=params["side"],
                stop=request.stop,
                tp1=request.tp1,
                client_id=cid,
                quantity=quantity,
            )
            self.store.update_leg(cid, "accepted", result)
            result = w._leg_query(intent, {"client_id": cid, "leg": "oco"})
            if result.get("state") == "live":
                try:
                    self._verify(result, request, {"client_id": cid, "params": json.dumps(params)})
                except (DemoUnavailable, ArithmeticError):
                    return False  # Never call a mismatched live exit verified or overlap it.
        except DemoRequestRejected:
            self.store.update_leg(cid, "rejected")
            # Only the unreserved part may be closed. Other plans and live/unknown exits are excluded.
            w._emergency(intent, request, quantity)
        except (DemoRequestUnknown, DemoUnavailable):
            pass  # A possible live exit owns its budget across restarts; never duplicate it.

    def manage(self, intent, snapshot):
        w = self.worker
        row = self.store.row(intent.content_digest)
        if not row["request"]:
            return
        request = DemoOrderRequest.model_validate_json(row["request"])
        if not any(l["leg"] == "entry" for l in self.store.legs(intent.content_digest)):
            w._finish(intent, "incomplete_preparation")
            return
        bad = False
        for leg in self.store.legs(intent.content_digest):
            if leg["leg"] == "oco" and leg["status"] == "live":
                try:
                    self._verify(json.loads(leg["response"]), request, leg)
                except (DemoUnavailable, ArithmeticError):
                    bad = True
                    result = json.loads(leg["response"])
                    if result.get("algoId"):
                        try:
                            w.client.cancel_algo(intent.inst_id, result["algoId"])
                        except (DemoRequestRejected, DemoRequestUnknown, DemoUnavailable):
                            pass
        exposure = self.store.plan_exposure(intent.content_digest)
        if bad:
            self.store.update(intent.content_digest, protection="protection_failed")
            w._cancel_entry(intent, request)
            return  # Cancel ACK cannot release a possibly live exit budget.
        free = exposure.remaining - exposure.reserved_exit
        if free > 0 and not exposure.inconsistent:
            # Fresh GET fills may precede account snapshot; no whole-position inference or remedy.
            if self._aligned(intent.inst_id, snapshot, protecting=True):
                if self._protect(intent, request, free) is False:
                    self.store.update(intent.content_digest, protection="protection_failed")
                    w._cancel_entry(intent, request)
                    return
                exposure = self.store.plan_exposure(intent.content_digest)
            else:
                w.error = "plan_position_reconciliation_gap"
        protection = (
            "protected"
            if exposure.remaining > 0
            and exposure.protected == exposure.remaining
            and not exposure.uncertain
            else "protection_unknown" if exposure.remaining > 0 or exposure.uncertain else "none"
        )
        self.store.update(intent.content_digest, protection=protection)
        if exposure.entered > 0 or w.now_ms() >= intent.expires_at_ms or not w.config.entry_enabled:
            w._cancel_entry(intent, request)
        if (
            row["entry_state"] in ENTRY_TERMINAL
            and exposure.remaining == 0
            and not exposure.uncertain
            and exposure.reserved_exit == 0
            and snapshot.observed_at_ms > row["terminal_at_ms"] > 0
            and self._aligned(intent.inst_id, snapshot)
        ):
            self.store.update(intent.content_digest, trade_state="closed", protection="none")
