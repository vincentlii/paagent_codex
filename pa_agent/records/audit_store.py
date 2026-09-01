"""Small SQLite index for analysis review and later outcome labelling.

The JSON record remains the complete immutable audit artifact.  This store only
indexes decision-critical fields so that many records can be compared without
loading prompts, raw model responses, or sensitive provider configuration.
"""

from __future__ import annotations

import copy
import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from pa_agent.data.contracts import ResolvedInstrumentIdentity
from pa_agent.records.codec import load_record
from pa_agent.records.projection import pa_joint_comparison, usage_from_call_attempts
from pa_agent.records.schema import AnalysisRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditWriteResult:
    """Outcome of a best-effort local audit database write."""

    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class AuditRebuildResult:
    """Counts from a non-destructive JSON-to-index rebuild."""

    indexed: int
    skipped: int


class AnalysisAuditStore:
    """Index completed or partial analysis records without owning their source data."""

    _SCHEMA_VERSION: ClassVar[int] = 12
    _ENTRY_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {"not_traded", "pending", "filled", "expired", "cancelled"}
    )
    _OUTCOMES: ClassVar[frozenset[str]] = frozenset(
        {"pending", "win", "loss", "breakeven", "invalidated", "not_applicable"}
    )
    _COUNTERFACTUAL_VERSION: ClassVar[str] = "closed_ohlc_tp1_v1"
    _COUNTERFACTUAL_TERMINAL: ClassVar[frozenset[str]] = frozenset(
        {
            "completed",
            "ambiguous_intrabar_order",
            "ambiguous_intrabar_exit",
            "ambiguous_gap_fill",
            "entry_not_triggered",
            "horizon_reached",
            "invalid_plan",
            "unsupported_market_fill",
            "not_applicable",
        }
    )

    def __init__(self, db_path: Path, *, simulation_horizon_bars: int = 20) -> None:
        self._db_path = Path(db_path)
        self._simulation_horizon_bars = max(1, int(simulation_horizon_bars))

    def record_analysis(
        self,
        record: AnalysisRecord,
        *,
        record_path: Path,
        partial_reason: str | None = None,
    ) -> AuditWriteResult:
        """Insert or update one analysis index row keyed by stable ``run_id``."""
        try:
            fields = self._run_fields(
                record, record_path=record_path, partial_reason=partial_reason
            )
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.execute(
                    """
                    INSERT INTO analysis_runs (
                        run_id, record_path, timestamp_local_ms, symbol, timeframe,
                        source_provider, resolved_symbol, instrument_type, bar_count,
                        decision_stance, model_name, run_status, error_type, partial_reason,
                        stage1_cycle_position, stage1_direction, stage1_gate_result,
                        order_type, order_direction, trade_confidence, estimated_win_rate,
                        snapshot_latest_ts_open, entry_price, stop_loss_price,
                        take_profit_price, take_profit_price_2,
                        terminal_outcome, htf_status, htf_timeframe, htf_reason,
                        vpa_mode, snapshot_hash, catalog_id,
                        volume_kind, venue, vpa_data_status,
                        vpa_background_relation, vpa_timing_relation, vpa_effect,
                        baseline_order_type, joint_order_type, baseline_trade_confidence,
                        joint_trade_confidence, joint_order_direction, joint_entry_price,
                        joint_stop_loss_price, joint_take_profit_price, joint_take_profit_price_2,
                        joint_observed_effect,
                        authority_violations_json, review_context_json,
                        validation_audit_json, joint_diff_json, usage_json, indexed_at_ms
                    ) VALUES (
                        :run_id, :record_path, :timestamp_local_ms, :symbol, :timeframe,
                        :source_provider, :resolved_symbol, :instrument_type, :bar_count,
                        :decision_stance, :model_name, :run_status, :error_type, :partial_reason,
                        :stage1_cycle_position, :stage1_direction, :stage1_gate_result,
                        :order_type, :order_direction, :trade_confidence, :estimated_win_rate,
                        :snapshot_latest_ts_open, :entry_price, :stop_loss_price,
                        :take_profit_price, :take_profit_price_2,
                        :terminal_outcome, :htf_status, :htf_timeframe, :htf_reason,
                        :vpa_mode, :snapshot_hash, :catalog_id,
                        :volume_kind, :venue, :vpa_data_status,
                        :vpa_background_relation, :vpa_timing_relation, :vpa_effect,
                        :baseline_order_type, :joint_order_type, :baseline_trade_confidence,
                        :joint_trade_confidence, :joint_order_direction, :joint_entry_price,
                        :joint_stop_loss_price, :joint_take_profit_price, :joint_take_profit_price_2,
                        :joint_observed_effect,
                        :authority_violations_json, :review_context_json,
                        :validation_audit_json, :joint_diff_json, :usage_json, :indexed_at_ms
                    )
                    ON CONFLICT(run_id) DO UPDATE SET
                        record_path = excluded.record_path,
                        source_provider = excluded.source_provider,
                        resolved_symbol = excluded.resolved_symbol,
                        instrument_type = excluded.instrument_type,
                        run_status = excluded.run_status,
                        error_type = excluded.error_type,
                        partial_reason = excluded.partial_reason,
                        stage1_cycle_position = excluded.stage1_cycle_position,
                        stage1_direction = excluded.stage1_direction,
                        stage1_gate_result = excluded.stage1_gate_result,
                        order_type = excluded.order_type,
                        order_direction = excluded.order_direction,
                        trade_confidence = excluded.trade_confidence,
                        estimated_win_rate = excluded.estimated_win_rate,
                        snapshot_latest_ts_open = excluded.snapshot_latest_ts_open,
                        entry_price = excluded.entry_price,
                        stop_loss_price = excluded.stop_loss_price,
                        take_profit_price = excluded.take_profit_price,
                        take_profit_price_2 = excluded.take_profit_price_2,
                        terminal_outcome = excluded.terminal_outcome,
                        htf_status = excluded.htf_status,
                        htf_timeframe = excluded.htf_timeframe,
                        htf_reason = excluded.htf_reason,
                        vpa_mode = excluded.vpa_mode,
                        snapshot_hash = excluded.snapshot_hash,
                        catalog_id = excluded.catalog_id,
                        volume_kind = excluded.volume_kind,
                        venue = excluded.venue,
                        vpa_data_status = excluded.vpa_data_status,
                        vpa_background_relation = excluded.vpa_background_relation,
                        vpa_timing_relation = excluded.vpa_timing_relation,
                        vpa_effect = excluded.vpa_effect,
                        baseline_order_type = excluded.baseline_order_type,
                        joint_order_type = excluded.joint_order_type,
                        baseline_trade_confidence = excluded.baseline_trade_confidence,
                        joint_trade_confidence = excluded.joint_trade_confidence,
                        joint_order_direction = excluded.joint_order_direction,
                        joint_entry_price = excluded.joint_entry_price,
                        joint_stop_loss_price = excluded.joint_stop_loss_price,
                        joint_take_profit_price = excluded.joint_take_profit_price,
                        joint_take_profit_price_2 = excluded.joint_take_profit_price_2,
                        joint_observed_effect = excluded.joint_observed_effect,
                        authority_violations_json = excluded.authority_violations_json,
                        review_context_json = excluded.review_context_json,
                        validation_audit_json = excluded.validation_audit_json,
                        joint_diff_json = excluded.joint_diff_json,
                        usage_json = excluded.usage_json,
                        indexed_at_ms = excluded.indexed_at_ms
                    """,
                    fields,
                )
                self._replace_calls(connection, record)
                self._replace_vpa_details(connection, record)
                self._update_counterfactual_outcomes(connection, record)
            return AuditWriteResult(ok=True)
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("Analysis audit index write failed: %s", exc)
            return AuditWriteResult(ok=False, error=str(exc))

    def record_outcome(
        self,
        run_id: str,
        *,
        entry_status: str,
        outcome: str,
        horizon_bars: int | None = None,
        realized_return_r: float | None = None,
        mfe_r: float | None = None,
        mae_r: float | None = None,
        notes: str = "",
    ) -> AuditWriteResult:
        """Attach a reviewed outcome without changing the original AI decision."""
        if entry_status not in self._ENTRY_STATUSES:
            return AuditWriteResult(ok=False, error=f"invalid entry_status: {entry_status}")
        if outcome not in self._OUTCOMES:
            return AuditWriteResult(ok=False, error=f"invalid outcome: {outcome}")
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                exists = connection.execute(
                    "SELECT 1 FROM analysis_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if exists is None:
                    return AuditWriteResult(ok=False, error=f"unknown run_id: {run_id}")
                connection.execute(
                    """
                    INSERT INTO analysis_outcomes (
                        run_id, entry_status, outcome, horizon_bars, realized_return_r,
                        mfe_r, mae_r, notes, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        entry_status = excluded.entry_status,
                        outcome = excluded.outcome,
                        horizon_bars = excluded.horizon_bars,
                        realized_return_r = excluded.realized_return_r,
                        mfe_r = excluded.mfe_r,
                        mae_r = excluded.mae_r,
                        notes = excluded.notes,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (
                        run_id,
                        entry_status,
                        outcome,
                        horizon_bars,
                        realized_return_r,
                        mfe_r,
                        mae_r,
                        notes,
                        self._now_ms(),
                    ),
                )
            return AuditWriteResult(ok=True)
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("Analysis outcome write failed: %s", exc)
            return AuditWriteResult(ok=False, error=str(exc))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM analysis_runs WHERE run_id = ?", (run_id,))

    def get_outcome(self, run_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM analysis_outcomes WHERE run_id = ?", (run_id,))

    def get_calls(self, run_id: str) -> list[dict[str, Any]]:
        """Return the ordered per-call ledger for one analysis run."""
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    """
                    SELECT * FROM analysis_calls
                    WHERE run_id = ?
                    ORDER BY arm, stage, attempt
                    """,
                    (run_id,),
                ).fetchall()
            return [dict(row) for row in rows]
        except (OSError, sqlite3.Error) as exc:
            logger.warning("Analysis call ledger query failed: %s", exc)
            return []

    def get_vpa_observations(self, run_id: str) -> list[dict[str, Any]]:
        return self._many(
            "SELECT * FROM vpa_observations WHERE run_id = ? "
            "ORDER BY observation_index",
            (run_id,),
        )

    def get_vpa_assessments(self, run_id: str) -> list[dict[str, Any]]:
        return self._many(
            "SELECT * FROM vpa_hypothesis_assessments WHERE run_id = ? "
            "ORDER BY hypothesis_id",
            (run_id,),
        )

    def get_vpa_evidence_bindings(self, run_id: str) -> list[dict[str, Any]]:
        return self._many(
            """
            SELECT 'observation' AS scope, CAST(observation_index AS TEXT) AS owner_id,
                   evidence_id, binding_source
            FROM vpa_observation_evidence WHERE run_id = ?
            UNION ALL
            SELECT 'assessment' AS scope, hypothesis_id AS owner_id,
                   evidence_id, binding_source
            FROM vpa_assessment_evidence WHERE run_id = ?
            ORDER BY scope, owner_id, evidence_id, binding_source
            """,
            (run_id, run_id),
        )

    def get_failures(self, run_id: str) -> list[dict[str, Any]]:
        return self._many(
            "SELECT * FROM analysis_failures WHERE run_id = ? ORDER BY failure_index",
            (run_id,),
        )

    def get_counterfactual_outcome(
        self,
        run_id: str,
        *,
        arm: str = "official",
    ) -> dict[str, Any] | None:
        """Return deterministic OHLC replay output, separate from actual fills."""
        return self._one(
            "SELECT * FROM counterfactual_outcomes WHERE run_id = ? AND arm = ?",
            (run_id, arm),
        )

    def list_runs(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return compact review rows, newest first, with no raw prompt content."""
        clauses: list[str] = []
        values: list[Any] = []
        if symbol:
            clauses.append("symbol = ?")
            values.append(symbol)
        if timeframe:
            clauses.append("timeframe = ?")
            values.append(timeframe)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT * FROM analysis_runs"
                    + where
                    + " ORDER BY timestamp_local_ms DESC LIMIT ?",
                    (*values, max(1, min(int(limit), 2000))),
                ).fetchall()
            return [dict(row) for row in rows]
        except (OSError, sqlite3.Error, ValueError) as exc:
            logger.warning("Analysis audit query failed: %s", exc)
            return []

    def rebuild_from_records(self, records_dir: Path) -> AuditRebuildResult:
        """Rebuild this derived index from saved JSON records without deleting them."""
        indexed = 0
        skipped = 0
        for path in sorted(Path(records_dir).glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                partial_reason = raw.get("_partial_reason")
                record = load_record(path).record
            except (OSError, ValueError, TypeError):
                skipped += 1
                continue
            if self.record_analysis(record, record_path=path, partial_reason=partial_reason).ok:
                indexed += 1
            else:
                skipped += 1
        return AuditRebuildResult(indexed=indexed, skipped=skipped)

    def _update_counterfactual_outcomes(
        self,
        connection: sqlite3.Connection,
        current_record: AnalysisRecord,
    ) -> None:
        """Advance pending plan replays using the latest closed-bar snapshot."""
        latest_ts = self._latest_closed_ts(current_record.kline_data)
        if latest_ts is None:
            return
        identity = ResolvedInstrumentIdentity.from_value(current_record.snapshot_context)
        if identity is None:
            return
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM analysis_runs WHERE symbol = ? AND timeframe = ? "
            "AND source_provider = ? AND venue = ? AND resolved_symbol = ? "
            "AND instrument_type = ? "
            "AND run_status = 'completed' AND snapshot_latest_ts_open IS NOT NULL "
            "AND snapshot_latest_ts_open <= ?",
            (
                current_record.meta.symbol,
                current_record.meta.timeframe,
                identity.provider,
                identity.venue,
                identity.resolved_symbol,
                identity.instrument_type,
                latest_ts,
            ),
        ).fetchall()
        bars = [bar for bar in current_record.kline_data if isinstance(bar, dict)]
        bar_timestamps = {
            ts
            for bar in bars
            if (ts := self._bar_ts(bar)) is not None and bar.get("closed") is not False
        }
        for row in rows:
            snapshot_ts = int(row["snapshot_latest_ts_open"])
            # Stateless replay must see the original snapshot bar and every
            # later bar in one window. If history has rolled out, retaining the
            # previous pending state is safer than inventing a fill path.
            if snapshot_ts not in bar_timestamps:
                continue
            future_bars = [
                bar
                for bar in bars
                if (self._bar_ts(bar) or -1) > snapshot_ts and bar.get("closed") is not False
            ]
            self._advance_counterfactual(
                connection,
                row,
                future_bars=future_bars,
            )

    def _advance_counterfactual(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        future_bars: list[dict[str, Any]],
    ) -> None:
        existing = connection.execute(
            "SELECT status, horizon_bars, assumption_version "
            "FROM counterfactual_outcomes WHERE run_id = ? AND arm = ?",
            (row["run_id"], "official"),
        ).fetchone()
        effective_horizon = self._effective_horizon_for_row(row)
        current_assumptions = (
            existing is not None
            and int(existing["horizon_bars"]) == effective_horizon
            and str(existing["assumption_version"]) == self._COUNTERFACTUAL_VERSION
        )
        if current_assumptions and str(existing["status"]) in self._COUNTERFACTUAL_TERMINAL:
            return
        from pa_agent.records.counterfactual import evaluate_trade_plan

        payload = asdict(
            evaluate_trade_plan(
                self._plan_from_row(row),
                future_bars,
                horizon_bars=effective_horizon,
            )
        )
        connection.execute(
            """
            INSERT INTO counterfactual_outcomes (
                run_id, arm, status, entry_status, outcome, horizon_bars,
                bars_observed, entry_bar_ts, exit_bar_ts, realized_return_r,
                mfe_r, mae_r, evaluated_through_ts, assumption_version, notes,
                updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, arm) DO UPDATE SET
                status = excluded.status,
                entry_status = excluded.entry_status,
                outcome = excluded.outcome,
                horizon_bars = excluded.horizon_bars,
                bars_observed = excluded.bars_observed,
                entry_bar_ts = excluded.entry_bar_ts,
                exit_bar_ts = excluded.exit_bar_ts,
                realized_return_r = excluded.realized_return_r,
                mfe_r = excluded.mfe_r,
                mae_r = excluded.mae_r,
                evaluated_through_ts = excluded.evaluated_through_ts,
                assumption_version = excluded.assumption_version,
                notes = excluded.notes,
                updated_at_ms = excluded.updated_at_ms
            """,
            (
                row["run_id"],
                "official",
                payload["status"],
                payload["entry_status"],
                payload["outcome"],
                payload["horizon_bars"],
                payload["bars_observed"],
                payload.get("entry_bar_ts"),
                payload.get("exit_bar_ts"),
                payload.get("realized_return_r"),
                payload.get("mfe_r"),
                payload.get("mae_r"),
                payload.get("evaluated_through_ts"),
                self._COUNTERFACTUAL_VERSION,
                payload.get("notes", ""),
                self._now_ms(),
            ),
        )

    @staticmethod
    def _plan_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "order_type": row["order_type"],
            "order_direction": row["order_direction"],
            "entry_price": row["entry_price"],
            "stop_loss_price": row["stop_loss_price"],
            "take_profit_price": row["take_profit_price"],
        }

    def _effective_horizon_for_row(self, row: sqlite3.Row) -> int:
        """Keep the origin snapshot inside the saved bar window during replay."""
        return min(
            self._simulation_horizon_bars,
            max(1, int(row["bar_count"]) - 1),
        )

    def _one(self, query: str, values: tuple[Any, ...]) -> dict[str, Any] | None:
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.row_factory = sqlite3.Row
                row = connection.execute(query, values).fetchone()
            return dict(row) if row is not None else None
        except (OSError, sqlite3.Error) as exc:
            logger.warning("Analysis audit query failed: %s", exc)
            return None

    def _many(self, query: str, values: tuple[Any, ...]) -> list[dict[str, Any]]:
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.row_factory = sqlite3.Row
                rows = connection.execute(query, values).fetchall()
            return [dict(row) for row in rows]
        except (OSError, sqlite3.Error) as exc:
            logger.warning("Analysis audit query failed: %s", exc)
            return []

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._db_path, timeout=5.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS analysis_runs (
                run_id TEXT PRIMARY KEY,
                record_path TEXT NOT NULL,
                timestamp_local_ms INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                source_provider TEXT,
                resolved_symbol TEXT,
                instrument_type TEXT,
                bar_count INTEGER NOT NULL,
                decision_stance TEXT NOT NULL,
                model_name TEXT,
                run_status TEXT NOT NULL,
                error_type TEXT,
                partial_reason TEXT,
                stage1_cycle_position TEXT,
                stage1_direction TEXT,
                stage1_gate_result TEXT,
                order_type TEXT,
                order_direction TEXT,
                trade_confidence REAL,
                estimated_win_rate REAL,
                snapshot_latest_ts_open INTEGER,
                entry_price REAL,
                stop_loss_price REAL,
                take_profit_price REAL,
                take_profit_price_2 REAL,
                terminal_outcome TEXT,
                htf_status TEXT,
                htf_timeframe TEXT,
                htf_reason TEXT,
                vpa_mode TEXT,
                snapshot_hash TEXT,
                catalog_id TEXT,
                volume_kind TEXT,
                venue TEXT,
                vpa_data_status TEXT,
                vpa_background_relation TEXT,
                vpa_timing_relation TEXT,
                vpa_effect TEXT,
                baseline_order_type TEXT,
                joint_order_type TEXT,
                baseline_trade_confidence REAL,
                joint_trade_confidence REAL,
                joint_order_direction TEXT,
                joint_entry_price REAL,
                joint_stop_loss_price REAL,
                joint_take_profit_price REAL,
                joint_take_profit_price_2 REAL,
                joint_observed_effect TEXT,
                authority_violations_json TEXT,
                review_context_json TEXT,
                validation_audit_json TEXT,
                joint_diff_json TEXT,
                usage_json TEXT,
                indexed_at_ms INTEGER NOT NULL
            )
            """)
        self._ensure_analysis_run_columns(connection)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS analysis_outcomes (
                run_id TEXT PRIMARY KEY REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
                entry_status TEXT NOT NULL,
                outcome TEXT NOT NULL,
                horizon_bars INTEGER,
                realized_return_r REAL,
                mfe_r REAL,
                mae_r REAL,
                notes TEXT NOT NULL DEFAULT '',
                updated_at_ms INTEGER NOT NULL
            )
            """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS analysis_calls (
                run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
                arm TEXT NOT NULL,
                stage TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                request_hash TEXT NOT NULL,
                response_hash TEXT,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                cached_prompt_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                latency_ms REAL,
                status TEXT NOT NULL,
                validation_category TEXT,
                error_type TEXT,
                analysis_backend TEXT NOT NULL DEFAULT 'direct',
                provider_id TEXT,
                job_digest TEXT,
                codex_thread_id TEXT,
                evidence_call_count INTEGER NOT NULL DEFAULT 0,
                evidence_response_bytes INTEGER NOT NULL DEFAULT 0,
                created_at_ms INTEGER NOT NULL,
                PRIMARY KEY (run_id, arm, stage, attempt)
            )
            """)
        self._ensure_analysis_call_columns(connection)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS vpa_observations (
                run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
                observation_index INTEGER NOT NULL,
                finding_kind TEXT,
                interpretation_label TEXT,
                epistemic_status TEXT,
                bar_range TEXT,
                interpretation TEXT,
                PRIMARY KEY (run_id, observation_index)
            )
            """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS vpa_observation_evidence (
                run_id TEXT NOT NULL,
                observation_index INTEGER NOT NULL,
                evidence_id TEXT NOT NULL,
                binding_source TEXT NOT NULL,
                PRIMARY KEY (run_id, observation_index, evidence_id, binding_source),
                FOREIGN KEY (run_id, observation_index)
                    REFERENCES vpa_observations(run_id, observation_index)
                    ON DELETE CASCADE
            )
            """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS vpa_hypothesis_assessments (
                run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
                hypothesis_id TEXT NOT NULL,
                relation TEXT,
                reason TEXT,
                PRIMARY KEY (run_id, hypothesis_id)
            )
            """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS vpa_assessment_evidence (
                run_id TEXT NOT NULL,
                hypothesis_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                binding_source TEXT NOT NULL,
                PRIMARY KEY (run_id, hypothesis_id, evidence_id, binding_source),
                FOREIGN KEY (run_id, hypothesis_id)
                    REFERENCES vpa_hypothesis_assessments(run_id, hypothesis_id)
                    ON DELETE CASCADE
            )
            """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS analysis_failures (
                run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
                failure_index INTEGER NOT NULL,
                stage TEXT,
                code TEXT,
                category TEXT,
                severity TEXT,
                owner TEXT,
                retry_policy TEXT,
                message TEXT,
                PRIMARY KEY (run_id, failure_index)
            )
            """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS counterfactual_outcomes (
                run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
                arm TEXT NOT NULL,
                status TEXT NOT NULL,
                entry_status TEXT NOT NULL,
                outcome TEXT NOT NULL,
                horizon_bars INTEGER NOT NULL,
                bars_observed INTEGER NOT NULL,
                entry_bar_ts INTEGER,
                exit_bar_ts INTEGER,
                realized_return_r REAL,
                mfe_r REAL,
                mae_r REAL,
                evaluated_through_ts INTEGER,
                assumption_version TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                updated_at_ms INTEGER NOT NULL,
                PRIMARY KEY (run_id, arm)
            )
            """)
        connection.execute("DELETE FROM counterfactual_outcomes WHERE arm = 'vpa'")
        connection.execute(
            "UPDATE OR IGNORE counterfactual_outcomes SET arm = 'official' "
            "WHERE arm = 'baseline'"
        )
        connection.execute("DELETE FROM counterfactual_outcomes WHERE arm = 'baseline'")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_runs_symbol_tf_time "
            "ON analysis_runs(symbol, timeframe, timestamp_local_ms DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_runs_instrument_time "
            "ON analysis_runs(source_provider, venue, resolved_symbol, instrument_type, timeframe, "
            "timestamp_local_ms DESC)"
        )
        connection.execute("DROP INDEX IF EXISTS idx_analysis_runs_vpa_pair")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_counterfactual_arm_outcome "
            "ON counterfactual_outcomes(arm, outcome, status)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_calls_stage_latency "
            "ON analysis_calls(arm, stage, latency_ms)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_vpa_observation_evidence_id "
            "ON vpa_observation_evidence(evidence_id, binding_source)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_vpa_assessment_relation "
            "ON vpa_hypothesis_assessments(relation)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_failures_stage "
            "ON analysis_failures(stage, code)"
        )
        connection.execute(f"PRAGMA user_version = {self._SCHEMA_VERSION}")

    @staticmethod
    def _ensure_analysis_run_columns(connection: sqlite3.Connection) -> None:
        """Migrate the derived index in place; source JSON records stay untouched."""
        existing = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(analysis_runs)").fetchall()
        }
        required = {
            "source_provider": "TEXT",
            "resolved_symbol": "TEXT",
            "instrument_type": "TEXT",
            "htf_status": "TEXT",
            "htf_timeframe": "TEXT",
            "htf_reason": "TEXT",
            "baseline_order_type": "TEXT",
            "joint_order_type": "TEXT",
            "baseline_trade_confidence": "REAL",
            "joint_trade_confidence": "REAL",
            "joint_observed_effect": "TEXT",
            "snapshot_latest_ts_open": "INTEGER",
            "entry_price": "REAL",
            "stop_loss_price": "REAL",
            "take_profit_price": "REAL",
            "take_profit_price_2": "REAL",
            "joint_order_direction": "TEXT",
            "joint_entry_price": "REAL",
            "joint_stop_loss_price": "REAL",
            "joint_take_profit_price": "REAL",
            "joint_take_profit_price_2": "REAL",
            "vpa_background_relation": "TEXT",
            "vpa_timing_relation": "TEXT",
            "authority_violations_json": "TEXT",
            "review_context_json": "TEXT",
            "validation_audit_json": "TEXT",
            "joint_diff_json": "TEXT",
        }
        for column, definition in required.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE analysis_runs ADD COLUMN {column} {definition}")

    @staticmethod
    def _ensure_analysis_call_columns(connection: sqlite3.Connection) -> None:
        """Extend the rebuildable call ledger with Codex runtime observability."""
        existing = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(analysis_calls)").fetchall()
        }
        required = {
            "analysis_backend": "TEXT NOT NULL DEFAULT 'direct'",
            "provider_id": "TEXT",
            "job_digest": "TEXT",
            "codex_thread_id": "TEXT",
            "evidence_call_count": "INTEGER NOT NULL DEFAULT 0",
            "evidence_response_bytes": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in required.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE analysis_calls ADD COLUMN {column} {definition}"
                )

    @classmethod
    def _replace_calls(
        cls,
        connection: sqlite3.Connection,
        record: AnalysisRecord,
    ) -> None:
        connection.execute("DELETE FROM analysis_calls WHERE run_id = ?", (record.meta.run_id,))
        for raw in record.call_attempts:
            if not isinstance(raw, dict):
                continue
            connection.execute(
                """
                INSERT INTO analysis_calls (
                    run_id, arm, stage, attempt, request_hash, response_hash,
                    prompt_tokens, completion_tokens, cached_prompt_tokens,
                    total_tokens, latency_ms, status, validation_category,
                    error_type, analysis_backend, provider_id, job_digest,
                    codex_thread_id, evidence_call_count,
                    evidence_response_bytes, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.meta.run_id,
                    str(raw.get("arm") or "baseline"),
                    str(raw.get("stage") or "unknown"),
                    max(1, int(raw.get("attempt") or 1)),
                    str(raw.get("request_hash") or ""),
                    str(raw.get("response_hash") or "") or None,
                    max(0, int(raw.get("prompt_tokens") or 0)),
                    max(0, int(raw.get("completion_tokens") or 0)),
                    max(0, int(raw.get("cached_prompt_tokens") or 0)),
                    max(0, int(raw.get("total_tokens") or 0)),
                    float(raw["latency_ms"]) if raw.get("latency_ms") is not None else None,
                    str(raw.get("status") or "unknown"),
                    str(raw.get("validation_category") or "") or None,
                    str(raw.get("error_type") or "") or None,
                    str(raw.get("analysis_backend") or "direct"),
                    str(raw.get("provider_id") or "") or None,
                    str(raw.get("job_digest") or "") or None,
                    str(raw.get("codex_thread_id") or "") or None,
                    max(0, int(raw.get("evidence_call_count") or 0)),
                    max(0, int(raw.get("evidence_response_bytes") or 0)),
                    int(raw.get("created_at_ms") or cls._now_ms()),
                ),
            )

    @classmethod
    def _replace_vpa_details(
        cls,
        connection: sqlite3.Connection,
        record: AnalysisRecord,
    ) -> None:
        """Project canonical VPA semantics and typed failures into query tables."""
        run_id = record.meta.run_id
        connection.execute("DELETE FROM vpa_observations WHERE run_id = ?", (run_id,))
        connection.execute(
            "DELETE FROM vpa_hypothesis_assessments WHERE run_id = ?",
            (run_id,),
        )
        connection.execute("DELETE FROM analysis_failures WHERE run_id = ?", (run_id,))

        report = record.vpa_evidence if isinstance(record.vpa_evidence, dict) else {}
        for index, observation in enumerate(report.get("observations", [])):
            if not isinstance(observation, dict):
                continue
            connection.execute(
                """
                INSERT INTO vpa_observations (
                    run_id, observation_index, finding_kind, interpretation_label,
                    epistemic_status, bar_range, interpretation
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    index,
                    observation.get("finding_kind"),
                    observation.get("interpretation_label"),
                    observation.get("epistemic_status"),
                    observation.get("bar_range"),
                    observation.get("interpretation"),
                ),
            )
            cls._insert_vpa_bindings(
                connection,
                table="vpa_observation_evidence",
                key_values=(run_id, index),
                declared=observation.get("evidence_ids"),
                auto_bound=observation.get("auto_bound_evidence_ids"),
            )

        for assessment in report.get("hypothesis_assessments", []):
            if not isinstance(assessment, dict):
                continue
            hypothesis_id = str(assessment.get("hypothesis_id") or "")
            if not hypothesis_id:
                continue
            connection.execute(
                """
                INSERT INTO vpa_hypothesis_assessments (
                    run_id, hypothesis_id, relation, reason
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    run_id,
                    hypothesis_id,
                    assessment.get("relation"),
                    assessment.get("reason"),
                ),
            )
            cls._insert_vpa_bindings(
                connection,
                table="vpa_assessment_evidence",
                key_values=(run_id, hypothesis_id),
                declared=assessment.get("evidence_ids"),
                auto_bound=assessment.get("auto_bound_evidence_ids"),
            )

        failures = cls._failure_projection(record)
        for index, failure in enumerate(failures):
            connection.execute(
                """
                INSERT INTO analysis_failures (
                    run_id, failure_index, stage, code, category, severity,
                    owner, retry_policy, message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    index,
                    failure.get("stage"),
                    failure.get("code"),
                    failure.get("category"),
                    failure.get("severity"),
                    failure.get("owner"),
                    failure.get("retry_policy"),
                    failure.get("message"),
                ),
            )

    @staticmethod
    def _insert_vpa_bindings(
        connection: sqlite3.Connection,
        *,
        table: str,
        key_values: tuple[Any, Any],
        declared: object,
        auto_bound: object,
    ) -> None:
        for source, values in (("declared", declared), ("auto", auto_bound)):
            if not isinstance(values, list):
                continue
            for evidence_id in dict.fromkeys(str(item) for item in values if str(item)):
                connection.execute(
                    f"INSERT OR IGNORE INTO {table} VALUES (?, ?, ?, ?)",
                    (*key_values, evidence_id, source),
                )

    @staticmethod
    def _failure_projection(record: AnalysisRecord) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []
        for event in record.validation_audit:
            if not isinstance(event, dict):
                continue
            severity = str(event.get("severity") or "")
            category = str(event.get("category") or "")
            if severity in {"warning", "normalization"} or category in {
                "warning",
                "normalization",
            }:
                continue
            failures.append(copy.deepcopy(event))
        exception = record.exception if isinstance(record.exception, dict) else {}
        if exception:
            failures.append(
                {
                    "stage": exception.get("stage"),
                    "code": exception.get("failed_check") or exception.get("type"),
                    "category": exception.get("type"),
                    "severity": "error",
                    "owner": exception.get("stage"),
                    "retry_policy": "recorded",
                    "message": exception.get("message"),
                }
            )
        return failures

    @classmethod
    def _run_fields(
        cls,
        record: AnalysisRecord,
        *,
        record_path: Path,
        partial_reason: str | None,
    ) -> dict[str, Any]:
        stage1 = record.stage1_diagnosis or {}
        stage2 = record.stage2_decision or {}
        decision = stage2.get("decision") if isinstance(stage2, dict) else {}
        decision = decision if isinstance(decision, dict) else {}
        terminal = stage2.get("terminal") if isinstance(stage2, dict) else {}
        terminal = terminal if isinstance(terminal, dict) else {}
        pa_hypotheses = record.pa_hypotheses if isinstance(record.pa_hypotheses, dict) else {}
        canonical_vpa = record.vpa_evidence if isinstance(record.vpa_evidence, dict) else {}
        canonical_joint = record.joint_decision if isinstance(record.joint_decision, dict) else {}
        catalog = record.vpa_catalog if isinstance(record.vpa_catalog, dict) else {}
        catalog = catalog if isinstance(catalog, dict) else {}
        provenance = catalog.get("provenance") if isinstance(catalog, dict) else {}
        provenance = provenance if isinstance(provenance, dict) else {}
        identity = ResolvedInstrumentIdentity.from_value(record.snapshot_context)
        arbitration = canonical_joint.get("joint_arbitration")
        arbitration = arbitration if isinstance(arbitration, dict) else {}
        impact = {
            "background_relation": canonical_vpa.get("background_relation"),
            "entry_timing_relation": canonical_vpa.get("entry_timing_relation"),
            "evidence_used": copy.deepcopy(arbitration.get("evidence_used") or []),
            "decision_effect": arbitration.get("decision_effect"),
            "reason": arbitration.get("reason"),
        }
        vpa_context = canonical_vpa
        comparison = pa_joint_comparison(record) or {}
        comparison = comparison if isinstance(comparison, dict) else {}
        observed = comparison.get("observed_effect") if isinstance(comparison, dict) else {}
        observed = observed if isinstance(observed, dict) else {}
        role_execution = {
            **(record.role_runtime if isinstance(record.role_runtime, dict) else {}),
            **(record.role_statuses if isinstance(record.role_statuses, dict) else {}),
        }
        joint_stage2 = (
            record.joint_stage2 if isinstance(record.joint_stage2, dict) else {}
        )
        joint_stage2 = joint_stage2 if isinstance(joint_stage2, dict) else {}
        joint_decision = joint_stage2.get("decision") if isinstance(joint_stage2, dict) else {}
        joint_decision = joint_decision if isinstance(joint_decision, dict) else {}
        htf = record.htf_context if isinstance(record.htf_context, dict) else {}
        exception = record.exception if isinstance(record.exception, dict) else {}
        run_outcome = record.run_outcome if isinstance(record.run_outcome, dict) else {}
        if exception:
            run_status = "failed"
        elif partial_reason:
            run_status = "partial"
        elif run_outcome.get("status") == "degraded_pa_only":
            run_status = "degraded"
        else:
            run_status = "completed"
        provider = record.meta.ai_provider if isinstance(record.meta.ai_provider, dict) else {}
        pa_evidence = stage1.get("pa_evidence") if isinstance(stage1, dict) else {}
        pa_evidence = pa_evidence if isinstance(pa_evidence, dict) else {}
        decision_review = {
            key: decision.get(key)
            for key in (
                "entry_rule",
                "reasoning",
                "key_factors",
                "watch_points",
                "risk_assessment",
                "invalidation_condition",
            )
        }
        review_context = {
            "pa_evidence": pa_evidence,
            "entry_setup": stage1.get("entry_setup"),
            "support_levels": stage1.get("support_levels"),
            "resistance_levels": stage1.get("resistance_levels"),
            "vpa_context": vpa_context,
            "vpa_impact": impact,
            "pa_hypotheses": pa_hypotheses,
            "vpa_evidence": canonical_vpa,
            "joint_arbitration": canonical_joint.get("joint_arbitration"),
            "run_outcome": run_outcome,
            "backend_fallback": copy.deepcopy(
                role_execution.get("backend_fallback")
            ),
            "decision_review": decision_review,
        }
        baseline_stage2 = (
            record.pa_baseline_stage2
            if isinstance(record.pa_baseline_stage2, dict)
            else {}
        )
        baseline_decision = baseline_stage2.get("decision")
        baseline_decision = baseline_decision if isinstance(baseline_decision, dict) else {}
        return {
            "run_id": record.meta.run_id,
            "record_path": str(record_path),
            "timestamp_local_ms": record.meta.timestamp_local_ms,
            "symbol": record.meta.symbol,
            "timeframe": record.meta.timeframe,
            "source_provider": identity.provider if identity is not None else None,
            "resolved_symbol": (
                identity.resolved_symbol if identity is not None else None
            ),
            "instrument_type": (
                identity.instrument_type if identity is not None else None
            ),
            "bar_count": record.meta.bar_count,
            "decision_stance": record.meta.decision_stance,
            "model_name": provider.get("model", ""),
            "run_status": run_status,
            "error_type": exception.get("type", "") or None,
            "partial_reason": partial_reason,
            "stage1_cycle_position": stage1.get("cycle_position"),
            "stage1_direction": stage1.get("direction"),
            "stage1_gate_result": stage1.get("gate_result"),
            "order_type": decision.get("order_type"),
            "order_direction": decision.get("order_direction"),
            "trade_confidence": decision.get("trade_confidence"),
            "estimated_win_rate": decision.get("estimated_win_rate"),
            "snapshot_latest_ts_open": cls._latest_closed_ts(record.kline_data),
            "entry_price": decision.get("entry_price"),
            "stop_loss_price": decision.get("stop_loss_price"),
            "take_profit_price": decision.get("take_profit_price"),
            "take_profit_price_2": decision.get("take_profit_price_2"),
            "terminal_outcome": terminal.get("outcome"),
            "htf_status": htf.get("status"),
            "htf_timeframe": htf.get("timeframe"),
            "htf_reason": htf.get("reason"),
            "vpa_mode": run_outcome.get("requested_vpa_mode"),
            "snapshot_hash": catalog.get("snapshot_hash"),
            "catalog_id": catalog.get("catalog_id"),
            "volume_kind": provenance.get("volume_kind"),
            "venue": (
                identity.venue if identity is not None else provenance.get("venue")
            ),
            "vpa_data_status": catalog.get("data_status"),
            "vpa_background_relation": impact.get("background_relation"),
            "vpa_timing_relation": impact.get("entry_timing_relation"),
            "vpa_effect": impact.get("decision_effect"),
            "baseline_order_type": (
                baseline_decision.get("order_type")
                if baseline_decision
                else decision.get("order_type") if catalog else None
            ),
            "joint_order_type": joint_decision.get("order_type"),
            "baseline_trade_confidence": (
                baseline_decision.get("trade_confidence")
                if baseline_decision
                else decision.get("trade_confidence") if catalog else None
            ),
            "joint_trade_confidence": joint_decision.get("trade_confidence"),
            "joint_order_direction": joint_decision.get("order_direction"),
            "joint_entry_price": joint_decision.get("entry_price"),
            "joint_stop_loss_price": joint_decision.get("stop_loss_price"),
            "joint_take_profit_price": joint_decision.get("take_profit_price"),
            "joint_take_profit_price_2": joint_decision.get("take_profit_price_2"),
            "joint_observed_effect": observed.get("classification"),
            "authority_violations_json": cls._json(
                role_execution.get("authority_violations") or []
            ),
            "review_context_json": cls._json(review_context),
            "validation_audit_json": cls._json(record.validation_audit),
            "joint_diff_json": cls._json(comparison),
            "usage_json": cls._json(usage_from_call_attempts(record.call_attempts)),
            "indexed_at_ms": cls._now_ms(),
        }

    @staticmethod
    def _json(value: Any) -> str:
        payload = {} if value is None else value
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _latest_closed_ts(bars: list[dict[str, Any]]) -> int | None:
        values = [
            ts
            for bar in bars
            if isinstance(bar, dict)
            and bar.get("closed") is not False
            and (ts := AnalysisAuditStore._bar_ts(bar)) is not None
        ]
        return max(values) if values else None

    @staticmethod
    def _bar_ts(bar: dict[str, Any]) -> int | None:
        try:
            return int(bar.get("ts_open"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _now_ms() -> int:
        return int(datetime.now(UTC).timestamp() * 1000)
