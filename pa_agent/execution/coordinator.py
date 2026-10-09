"""Route persisted decisions into isolated, recoverable paper accounts."""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pa_agent.data.instrument import InstrumentSpec
from pa_agent.execution.paper import PaperBroker, PaperTradePlan, RiskPolicy
from pa_agent.execution.plans import (
    closed_bars_from_record,
    decision_bar_ts,
    decision_path,
    instrument_spec_from_record,
    market_observed_at_ms,
    official_paper_plan,
    research_arm_plan,
)
from pa_agent.records.analysis_history import list_record_paths, load_record

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionAction:
    account_id: str
    arm: str
    status: str
    order_id: str | None = None
    reason: str | None = None
    received_path: str | None = None
    treatment_received: bool | None = None


class PaperExecutionCoordinator:
    """Keep production PA and every experimental replicate in separate accounts."""

    def __init__(self, db_path: Path, settings: Any) -> None:
        self._db_path = Path(db_path)
        self._settings = settings
        self.enabled = bool(getattr(settings, "enabled", False))
        self._brokers: dict[str, PaperBroker] = {}
        self._lock = threading.RLock()
        if self.enabled:
            with self._connect() as connection:
                self._ensure_disposition_schema(connection)

    def observe_official(
        self,
        spec: InstrumentSpec,
        timeframe: str,
        bars: tuple[Any, ...] | list[Any],
        *,
        observed_at_ms: int | None = None,
    ) -> int:
        if not self.enabled:
            return 0
        if observed_at_ms is None:
            observed_at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        count = self._broker("pa-official").observe_market(
            spec, timeframe, bars, observed_at_ms=observed_at_ms
        )
        # Shared frozen market store; each versioned account advances its own orders.
        for account in self._pipeline_accounts():
            self._broker(account).observe_market(spec, timeframe, bars, observed_at_ms=observed_at_ms)
            self._broker(account).expire_pending(spec, timeframe, now_ms=int(datetime.now(timezone.utc).timestamp() * 1000))
        return count

    def _pipeline_accounts(self) -> list[str]:
        # Ensure the ledger exists even before the first accepted plan.
        self._broker("pa-official")
        with self._connect() as connection:
            return [row[0] for row in connection.execute(
                "SELECT account_id FROM paper_accounts WHERE account_id IN "
                "('pipeline-pa_only', 'pipeline-pa_shadow', 'pipeline-pa_vpa_joint')")]

    def expire_pending(self) -> int:
        """Clock-only sweep after observations, including paused/failed-feed cycles."""
        if not self.enabled:
            return 0
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return sum(self._broker(account).expire_pending(now_ms=now_ms)
                   for account in self._pipeline_accounts())

    def active_market_keys(self) -> set[tuple[str, str, str, str]]:
        """Markets that still require observation even when analysis is disabled."""
        if not self.enabled:
            return set()
        self._broker("pa-official")
        with self._connect() as connection:
            return {tuple(row) for row in connection.execute(
                "SELECT DISTINCT provider, venue, symbol, timeframe FROM paper_orders "
                "WHERE status IN ('pending', 'open') AND account_id IN "
                "('pa-official', 'pipeline-pa_only', 'pipeline-pa_shadow', 'pipeline-pa_vpa_joint')"
            )}

    def submit_official(
        self,
        record: object,
        spec: InstrumentSpec,
        *,
        task_id: str,
    ) -> ExecutionAction:
        account_id = "pa-official"
        arm = "pa_official"
        pipeline = getattr(record, "analysis_pipeline", None)
        if pipeline is not None:
            arm = pipeline.pipeline
            account_id = f"pipeline-{arm}"
        if not self.enabled:
            return ExecutionAction(account_id, arm, "disabled")
        existing = self._existing_action(account_id, arm, task_id)
        if existing is not None:
            return existing
        try:
            plan = official_paper_plan(record, spec, dedupe_key=task_id)
        except ValueError as exc:
            action = ExecutionAction(
                account_id, arm, "rejected", reason=str(exc), received_path=decision_path(record)
            )
            self._record_disposition(action, record=record, dedupe_key=task_id)
            return action
        broker = self._broker(account_id)
        timeframe = str(getattr(getattr(record, "meta", None), "timeframe", "") or "")
        try:
            broker.observe_market(
                spec, timeframe, closed_bars_from_record(record),
                observed_at_ms=market_observed_at_ms(record),
            )
            if pipeline is not None:
                # Recheck at activation, after any stored-bar replay/IO delay.
                plan = official_paper_plan(record, spec, dedupe_key=task_id)
            broker.supersede_pending(
                spec,
                timeframe,
                as_of_bar_ts=decision_bar_ts(record),
                excluding_dedupe_key=task_id,
                expires_at_ms=(record.analysis_timing.decision_expires_at_ms if pipeline is not None else None),
            )
        except ValueError as exc:
            action = ExecutionAction(
                account_id, arm, "rejected", reason=str(exc),
                received_path=decision_path(record),
            )
            self._record_disposition(action, record=record, dedupe_key=task_id)
            return action
        if plan is None:
            action = ExecutionAction(
                account_id, arm, "no_trade", received_path=decision_path(record)
            )
            self._record_disposition(action, record=record, dedupe_key=task_id)
            return action
        action = self._submit(
            account_id,
            plan,
            spec,
            received_path=decision_path(record),
            treatment_received=None,
        )
        self._record_disposition(action, record=record, dedupe_key=task_id)
        return action

    def reconcile_official(
        self,
        successful_jobs: list[dict[str, Any]],
        records_dir: Path,
    ) -> tuple[ExecutionAction, ...]:
        """Recover persisted analyses whose paper disposition was not completed."""
        if not self.enabled:
            return ()
        pending = {
            str(job.get("run_id") or ""): str(job.get("task_id") or "")
            for job in successful_jobs
            if job.get("run_id") and job.get("task_id")
            and not self._has_official_disposition(str(job.get("task_id")))
        }
        if not pending:
            return ()
        records: dict[str, object] = {}
        for path in list_record_paths(records_dir):
            record = load_record(path)
            run_id = str(getattr(getattr(record, "meta", None), "run_id", "") or "")
            if run_id in pending:
                records[run_id] = record
                if len(records) == len(pending):
                    break
        actions: list[ExecutionAction] = []
        for run_id, task_id in pending.items():
            record = records.get(run_id)
            if record is None:
                continue
            spec = instrument_spec_from_record(record)
            if spec is None:
                action = ExecutionAction(
                    "pa-official",
                    "pa_official",
                    "rejected",
                    reason="persisted analysis has no instrument spec",
                    received_path=decision_path(record),
                )
                self._record_disposition(action, record=record, dedupe_key=task_id)
                actions.append(action)
                continue
            actions.append(self.submit_official(record, spec, task_id=task_id))
        return tuple(actions)

    def record_aabb(
        self,
        result: object,
        frozen: object,
    ) -> tuple[ExecutionAction, ...]:
        if not self.enabled:
            return ()
        report = getattr(result, "protocol_report", {})
        if getattr(result, "status", None) != "completed" or not bool(report.get("valid")):
            raise ValueError("only a completed valid A/A/B/B experiment may enter research paper")
        records = tuple(getattr(result, "records", ()) or ())
        if len(records) != 4:
            raise ValueError("research paper requires all four A/A/B/B arms")
        spec = InstrumentSpec.from_value(getattr(frozen, "instrument_spec", None))
        if spec is None:
            raise ValueError("research paper requires the frozen instrument spec")
        cohort = str(getattr(frozen, "cohort_id", "") or "")
        market_hash = str(getattr(frozen, "market_snapshot_hash", "") or "")
        frame = getattr(frozen, "frame", None)
        timeframe = str(getattr(frame, "timeframe", "") or "")
        bars = tuple(getattr(frame, "bars", ()) or ())
        if not cohort or not market_hash or not timeframe or not bars:
            raise ValueError("frozen experiment market identity is incomplete")

        compiled: list[tuple[object, str, str, str, bool | None, PaperTradePlan | None]] = []
        for record in records:
            context = getattr(record, "experiment_context", {}) or {}
            arm = str(context.get("arm_id") or "")
            group = str(context.get("group") or "")
            received = decision_path(record)
            treatment = (received == "joint") if group == "B" else None
            account_id = f"research-{cohort}-{arm.lower()}"
            plan = research_arm_plan(record, spec, market_snapshot_hash=market_hash)
            compiled.append((record, arm, account_id, received, treatment, plan))

        actions: list[ExecutionAction] = []
        for record, arm, account_id, received, treatment, plan in compiled:
            existing = self._existing_action(account_id, arm, market_hash)
            if existing is not None:
                actions.append(existing)
                continue
            broker = self._broker(account_id)
            metadata = getattr(frame, "data_metadata", {}) or {}
            observed_at_ms = metadata.get("analysis_as_of_ms") or getattr(frame, "snapshot_ts_local_ms", None)
            broker.observe_market(
                spec, timeframe, bars,
                observed_at_ms=observed_at_ms or market_observed_at_ms(record),
            )
            broker.supersede_pending(
                spec,
                timeframe,
                as_of_bar_ts=max(int(bar.ts_open) for bar in bars if bar.closed),
                excluding_dedupe_key=market_hash,
            )
            if plan is None:
                action = ExecutionAction(
                    account_id, arm, "no_trade", received_path=received,
                    treatment_received=treatment,
                )
            else:
                action = self._submit(
                    account_id,
                    plan,
                    spec,
                    received_path=received,
                    treatment_received=treatment,
                )
            self._record_disposition(
                action,
                record=record,
                dedupe_key=market_hash,
                cohort_id=cohort,
                market_snapshot_hash=market_hash,
            )
            actions.append(action)
        return tuple(actions)

    def _submit(
        self,
        account_id: str,
        plan: PaperTradePlan,
        spec: InstrumentSpec,
        *,
        received_path: str,
        treatment_received: bool | None,
    ) -> ExecutionAction:
        broker = self._broker(account_id)
        try:
            order = broker.submit(plan, spec, activate_now=plan.available_at_ms is not None)
        except ValueError as exc:
            broker.record_rejection(plan, spec, str(exc))
            logger.warning("Paper plan rejected for %s: %s", account_id, exc)
            return ExecutionAction(
                account_id, plan.arm, "rejected", reason=str(exc),
                received_path=received_path, treatment_received=treatment_received,
            )
        return ExecutionAction(
            account_id, plan.arm, "submitted", order.order_id,
            received_path=received_path, treatment_received=treatment_received,
        )

    def _record_disposition(
        self,
        action: ExecutionAction,
        *,
        record: object,
        dedupe_key: str,
        cohort_id: str | None = None,
        market_snapshot_hash: str | None = None,
    ) -> None:
        run_id = str(getattr(getattr(record, "meta", None), "run_id", "") or "")
        with self._connect() as connection:
            self._ensure_disposition_schema(connection)
            connection.execute(
                """
                INSERT INTO paper_decisions(
                    account_id, arm, dedupe_key, run_id, status, order_id, reason,
                    received_path, treatment_received, cohort_id, market_snapshot_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, arm, dedupe_key) DO UPDATE SET
                    run_id = excluded.run_id,
                    status = excluded.status,
                    order_id = excluded.order_id,
                    reason = excluded.reason,
                    received_path = excluded.received_path,
                    treatment_received = excluded.treatment_received,
                    cohort_id = excluded.cohort_id,
                    market_snapshot_hash = excluded.market_snapshot_hash
                """,
                (
                    action.account_id, action.arm, dedupe_key, run_id, action.status,
                    action.order_id, action.reason, action.received_path,
                    (int(action.treatment_received)
                     if action.treatment_received is not None else None),
                    cohort_id, market_snapshot_hash,
                ),
            )

    def _existing_action(
        self,
        account_id: str,
        arm: str,
        dedupe_key: str,
    ) -> ExecutionAction | None:
        with self._connect() as connection:
            self._ensure_disposition_schema(connection)
            row = connection.execute(
                "SELECT * FROM paper_decisions WHERE account_id = ? AND arm = ? "
                "AND dedupe_key = ?",
                (account_id, arm, dedupe_key),
            ).fetchone()
        if row is None:
            return None
        return ExecutionAction(
            account_id=row["account_id"], arm=row["arm"], status=row["status"],
            order_id=row["order_id"], reason=row["reason"],
            received_path=row["received_path"],
            treatment_received=(
                bool(row["treatment_received"])
                if row["treatment_received"] is not None else None
            ),
        )

    def _has_official_disposition(self, task_id: str) -> bool:
        with self._connect() as connection:
            self._ensure_disposition_schema(connection)
            return connection.execute(
                "SELECT 1 FROM paper_decisions WHERE dedupe_key = ? AND account_id IN "
                "('pa-official', 'pipeline-pa_only', 'pipeline-pa_shadow', 'pipeline-pa_vpa_joint') LIMIT 1",
                (task_id,),
            ).fetchone() is not None

    def _broker(self, account_id: str) -> PaperBroker:
        with self._lock:
            broker = self._brokers.get(account_id)
            if broker is not None:
                return broker
            policy = RiskPolicy(
                risk_fraction=float(self._settings.risk_fraction),
                max_portfolio_risk_fraction=float(
                    self._settings.max_portfolio_risk_fraction
                ),
                max_leverage=float(self._settings.max_leverage),
            )
            broker = PaperBroker(
                self._db_path,
                account_id=account_id,
                initial_equity=float(self._settings.initial_equity),
                risk_policy=policy,
                fee_bps=float(self._settings.fee_bps),
                slippage_bps=float(self._settings.slippage_bps),
            )
            self._brokers[account_id] = broker
            return broker

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _ensure_disposition_schema(connection: sqlite3.Connection) -> None:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS paper_decisions(
                account_id TEXT NOT NULL, arm TEXT NOT NULL, dedupe_key TEXT NOT NULL,
                run_id TEXT NOT NULL, status TEXT NOT NULL, order_id TEXT, reason TEXT,
                received_path TEXT, treatment_received INTEGER, cohort_id TEXT,
                market_snapshot_hash TEXT,
                PRIMARY KEY(account_id, arm, dedupe_key)
            )
        """)


def actions_as_dicts(actions: tuple[ExecutionAction, ...]) -> list[dict[str, Any]]:
    return [asdict(action) for action in actions]
