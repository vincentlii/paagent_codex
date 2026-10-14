"""Independent Demo ledger. Network operations never occur inside a transaction."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from pa_agent.execution.demo_contracts import (
    DemoIntent,
    DemoOrderRequest,
    DemoReceipt,
    canonical,
    digest,
    client_id,
)

ENTRY_TERMINAL = frozenset({"filled", "canceled", "rejected"})
LEG_TERMINAL = frozenset({"effective", "canceled", "order_failed", "rejected", "filled"})


class DemoStore:
    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        if self.path.is_symlink():
            raise ValueError("invalid_demo_database_path")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if (
                tables
                and not {"metadata", "intents", "legs", "watermarks", "events", "fills", "bills"}
                <= tables
            ):
                raise ValueError("foreign_database")
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
                raise ValueError("unsupported_demo_schema")
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS intents(
                    id TEXT PRIMARY KEY,task TEXT NOT NULL,epoch TEXT NOT NULL,body TEXT NOT NULL,
                    receipt TEXT NOT NULL,request TEXT,entry_state TEXT NOT NULL DEFAULT 'queued',
                    protection TEXT NOT NULL DEFAULT 'none',trade_state TEXT NOT NULL DEFAULT 'active',
                    filled TEXT NOT NULL DEFAULT '0',reason TEXT NOT NULL DEFAULT '',
                    terminal_at_ms INTEGER NOT NULL DEFAULT 0,terminal_filled TEXT NOT NULL DEFAULT '0',
                    UNIQUE(task,epoch));
                CREATE TABLE IF NOT EXISTS legs(
                    client_id TEXT PRIMARY KEY,intent_id TEXT NOT NULL,leg TEXT NOT NULL,generation INTEGER NOT NULL,
                    params TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'prepared',response TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(intent_id,leg,generation));
                CREATE TABLE IF NOT EXISTS watermarks(key TEXT PRIMARY KEY,bar_ms INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,intent_id TEXT,kind TEXT,payload TEXT,at_ms INTEGER);
                CREATE TABLE IF NOT EXISTS fills(key TEXT PRIMARY KEY,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bills(key TEXT PRIMARY KEY,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS triggered_orders(
                    parent_client_id TEXT NOT NULL,inst_id TEXT NOT NULL,order_id TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'unknown',body TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(parent_client_id,order_id));
            """)
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def quick_check(self):
        with self.connect() as db:
            return db.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    def pin_account(self, account):
        if not account:
            raise ValueError("account_not_pinned")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO metadata VALUES('account',?)", (account,))
            if (
                db.execute("SELECT value FROM metadata WHERE key='account'").fetchone()[0]
                != account
            ):
                raise ValueError("account_identity_changed")

    def register_intent(self, intent):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT id,receipt FROM intents WHERE task=? AND epoch=?",
                (intent.task_id, intent.cutover_id),
            ).fetchone()
            if old:
                if old["id"] != intent.content_digest:
                    raise ValueError("intent_identity_conflict")
                return DemoReceipt.model_validate_json(old["receipt"])
            key = canonical([intent.source_identity, intent.inst_id, intent.timeframe])
            row = db.execute("SELECT bar_ms FROM watermarks WHERE key=?", (key,)).fetchone()
            stale = row is not None and intent.source_bar_ms <= row[0]
            receipt = DemoReceipt(
                intent_digest=intent.content_digest,
                task_id=intent.task_id,
                cutover_id=intent.cutover_id,
                disposition="rejected" if stale else "accepted",
                reason="stale_decision" if stale else "",
                accepted_at_ms=intent.generated_at_ms,
            )
            db.execute(
                "INSERT INTO intents(id,task,epoch,body,receipt,trade_state) VALUES(?,?,?,?,?,?)",
                (
                    intent.content_digest,
                    intent.task_id,
                    intent.cutover_id,
                    intent.model_dump_json(),
                    receipt.model_dump_json(),
                    "rejected" if stale else "active",
                ),
            )
            if not stale:
                db.execute(
                    "INSERT INTO watermarks VALUES(?,?) ON CONFLICT(key) DO UPDATE SET bar_ms=excluded.bar_ms",
                    (key, intent.source_bar_ms),
                )
            return receipt if stale else None

    def save_prepared(self, intent, request):
        with self.connect() as db:
            row = db.execute(
                "SELECT request FROM intents WHERE id=?", (intent.content_digest,)
            ).fetchone()
            if row is None or (
                row[0] is not None and DemoOrderRequest.model_validate_json(row[0]) != request
            ):
                raise ValueError("request_semantics_conflict")
            db.execute(
                "UPDATE intents SET request=? WHERE id=?",
                (request.model_dump_json(), intent.content_digest),
            )

    def is_latest(self, intent):
        key = canonical([intent.source_identity, intent.inst_id, intent.timeframe])
        with self.connect() as db:
            row = db.execute("SELECT bar_ms FROM watermarks WHERE key=?", (key,)).fetchone()
        return row is not None and row[0] == intent.source_bar_ms

    def prepare_leg(self, intent_id, leg, params, *, generation):
        if (
            leg not in {"entry", "oco", "emergency"}
            or generation < 1
            or (leg == "entry" and generation != 1)
        ):
            raise ValueError("invalid_leg_generation")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body,request FROM intents WHERE id=?", (intent_id,)).fetchone()
            intent = DemoIntent.model_validate_json(row["body"])
            request = DemoOrderRequest.model_validate_json(row["request"])
            cid = client_id(request.account_key, intent, leg, generation)
            old = db.execute("SELECT params FROM legs WHERE client_id=?", (cid,)).fetchone()
            if old:
                if old[0] != canonical(params):
                    raise ValueError("leg_semantics_conflict")
                return cid
            previous = db.execute(
                "SELECT generation,status FROM legs WHERE intent_id=? AND leg=? ORDER BY generation DESC LIMIT 1",
                (intent_id, leg),
            ).fetchone()
            if generation > 1 and (
                previous is None or previous[0] != generation - 1 or previous[1] not in LEG_TERMINAL
            ):
                raise ValueError("previous_leg_not_terminal")
            db.execute(
                "INSERT INTO legs(client_id,intent_id,leg,generation,params) VALUES(?,?,?,?,?)",
                (cid, intent_id, leg, generation, canonical(params)),
            )
            return cid

    def legs(self, intent_id):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM legs WHERE intent_id=? ORDER BY generation", (intent_id,)
                )
            ]

    def update_leg(self, cid, status, response=None):
        with self.connect() as db:
            previous = db.execute(
                "SELECT status,response FROM legs WHERE client_id=?", (cid,)
            ).fetchone()
            if previous and previous["status"] in LEG_TERMINAL and status not in LEG_TERMINAL:
                return
            body = json.loads(previous["response"]) if previous else {}
            body.update(response or {})
            db.execute(
                "UPDATE legs SET status=?,response=? WHERE client_id=?",
                (status, canonical(body), cid),
            )

    def register_triggered(self, parent_client_id, inst_id, order_ids):
        with self.connect() as db:
            for order_id in order_ids:
                db.execute(
                    "INSERT OR IGNORE INTO triggered_orders(parent_client_id,inst_id,order_id) VALUES(?,?,?)",
                    (parent_client_id, inst_id, order_id),
                )

    def triggered_orders(self, intent_id):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT t.* FROM triggered_orders t JOIN legs l "
                    "ON l.client_id=t.parent_client_id WHERE l.intent_id=?",
                    (intent_id,),
                )
            ]

    def observe_triggered(self, parent_client_id, order_id, response):
        with self.connect() as db:
            old = db.execute(
                "SELECT state FROM triggered_orders WHERE parent_client_id=? AND order_id=?",
                (parent_client_id, order_id),
            ).fetchone()
            state = str(response.get("state", "unknown"))
            if old and old[0] in ENTRY_TERMINAL and state not in ENTRY_TERMINAL:
                return
            db.execute(
                "UPDATE triggered_orders SET state=?,body=? WHERE parent_client_id=? AND order_id=?",
                (state, canonical(response), parent_client_id, order_id),
            )

    def row(self, intent_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM intents WHERE id=?", (intent_id,)).fetchone()
            return dict(row) if row else None

    def update(self, intent_id, **values):
        allowed = {
            "entry_state",
            "protection",
            "trade_state",
            "filled",
            "reason",
            "terminal_at_ms",
            "terminal_filled",
        }
        if not values or not set(values) <= allowed:
            raise ValueError("invalid_state_update")
        with self.connect() as db:
            old = db.execute("SELECT * FROM intents WHERE id=?", (intent_id,)).fetchone()
            if "filled" in values:
                fill = Decimal(values["filled"])
                if not fill.is_finite() or fill < 0:
                    raise ValueError("invalid_fill")
                values["filled"] = str(max(fill, Decimal(old["filled"])))
            if old["entry_state"] in ENTRY_TERMINAL:
                values.pop("entry_state", None)
            elif (
                Decimal(values.get("filled", old["filled"])) > 0
                and values.get("entry_state") == "live"
            ):
                values["entry_state"] = "partially_filled"
            if values:
                db.execute(
                    "UPDATE intents SET " + ",".join(f"{k}=?" for k in values) + " WHERE id=?",
                    (*values.values(), intent_id),
                )

    def pending_intents(self):
        with self.connect() as db:
            return tuple(
                DemoIntent.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT body FROM intents WHERE trade_state='active' ORDER BY rowid"
                )
            )

    def record_observation(self, intent_id, kind, payload, *, observed_at_ms):
        with self.connect() as db:
            db.execute(
                "INSERT INTO events(intent_id,kind,payload,at_ms) VALUES(?,?,?,?)",
                (intent_id, kind, canonical(payload), observed_at_ms),
            )

    def apply_snapshot(self, snapshot):
        self.pin_account(snapshot.account_key)
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO metadata VALUES('ledger_start_ms',?)",
                (str(snapshot.observed_at_ms),),
            )
            db.execute(
                "INSERT INTO metadata VALUES('snapshot',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (snapshot.model_dump_json(),),
            )
            for table, rows, keys in (
                ("fills", snapshot.fills, ("instId", "tradeId")),
                ("bills", snapshot.bills, ("billId",)),
            ):
                for row in rows:
                    if not all(row.get(key) for key in keys):
                        raise ValueError("missing_exchange_event_identity")
                    identity = digest([snapshot.account_key, *[row[key] for key in keys]])
                    db.execute(
                        f"INSERT OR IGNORE INTO {table} VALUES(?,?)", (identity, canonical(row))
                    )
            for name, rows, complete in (
                ("bill_cursor", snapshot.bills, snapshot.coverage.get("bills")),
                ("fill_cursor", snapshot.fills, snapshot.coverage.get("fills")),
            ):
                if complete and rows and rows[0].get("billId"):
                    db.execute(
                        "INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (name, rows[0]["billId"]),
                    )

    def sync_cursors(self):
        with self.connect() as db:
            values = dict(
                db.execute(
                    "SELECT key,value FROM metadata WHERE key IN ('bill_cursor','fill_cursor')"
                )
            )
        return values.get("bill_cursor"), (
            {"SWAP": values["fill_cursor"]} if values.get("fill_cursor") else {}
        )

    def unreflected_margin(self, snapshot):
        total = Decimal(0)
        for intent in self.pending_intents():
            row = self.row(intent.content_digest)
            if row["request"]:
                request = DemoOrderRequest.model_validate_json(row["request"])
                # Only this balance snapshot can prove exchange-side reservation;
                # a later GET acknowledging a live order does not prove availBal saw it.
                reflected = any(o.get("clOrdId") == request.client_id for o in snapshot.orders)
                if not reflected and row["entry_state"] not in ENTRY_TERMINAL:
                    position = next(
                        (p for p in snapshot.positions if p.get("instId") == intent.inst_id), {}
                    )
                    known_filled = min(
                        abs(Decimal(position.get("pos", "0"))), Decimal(row["filled"]), request.sz
                    )
                    total += request.margin * (1 - known_filled / request.sz)
        return total

    def reserved_margin(self):
        total = Decimal(0)
        for intent in self.pending_intents():
            row = self.row(intent.content_digest)
            if row["request"] and row["entry_state"] not in ENTRY_TERMINAL:
                request = DemoOrderRequest.model_validate_json(row["request"])
                total += request.margin * max(Decimal(0), 1 - Decimal(row["filled"]) / request.sz)
        return total

    def projection_rows(self, *, now_ms):
        with self.connect() as db:
            start = db.execute("SELECT value FROM metadata WHERE key='ledger_start_ms'").fetchone()
            return {
                "intents": [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM intents ORDER BY (trade_state='active') DESC,rowid DESC LIMIT 150"
                    )
                ],
                "fills": [
                    json.loads(r[0])
                    for r in db.execute("SELECT body FROM fills ORDER BY rowid DESC LIMIT 150")
                ][::-1],
                "bills": [
                    json.loads(r[0])
                    for r in db.execute("SELECT body FROM bills ORDER BY rowid LIMIT 20000")
                ],
                "ledger_complete": db.execute("SELECT COUNT(*) FROM bills").fetchone()[0] <= 20000,
                "ledger_start_ms": int(start[0]) if start else None,
                "total_orders": db.execute(
                    "SELECT COUNT(*) FROM intents WHERE request IS NOT NULL"
                ).fetchone()[0],
                "total_decisions": db.execute("SELECT COUNT(*) FROM intents").fetchone()[0],
            }
