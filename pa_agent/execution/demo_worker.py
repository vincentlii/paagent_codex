"""Serial Demo executor. Persist every potentially executed leg before its POST."""

from __future__ import annotations

import json
import os
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from pa_agent.execution.demo_contracts import DemoReceipt, DemoOrderRequest
from pa_agent.execution.demo_plans import bind_demo_limit
from pa_agent.execution.demo_spool import read_json, atomic_json
from pa_agent.execution.demo_store import ENTRY_TERMINAL, LEG_TERMINAL
from pa_agent.execution.okx_demo import DemoRequestRejected, DemoRequestUnknown, DemoUnavailable


class WorkerLock:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("a+b")
        if path.stat().st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise RuntimeError("worker_already_running") from None

    def close(self):
        if not self.stream.closed:
            if os.name == "nt":
                import msvcrt

                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            self.stream.close()


class DemoWorker:
    def __init__(self, config, paths, client, store, spool, *, now_ms):
        self.config, self.paths, self.client, self.store, self.spool = (
            config,
            paths,
            client,
            store,
            spool,
        )
        self.now_ms = now_ms
        self.lock = WorkerLock(paths.state_db.parent / "worker.lock")
        self.snapshot = None
        self.error = "not_reconciled"
        from pa_agent.execution.demo_independent import IndependentPlanManager

        self.independent = IndependentPlanManager(self)

    def _control_ready(self):
        try:
            value = read_json(self.paths.control)
            age = self.now_ms() - value["generated_at_ms"]
            return (
                0 <= age <= 30000
                and value.get("running") is True
                and value.get("cutover_id") == self.config.cutover_id
            )
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def reconcile(self):
        started_at_ms = self.now_ms()
        bill_cursor, fill_cursors = self.store.sync_cursors()
        snapshot = self.client.snapshot(since_bill_id=bill_cursor, since_fill_ids=fill_cursors)
        if not self.config.account_key or snapshot.account_key != self.config.account_key:
            raise DemoUnavailable("account_identity_mismatch")
        self.store.apply_snapshot(snapshot, started_at_ms=started_at_ms)
        self.snapshot = snapshot
        return snapshot

    def _known_ids(self):
        clients, exchange_ids = set(), set()
        for intent in self.store.pending_intents():
            for leg in self.store.legs(intent.content_digest):
                clients.add(leg["client_id"])
                response = json.loads(leg["response"])
                for key in ("ordId", "algoId", "actualOrdId"):
                    if response.get(key):
                        exchange_ids.add(response[key])
            exchange_ids.update(
                c["order_id"] for c in self.store.triggered_orders(intent.content_digest)
            )
        return clients, exchange_ids

    def _entry_block(self, snapshot):
        if not self.config.enabled or not self.config.entry_enabled or not self._control_ready():
            return "entry_paused"
        return self._account_block(snapshot)

    def _account_block(self, snapshot):
        if snapshot.account_mode != "2" or snapshot.position_mode != "net_mode":
            return "account_mode_mismatch"
        if not snapshot.coverage or not all(snapshot.coverage.values()):
            return "reconciliation_gap"
        if (
            abs(snapshot.server_time_ms - self.now_ms()) > 5000
            or not 0 <= self.now_ms() - snapshot.observed_at_ms <= 30000
        ):
            return "stale_account"
        known, exchange_ids = self._known_ids()
        if any(
            row.get("clOrdId") not in known and row.get("ordId") not in exchange_ids
            for row in snapshot.orders
        ):
            return "external_order"
        if any(row.get("algoClOrdId") not in known for row in snapshot.algos):
            return "external_algo"
        if any(
            leg["leg"] == "entry" and leg["status"] == "observation_unknown"
            for intent in self.store.pending_intents()
            for leg in self.store.legs(intent.content_digest)
        ):
            return "entry_observation_unknown"
        managed = {
            i.inst_id
            for i in self.store.pending_intents()
            if self.store.row(i.content_digest)["request"]
        }
        if any(
            Decimal(row.get("pos", "0")) != 0 and row.get("instId") not in managed
            for row in snapshot.positions
        ):
            return "external_position"
        if any(
            self.store.row(i.content_digest)["protection"]
            in {"protection_failed", "protection_unknown"}
            for i in self.store.pending_intents()
        ):
            return "protection_failure"
        return ""

    def _finish(self, intent, reason):
        self.store.update(
            intent.content_digest, trade_state="rejected" if reason else "closed", reason=reason
        )

    def _cancel_entry(self, intent, request):
        row = self.store.row(intent.content_digest)
        if row["entry_state"] in ENTRY_TERMINAL:
            return
        try:
            self.client.cancel_order(request.inst_id, request.client_id)
        except (DemoRequestRejected, DemoRequestUnknown, DemoUnavailable):
            pass  # Final status still comes from GET, including after a timeout.
        self.store.update(intent.content_digest, entry_state="cancel_pending")

    def _leg_query(self, intent, leg):
        try:
            result = (
                self.client.get_algo(intent.inst_id, leg["client_id"])
                if leg["leg"] == "oco"
                else self.client.get_order(intent.inst_id, leg["client_id"])
            )
        except (DemoRequestRejected, DemoRequestUnknown, DemoUnavailable):
            return {}
        if result:
            if result.get("instId") != intent.inst_id:
                raise DemoUnavailable("order_identity_mismatch")
            identity_field = "algoClOrdId" if leg["leg"] == "oco" else "clOrdId"
            if result.get(identity_field) not in (None, leg["client_id"]):
                raise DemoUnavailable("order_client_identity_mismatch")
            if intent.schema_version == "okx_demo_intent_v2" and leg["leg"] == "entry":
                previous = json.loads(leg.get("response", "{}"))
                if Decimal(result.get("accFillSz", "0")) > Decimal(previous.get("accFillSz", "0")):
                    result = result | {"_fill_observed_at_ms": self.now_ms()}
            self.store.update_leg(leg["client_id"], result.get("state", "unknown"), result)
            if leg["leg"] == "oco":
                ids = json.loads(result.get("child_order_ids", "[]"))
                ids.extend([result[key] for key in ("actualOrdId", "ordId") if result.get(key)])
                self.store.register_triggered(leg["client_id"], intent.inst_id, tuple(set(ids)))
        return result

    def _exits_terminal(self, intent):
        complete = True
        for leg in self.store.legs(intent.content_digest):
            if leg["leg"] != "oco" or leg["status"] != "effective":
                continue
            self._leg_query(intent, leg)  # A trigger may expose its child IDs only on a later read.
            children = [
                c
                for c in self.store.triggered_orders(intent.content_digest)
                if c["parent_client_id"] == leg["client_id"]
            ]
            if not children:
                complete = False
            for child in children:
                try:
                    result = self.client.get_order(intent.inst_id, order_id=child["order_id"])
                except (DemoRequestRejected, DemoRequestUnknown, DemoUnavailable):
                    result = {}
                if (
                    result.get("instId") != intent.inst_id
                    or result.get("ordId") != child["order_id"]
                ):
                    complete = False
                    continue
                self.store.observe_triggered(leg["client_id"], child["order_id"], result)
                if result.get("state") not in ENTRY_TERMINAL:
                    complete = False
        return complete

    def _protect(self, intent, request):
        legs = [l for l in self.store.legs(intent.content_digest) if l["leg"] == "oco"]
        last = legs[-1] if legs else None
        if last and last["status"] not in LEG_TERMINAL:
            result = self._leg_query(intent, last)
            if result.get("state") == "live":
                self._verify_protection(result, request)
                self.store.update(intent.content_digest, protection="protected")
                return True
            if not result:
                self.store.update(intent.content_digest, protection="protection_unknown")
                return False
            if result.get("state") not in LEG_TERMINAL:
                return False
            last = self.store.legs(intent.content_digest)[-1]
        # A consumed OCO cannot protect a late entry fill: allocate a durable new generation.
        generation = max((l["generation"] for l in legs), default=0) + 1
        params = {
            "stop": str(request.stop),
            "tp1": str(request.tp1),
            "side": "sell" if request.side == "buy" else "buy",
        }
        cid = self.store.prepare_leg(intent.content_digest, "oco", params, generation=generation)
        self.store.update_leg(cid, "submission_unknown")
        try:
            response = self.client.place_oco(
                inst_id=intent.inst_id,
                side=params["side"],
                stop=request.stop,
                tp1=request.tp1,
                client_id=cid,
            )
            self.store.update_leg(cid, "accepted", response)
        except DemoRequestRejected:
            self.store.update_leg(cid, "rejected")
            self.store.update(intent.content_digest, protection="protection_failed")
            return False
        except (DemoRequestUnknown, DemoUnavailable):
            self.store.update(intent.content_digest, protection="protection_unknown")
            return False
        # At most one POST per tick. An immediately consumed OCO is not fresh protection.
        result = self._leg_query(intent, {"client_id": cid, "leg": "oco"})
        if result.get("state") == "live":
            self._verify_protection(result, request)
            self.store.update(intent.content_digest, protection="protected")
            return True
        self.store.update(intent.content_digest, protection="protection_unknown")
        return False

    @staticmethod
    def _verify_protection(result, request):
        if (
            Decimal(result.get("slTriggerPx", "0")) != request.stop
            or Decimal(result.get("tpTriggerPx", "0")) != request.tp1
            or result.get("closeFraction") != "1"
            or result.get("ordType") != "oco"
            or result.get("reduceOnly") != "true"
            or result.get("posSide") != "net"
        ):
            raise DemoUnavailable("protection_parameters_mismatch")

    def _emergency(self, intent, request, quantity):
        legs = [l for l in self.store.legs(intent.content_digest) if l["leg"] == "emergency"]
        if (
            intent.schema_version == "okx_demo_intent_v1"
            and legs
            and legs[-1]["status"] not in LEG_TERMINAL
        ):
            self._leg_query(intent, legs[-1])
            return  # Unknown submit is never resent.
        generation = max((l["generation"] for l in legs), default=0) + 1
        params = {"quantity": str(quantity), "side": "sell" if request.side == "buy" else "buy"}
        cid = self.store.prepare_leg(
            intent.content_digest, "emergency", params, generation=generation
        )
        self.store.update_leg(cid, "submission_unknown")
        try:
            response = self.client.close_position(
                inst_id=intent.inst_id, side=params["side"], quantity=quantity, client_id=cid
            )
            self.store.update_leg(cid, "accepted", response)
        except DemoRequestRejected:
            self.store.update_leg(cid, "rejected")
        except (DemoRequestUnknown, DemoUnavailable):
            pass
        self.store.record_observation(
            intent.content_digest,
            "protection_failure_emergency",
            params,
            observed_at_ms=self.now_ms(),
        )

    def _manage(self, intent, snapshot):
        row = self.store.row(intent.content_digest)
        if not row["request"]:
            return
        request = DemoOrderRequest.model_validate_json(row["request"])
        entry_leg = next(
            (l for l in self.store.legs(intent.content_digest) if l["leg"] == "entry"), None
        )
        if not entry_leg:
            # Crash after save_prepared and before durable leg: no POST was released.
            self._finish(intent, "incomplete_preparation")
            return
        observed = self._leg_query(intent, entry_leg)
        if observed:
            self.store.update(
                intent.content_digest,
                entry_state=observed.get("state", "submission_unknown"),
                filled=observed.get("accFillSz", "0"),
            )
            latest = self.store.row(intent.content_digest)
            if observed.get("state") in ENTRY_TERMINAL and (
                not latest["terminal_at_ms"]
                or Decimal(latest["terminal_filled"]) != Decimal(latest["filled"])
            ):
                self.store.update(
                    intent.content_digest,
                    terminal_at_ms=self.now_ms(),
                    terminal_filled=latest["filled"],
                )
        position = next(
            (
                p
                for p in snapshot.positions
                if p.get("instId") == intent.inst_id and Decimal(p.get("pos", "0")) != 0
            ),
            None,
        )
        quantity = abs(Decimal(position["pos"])) if position else Decimal(0)
        filled = Decimal(self.store.row(intent.content_digest)["filled"])
        if position and (
            quantity > filled
            or (Decimal(position["pos"]) > 0) != (request.side == "buy")
            or position.get("mgnMode") != "isolated"
            or position.get("posSide") != "net"
        ):
            raise DemoUnavailable("unattributed_position")
        oco_consumed = False
        for leg in self.store.legs(intent.content_digest):
            if leg["leg"] == "oco" and leg["status"] not in LEG_TERMINAL:
                result = self._leg_query(intent, leg)
                oco_consumed |= result.get("state") == "effective"
        row = self.store.row(intent.content_digest)
        should_cancel = (
            quantity > 0
            or oco_consumed
            or self.now_ms() >= intent.expires_at_ms
            or row["reason"] == "superseded"
            or not self.config.entry_enabled
        )
        if quantity:
            try:
                protected = row["protection"] != "protection_failed" and self._protect(
                    intent, request
                )
            except DemoUnavailable:
                self.store.update(intent.content_digest, protection="protection_failed")
                protected = False
            if should_cancel:
                self._cancel_entry(intent, request)
            if not protected:
                self.store.update(intent.content_digest, protection="protection_failed")
                self._emergency(intent, request, quantity)
        else:
            if should_cancel:
                self._cancel_entry(intent, request)
            # Cancel residual protection only after parent entry is confirmed terminal.
            row = self.store.row(intent.content_digest)
            if (
                row["entry_state"] in ENTRY_TERMINAL
                and observed.get("state") in ENTRY_TERMINAL
                and Decimal(observed.get("accFillSz", "0")) >= Decimal(row["filled"])
                and snapshot.coverage.get("positions")
                and snapshot.observed_at_ms > row["terminal_at_ms"] > 0
            ):
                unresolved = not self._exits_terminal(intent)
                for leg in self.store.legs(intent.content_digest):
                    if leg["leg"] != "entry" and leg["status"] not in LEG_TERMINAL:
                        result = self._leg_query(intent, leg)
                        if result.get("state") not in LEG_TERMINAL:
                            unresolved = True
                            if leg["leg"] == "oco" and result.get("algoId"):
                                try:
                                    self.client.cancel_algo(intent.inst_id, result["algoId"])
                                except (DemoRequestRejected, DemoRequestUnknown, DemoUnavailable):
                                    pass
                if not unresolved:
                    self.store.update(
                        intent.content_digest, trade_state="closed", protection="none"
                    )

    def _accept(self, intent, snapshot):
        if intent.cutover_id != self.config.cutover_id:
            reason = "skipped_cutover"
        elif self.now_ms() >= intent.fresh_until_ms or self.now_ms() < intent.generated_at_ms:
            reason = "decision_expired"
        elif intent.inst_id not in self.config.symbols:
            reason = "instrument_disabled"
        else:
            reason = ""
        if reason:
            return DemoReceipt(
                intent_digest=intent.content_digest,
                task_id=intent.task_id,
                cutover_id=intent.cutover_id,
                disposition="rejected",
                reason=reason,
                accepted_at_ms=self.now_ms(),
            )
        old = self.store.register_intent(intent)
        return old or DemoReceipt(
            intent_digest=intent.content_digest,
            task_id=intent.task_id,
            cutover_id=intent.cutover_id,
            disposition="accepted",
            accepted_at_ms=self.now_ms(),
        )

    def _start(self, intent, snapshot):
        row = self.store.row(intent.content_digest)
        if row["request"]:
            return
        if intent.inst_id in self.config.independent_accepted_symbols or any(
            i.inst_id == intent.inst_id
            and i.schema_version == "okx_demo_intent_v2"
            and self.store.row(i.content_digest)["request"]
            for i in self.store.pending_intents()
        ):
            self._finish(intent, "legacy_intent_not_accepted")
            return
        if not self.store.is_latest(intent):
            self._finish(intent, "superseded_before_submit")
            return
        if self.now_ms() >= intent.fresh_until_ms:
            self._finish(intent, "decision_expired")
            return
        if intent.disposition == "rejected":
            self._finish(intent, intent.source_reason)
            return
        # Only newer validated decisions supersede earlier unfilled remainders.
        older = [
            i
            for i in self.store.pending_intents()
            if i.content_digest != intent.content_digest
            and i.inst_id == intent.inst_id
            and i.source_bar_ms < intent.source_bar_ms
            and self.store.row(i.content_digest)["request"]
        ]
        for previous in older:
            self.store.update(previous.content_digest, reason="superseded")
            request = DemoOrderRequest.model_validate_json(
                self.store.row(previous.content_digest)["request"]
            )
            self._cancel_entry(previous, request)
        if intent.disposition == "no_trade":
            self._finish(intent, "")
            return
        if any(
            Decimal(p.get("pos", "0")) != 0 and p.get("instId") == intent.inst_id
            for p in snapshot.positions
        ):
            self._finish(intent, "position_exists")
            return
        if older:
            return  # Wait for final cancel and possible late fills before replacing.
        block = self._entry_block(snapshot)
        if block:
            self.error = block
            return
        if intent.inst_id not in self.config.accepted_symbols:
            self._finish(intent, "instrument_not_live_accepted")
            return
        self._submit_limit(intent, snapshot)

    def _submit_limit(self, intent, snapshot):
        if Decimal(snapshot.leverage.get(intent.inst_id, "0")) != self.config.leverage:
            self._finish(intent, "leverage_mismatch")
            return
        contract = next((c for c in snapshot.contracts if c.inst_id == intent.inst_id), None)
        if contract is None:
            self._finish(intent, "contract_unavailable")
            return
        quote = snapshot.quotes.get(intent.inst_id, {})
        try:
            ask, bid = Decimal(quote["askPx"]), Decimal(quote["bidPx"])
            if (
                not ask.is_finite()
                or not bid.is_finite()
                or not 0 < bid <= ask
                or not 0 <= self.now_ms() - int(quote["ts"]) <= 5000
            ):
                raise ValueError("stale_target_quote")
        except (KeyError, ValueError, ArithmeticError):
            self.error = "target_quote_unavailable"
            return
        if (intent.side == "buy" and ask <= intent.stop) or (
            intent.side == "sell" and bid >= intent.stop
        ):
            self._finish(intent, "target_price_beyond_original_stop")
            return
        try:
            request = bind_demo_limit(
                intent,
                contract,
                account_key=snapshot.account_key,
                available_margin=(snapshot.available or Decimal(0))
                - self.store.unreflected_margin(snapshot),
            )
        except ValueError as exc:
            self._finish(intent, str(exc))
            return
        self.store.save_prepared(intent, request)
        cid = self.store.prepare_leg(
            intent.content_digest, "entry", request.model_dump(mode="json"), generation=1
        )
        self.store.update_leg(cid, "submission_unknown")
        self.store.update(intent.content_digest, entry_state="submission_unknown")
        try:
            response = self.client.place_limit(request)
        except DemoRequestRejected as exc:
            self.store.update_leg(cid, "rejected")
            self.store.update(
                intent.content_digest,
                entry_state="rejected",
                trade_state="rejected",
                reason=str(exc),
            )
        except (DemoRequestUnknown, DemoUnavailable):
            return
        else:
            self.store.update_leg(cid, "accepted", response)
            self.store.update(intent.content_digest, entry_state="submitted")

    def tick(self):
        self.error = ""
        try:
            snapshot = self.reconcile()
            # Gather all per-plan fills before comparing them with a shared net position.
            for intent in self.store.pending_intents():
                if intent.schema_version == "okx_demo_intent_v2":
                    self.independent.observe(intent)
            for intent in self.store.pending_intents():
                if intent.schema_version == "okx_demo_intent_v2":
                    self.independent.manage(intent, snapshot)
                else:
                    self._manage(intent, snapshot)
            for intent in self.spool.pending():
                self.spool.ack(self._accept(intent, snapshot))
            for intent in self.store.pending_intents():
                if intent.schema_version == "okx_demo_intent_v2":
                    self.independent.start(intent, snapshot)
                else:
                    self._start(intent, snapshot)
            self.error = self.error or self._account_block(snapshot)
        except (DemoUnavailable, DemoRequestUnknown, DemoRequestRejected) as exc:
            self.error = str(exc)
        if self.snapshot is not None:
            from pa_agent.operations.demo_trading import build_demo_projection

            projection = build_demo_projection(self.store, self.snapshot, now_ms=self.now_ms())
            if self.error:
                projection.update(state="blocked", reason=self.error)
            atomic_json(self.paths.projection, projection)
            return projection
        return {"state": "unavailable", "reason": self.error}

    def close(self):
        self.lock.close()


