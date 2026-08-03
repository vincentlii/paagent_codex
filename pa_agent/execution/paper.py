"""Deterministic paper execution for validated analysis plans."""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pa_agent.data.base import KlineBar
from pa_agent.data.instrument import InstrumentSpec

Side = Literal["long", "short"]
PaperOrderType = Literal["market", "limit", "breakout"]


@dataclass(frozen=True)
class RiskPolicy:
    risk_fraction: float = 0.01
    max_portfolio_risk_fraction: float = 0.03
    max_leverage: float = 3.0

    def __post_init__(self) -> None:
        if not 0 < self.risk_fraction <= 1:
            raise ValueError("risk_fraction must be in (0, 1]")
        if not 0 < self.max_portfolio_risk_fraction <= 1:
            raise ValueError("max_portfolio_risk_fraction must be in (0, 1]")
        if self.max_leverage <= 0:
            raise ValueError("max_leverage must be positive")


@dataclass(frozen=True)
class PositionSize:
    quantity: float
    notional: float
    risk_budget: float
    structural_risk: float
    leverage: float


@dataclass(frozen=True)
class PaperTradePlan:
    run_id: str
    arm: str
    symbol: str
    timeframe: str
    side: Side
    order_type: PaperOrderType
    entry_price: float
    stop_price: float
    take_profit_price: float
    take_profit_price_2: float | None
    created_bar_ts: int
    dedupe_key: str | None = None


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    status: str
    quantity: float
    plan: PaperTradePlan


def size_for_structural_stop(
    *, equity: float, entry_price: float, stop_price: float,
    spec: InstrumentSpec, policy: RiskPolicy,
    risk_budget_limit: float | None = None,
    notional_limit: float | None = None,
) -> PositionSize:
    """Size by structural stop risk, then apply venue and leverage constraints."""
    equity = float(equity)
    entry_price = float(entry_price)
    stop_price = float(stop_price)
    if equity <= 0 or entry_price <= 0:
        raise ValueError("equity and entry price must be positive")
    unit_risk = abs(entry_price - stop_price) * spec.contract_multiplier
    if unit_risk <= 0:
        raise ValueError("structural stop must differ from entry")
    if spec.quantity_step <= 0:
        raise ValueError("quantity step must be positive")
    risk_budget = equity * policy.risk_fraction
    if risk_budget_limit is not None:
        risk_budget = min(risk_budget, max(0.0, float(risk_budget_limit)))
    leverage_notional = equity * policy.max_leverage
    if notional_limit is not None:
        leverage_notional = min(leverage_notional, max(0.0, float(notional_limit)))
    raw_quantity = min(
        risk_budget / unit_risk,
        leverage_notional / (entry_price * spec.contract_multiplier),
        spec.maximum_quantity,
    )
    quantity = math.floor((raw_quantity + 1e-12) / spec.quantity_step) * spec.quantity_step
    quantity = round(quantity, 12)
    notional = quantity * spec.contract_multiplier * entry_price
    if quantity < spec.minimum_quantity:
        raise ValueError("position is below minimum quantity")
    if notional < spec.minimum_notional:
        raise ValueError("position is below minimum notional")
    return PositionSize(
        quantity=quantity,
        notional=notional,
        risk_budget=risk_budget,
        structural_risk=quantity * unit_risk,
        leverage=notional / equity,
    )


