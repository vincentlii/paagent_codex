"""Small SQLite index for analysis review and later outcome labelling.

The JSON record remains the complete immutable audit artifact.  This store only
indexes decision-critical fields so that many records can be compared without
loading prompts, raw model responses, or sensitive provider configuration.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from pa_agent.config.vpa import VPA_PAIR_IDENTITY_VERSION
from pa_agent.records.schema import AnalysisRecord

logger = logging.getLogger(__name__)

_EXECUTABLE_ORDER_TYPES = frozenset({"限价单", "突破单", "市价单", "limit", "breakout", "market"})


def _is_executable_order(value: object) -> bool:
    return str(value or "").strip().lower() in {
        item.lower() for item in _EXECUTABLE_ORDER_TYPES
    }


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

    _SCHEMA_VERSION: ClassVar[int] = 7
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
            "arm_failed",
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
            fields = self._run_fields(record, record_path=record_path, partial_reason=partial_reason)
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.execute(
                    """
                    INSERT INTO analysis_runs (
                        run_id, record_path, timestamp_local_ms, symbol, timeframe, bar_count,
                        decision_stance, model_name, run_status, error_type, partial_reason,
                        stage1_cycle_position, stage1_direction, stage1_gate_result,
                        order_type, order_direction, trade_confidence, estimated_win_rate,
                        snapshot_latest_ts_open, entry_price, stop_loss_price,
                        take_profit_price, take_profit_price_2,
                        terminal_outcome, htf_status, htf_timeframe, htf_reason,
                        vpa_mode, pair_id, snapshot_hash, catalog_id,
                        volume_kind, venue, vpa_data_status,
                        vpa_background_relation, vpa_timing_relation, vpa_effect,
                        baseline_order_type, vpa_order_type, baseline_trade_confidence,
                        vpa_trade_confidence, vpa_order_direction, vpa_entry_price,
                        vpa_stop_loss_price, vpa_take_profit_price, vpa_take_profit_price_2,
                        observed_effect, experiment_type, pair_identity_version,
                        ab_protocol_valid,
                        full_prompt_ab_eligible, ab_first_pass_clean,
                        authority_violations_json, review_context_json,
                        validation_audit_json, pair_diff_json, usage_json, indexed_at_ms
                    ) VALUES (
                        :run_id, :record_path, :timestamp_local_ms, :symbol, :timeframe, :bar_count,
                        :decision_stance, :model_name, :run_status, :error_type, :partial_reason,
                        :stage1_cycle_position, :stage1_direction, :stage1_gate_result,
                        :order_type, :order_direction, :trade_confidence, :estimated_win_rate,
                        :snapshot_latest_ts_open, :entry_price, :stop_loss_price,
                        :take_profit_price, :take_profit_price_2,
                        :terminal_outcome, :htf_status, :htf_timeframe, :htf_reason,
                        :vpa_mode, :pair_id, :snapshot_hash, :catalog_id,
                        :volume_kind, :venue, :vpa_data_status,
                        :vpa_background_relation, :vpa_timing_relation, :vpa_effect,
                        :baseline_order_type, :vpa_order_type, :baseline_trade_confidence,
                        :vpa_trade_confidence, :vpa_order_direction, :vpa_entry_price,
                        :vpa_stop_loss_price, :vpa_take_profit_price, :vpa_take_profit_price_2,
                        :observed_effect, :experiment_type, :pair_identity_version,
                        :ab_protocol_valid,
                        :full_prompt_ab_eligible, :ab_first_pass_clean,
                        :authority_violations_json, :review_context_json,
                        :validation_audit_json, :pair_diff_json, :usage_json, :indexed_at_ms
                    )
                    ON CONFLICT(run_id) DO UPDATE SET
                        record_path = excluded.record_path,
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
                        pair_id = excluded.pair_id,
                        snapshot_hash = excluded.snapshot_hash,
                        catalog_id = excluded.catalog_id,
                        volume_kind = excluded.volume_kind,
                        venue = excluded.venue,
                        vpa_data_status = excluded.vpa_data_status,
                        vpa_background_relation = excluded.vpa_background_relation,
                        vpa_timing_relation = excluded.vpa_timing_relation,
                        vpa_effect = excluded.vpa_effect,
                        baseline_order_type = excluded.baseline_order_type,
                        vpa_order_type = excluded.vpa_order_type,
                        baseline_trade_confidence = excluded.baseline_trade_confidence,
                        vpa_trade_confidence = excluded.vpa_trade_confidence,
                        vpa_order_direction = excluded.vpa_order_direction,
                        vpa_entry_price = excluded.vpa_entry_price,
                        vpa_stop_loss_price = excluded.vpa_stop_loss_price,
                        vpa_take_profit_price = excluded.vpa_take_profit_price,
                        vpa_take_profit_price_2 = excluded.vpa_take_profit_price_2,
                        observed_effect = excluded.observed_effect,
                        experiment_type = excluded.experiment_type,
                        pair_identity_version = excluded.pair_identity_version,
                        ab_protocol_valid = excluded.ab_protocol_valid,
                        full_prompt_ab_eligible = excluded.full_prompt_ab_eligible,
                        ab_first_pass_clean = excluded.ab_first_pass_clean,
                        authority_violations_json = excluded.authority_violations_json,
                        review_context_json = excluded.review_context_json,
                        validation_audit_json = excluded.validation_audit_json,
                        pair_diff_json = excluded.pair_diff_json,
                        usage_json = excluded.usage_json,
                        indexed_at_ms = excluded.indexed_at_ms
                    """,
                    fields,
                )
                self._replace_calls(connection, record)
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

    def get_counterfactual_outcome(
        self,
        run_id: str,
        *,
        arm: str = "baseline",
    ) -> dict[str, Any] | None:
        """Return deterministic OHLC replay output, separate from actual fills."""
        return self._one(
            "SELECT * FROM counterfactual_outcomes WHERE run_id = ? AND arm = ?",
            (run_id, arm),
        )

    def counterfactual_performance_report(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        min_decisive_snapshots: int = 30,
    ) -> dict[str, Any]:
        """Summarize replayed A/B plans without presenting them as live PnL."""
        clauses = [
            "r.run_status = 'completed'",
            "r.pair_id IS NOT NULL",
            "r.pair_id <> ''",
            "r.experiment_type = 'full_prompt_ab'",
            f"r.pair_identity_version = '{VPA_PAIR_IDENTITY_VERSION}'",
            "r.ab_protocol_valid = 1",
            "r.full_prompt_ab_eligible = 1",
        ]
        values: list[Any] = []
        if symbol:
            clauses.append("r.symbol = ?")
            values.append(symbol)
        if timeframe:
            clauses.append("r.timeframe = ?")
            values.append(timeframe)
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT c.*, r.snapshot_hash, r.pair_id FROM counterfactual_outcomes c "
                    "JOIN analysis_runs r ON r.run_id = c.run_id WHERE "
                    + " AND ".join(clauses),
                    tuple(values),
                ).fetchall()
        except (OSError, sqlite3.Error, ValueError) as exc:
            return {"status": "unavailable", "reason": str(exc)}

        groups: dict[str, dict[str, list[sqlite3.Row]]] = {}
        for row in rows:
            snapshot_key = str(row["pair_id"] or row["snapshot_hash"] or row["run_id"])
            groups.setdefault(snapshot_key, {}).setdefault(str(row["arm"]), []).append(row)

        def summarize(arm: str, snapshot_keys: set[str] | None = None) -> dict[str, Any]:
            selected = {
                key: arms.get(arm, [])
                for key, arms in groups.items()
                if snapshot_keys is None or key in snapshot_keys
            }
            selected = {key: arm_rows for key, arm_rows in selected.items() if arm_rows}
            arm_rows = [row for group_rows in selected.values() for row in group_rows]
            decisive = [row for row in arm_rows if row["outcome"] in {"win", "loss"}]
            per_snapshot_win_rate: list[float] = []
            per_snapshot_return_r: list[float] = []
            for group_rows in selected.values():
                group_decisive = [
                    row for row in group_rows if row["outcome"] in {"win", "loss"}
                ]
                if not group_decisive:
                    continue
                per_snapshot_win_rate.append(
                    sum(row["outcome"] == "win" for row in group_decisive)
                    / len(group_decisive)
                )
                per_snapshot_return_r.append(
                    sum(float(row["realized_return_r"]) for row in group_decisive)
                    / len(group_decisive)
                )
            status_counts: dict[str, int] = {}
            for row in arm_rows:
                status = str(row["status"] or "unknown")
                status_counts[status] = status_counts.get(status, 0) + 1
            wins = sum(row["outcome"] == "win" for row in decisive)
            losses = sum(row["outcome"] == "loss" for row in decisive)
            return {
                "plans": len(arm_rows),
                "unique_snapshots_with_plan": len(selected),
                "decisive_plans": len(decisive),
                "unique_decisive_snapshots": len(per_snapshot_win_rate),
                "wins": wins,
                "losses": losses,
                "attempt_win_rate": round(wins / len(decisive), 4) if decisive else None,
                "win_rate": (
                    round(sum(per_snapshot_win_rate) / len(per_snapshot_win_rate), 4)
                    if per_snapshot_win_rate
                    else None
                ),
                "mean_return_r": (
                    round(sum(per_snapshot_return_r) / len(per_snapshot_return_r), 4)
                    if per_snapshot_return_r
                    else None
                ),
                "ambiguous": sum(row["outcome"] == "ambiguous" for row in arm_rows),
                "unresolved": sum(
                    row["outcome"] in {"pending", "unresolved"} for row in arm_rows
                ),
                "status_counts": status_counts,
            }

        summaries = {arm: summarize(arm) for arm in ("baseline", "vpa")}
        matched_decisive = {
            key
            for key, arms in groups.items()
            if all(
                any(row["outcome"] in {"win", "loss"} for row in arms.get(arm, []))
                for arm in ("baseline", "vpa")
            )
        }
        matched = {arm: summarize(arm, matched_decisive) for arm in ("baseline", "vpa")}
        required = max(1, int(min_decisive_snapshots))
        enough = len(matched_decisive) >= required

        baseline_win_rate = matched["baseline"]["win_rate"]
        vpa_win_rate = matched["vpa"]["win_rate"]
        baseline_mean_r = matched["baseline"]["mean_return_r"]
        vpa_mean_r = matched["vpa"]["mean_return_r"]
        paired_return_deltas: list[float] = []
        for key in sorted(matched_decisive):
            snapshot_returns: dict[str, float] = {}
            for arm in ("baseline", "vpa"):
                decisive_rows = [
                    row
                    for row in groups[key].get(arm, [])
                    if row["outcome"] in {"win", "loss"}
                ]
                snapshot_returns[arm] = sum(
                    float(row["realized_return_r"]) for row in decisive_rows
                ) / len(decisive_rows)
            paired_return_deltas.append(
                snapshot_returns["vpa"] - snapshot_returns["baseline"]
            )
        tolerance = 1e-12
        improved = sum(delta > tolerance for delta in paired_return_deltas)
        worsened = sum(delta < -tolerance for delta in paired_return_deltas)
        unchanged = len(paired_return_deltas) - improved - worsened
        return {
            "status": "counterfactual_only",
            "experiment_type": "full_prompt_ab",
            "statistically_reviewable": enough,
            "matched_decisive_snapshots": len(matched_decisive),
            "minimum_matched_snapshots": required,
            "baseline": summaries["baseline"],
            "vpa": summaries["vpa"],
            "matched_comparison": {
                "baseline": matched["baseline"],
                "vpa": matched["vpa"],
                "win_rate_delta": (
                    round(vpa_win_rate - baseline_win_rate, 4)
                    if vpa_win_rate is not None and baseline_win_rate is not None
                    else None
                ),
                "mean_return_r_delta": (
                    round(vpa_mean_r - baseline_mean_r, 4)
                    if vpa_mean_r is not None and baseline_mean_r is not None
                    else None
                ),
                "improved_snapshots": improved,
                "worsened_snapshots": worsened,
                "unchanged_snapshots": unchanged,
                "directional_sign_test_p": _two_sided_sign_test(improved, worsened),
            },
            "limitations": [
                "Uses closed OHLC and planned prices, not broker fills, fees, slippage, or spread.",
                "Each replay horizon is capped at the saved bar window minus the origin snapshot.",
                "Same-bar event ordering is classified as ambiguous and excluded from win/loss.",
                "Market orders without an actual fill price are excluded from outcomes.",
                "Repeated runs are averaged within each frozen snapshot; only matched decisive A/B snapshots drive deltas.",
                "Manual analysis_outcomes remains the source for actual executed-trade review.",
            ],
        }

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

    def vpa_frequency_guard_report(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        min_unique_snapshots: int = 50,
        max_relative_trade_rate_decline: float = 0.15,
    ) -> dict[str, Any]:
        """Report paired A/B frequency effects without enabling Soft execution.

        Repeated runs on the same frozen snapshot are averaged inside one pair
        group, so LLM repeat attempts do not masquerade as independent samples.
        The report is an activation guard and audit aid, never an entry-time
        VPA gate or a profitability claim.
        """
        pair_clauses = [
            "vpa_mode IN ('shadow', 'soft')",
            "pair_id IS NOT NULL",
            "pair_id <> ''",
            "experiment_type = 'full_prompt_ab'",
        ]
        clauses = [
            *pair_clauses,
            f"pair_identity_version = '{VPA_PAIR_IDENTITY_VERSION}'",
            "ab_protocol_valid = 1",
            "full_prompt_ab_eligible = 1",
            "vpa_order_type IS NOT NULL",
        ]
        values: list[Any] = []
        if symbol:
            clauses.append("symbol = ?")
            pair_clauses.append("symbol = ?")
            values.append(symbol)
        if timeframe:
            clauses.append("timeframe = ?")
            pair_clauses.append("timeframe = ?")
            values.append(timeframe)
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT pair_id, order_type, vpa_order_type, observed_effect "
                    "FROM analysis_runs WHERE " + " AND ".join(clauses),
                    tuple(values),
                ).fetchall()
                pair_status = connection.execute(
                    "SELECT COUNT(*) AS total_attempts, "
                    "SUM(CASE WHEN observed_effect = 'vpa_arm_failed' THEN 1 ELSE 0 END) "
                    "AS failed_attempts, "
                    "SUM(CASE WHEN COALESCE(ab_protocol_valid, 0) <> 1 THEN 1 ELSE 0 END) "
                    "AS protocol_invalid_attempts, "
                    "SUM(CASE WHEN COALESCE(full_prompt_ab_eligible, 0) <> 1 "
                    f"OR COALESCE(pair_identity_version, '') <> '{VPA_PAIR_IDENTITY_VERSION}' "
                    "THEN 1 ELSE 0 END) "
                    "AS ineligible_attempts FROM analysis_runs WHERE "
                    + " AND ".join(pair_clauses),
                    tuple(values),
                ).fetchone()
                labelled_outcomes = connection.execute(
                    "SELECT COUNT(*) FROM analysis_outcomes o "
                    "JOIN analysis_runs r ON r.run_id = o.run_id "
                    "WHERE r.vpa_mode IN ('shadow', 'soft') "
                    "AND r.pair_id IS NOT NULL AND r.pair_id <> '' "
                    "AND r.experiment_type = 'full_prompt_ab' "
                    f"AND r.pair_identity_version = '{VPA_PAIR_IDENTITY_VERSION}' "
                    "AND r.ab_protocol_valid = 1 "
                    "AND r.full_prompt_ab_eligible = 1 "
                    "AND r.vpa_order_type IS NOT NULL "
                    "AND o.entry_status = 'filled' "
                    "AND o.outcome IN ('win', 'loss', 'breakeven')"
                    + (" AND r.symbol = ?" if symbol else "")
                    + (" AND r.timeframe = ?" if timeframe else ""),
                    tuple(([symbol] if symbol else []) + ([timeframe] if timeframe else [])),
                ).fetchone()[0]
        except (OSError, sqlite3.Error, ValueError) as exc:
            logger.warning("VPA frequency report query failed: %s", exc)
            return {"status": "unavailable", "reason": str(exc), "ready_for_soft": False}

        groups: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            groups.setdefault(str(row["pair_id"]), []).append(row)
        baseline_rates: list[float] = []
        vpa_rates: list[float] = []
        attempt_effect_counts: dict[str, int] = {}
        snapshot_effect_counts: dict[str, int] = {}
        mixed_repeated_attempts = 0
        for attempts in groups.values():
            baseline_rates.append(
                sum(_is_executable_order(item["order_type"]) for item in attempts) / len(attempts)
            )
            vpa_rates.append(
                sum(_is_executable_order(item["vpa_order_type"]) for item in attempts) / len(attempts)
            )
            effects: set[str] = set()
            for item in attempts:
                effect = str(item["observed_effect"] or "unknown")
                effects.add(effect)
                attempt_effect_counts[effect] = attempt_effect_counts.get(effect, 0) + 1
            snapshot_effect = next(iter(effects)) if len(effects) == 1 else "mixed_repeated_attempts"
            if snapshot_effect == "mixed_repeated_attempts":
                mixed_repeated_attempts += 1
            snapshot_effect_counts[snapshot_effect] = (
                snapshot_effect_counts.get(snapshot_effect, 0) + 1
            )
        baseline_rate = sum(baseline_rates) / len(baseline_rates) if baseline_rates else 0.0
        vpa_rate = sum(vpa_rates) / len(vpa_rates) if vpa_rates else 0.0
        relative_decline = (
            max(0.0, (baseline_rate - vpa_rate) / baseline_rate)
            if baseline_rate > 0
            else None
        )
        reasons = [
            "Soft remains locked: this report is advisory and does not alter any live decision.",
            "Counterfactual A/B frequency is not a profitability estimate.",
        ]
        if len(groups) < max(1, int(min_unique_snapshots)):
            reasons.append("Insufficient unique frozen snapshots for activation review.")
        if baseline_rate <= 0:
            reasons.append("Baseline has no executable-order rate to compare.")
        if relative_decline is not None and relative_decline > max(0.0, max_relative_trade_rate_decline):
            reasons.append("VPA counterfactual trade rate declines beyond the configured review tolerance.")
        if mixed_repeated_attempts:
            reasons.append(
                "Some frozen snapshots have mixed repeated A/B effects; inspect those records before treating VPA impact as stable."
            )
        total_pair_attempts = int(pair_status["total_attempts"] or 0)
        failed_vpa_attempts = int(pair_status["failed_attempts"] or 0)
        protocol_invalid_attempts = int(
            pair_status["protocol_invalid_attempts"] or 0
        )
        ineligible_attempts = int(pair_status["ineligible_attempts"] or 0)
        if failed_vpa_attempts:
            reasons.append(
                "Some VPA arms failed validation and are excluded from A/B effect and trade-rate statistics."
            )
        if ineligible_attempts:
            reasons.append(
                "Runs with retries, protocol defects, or VPA authority drift are excluded from A/B statistics."
            )
        return {
            "status": "shadow_observation",
            "experiment_type": "full_prompt_ab",
            "ready_for_soft": False,
            "paired_attempts": len(rows),
            "all_paired_attempts": total_pair_attempts,
            "failed_vpa_attempts": failed_vpa_attempts,
            "protocol_invalid_attempts": protocol_invalid_attempts,
            "ineligible_attempts": ineligible_attempts,
            "vpa_arm_failure_rate": (
                round(failed_vpa_attempts / total_pair_attempts, 4)
                if total_pair_attempts
                else 0.0
            ),
            "unique_snapshots": len(groups),
            "baseline_executable_rate": round(baseline_rate, 4),
            "vpa_executable_rate": round(vpa_rate, 4),
            "relative_trade_rate_decline": (
                round(relative_decline, 4) if relative_decline is not None else None
            ),
            "max_relative_trade_rate_decline": max_relative_trade_rate_decline,
            "labelled_filled_outcomes": int(labelled_outcomes),
            "effect_counts": snapshot_effect_counts,
            "attempt_effect_counts": attempt_effect_counts,
            "mixed_repeated_attempts": mixed_repeated_attempts,
            "reasons": reasons,
        }

    def rebuild_from_records(self, records_dir: Path) -> AuditRebuildResult:
        """Rebuild this derived index from saved JSON records without deleting them."""
        indexed = 0
        skipped = 0
        for path in sorted(Path(records_dir).glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                partial_reason = raw.pop("_partial_reason", None)
                record = AnalysisRecord.model_validate(raw)
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
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM analysis_runs WHERE symbol = ? AND timeframe = ? "
            "AND run_status = 'completed' AND snapshot_latest_ts_open IS NOT NULL "
            "AND snapshot_latest_ts_open <= ?",
            (current_record.meta.symbol, current_record.meta.timeframe, latest_ts),
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
                if (self._bar_ts(bar) or -1) > snapshot_ts
                and bar.get("closed") is not False
            ]
            self._advance_counterfactual_arm(
                connection,
                row,
                arm="baseline",
                future_bars=future_bars,
            )
            if row["pair_id"]:
                self._advance_counterfactual_arm(
                    connection,
                    row,
                    arm="vpa",
                    future_bars=future_bars,
                )

    def _advance_counterfactual_arm(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        arm: str,
        future_bars: list[dict[str, Any]],
    ) -> None:
        existing = connection.execute(
            "SELECT status, horizon_bars, assumption_version "
            "FROM counterfactual_outcomes WHERE run_id = ? AND arm = ?",
            (row["run_id"], arm),
        ).fetchone()
        effective_horizon = self._effective_horizon_for_row(row)
        current_assumptions = (
            existing is not None
            and int(existing["horizon_bars"]) == effective_horizon
            and str(existing["assumption_version"]) == self._COUNTERFACTUAL_VERSION
        )
        if (
            current_assumptions
            and str(existing["status"]) in self._COUNTERFACTUAL_TERMINAL
        ):
            return
        if arm == "vpa" and row["observed_effect"] == "vpa_arm_failed":
            payload = {
                "status": "arm_failed",
                "entry_status": "not_traded",
                "outcome": "not_applicable",
                "horizon_bars": effective_horizon,
                "bars_observed": 0,
                "entry_bar_ts": None,
                "exit_bar_ts": None,
                "realized_return_r": None,
                "mfe_r": None,
                "mae_r": None,
                "evaluated_through_ts": None,
                "notes": "VPA arm did not produce a validated decision.",
            }
        else:
            from pa_agent.records.counterfactual import evaluate_trade_plan

            payload = asdict(
                evaluate_trade_plan(
                    self._plan_from_row(row, arm=arm),
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
                arm,
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
    def _plan_from_row(row: sqlite3.Row, *, arm: str) -> dict[str, Any]:
        prefix = "vpa_" if arm == "vpa" else ""
        return {
            "order_type": row[f"{prefix}order_type"],
            "order_direction": row[f"{prefix}order_direction"],
            "entry_price": row[f"{prefix}entry_price"],
            "stop_loss_price": row[f"{prefix}stop_loss_price"],
            "take_profit_price": row[f"{prefix}take_profit_price"],
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

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._db_path, timeout=5.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_runs (
                run_id TEXT PRIMARY KEY,
                record_path TEXT NOT NULL,
                timestamp_local_ms INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
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
                pair_id TEXT,
                snapshot_hash TEXT,
                catalog_id TEXT,
                volume_kind TEXT,
                venue TEXT,
                vpa_data_status TEXT,
                vpa_background_relation TEXT,
                vpa_timing_relation TEXT,
                vpa_effect TEXT,
                baseline_order_type TEXT,
                vpa_order_type TEXT,
                baseline_trade_confidence REAL,
                vpa_trade_confidence REAL,
                vpa_order_direction TEXT,
                vpa_entry_price REAL,
                vpa_stop_loss_price REAL,
                vpa_take_profit_price REAL,
                vpa_take_profit_price_2 REAL,
                observed_effect TEXT,
                experiment_type TEXT,
                pair_identity_version TEXT,
                ab_protocol_valid INTEGER,
                full_prompt_ab_eligible INTEGER,
                ab_first_pass_clean INTEGER,
                authority_violations_json TEXT,
                review_context_json TEXT,
                validation_audit_json TEXT,
                pair_diff_json TEXT,
                usage_json TEXT,
                indexed_at_ms INTEGER NOT NULL
            )
            """
        )
        self._ensure_analysis_run_columns(connection)
        connection.execute(
            """
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
            """
        )
        connection.execute(
            """
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
                created_at_ms INTEGER NOT NULL,
                PRIMARY KEY (run_id, arm, stage, attempt)
            )
            """
        )
        connection.execute(
            """
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
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_runs_symbol_tf_time "
            "ON analysis_runs(symbol, timeframe, timestamp_local_ms DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_runs_vpa_pair "
            "ON analysis_runs(pair_id, timestamp_local_ms DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_counterfactual_arm_outcome "
            "ON counterfactual_outcomes(arm, outcome, status)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_calls_stage_latency "
            "ON analysis_calls(arm, stage, latency_ms)"
        )
        connection.execute(f"PRAGMA user_version = {self._SCHEMA_VERSION}")

    @staticmethod
    def _ensure_analysis_run_columns(connection: sqlite3.Connection) -> None:
        """Migrate the derived index in place; source JSON records stay untouched."""
        existing = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(analysis_runs)").fetchall()
        }
        required = {
            "htf_status": "TEXT",
            "htf_timeframe": "TEXT",
            "htf_reason": "TEXT",
            "baseline_order_type": "TEXT",
            "vpa_order_type": "TEXT",
            "baseline_trade_confidence": "REAL",
            "vpa_trade_confidence": "REAL",
            "observed_effect": "TEXT",
            "snapshot_latest_ts_open": "INTEGER",
            "entry_price": "REAL",
            "stop_loss_price": "REAL",
            "take_profit_price": "REAL",
            "take_profit_price_2": "REAL",
            "vpa_order_direction": "TEXT",
            "vpa_entry_price": "REAL",
            "vpa_stop_loss_price": "REAL",
            "vpa_take_profit_price": "REAL",
            "vpa_take_profit_price_2": "REAL",
            "vpa_background_relation": "TEXT",
            "vpa_timing_relation": "TEXT",
            "experiment_type": "TEXT",
            "pair_identity_version": "TEXT",
            "ab_protocol_valid": "INTEGER",
            "full_prompt_ab_eligible": "INTEGER",
            "ab_first_pass_clean": "INTEGER",
            "authority_violations_json": "TEXT",
            "review_context_json": "TEXT",
            "validation_audit_json": "TEXT",
        }
        for column, definition in required.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE analysis_runs ADD COLUMN {column} {definition}")

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
                    error_type, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    int(raw.get("created_at_ms") or cls._now_ms()),
                ),
            )

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
        sidecar = record.vpa_sidecar if isinstance(record.vpa_sidecar, dict) else {}
        catalog = sidecar.get("evidence_catalog") if isinstance(sidecar, dict) else {}
        catalog = catalog if isinstance(catalog, dict) else {}
        provenance = catalog.get("provenance") if isinstance(catalog, dict) else {}
        provenance = provenance if isinstance(provenance, dict) else {}
        impact = sidecar.get("stage2_impact") if isinstance(sidecar, dict) else {}
        impact = impact if isinstance(impact, dict) else {}
        vpa_context = sidecar.get("stage1_context") if isinstance(sidecar, dict) else {}
        vpa_context = vpa_context if isinstance(vpa_context, dict) else {}
        comparison = sidecar.get("comparison") if isinstance(sidecar, dict) else {}
        comparison = comparison if isinstance(comparison, dict) else {}
        observed = comparison.get("observed_effect") if isinstance(comparison, dict) else {}
        observed = observed if isinstance(observed, dict) else {}
        paired_execution = sidecar.get("paired_execution") if isinstance(sidecar, dict) else {}
        paired_execution = paired_execution if isinstance(paired_execution, dict) else {}
        vpa_arm = sidecar.get("vpa_arm") if isinstance(sidecar, dict) else {}
        vpa_arm = vpa_arm if isinstance(vpa_arm, dict) else {}
        vpa_stage2 = vpa_arm.get("stage2_decision") if isinstance(vpa_arm, dict) else {}
        vpa_stage2 = vpa_stage2 if isinstance(vpa_stage2, dict) else {}
        vpa_decision = vpa_stage2.get("decision") if isinstance(vpa_stage2, dict) else {}
        vpa_decision = vpa_decision if isinstance(vpa_decision, dict) else {}
        htf = record.htf_context if isinstance(record.htf_context, dict) else {}
        exception = record.exception if isinstance(record.exception, dict) else {}
        if exception:
            run_status = "failed"
        elif partial_reason:
            run_status = "partial"
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
            "decision_review": decision_review,
        }
        return {
            "run_id": record.meta.run_id,
            "record_path": str(record_path),
            "timestamp_local_ms": record.meta.timestamp_local_ms,
            "symbol": record.meta.symbol,
            "timeframe": record.meta.timeframe,
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
            "vpa_mode": sidecar.get("mode"),
            "pair_id": sidecar.get("pair_id"),
            "snapshot_hash": sidecar.get("snapshot_hash"),
            "catalog_id": catalog.get("catalog_id"),
            "volume_kind": provenance.get("volume_kind"),
            "venue": provenance.get("venue"),
            "vpa_data_status": catalog.get("data_status"),
            "vpa_background_relation": impact.get("background_relation"),
            "vpa_timing_relation": impact.get("entry_timing_relation"),
            "vpa_effect": impact.get("decision_effect"),
            "baseline_order_type": decision.get("order_type") if sidecar else None,
            "vpa_order_type": vpa_decision.get("order_type"),
            "baseline_trade_confidence": decision.get("trade_confidence") if sidecar else None,
            "vpa_trade_confidence": vpa_decision.get("trade_confidence"),
            "vpa_order_direction": vpa_decision.get("order_direction"),
            "vpa_entry_price": vpa_decision.get("entry_price"),
            "vpa_stop_loss_price": vpa_decision.get("stop_loss_price"),
            "vpa_take_profit_price": vpa_decision.get("take_profit_price"),
            "vpa_take_profit_price_2": vpa_decision.get("take_profit_price_2"),
            "observed_effect": observed.get("classification"),
            "experiment_type": paired_execution.get("experiment_type"),
            "pair_identity_version": paired_execution.get("pair_identity_version"),
            "ab_protocol_valid": int(bool(paired_execution.get("protocol_valid"))),
            "full_prompt_ab_eligible": int(
                bool(paired_execution.get("full_prompt_ab_eligible"))
            ),
            "ab_first_pass_clean": int(
                bool(paired_execution.get("first_pass_clean"))
            ),
            "authority_violations_json": cls._json(
                paired_execution.get("authority_violations") or []
            ),
            "review_context_json": cls._json(review_context),
            "validation_audit_json": cls._json(record.validation_audit),
            "pair_diff_json": cls._json(comparison),
            "usage_json": cls._json(record.usage_total),
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


def _two_sided_sign_test(improved: int, worsened: int) -> float | None:
    """Exact two-sided sign test for paired snapshot return direction."""
    n = max(0, int(improved)) + max(0, int(worsened))
    if n == 0:
        return None
    tail = min(max(0, int(improved)), max(0, int(worsened)))
    probability = 2.0 * sum(math.comb(n, index) for index in range(tail + 1)) / (2**n)
    return round(min(1.0, probability), 6)