def backup_demo(paths, directory: Path, *, now_ms: int):
    """Consistent own-ledger backup. Never traverse adjacent credentials or Host DBs."""
    if not paths.state_db.is_file():
        raise ValueError("demo_database_missing")
    lock = WorkerLock(paths.state_db.parent / "worker.lock")
    try:
        return _backup_locked(paths, directory, now_ms=now_ms)
    finally:
        lock.close()


def _backup_locked(paths, directory: Path, *, now_ms: int):
    import hashlib
    import sqlite3
    from pa_agent.execution.demo_contracts import canonical

    if not paths.state_db.is_file() or paths.state_db.is_symlink():
        raise ValueError("demo_database_missing")
    destination = Path(directory) / f"demo-{now_ms}"
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    with closing(
        sqlite3.connect(paths.state_db.resolve().as_uri() + "?mode=ro", uri=True)
    ) as source:
        if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("demo_database_corrupt")
        with closing(sqlite3.connect(destination / "execution.sqlite3")) as target:
            source.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("demo_backup_corrupt")
    if paths.projection.exists():
        atomic_json(destination / "projection.json", read_json(paths.projection), mode=0o600)
    for receipt in paths.receipts.glob("intent-*.json"):
        from pa_agent.execution.demo_contracts import DemoReceipt

        parsed = DemoReceipt.model_validate(read_json(receipt))
        atomic_json(
            destination / "receipts" / receipt.name, parsed.model_dump(mode="json"), mode=0o600
        )
    files = {
        str(p.relative_to(destination))
        .replace("\\", "/"): hashlib.sha256(p.read_bytes())
        .hexdigest()
        for p in destination.rglob("*")
        if p.is_file()
    }
    (destination / "manifest.json").write_text(
        canonical(
            {
                "schema_version": 1,
                "created_at_ms": now_ms,
                "environment": "okx_demo_v1",
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    return destination


def _prepare_cutover(config, paths, *, now_ms):
    from pa_agent.execution.demo_dispatch import apply_execution_cutover, cutover_fingerprint
    from pa_agent.operations.runtime_state import RuntimeStatusStore

    host_path = Path(
        os.environ.get("PA_AGENT_HOST_STATUS", "/var/lib/pa-agent-control/status/status.json")
    )
    if host_path.is_symlink():
        raise ValueError("cutover_host_invalid")
    host = RuntimeStatusStore(host_path).read()
    if host is None:
        raise ValueError("cutover_host_invalid")
    worker = read_json(paths.projection)
    if not 0 <= now_ms - int(host.get("updated_at_ms", 0)) <= 30000:
        raise ValueError("cutover_host_stale")
    scheduler = host.get("scheduler", {})
    if any(type(scheduler.get(k)) is not int for k in ("running", "queued", "unfinished")):
        raise ValueError("cutover_scheduler_unknown")
    if not 0 <= now_ms - int(worker.get("exchange_observed_at_ms", 0)) <= 30000:
        raise ValueError("cutover_worker_stale")
    safe = (
        worker.get("state") == "ready"
        and worker.get("coverage")
        and all(worker["coverage"].values())
        and worker.get("total_active_trades") == 0
        and worker.get("outside_orders") == 0
        and not worker.get("positions")
    )
    apply_execution_cutover(
        paused=host.get("service_state") == "paused",
        host_idle=scheduler["running"] == 0
        and scheduler["queued"] == 0
        and scheduler["unfinished"] == 0,
        worker_idle=safe,
        recovery_blocked=bool(host.get("recovery_blocked_assets")),
        config=config,
    )
    atomic_json(
        paths.intents / "cutover.json",
        {"config_digest": cutover_fingerprint(config), "at_ms": now_ms},
    )


def main(argv=None):
    """No option enables real trading; --probe is explicit GET-only live access."""
    import argparse
    import signal
    import threading
    import time
    from pa_agent.config.paths import resolve_runtime_paths
    from pa_agent.execution.demo_contracts import resolve_demo_paths
    from pa_agent.execution.demo_dispatch import load_demo_config, require_cutover
    from pa_agent.execution.demo_store import DemoStore
    from pa_agent.execution.demo_spool import DemoSpool
    from pa_agent.execution.okx_demo import OkxDemoClient, load_demo_credentials

    parser = argparse.ArgumentParser(description="OKX Demo-only executor; defaults disabled")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check", action="store_true", help="offline config check; no credentials or network"
    )
    group.add_argument("--probe", action="store_true", help="explicit private GET-only preflight")
    group.add_argument("--backup", type=Path, help="consistent own-ledger backup; no network")
    group.add_argument(
        "--prepare-cutover", action="store_true", help="Host user: certify paused/idle cutover"
    )
    args = parser.parse_args(argv)
    clock = lambda: int(time.time() * 1000)
    client, worker = None, None
    try:
        config = load_demo_config()
        paths = resolve_demo_paths(resolve_runtime_paths(), environ=os.environ)
        if args.check:
            if config.enabled:
                for directory, access in (
                    (paths.intents, os.R_OK | os.X_OK),
                    (paths.receipts, os.R_OK | os.W_OK | os.X_OK),
                    (paths.state_db.parent, os.R_OK | os.W_OK | os.X_OK),
                    (paths.projection.parent, os.R_OK | os.W_OK | os.X_OK),
                ):
                    if (
                        directory.is_symlink()
                        or not directory.is_dir()
                        or not os.access(directory, access)
                    ):
                        raise ValueError("demo_directory_permissions")
            print(
                "OKX Demo configuration: "
                + ("enabled; entry gated" if config.enabled else "disabled")
            )
            return 0
        if args.backup is not None:
            backup_demo(paths, args.backup, now_ms=clock())
            print("OKX Demo backup: complete")
            return 0
        if args.prepare_cutover:
            _prepare_cutover(config, paths, now_ms=clock())
            print("OKX Demo cutover: accepted at paused/idle boundary")
            return 0
        if not config.enabled and not args.probe:
            active = paths.state_db.is_file() and bool(DemoStore(paths.state_db).pending_intents())
            if not active:
                print("OKX Demo disabled")
                return 0
            # Disabling entry never abandons an existing exchange position/order.
            print("OKX Demo entry disabled; existing trades still require reconciliation")
        credential_dir = os.environ.get("CREDENTIALS_DIRECTORY")
        if not credential_dir:
            raise DemoUnavailable("credential_directory_required")
        client = OkxDemoClient(
            load_demo_credentials(Path(credential_dir)), instruments=config.symbols
        )
        if args.probe:
            snapshot = client.snapshot()
            if config.account_key and config.account_key != snapshot.account_key:
                raise DemoUnavailable("account_identity_mismatch")
            if snapshot.account_mode != "2" or snapshot.position_mode != "net_mode":
                raise DemoUnavailable("account_mode_mismatch")
            if not snapshot.coverage or not all(snapshot.coverage.values()):
                raise DemoUnavailable("reconciliation_gap")
            if (
                snapshot.orders
                or snapshot.algos
                or any(Decimal(p.get("pos", "0")) != 0 for p in snapshot.positions)
            ):
                raise DemoUnavailable("preflight_account_not_empty")
            if abs(snapshot.server_time_ms - clock()) > 5000:
                raise DemoUnavailable("clock_mismatch")
            if (
                set(config.symbols) != {c.inst_id for c in snapshot.contracts}
                or any(
                    Decimal(snapshot.leverage.get(inst, "0")) != config.leverage
                    for inst in config.symbols
                )
                or snapshot.available is None
                or snapshot.available <= 0
            ):
                raise DemoUnavailable("preflight_contract_leverage_or_balance")
            print("OKX Demo read-only probe complete; account fingerprint: " + snapshot.account_key)
            print("Protection support and real order lifecycle: NOT verified")
            return 0
        if config.entry_enabled:
            require_cutover(paths, config)
        worker = DemoWorker(
            config, paths, client, DemoStore(paths.state_db), DemoSpool(paths), now_ms=clock
        )
        stopped = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stopped.set())
        while not stopped.is_set():
            projection = worker.tick()
            # REST limits and errors back off; protection reconciliation continues.
            active = bool(worker.store.pending_intents())
            stopped.wait(10 if projection.get("state") != "ready" else 2 if active else 10)
        worker.config = config.model_copy(update={"entry_enabled": False})
        worker.tick()  # Best-effort remainder cancel, never claim it confirmed on shutdown.
        return 0
    except (OSError, ValueError, RuntimeError, ArithmeticError):
        print(
            "OKX Demo operation failed; configuration, permissions or reconciliation require review"
        )
        return 2  # Never print raw validation/HTTP errors or credentials.
    finally:
        if worker is not None:
            worker.close()
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