class PaperBroker:
    """Persistent closed-bar paper broker with conservative intrabar ordering."""

    def __init__(
        self, db_path: Path, *, account_id: str, initial_equity: float,
        risk_policy: RiskPolicy, fee_bps: float = 5.0, slippage_bps: float = 2.0,
    ) -> None:
        if initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        if fee_bps < 0 or slippage_bps < 0:
            raise ValueError("fee and slippage must be non-negative")
        self._db_path = Path(db_path)
        self._account_id = str(account_id).strip()
        if not self._account_id:
            raise ValueError("account_id is required")
        self._policy = risk_policy
        self._fee_bps = float(fee_bps)
        self._slippage_bps = float(slippage_bps)
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO paper_accounts(account_id, equity, initial_equity) VALUES (?, ?, ?)",
                (self._account_id, float(initial_equity), float(initial_equity)),
            )

    def submit(self, plan: PaperTradePlan, spec: InstrumentSpec) -> PaperOrder:
        self._validate_plan(plan, spec)
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM paper_orders WHERE account_id = ? AND arm = ? "
                "AND ((dedupe_key IS NOT NULL AND dedupe_key = ?) "
                "OR (dedupe_key IS NULL AND run_id = ?))",
                (self._account_id, plan.arm, plan.dedupe_key, plan.run_id),
            ).fetchone()
            if existing is not None:
                return self._paper_order(existing)
            equity = float(connection.execute(
                "SELECT equity FROM paper_accounts WHERE account_id = ?", (self._account_id,)
            ).fetchone()[0])
            active_risk, active_notional = self._active_exposure(connection)
            sized = size_for_structural_stop(
                equity=equity, entry_price=plan.entry_price, stop_price=plan.stop_price,
                spec=spec, policy=self._policy,
                risk_budget_limit=(
                    equity * self._policy.max_portfolio_risk_fraction - active_risk
                ),
                notional_limit=(equity * self._policy.max_leverage - active_notional),
            )
            order_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO paper_orders(
                    order_id, account_id, run_id, arm, provider, venue, instrument_type,
                    symbol, timeframe, side, order_type, entry_price, stop_price,
                    take_profit_price, take_profit_price_2, created_bar_ts, dedupe_key,
                    quantity, contract_multiplier, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    order_id, self._account_id, plan.run_id, plan.arm, spec.provider,
                    spec.venue, spec.instrument_type, spec.symbol, plan.timeframe, plan.side,
                    plan.order_type, plan.entry_price, plan.stop_price, plan.take_profit_price,
                    plan.take_profit_price_2, int(plan.created_bar_ts), plan.dedupe_key,
                    sized.quantity, spec.contract_multiplier,
                ),
            )
            self._event(connection, order_id, "submitted", None, {"sizing": asdict(sized)})
            self._replay_stored_bars(connection, order_id)
            row = connection.execute(
                "SELECT * FROM paper_orders WHERE order_id = ?", (order_id,)
            ).fetchone()
            return self._paper_order(row)

    def _replay_stored_bars(
        self,
        connection: sqlite3.Connection,
        order_id: str,
    ) -> None:
        order = connection.execute(
            "SELECT * FROM paper_orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        evaluated = (
            int(order["evaluated_through_ts"])
            if order["evaluated_through_ts"] is not None
            else int(order["created_bar_ts"])
        )
        bars = connection.execute(
            "SELECT * FROM paper_market_bars WHERE provider = ? AND venue = ? "
            "AND instrument_type = ? AND symbol = ? AND timeframe = ? AND ts_open > ? "
            "ORDER BY ts_open",
            (
                order["provider"], order["venue"], order["instrument_type"],
                order["symbol"], order["timeframe"], evaluated,
            ),
        ).fetchall()
        for raw in bars:
            order = connection.execute(
                "SELECT * FROM paper_orders WHERE order_id = ?", (order_id,)
            ).fetchone()
            if order["status"] not in {"pending", "open"}:
                break
            self._advance(
                connection,
                order,
                KlineBar(
                    seq=1,
                    ts_open=int(raw["ts_open"]),
                    open=float(raw["open"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    close=float(raw["close"]),
                    volume=float(raw["volume"]),
                    amount=float(raw["amount"]),
                    closed=True,
                ),
            )
            connection.execute(
                "UPDATE paper_orders SET evaluated_through_ts = ? WHERE order_id = ?",
                (int(raw["ts_open"]), order_id),
            )

    def observe_market(
        self,
        spec: InstrumentSpec,
        timeframe: str,
        bars: list[KlineBar] | tuple[KlineBar, ...],
    ) -> int:
        """Persist and advance one exact market in chronological closed-bar order."""
        closed = sorted(
            (bar for bar in bars if bar.closed),
            key=lambda bar: int(bar.ts_open),
        )
        if not closed:
            return 0
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            market_key = (
                spec.provider, spec.venue, spec.instrument_type, spec.symbol,
                str(timeframe),
            )
            observed = 0
            for bar in closed:
                bar_ts = int(bar.ts_open)
                existing = connection.execute(
                    "SELECT open, high, low, close, volume, amount FROM paper_market_bars "
                    "WHERE provider = ? AND venue = ? AND instrument_type = ? "
                    "AND symbol = ? AND timeframe = ? AND ts_open = ?",
                    (*market_key, bar_ts),
                ).fetchone()
                facts = (
                    float(bar.open), float(bar.high), float(bar.low), float(bar.close),
                    float(bar.volume), float(getattr(bar, "amount", 0.0)),
                )
                if existing is not None:
                    if tuple(float(value) for value in existing) != facts:
                        raise ValueError(
                            "conflicting closed bar for paper market identity and timestamp"
                        )
                    continue
                connection.execute(
                    "INSERT INTO paper_market_bars(provider, venue, instrument_type, "
                    "symbol, timeframe, ts_open, open, high, low, close, volume, amount) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*market_key, bar_ts, *facts),
                )
                observed += 1
            orders = connection.execute(
                "SELECT order_id FROM paper_orders WHERE account_id = ? AND provider = ? "
                "AND venue = ? AND instrument_type = ? AND symbol = ? AND timeframe = ? "
                "AND status IN ('pending', 'open') ORDER BY created_bar_ts, order_id",
                (self._account_id, *market_key),
            ).fetchall()
            for order in orders:
                self._replay_stored_bars(connection, str(order["order_id"]))
            return observed

    def account_snapshot(self) -> dict[str, Any]:
        with self._connect() as connection:
            self._ensure_schema(connection)
            account = connection.execute(
                "SELECT * FROM paper_accounts WHERE account_id = ?", (self._account_id,)
            ).fetchone()
            open_count = connection.execute(
                "SELECT COUNT(*) FROM paper_orders WHERE account_id = ? AND status = 'open'",
                (self._account_id,),
            ).fetchone()[0]
            result = dict(account)
            result["open_position_count"] = int(open_count)
            return result

    def order(self, order_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            self._ensure_schema(connection)
            row = connection.execute(
                "SELECT * FROM paper_orders WHERE account_id = ? AND order_id = ?",
                (self._account_id, order_id),
            ).fetchone()
            return dict(row) if row is not None else None

    def events(self, order_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._ensure_schema(connection)
            rows = connection.execute(
                "SELECT * FROM paper_events WHERE order_id = ? ORDER BY event_id", (order_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def record_rejection(
        self,
        plan: PaperTradePlan,
        spec: InstrumentSpec,
        reason: str,
    ) -> None:
        """Persist a non-executed candidate without changing analysis status."""
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.execute(
                """
                INSERT OR IGNORE INTO paper_rejections(
                    account_id, run_id, arm, dedupe_key, provider, venue,
                    instrument_type, symbol, timeframe, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._account_id, plan.run_id, plan.arm, plan.dedupe_key,
                    spec.provider, spec.venue, spec.instrument_type, spec.symbol,
                    plan.timeframe, str(reason),
                ),
            )

    def supersede_pending(
        self,
        spec: InstrumentSpec,
        timeframe: str,
        *,
        as_of_bar_ts: int,
        excluding_dedupe_key: str,
    ) -> int:
        """Cancel stale unfilled plans after later market bars were replayed."""
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT order_id FROM paper_orders WHERE account_id = ? "
                "AND provider = ? AND venue = ? AND instrument_type = ? "
                "AND symbol = ? AND timeframe = ? AND status = 'pending' "
                "AND created_bar_ts <= ? AND COALESCE(dedupe_key, '') != ?",
                (
                    self._account_id, spec.provider, spec.venue,
                    spec.instrument_type, spec.symbol, str(timeframe),
                    int(as_of_bar_ts), str(excluding_dedupe_key),
                ),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE paper_orders SET status = 'cancelled', "
                    "exit_reason = 'superseded_by_new_analysis' WHERE order_id = ?",
                    (row["order_id"],),
                )
                self._event(
                    connection,
                    str(row["order_id"]),
                    "cancelled",
                    int(as_of_bar_ts),
                    {"reason": "superseded_by_new_analysis"},
                )
            return len(rows)

    def _advance(self, connection: sqlite3.Connection, row: sqlite3.Row, bar: KlineBar) -> None:
        if int(bar.ts_open) <= int(row["created_bar_ts"]):
            return
        fill_price = float(row["fill_price"]) if row["fill_price"] is not None else None
        filled_this_bar = row["status"] == "pending"
        if filled_this_bar:
            fill_price = self._entry_fill(row, bar)
            if fill_price is None:
                self._event(connection, row["order_id"], "bar_observed", int(bar.ts_open), {"status": "pending"})
                return
            connection.execute(
                "UPDATE paper_orders SET status = 'open', fill_price = ?, fill_bar_ts = ? WHERE order_id = ?",
                (fill_price, int(bar.ts_open), row["order_id"]),
            )
            self._event(connection, row["order_id"], "filled", int(bar.ts_open), {"price": fill_price})
        exit_fill = self._exit_fill(row, bar, filled_this_bar=filled_this_bar)
        if exit_fill is None:
            self._event(connection, row["order_id"], "bar_observed", int(bar.ts_open), {"status": "open"})
            return
        exit_price, reason = exit_fill
        assert fill_price is not None
        quantity = float(row["quantity"])
        direction = 1.0 if row["side"] == "long" else -1.0
        multiplier = float(row["contract_multiplier"])
        gross_pnl = direction * (exit_price - fill_price) * quantity * multiplier
        fees = (
            abs(fill_price * quantity * multiplier)
            + abs(exit_price * quantity * multiplier)
        ) * self._fee_bps / 10_000.0
        net_pnl = gross_pnl - fees
        connection.execute(
            "UPDATE paper_orders SET status = 'closed', exit_price = ?, exit_bar_ts = ?, "
            "exit_reason = ?, realized_pnl = ?, fees = ? WHERE order_id = ?",
            (exit_price, int(bar.ts_open), reason, net_pnl, fees, row["order_id"]),
        )
        connection.execute(
            "UPDATE paper_accounts SET equity = equity + ? WHERE account_id = ?",
            (net_pnl, self._account_id),
        )
        self._event(connection, row["order_id"], "closed", int(bar.ts_open), {
            "price": exit_price, "reason": reason, "net_pnl": net_pnl,
        })

    def _entry_fill(self, row: sqlite3.Row, bar: KlineBar) -> float | None:
        entry = float(row["entry_price"])
        side = str(row["side"])
        order_type = str(row["order_type"])
        triggered = order_type == "market"
        if order_type == "limit":
            triggered = bar.low <= entry if side == "long" else bar.high >= entry
        elif order_type == "breakout":
            triggered = bar.high >= entry if side == "long" else bar.low <= entry
        if not triggered:
            return None
        if order_type == "market":
            base = float(bar.open)
        elif order_type == "limit":
            base = min(float(bar.open), entry) if side == "long" else max(float(bar.open), entry)
        else:
            base = max(float(bar.open), entry) if side == "long" else min(float(bar.open), entry)
        return self._with_slippage(base, buy=side == "long")

    def _exit_fill(
        self,
        row: sqlite3.Row,
        bar: KlineBar,
        *,
        filled_this_bar: bool,
    ) -> tuple[float, str] | None:
        side = str(row["side"])
        stop = float(row["stop_price"])
        target = float(row["take_profit_price"])
        stop_hit = bar.low <= stop if side == "long" else bar.high >= stop
        target_hit = bar.high >= target if side == "long" else bar.low <= target
        if stop_hit:
            if target_hit:
                reason = "protective_stop_ambiguous_intrabar"
            elif filled_this_bar and row["order_type"] != "market":
                reason = "protective_stop_ambiguous_entry_bar"
            else:
                reason = "protective_stop"
            stop_fill = (
                min(float(bar.open), stop)
                if side == "long"
                else max(float(bar.open), stop)
            )
            return self._with_slippage(stop_fill, buy=side == "short"), reason
        if target_hit:
            return self._with_slippage(target, buy=side == "short"), "take_profit_1"
        return None

    def _with_slippage(self, price: float, *, buy: bool) -> float:
        sign = 1.0 if buy else -1.0
        return float(price) * (1.0 + sign * self._slippage_bps / 10_000.0)

    @staticmethod
    def _validate_plan(plan: PaperTradePlan, spec: InstrumentSpec) -> None:
        if not plan.run_id or not plan.arm or not plan.symbol or not plan.timeframe:
            raise ValueError("paper trade plan identity is incomplete")
        if plan.symbol.upper() != spec.symbol.upper():
            raise ValueError("plan symbol does not match instrument spec")
        prices = (plan.entry_price, plan.stop_price, plan.take_profit_price)
        if any(not math.isfinite(float(value)) or float(value) <= 0 for value in prices):
            raise ValueError("paper trade prices must be finite and positive")
        if plan.side == "long" and not plan.stop_price < plan.entry_price < plan.take_profit_price:
            raise ValueError("long plan prices are not directionally valid")
        if plan.side == "short" and not plan.take_profit_price < plan.entry_price < plan.stop_price:
            raise ValueError("short plan prices are not directionally valid")
        if plan.take_profit_price_2 is not None:
            tp2 = float(plan.take_profit_price_2)
            if not math.isfinite(tp2) or tp2 <= 0:
                raise ValueError("second target must be finite and positive")
            if plan.side == "long" and tp2 < plan.take_profit_price:
                raise ValueError("long second target cannot be below first target")
            if plan.side == "short" and tp2 > plan.take_profit_price:
                raise ValueError("short second target cannot be above first target")
        for price in prices:
            units = float(price) / spec.tick_size
            if abs(units - round(units)) > 1e-7:
                raise ValueError("paper trade price is not aligned to venue tick size")

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _active_exposure(self, connection: sqlite3.Connection) -> tuple[float, float]:
        rows = connection.execute(
            "SELECT quantity, contract_multiplier, entry_price, stop_price FROM paper_orders "
            "WHERE account_id = ? AND status IN ('pending', 'open')",
            (self._account_id,),
        ).fetchall()
        risk = sum(
            float(row["quantity"]) * float(row["contract_multiplier"])
            * abs(float(row["entry_price"]) - float(row["stop_price"]))
            for row in rows
        )
        notional = sum(
            float(row["quantity"]) * float(row["contract_multiplier"])
            * float(row["entry_price"]) for row in rows
        )
        return risk, notional

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS paper_accounts("
            "account_id TEXT PRIMARY KEY, equity REAL NOT NULL, initial_equity REAL NOT NULL)"
        )
        connection.execute("""
            CREATE TABLE IF NOT EXISTS paper_orders(
                order_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, run_id TEXT NOT NULL,
                arm TEXT NOT NULL, provider TEXT NOT NULL, venue TEXT NOT NULL,
                instrument_type TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                side TEXT NOT NULL, order_type TEXT NOT NULL, entry_price REAL NOT NULL,
                stop_price REAL NOT NULL, take_profit_price REAL NOT NULL,
                take_profit_price_2 REAL, created_bar_ts INTEGER NOT NULL,
                dedupe_key TEXT, quantity REAL NOT NULL, status TEXT NOT NULL, fill_price REAL,
                contract_multiplier REAL NOT NULL DEFAULT 1.0,
                fill_bar_ts INTEGER, exit_price REAL, exit_bar_ts INTEGER,
                exit_reason TEXT, realized_pnl REAL, fees REAL,
                evaluated_through_ts INTEGER,
                UNIQUE(account_id, run_id, arm)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS paper_events(
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL,
                event_type TEXT NOT NULL, bar_ts INTEGER, details_json TEXT NOT NULL
            )
        """)
        existing = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(paper_orders)").fetchall()
        }
        if "dedupe_key" not in existing:
            connection.execute("ALTER TABLE paper_orders ADD COLUMN dedupe_key TEXT")
        if "evaluated_through_ts" not in existing:
            connection.execute(
                "ALTER TABLE paper_orders ADD COLUMN evaluated_through_ts INTEGER"
            )
        if "contract_multiplier" not in existing:
            connection.execute(
                "ALTER TABLE paper_orders ADD COLUMN contract_multiplier "
                "REAL NOT NULL DEFAULT 1.0"
            )
        connection.execute("DROP TABLE IF EXISTS paper_market_progress")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS paper_market_bars(
                provider TEXT NOT NULL, venue TEXT NOT NULL,
                instrument_type TEXT NOT NULL, symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL, ts_open INTEGER NOT NULL,
                open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                close REAL NOT NULL, volume REAL NOT NULL, amount REAL NOT NULL,
                PRIMARY KEY(provider, venue, instrument_type, symbol, timeframe, ts_open)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS paper_rejections(
                rejection_id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL, run_id TEXT NOT NULL, arm TEXT NOT NULL,
                dedupe_key TEXT, provider TEXT NOT NULL, venue TEXT NOT NULL,
                instrument_type TEXT NOT NULL, symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL, reason TEXT NOT NULL,
                UNIQUE(account_id, arm, dedupe_key)
            )
        """)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_paper_orders_active "
            "ON paper_orders(account_id, symbol, status, created_bar_ts)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_orders_dedupe "
            "ON paper_orders(account_id, arm, dedupe_key) WHERE dedupe_key IS NOT NULL"
        )

    @staticmethod
    def _event(
        connection: sqlite3.Connection, order_id: str, event_type: str,
        bar_ts: int | None, details: dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO paper_events(order_id, event_type, bar_ts, details_json) VALUES (?, ?, ?, ?)",
            (order_id, event_type, bar_ts, json.dumps(details, sort_keys=True)),
        )

    @staticmethod
    def _paper_order(row: sqlite3.Row) -> PaperOrder:
        plan = PaperTradePlan(
            run_id=row["run_id"], arm=row["arm"], symbol=row["symbol"],
            timeframe=row["timeframe"], side=row["side"], order_type=row["order_type"],
            entry_price=float(row["entry_price"]), stop_price=float(row["stop_price"]),
            take_profit_price=float(row["take_profit_price"]),
            take_profit_price_2=(float(row["take_profit_price_2"])
                                 if row["take_profit_price_2"] is not None else None),
            created_bar_ts=int(row["created_bar_ts"]),
            dedupe_key=row["dedupe_key"],
        )
        return PaperOrder(
            order_id=row["order_id"], status=row["status"],
            quantity=float(row["quantity"]), plan=plan,
        )


__all__ = [
    "InstrumentSpec", "PaperBroker", "PaperOrder", "PaperTradePlan",
    "PositionSize", "RiskPolicy", "size_for_structural_stop",
]
