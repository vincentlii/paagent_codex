"""SQLite manifest and matched reports for offline A/A/B/B experiments."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pa_agent.data.bar_close_wait import timeframe_to_seconds

logger = logging.getLogger(__name__)

_EXECUTABLE_ORDER_TYPES = frozenset(
    {
        "limit",
        "breakout",
        "market",
        "\u9650\u4ef7\u5355",
        "\u7a81\u7834\u5355",
        "\u5e02\u4ef7\u5355",
    }
)
_NO_ORDER_TYPES = frozenset({"no_order", "none", "not_applicable", "\u4e0d\u4e0b\u5355"})
_ZERO_RETURN_STATUSES = frozenset({"entry_not_triggered", "no_order"})
_ZERO_RETURN_ENTRY_STATUSES = frozenset({"not_triggered", "untriggered"})
_CENSORED_STATUSES = frozenset(
    {
        "pending",
        "pending_entry",
        "pending_exit",
        "ambiguous",
        "ambiguous_entry",
        "ambiguous_exit",
        "ambiguous_entry_and_exit",
    }
)
_CENSORED_OUTCOMES = frozenset({"pending", "unresolved", "ambiguous"})
_TRADE_OUTCOMES = frozenset({"win", "loss", "breakeven"})
_PA_VPA_PROTOCOL_VERSIONS = ("pa_vpa_aabb_v1", "pa_vpa_aabb_v2")


def _is_executable_order(value: object) -> bool:
    return str(value or "").strip().lower() in {item.lower() for item in _EXECUTABLE_ORDER_TYPES}


@dataclass(frozen=True)
class ExperimentWriteResult:
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class ContinuousSnapshotClaim:
    acquired: bool
    reason: str
    attempt: int = 0
    lease_token: str | None = None
    experiment_id: str | None = None


@dataclass(frozen=True)
class _ArmOutcome:
    state: str
    raw_return_r: float | None
    adjusted_return_r: float | None
    traded: bool
    won: bool | None
    integrity_conflict: bool = False


def _row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class AnalysisExperimentStore:
    """Own experiment manifests while analysis JSON remains the source artifact."""

    def __init__(self, db_path: Path, manifest_dir: Path | None = None) -> None:
        self._db_path = Path(db_path)
        self._manifest_dir = Path(manifest_dir or self._db_path.parent / "experiments")

    def start_experiment(self, manifest: dict[str, Any]) -> ExperimentWriteResult:
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.execute(
                    """
                    INSERT INTO analysis_experiments (
                        experiment_id, protocol_version, status, symbol, timeframe,
                        frozen_input_hash, market_snapshot_hash, cohort_id,
                        snapshot_hash, htf_hash, catalog_id,
                        provider_fingerprint, settings_fingerprint,
                        analysis_fingerprint, order_scheme,
                        arm_sequence_json, source_run_id, source_record_path,
                        protocol_valid, protocol_errors_json,
                        external_comparison_json, created_at_ms, completed_at_ms
                    ) VALUES (
                        :experiment_id, :protocol_version, :status, :symbol, :timeframe,
                        :frozen_input_hash, :market_snapshot_hash, :cohort_id,
                        :snapshot_hash, :htf_hash, :catalog_id,
                        :provider_fingerprint, :settings_fingerprint,
                        :analysis_fingerprint, :order_scheme,
                        :arm_sequence_json, :source_run_id, :source_record_path,
                        NULL, '[]', '{}', :created_at_ms, NULL
                    )
                    """,
                    {
                        **manifest,
                        "arm_sequence_json": self._json(manifest.get("arm_sequence") or []),
                        "created_at_ms": self._now_ms(),
                    },
                )
            return self._sync_json_manifest(str(manifest["experiment_id"]))
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("Experiment manifest start failed: %s", exc)
            return ExperimentWriteResult(ok=False, error=str(exc))

    def record_arm(
        self,
        experiment_id: str,
        arm: dict[str, Any],
    ) -> ExperimentWriteResult:
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.execute(
                    """
                    INSERT INTO experiment_arms (
                        experiment_id, arm_id, group_name, replicate,
                        sequence_position, assigned_mode, received_path, run_id,
                        record_path, run_status, first_pass_clean,
                        treatment_received, stage1_request_hash,
                        client_instance_token, persisted, error, recorded_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(experiment_id, arm_id) DO UPDATE SET
                        received_path = excluded.received_path,
                        run_id = excluded.run_id,
                        record_path = excluded.record_path,
                        run_status = excluded.run_status,
                        first_pass_clean = excluded.first_pass_clean,
                        treatment_received = excluded.treatment_received,
                        stage1_request_hash = excluded.stage1_request_hash,
                        client_instance_token = excluded.client_instance_token,
                        persisted = excluded.persisted,
                        error = excluded.error,
                        recorded_at_ms = excluded.recorded_at_ms
                    """,
                    (
                        experiment_id,
                        arm["arm_id"],
                        arm["group_name"],
                        int(arm["replicate"]),
                        int(arm["sequence_position"]),
                        arm["assigned_mode"],
                        arm.get("received_path"),
                        arm.get("run_id"),
                        arm.get("record_path"),
                        arm.get("run_status"),
                        int(bool(arm.get("first_pass_clean"))),
                        int(bool(arm.get("treatment_received"))),
                        arm.get("stage1_request_hash"),
                        arm.get("client_instance_token"),
                        int(bool(arm.get("persisted"))),
                        arm.get("error"),
                        self._now_ms(),
                    ),
                )
            return self._sync_json_manifest(experiment_id)
        except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
            logger.warning("Experiment arm write failed: %s", exc)
            return ExperimentWriteResult(ok=False, error=str(exc))

    def finish_experiment(
        self,
        experiment_id: str,
        *,
        status: str,
        protocol_report: dict[str, Any],
        external_comparison: dict[str, Any],
    ) -> ExperimentWriteResult:
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                cursor = connection.execute(
                    """
                    UPDATE analysis_experiments SET
                        status = ?, protocol_valid = ?, protocol_errors_json = ?,
                        external_comparison_json = ?, completed_at_ms = ?
                    WHERE experiment_id = ?
                    """,
                    (
                        status,
                        int(bool(protocol_report.get("valid"))),
                        self._json(protocol_report.get("errors") or []),
                        self._json(external_comparison),
                        self._now_ms(),
                        experiment_id,
                    ),
                )
                if cursor.rowcount != 1:
                    return ExperimentWriteResult(
                        ok=False,
                        error=f"unknown experiment_id: {experiment_id}",
                    )
            return self._sync_json_manifest(experiment_id)
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("Experiment manifest finish failed: %s", exc)
            return ExperimentWriteResult(ok=False, error=str(exc))

    def get_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    "SELECT * FROM analysis_experiments WHERE experiment_id = ?",
                    (experiment_id,),
                ).fetchone()
                if row is None:
                    return None
                arms = connection.execute(
                    "SELECT * FROM experiment_arms WHERE experiment_id = ? "
                    "ORDER BY sequence_position",
                    (experiment_id,),
                ).fetchall()
            result = dict(row)
            result["arm_sequence"] = json.loads(result.pop("arm_sequence_json"))
            result["protocol_errors"] = json.loads(result.pop("protocol_errors_json"))
            result["external_comparison"] = json.loads(result.pop("external_comparison_json"))
            result["arms"] = [dict(item) for item in arms]
            return result
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            logger.warning("Experiment manifest query failed: %s", exc)
            return None

    def find_completed_experiment(
        self,
        *,
        cohort_id: str,
        market_snapshot_hash: str,
    ) -> dict[str, Any] | None:
        """Return the newest valid completion for one paired-snapshot unit."""
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                row = connection.execute(
                    """
                    SELECT experiment_id FROM analysis_experiments
                    WHERE cohort_id = ? AND market_snapshot_hash = ?
                      AND status = 'completed' AND protocol_valid = 1
                    ORDER BY completed_at_ms DESC LIMIT 1
                    """,
                    (cohort_id, market_snapshot_hash),
                ).fetchone()
            return self.get_experiment(str(row[0])) if row is not None else None
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("Completed experiment query failed: %s", exc)
            raise

    def _sync_json_manifest(self, experiment_id: str) -> ExperimentWriteResult:
        """Atomically mirror the SQLite manifest as a rebuildable JSON artifact."""
        if not re.fullmatch(r"[A-Za-z0-9._-]+", experiment_id):
            return ExperimentWriteResult(ok=False, error="invalid experiment_id")
        manifest = self.get_experiment(experiment_id)
        if manifest is None:
            return ExperimentWriteResult(
                ok=False,
                error=f"experiment manifest unavailable: {experiment_id}",
            )
        try:
            self._manifest_dir.mkdir(parents=True, exist_ok=True)
            path = self._manifest_dir / f"{experiment_id}.json"
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, path)
            return ExperimentWriteResult(ok=True)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("Experiment JSON manifest sync failed: %s", exc)
            return ExperimentWriteResult(ok=False, error=str(exc))

    def list_experiments(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        cohort_id: str | None = None,
        protocol_version: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if symbol:
            clauses.append("symbol = ?")
            values.append(symbol)
        if timeframe:
            clauses.append("timeframe = ?")
            values.append(timeframe)
        if cohort_id:
            clauses.append("cohort_id = ?")
            values.append(cohort_id)
        if protocol_version:
            clauses.append("protocol_version = ?")
            values.append(protocol_version)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT * FROM analysis_experiments"
                    + where
                    + " ORDER BY created_at_ms DESC LIMIT ?",
                    (*values, max(1, min(int(limit), 2000))),
                ).fetchall()
            return [dict(row) for row in rows]
        except (OSError, sqlite3.Error, ValueError) as exc:
            logger.warning("Experiment list failed: %s", exc)
            return []

    def claim_continuous_snapshot(
        self,
        *,
        pipeline_id: str,
        cohort_id: str,
        market_snapshot_hash: str,
        source_run_id: str,
        source_record_path: str,
        max_attempts: int,
        lease_seconds: float,
        now_ms: int | None = None,
    ) -> ContinuousSnapshotClaim:
        """Atomically lease one paired market snapshot for continuous research."""
        timestamp = self._now_ms() if now_ms is None else int(now_ms)
        lease_until_ms = timestamp + max(1, int(float(lease_seconds) * 1000))
        lease_token = uuid4().hex
        maximum_attempts = max(1, int(max_attempts))
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.commit()
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT status, attempt_count, lease_until_ms, next_retry_at_ms,
                           experiment_id
                    FROM continuous_aabb_snapshots
                    WHERE pipeline_id = ? AND cohort_id = ? AND market_snapshot_hash = ?
                    """,
                    (pipeline_id, cohort_id, market_snapshot_hash),
                ).fetchone()
                if row is not None:
                    status = str(row[0])
                    attempts = int(row[1])
                    active_until = int(row[2] or 0)
                    next_retry = int(row[3] or 0)
                    experiment_id = str(row[4]) if row[4] else None
                    if status == "completed":
                        return ContinuousSnapshotClaim(
                            acquired=False,
                            reason="already_completed",
                            attempt=attempts,
                            experiment_id=experiment_id,
                        )
                    if status == "terminal_failed" or attempts >= maximum_attempts:
                        return ContinuousSnapshotClaim(
                            acquired=False,
                            reason="attempts_exhausted",
                            attempt=attempts,
                            experiment_id=experiment_id,
                        )
                    if status == "running" and active_until > timestamp:
                        return ContinuousSnapshotClaim(
                            acquired=False,
                            reason="lease_active",
                            attempt=attempts,
                            experiment_id=experiment_id,
                        )
                    if status == "retry_wait" and next_retry > timestamp:
                        return ContinuousSnapshotClaim(
                            acquired=False,
                            reason="retry_backoff",
                            attempt=attempts,
                            experiment_id=experiment_id,
                        )
                    attempt = attempts + 1
                    connection.execute(
                        """
                        UPDATE continuous_aabb_snapshots SET
                            source_run_id = ?, source_record_path = ?, status = 'running',
                            attempt_count = ?, lease_token = ?, lease_until_ms = ?,
                            next_retry_at_ms = NULL, updated_at_ms = ?
                        WHERE pipeline_id = ? AND cohort_id = ?
                          AND market_snapshot_hash = ?
                        """,
                        (
                            source_run_id,
                            source_record_path,
                            attempt,
                            lease_token,
                            lease_until_ms,
                            timestamp,
                            pipeline_id,
                            cohort_id,
                            market_snapshot_hash,
                        ),
                    )
                else:
                    attempt = 1
                    connection.execute(
                        """
                        INSERT INTO continuous_aabb_snapshots (
                            pipeline_id, cohort_id, market_snapshot_hash,
                            source_run_id, source_record_path, status, attempt_count,
                            lease_token, lease_until_ms, next_retry_at_ms,
                            experiment_id, last_error, created_at_ms, updated_at_ms
                        ) VALUES (?, ?, ?, ?, ?, 'running', 1, ?, ?, NULL, NULL, NULL, ?, ?)
                        """,
                        (
                            pipeline_id,
                            cohort_id,
                            market_snapshot_hash,
                            source_run_id,
                            source_record_path,
                            lease_token,
                            lease_until_ms,
                            timestamp,
                            timestamp,
                        ),
                    )
            return ContinuousSnapshotClaim(
                acquired=True,
                reason="acquired",
                attempt=attempt,
                lease_token=lease_token,
            )
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("Continuous experiment claim failed: %s", exc)
            return ContinuousSnapshotClaim(acquired=False, reason="store_error")

    def finish_continuous_snapshot(
        self,
        *,
        pipeline_id: str,
        cohort_id: str,
        market_snapshot_hash: str,
        lease_token: str,
        completed: bool,
        retryable: bool,
        experiment_id: str | None,
        error: str | None,
        max_attempts: int,
        retry_backoff_seconds: float,
        now_ms: int | None = None,
    ) -> ExperimentWriteResult:
        """Finalize the current lease without allowing a stale worker to overwrite it."""
        timestamp = self._now_ms() if now_ms is None else int(now_ms)
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                row = connection.execute(
                    """
                    SELECT attempt_count FROM continuous_aabb_snapshots
                    WHERE pipeline_id = ? AND cohort_id = ? AND market_snapshot_hash = ?
                      AND status = 'running' AND lease_token = ?
                    """,
                    (pipeline_id, cohort_id, market_snapshot_hash, lease_token),
                ).fetchone()
                if row is None:
                    return ExperimentWriteResult(ok=False, error="stale_or_unknown_lease")
                attempts = int(row[0])
                can_retry = bool(retryable) and attempts < max(1, int(max_attempts))
                status = "completed" if completed else (
                    "retry_wait" if can_retry else "terminal_failed"
                )
                next_retry_at_ms = (
                    timestamp + max(0, int(float(retry_backoff_seconds) * 1000))
                    if status == "retry_wait"
                    else None
                )
                cursor = connection.execute(
                    """
                    UPDATE continuous_aabb_snapshots SET
                        status = ?, lease_token = NULL, lease_until_ms = NULL,
                        next_retry_at_ms = ?, experiment_id = ?, last_error = ?,
                        updated_at_ms = ?
                    WHERE pipeline_id = ? AND cohort_id = ? AND market_snapshot_hash = ?
                      AND status = 'running' AND lease_token = ?
                    """,
                    (
                        status,
                        next_retry_at_ms,
                        experiment_id,
                        error,
                        timestamp,
                        pipeline_id,
                        cohort_id,
                        market_snapshot_hash,
                        lease_token,
                    ),
                )
                if cursor.rowcount != 1:
                    return ExperimentWriteResult(ok=False, error="stale_or_unknown_lease")
            return ExperimentWriteResult(ok=True)
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("Continuous experiment finish failed: %s", exc)
            return ExperimentWriteResult(ok=False, error=str(exc))

    def frequency_report(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        cohort_id: str | None = None,
        min_unique_snapshots: int = 300,
        max_relative_trade_rate_decline: float = 0.15,
    ) -> dict[str, Any]:
        rows = self._joined_arm_rows(
            symbol=symbol,
            timeframe=timeframe,
            cohort_id=cohort_id,
            protocol_versions=_PA_VPA_PROTOCOL_VERSIONS,
        )
        if isinstance(rows, dict):
            return {**rows, "ready_for_soft": False}
        itt = self._frequency_summary(rows, per_protocol=False)
        per_protocol = self._frequency_summary(rows, per_protocol=True)
        protocol_versions = sorted(
            {str(row["protocol_version"] or "") for row in rows}
        )
        unique_snapshots = len({str(row["market_snapshot_hash"]) for row in rows})
        cohort_ids = sorted({str(row["cohort_id"] or "") for row in rows})
        reviewable_snapshots = len(
            {
                str(row["market_snapshot_hash"])
                for row in self._protocol_rows(rows, per_protocol=True)
            }
        )
        baseline = itt["A"]["executable_rate"]
        vpa = itt["B"]["executable_rate"]
        decline = (
            max(0.0, (baseline - vpa) / baseline)
            if baseline is not None and baseline > 0 and vpa is not None
            else None
        )
        reasons = [
            "Offline A/A/B/B frequency is not a profitability estimate.",
            "Soft promotion remains a separate out-of-sample decision.",
        ]
        if reviewable_snapshots < max(1, int(min_unique_snapshots)):
            reasons.append("Insufficient unique market snapshots for activation review.")
        if len(cohort_ids) > 1:
            reasons.append(
                "Multiple analysis cohorts are present; aggregate rates are descriptive only. "
                "Run quality and promotion review within one cohort."
            )
        failed_arms = sum(not bool(row["persisted"]) for row in rows)
        if failed_arms:
            reasons.append(
                "Assigned but unpersisted arms remain in ITT as non-executable; "
                "inspect execution reliability before interpreting treatment effects."
            )
        if decline is not None and decline > max(0.0, max_relative_trade_rate_decline):
            reasons.append("Assigned-B trade frequency declines beyond the review tolerance.")
        return {
            "status": "offline_aabb",
            "protocol_version": (
                protocol_versions[0] if len(protocol_versions) == 1 else "mixed"
            ),
            "protocol_versions": protocol_versions,
            "unique_snapshots": unique_snapshots,
            "reviewable_unique_snapshots": reviewable_snapshots,
            "cohort_count": len(cohort_ids),
            "cohort_ids": cohort_ids,
            "assigned_arms": len(rows),
            "unpersisted_arms": failed_arms,
            "intention_to_treat": itt,
            "per_protocol": per_protocol,
            "relative_trade_rate_decline": round(decline, 4) if decline is not None else None,
            "max_relative_trade_rate_decline": max_relative_trade_rate_decline,
            "ready_for_soft": False,
            "reasons": reasons,
        }

    def counterfactual_report(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        cohort_id: str | None = None,
        min_decisive_snapshots: int = 30,
        cost_per_trade_r: float = 0.0,
    ) -> dict[str, Any]:
        rows = self._joined_outcome_rows(
            symbol=symbol,
            timeframe=timeframe,
            cohort_id=cohort_id,
            protocol_versions=_PA_VPA_PROTOCOL_VERSIONS,
        )
        if isinstance(rows, dict):
            return {**rows, "ready_for_soft": False}
        itt = self._outcome_summary(rows, per_protocol=False, cost_per_trade_r=cost_per_trade_r)
        per_protocol = self._outcome_summary(
            rows,
            per_protocol=True,
            cost_per_trade_r=cost_per_trade_r,
        )
        cohort_ids = sorted({str(row["cohort_id"] or "") for row in rows})
        itt_comparison = self._matched_outcome_comparison(
            rows,
            per_protocol=False,
            cost_per_trade_r=cost_per_trade_r,
        )
        per_protocol_comparison = self._matched_outcome_comparison(
            rows,
            per_protocol=True,
            cost_per_trade_r=cost_per_trade_r,
        )
        if len(cohort_ids) == 1:
            matched_comparison = itt_comparison
            matched = int(itt_comparison["matched_episodes"])
            legacy_matched = int(itt_comparison["matched_decisive_snapshots"])
        else:
            matched_comparison = {
                **itt_comparison,
                "status": "mixed_cohorts_not_comparable",
                "mean_return_r_delta": None,
                "raw_mean_return_r_delta": None,
                "win_rate_delta": None,
                "directional_sign_test_p": None,
            }
            per_protocol_comparison = {
                **per_protocol_comparison,
                "status": "mixed_cohorts_not_comparable",
                "mean_return_r_delta": None,
                "raw_mean_return_r_delta": None,
                "win_rate_delta": None,
                "directional_sign_test_p": None,
            }
            matched = int(itt_comparison["matched_episodes"])
            legacy_matched = int(itt_comparison["unique_matched_market_snapshots"])
        missing_runs = int(itt_comparison["missing_runs"])
        return {
            "status": "counterfactual_only",
            "protocol_version": "pa_vpa_aabb_outcomes_v2",
            "statistically_reviewable": (
                len(cohort_ids) == 1
                and missing_runs == 0
                and int(itt_comparison["integrity_conflict_runs"]) == 0
                and matched >= max(1, int(min_decisive_snapshots))
            ),
            "matched_decisive_snapshots": legacy_matched,
            "matched_evaluable_snapshots": int(
                itt_comparison["matched_evaluable_snapshots"]
            ),
            "matched_episodes": matched,
            "minimum_matched_snapshots": max(1, int(min_decisive_snapshots)),
            "minimum_matched_episodes": max(1, int(min_decisive_snapshots)),
            "cost_per_trade_r": max(0.0, float(cost_per_trade_r)),
            "assigned_arms": len(rows),
            "unpersisted_arms": sum(not bool(row["persisted"]) for row in rows),
            "missing_outcome_arms": missing_runs,
            "cohort_count": len(cohort_ids),
            "cohort_ids": cohort_ids,
            "intention_to_treat": itt,
            "per_protocol": per_protocol,
            "matched_comparison": matched_comparison,
            "per_protocol_matched_comparison": per_protocol_comparison,
            "ready_for_soft": False,
            "limitations": [
                "Closed OHLC evaluates plans, not broker fills or live PnL.",
                "No-order and untriggered-entry arms contribute 0R under intention-to-treat.",
                "Pending and ambiguous paths are censored; missing assigned arms remain in coverage.",
                "Replicates are averaged within snapshots and overlapping forward windows are "
                "grouped into episodes before inference.",
                "Fees and slippage are represented only by the explicit cost_per_trade_r input.",
                "Trade win rate remains conditional on completed trades; ITT return includes "
                "the frequency effect through 0R no-action outcomes.",
            ],
        }

    def _joined_arm_rows(
        self,
        *,
        symbol: str | None,
        timeframe: str | None,
        cohort_id: str | None,
        protocol_versions: tuple[str, ...] | None = None,
    ) -> list[sqlite3.Row] | dict[str, Any]:
        clauses: list[str] = []
        values: list[Any] = []
        if symbol:
            clauses.append("e.symbol = ?")
            values.append(symbol)
        if timeframe:
            clauses.append("e.timeframe = ?")
            values.append(timeframe)
        if cohort_id:
            clauses.append("e.cohort_id = ?")
            values.append(cohort_id)
        if protocol_versions:
            placeholders = ",".join("?" for _item in protocol_versions)
            clauses.append(f"e.protocol_version IN ({placeholders})")
            values.extend(protocol_versions)
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.row_factory = sqlite3.Row
                has_analysis_runs = self._table_exists(connection, "analysis_runs")
                order_select = (
                    "r.order_type AS indexed_order_type"
                    if has_analysis_runs
                    else "NULL AS indexed_order_type"
                )
                run_join = (
                    " LEFT JOIN analysis_runs r ON r.run_id = a.run_id" if has_analysis_runs else ""
                )
                query = (
                    "SELECT e.frozen_input_hash, e.market_snapshot_hash, e.cohort_id, "
                    "e.experiment_id, e.protocol_version, "
                    "e.status AS experiment_status, e.protocol_valid, "
                    f"a.*, {order_select} FROM experiment_arms a "
                    "JOIN analysis_experiments e ON e.experiment_id = a.experiment_id"
                    f"{run_join}" + (" WHERE " + " AND ".join(clauses) if clauses else "")
                )
                return connection.execute(
                    query,
                    tuple(values),
                ).fetchall()
        except (OSError, sqlite3.Error, ValueError) as exc:
            return {"status": "unavailable", "reason": str(exc)}

    def _joined_outcome_rows(
        self,
        *,
        symbol: str | None,
        timeframe: str | None,
        cohort_id: str | None,
        protocol_versions: tuple[str, ...] | None = None,
    ) -> list[sqlite3.Row] | dict[str, Any]:
        clauses: list[str] = []
        values: list[Any] = []
        if symbol:
            clauses.append("e.symbol = ?")
            values.append(symbol)
        if timeframe:
            clauses.append("e.timeframe = ?")
            values.append(timeframe)
        if cohort_id:
            clauses.append("e.cohort_id = ?")
            values.append(cohort_id)
        if protocol_versions:
            placeholders = ",".join("?" for _item in protocol_versions)
            clauses.append(f"e.protocol_version IN ({placeholders})")
            values.extend(protocol_versions)
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.row_factory = sqlite3.Row
                has_outcomes = self._table_exists(connection, "counterfactual_outcomes")
                outcome_columns = (
                    self._table_columns(connection, "counterfactual_outcomes")
                    if has_outcomes
                    else set()
                )
                has_analysis_runs = self._table_exists(connection, "analysis_runs")
                run_columns = (
                    self._table_columns(connection, "analysis_runs")
                    if has_analysis_runs
                    else set()
                )

                def selected_column(
                    table_alias: str,
                    columns: set[str],
                    column: str,
                    alias: str | None = None,
                ) -> str:
                    output_name = alias or column
                    if column in columns:
                        return f"{table_alias}.{column} AS {output_name}"
                    return f"NULL AS {output_name}"

                outcome_select = ", ".join(
                    selected_column("c", outcome_columns, column, alias)
                    for column, alias in (
                        ("status", "cf_status"),
                        ("entry_status", None),
                        ("outcome", None),
                        ("realized_return_r", None),
                        ("horizon_bars", None),
                        ("entry_bar_ts", None),
                        ("exit_bar_ts", None),
                        ("evaluated_through_ts", None),
                    )
                )
                run_select = ", ".join(
                    (
                        selected_column(
                            "r",
                            run_columns,
                            "order_type",
                            "indexed_order_type",
                        ),
                        selected_column(
                            "r",
                            run_columns,
                            "snapshot_latest_ts_open",
                        ),
                        selected_column("r", run_columns, "bar_count"),
                    )
                )
                outcome_join = (
                    " LEFT JOIN counterfactual_outcomes c ON c.run_id = a.run_id "
                    "AND c.arm = 'official'"
                    if has_outcomes
                    else ""
                )
                run_join = (
                    " LEFT JOIN analysis_runs r ON r.run_id = a.run_id"
                    if has_analysis_runs
                    else ""
                )
                query = (
                    "SELECT e.frozen_input_hash, e.market_snapshot_hash, e.cohort_id, "
                    "e.protocol_version, "
                    "e.symbol AS experiment_symbol, e.timeframe AS experiment_timeframe, "
                    "e.created_at_ms AS experiment_created_at_ms, "
                    "e.experiment_id, e.status AS experiment_status, e.protocol_valid, "
                    f"a.*, {run_select}, {outcome_select} FROM experiment_arms a "
                    "JOIN analysis_experiments e ON e.experiment_id = a.experiment_id"
                    f"{run_join}{outcome_join}"
                    + (" WHERE " + " AND ".join(clauses) if clauses else "")
                )
                return connection.execute(
                    query,
                    tuple(values),
                ).fetchall()
        except (OSError, sqlite3.Error, ValueError) as exc:
            return {"status": "unavailable", "reason": str(exc)}

    @staticmethod
    def _protocol_rows(rows: list[sqlite3.Row], *, per_protocol: bool) -> list[sqlite3.Row]:
        if not per_protocol:
            return rows
        return [
            row
            for row in rows
            if str(row["experiment_status"] or "") == "completed"
            and bool(row["protocol_valid"])
            and bool(row["persisted"])
            and str(row["run_status"] or "") in {"completed", "completed_with_warnings"}
            and (str(row["group_name"]) == "A" or bool(row["treatment_received"]))
        ]

    @classmethod
    def _frequency_summary(
        cls,
        rows: list[sqlite3.Row],
        *,
        per_protocol: bool,
    ) -> dict[str, Any]:
        selected = cls._protocol_rows(rows, per_protocol=per_protocol)
        result: dict[str, Any] = {}
        for group in ("A", "B"):
            group_rows = [row for row in selected if str(row["group_name"]) == group]
            by_snapshot: dict[str, list[sqlite3.Row]] = {}
            for row in group_rows:
                by_snapshot.setdefault(str(row["market_snapshot_hash"]), []).append(row)
            snapshot_rates = [
                sum(_is_executable_order(row["indexed_order_type"]) for row in values) / len(values)
                for values in by_snapshot.values()
            ]
            result[group] = {
                "runs": len(group_rows),
                "unique_snapshots": len(by_snapshot),
                "unpersisted_runs": sum(not bool(row["persisted"]) for row in group_rows),
                "executable_rate": (
                    round(sum(snapshot_rates) / len(snapshot_rates), 4) if snapshot_rates else None
                ),
            }
        return result

    @classmethod
    def _arm_outcome(
        cls,
        row: sqlite3.Row,
        *,
        cost_per_trade_r: float,
    ) -> _ArmOutcome:
        if not bool(_row_value(row, "persisted")) or not str(
            _row_value(row, "run_id") or ""
        ):
            return _ArmOutcome("missing", None, None, False, None)

        order_type = str(_row_value(row, "indexed_order_type") or "").strip().lower()
        status = str(_row_value(row, "cf_status") or "").strip().lower()
        entry_status = str(_row_value(row, "entry_status") or "").strip().lower()
        outcome = str(_row_value(row, "outcome") or "").strip().lower()
        recorded_trade = outcome in _TRADE_OUTCOMES
        if order_type in _NO_ORDER_TYPES:
            if recorded_trade:
                return _ArmOutcome(
                    "missing",
                    None,
                    None,
                    False,
                    None,
                    integrity_conflict=True,
                )
            return _ArmOutcome(
                "zero_return",
                0.0,
                0.0,
                False,
                None,
            )
        if status in _ZERO_RETURN_STATUSES or entry_status in _ZERO_RETURN_ENTRY_STATUSES:
            if recorded_trade:
                return _ArmOutcome(
                    "missing",
                    None,
                    None,
                    False,
                    None,
                    integrity_conflict=True,
                )
            return _ArmOutcome(
                "zero_return",
                0.0,
                0.0,
                False,
                None,
            )
        if recorded_trade:
            realized = _finite_float(_row_value(row, "realized_return_r"))
            if realized is None:
                return _ArmOutcome("missing", None, None, False, None)
            tolerance = 1e-12
            return_conflict = (
                (outcome == "win" and realized <= tolerance)
                or (outcome == "loss" and realized >= -tolerance)
                or (outcome == "breakeven" and abs(realized) > tolerance)
            )
            if return_conflict:
                return _ArmOutcome(
                    "missing",
                    None,
                    None,
                    False,
                    None,
                    integrity_conflict=True,
                )
            cost = max(0.0, float(cost_per_trade_r))
            won = True if outcome == "win" else False if outcome == "loss" else None
            return _ArmOutcome(
                "trade",
                realized,
                realized - cost,
                True,
                won,
            )
        if status in _CENSORED_STATUSES or outcome in _CENSORED_OUTCOMES:
            return _ArmOutcome("censored", None, None, False, None)
        return _ArmOutcome("missing", None, None, False, None)

    @classmethod
    def _snapshot_outcomes(
        cls,
        rows: list[sqlite3.Row],
        *,
        cost_per_trade_r: float,
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for row in rows:
            key = (
                str(_row_value(row, "cohort_id") or ""),
                str(_row_value(row, "market_snapshot_hash") or ""),
            )
            grouped.setdefault(key, []).append(row)

        snapshots: list[dict[str, Any]] = []
        for (cohort_id, market_hash), snapshot_rows in grouped.items():
            start_ms, end_ms = cls._snapshot_interval(snapshot_rows)
            groups: dict[str, dict[str, Any]] = {}
            for group_name in ("A", "B"):
                arm_rows = [
                    row
                    for row in snapshot_rows
                    if str(_row_value(row, "group_name") or "") == group_name
                ]
                outcomes = [
                    cls._arm_outcome(row, cost_per_trade_r=cost_per_trade_r)
                    for row in arm_rows
                ]
                evaluable = [
                    item for item in outcomes if item.state in {"trade", "zero_return"}
                ]
                complete = bool(outcomes) and len(evaluable) == len(outcomes)
                trades = [item for item in outcomes if item.traded]
                groups[group_name] = {
                    "assigned_arms": len(outcomes),
                    "complete": complete,
                    "raw_return_r": (
                        sum(float(item.raw_return_r) for item in evaluable) / len(evaluable)
                        if complete
                        else None
                    ),
                    "adjusted_return_r": (
                        sum(float(item.adjusted_return_r) for item in evaluable)
                        / len(evaluable)
                        if complete
                        else None
                    ),
                    "trade_count": len(trades),
                    "trade_win_rate": (
                        sum(item.won is True for item in trades) / len(trades)
                        if trades
                        else None
                    ),
                    "zero_return_arms": sum(
                        item.state == "zero_return" for item in outcomes
                    ),
                    "censored_arms": sum(item.state == "censored" for item in outcomes),
                    "missing_arms": sum(item.state == "missing" for item in outcomes),
                    "integrity_conflicts": sum(
                        item.integrity_conflict for item in outcomes
                    ),
                }
            first = snapshot_rows[0]
            snapshots.append(
                {
                    "cohort_id": cohort_id,
                    "market_snapshot_hash": market_hash,
                    "symbol": str(_row_value(first, "experiment_symbol") or ""),
                    "timeframe": str(_row_value(first, "experiment_timeframe") or ""),
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "groups": groups,
                }
            )
        return snapshots

    @staticmethod
    def _snapshot_interval(rows: list[sqlite3.Row]) -> tuple[int | None, int | None]:
        starts = [
            int(value)
            for row in rows
            if (value := _row_value(row, "snapshot_latest_ts_open")) is not None
        ]
        if not starts:
            return None, None
        start = min(starts)
        ends = [
            int(value)
            for row in rows
            for key in ("evaluated_through_ts", "exit_bar_ts")
            if (value := _row_value(row, key)) is not None and int(value) >= start
        ]
        if ends:
            return start, max(ends)
        timeframe = str(_row_value(rows[0], "experiment_timeframe") or "")
        duration_s = timeframe_to_seconds(timeframe) or 1
        horizons = [
            max(1, int(value))
            for row in rows
            if (value := _row_value(row, "horizon_bars")) is not None
        ]
        horizon = max(horizons) if horizons else 1
        return start, start + duration_s * 1000 * horizon

    @staticmethod
    def _episode_groups(
        snapshots: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        unknown: list[dict[str, Any]] = []
        for snapshot in snapshots:
            if snapshot.get("start_ms") is None or snapshot.get("end_ms") is None:
                unknown.append(snapshot)
                continue
            key = (
                str(snapshot.get("cohort_id") or ""),
                str(snapshot.get("symbol") or ""),
                str(snapshot.get("timeframe") or ""),
            )
            buckets.setdefault(key, []).append(snapshot)

        episodes: list[list[dict[str, Any]]] = [[item] for item in unknown]
        for bucket in buckets.values():
            ordered = sorted(
                bucket,
                key=lambda item: (
                    int(item["start_ms"]),
                    str(item.get("market_snapshot_hash") or ""),
                ),
            )
            current: list[dict[str, Any]] = []
            current_end: int | None = None
            for snapshot in ordered:
                start = int(snapshot["start_ms"])
                end = int(snapshot["end_ms"])
                if current and current_end is not None and start < current_end:
                    current.append(snapshot)
                    current_end = max(current_end, end)
                    continue
                if current:
                    episodes.append(current)
                current = [snapshot]
                current_end = end
            if current:
                episodes.append(current)
        return episodes

    @classmethod
    def _outcome_summary(
        cls,
        rows: list[sqlite3.Row],
        *,
        per_protocol: bool,
        cost_per_trade_r: float,
    ) -> dict[str, Any]:
        selected = cls._protocol_rows(rows, per_protocol=per_protocol)
        snapshots = cls._snapshot_outcomes(
            selected,
            cost_per_trade_r=cost_per_trade_r,
        )
        episodes = cls._episode_groups(snapshots)
        result: dict[str, Any] = {}
        for group in ("A", "B"):
            group_rows = [row for row in selected if str(row["group_name"]) == group]
            arm_outcomes = [
                cls._arm_outcome(row, cost_per_trade_r=cost_per_trade_r)
                for row in group_rows
            ]
            group_snapshots = [
                snapshot["groups"][group]
                for snapshot in snapshots
                if snapshot["groups"][group]["assigned_arms"]
            ]
            complete_snapshots = [item for item in group_snapshots if item["complete"]]
            episode_returns: list[float] = []
            episode_adjusted_returns: list[float] = []
            episode_win_rates: list[float] = []
            for episode in episodes:
                values = [
                    snapshot["groups"][group]
                    for snapshot in episode
                    if snapshot["groups"][group]["complete"]
                ]
                if values:
                    episode_returns.append(
                        sum(float(item["raw_return_r"]) for item in values) / len(values)
                    )
                    episode_adjusted_returns.append(
                        sum(float(item["adjusted_return_r"]) for item in values)
                        / len(values)
                    )
                win_rates = [
                    float(item["trade_win_rate"])
                    for item in values
                    if item["trade_win_rate"] is not None
                ]
                if win_rates:
                    episode_win_rates.append(sum(win_rates) / len(win_rates))
            trades = [item for item in arm_outcomes if item.traded]
            evaluable = [
                item for item in arm_outcomes if item.state in {"trade", "zero_return"}
            ]
            result[group] = {
                "runs": len(group_rows),
                "evaluable_runs": len(evaluable),
                "zero_return_runs": sum(
                    item.state == "zero_return" for item in arm_outcomes
                ),
                "decisive_runs": len(trades),
                "trade_outcome_runs": len(trades),
                "censored_runs": sum(item.state == "censored" for item in arm_outcomes),
                "missing_runs": sum(item.state == "missing" for item in arm_outcomes),
                "unresolved_runs": sum(
                    item.state in {"censored", "missing"} for item in arm_outcomes
                ),
                "outcome_coverage": (
                    round(len(evaluable) / len(group_rows), 4) if group_rows else None
                ),
                "evaluable_snapshots": len(complete_snapshots),
                "decisive_snapshots": sum(
                    item["complete"] and item["trade_count"] > 0
                    for item in group_snapshots
                ),
                "evaluable_episodes": len(episode_returns),
                "wins": sum(item.won is True for item in trades),
                "losses": sum(item.won is False for item in trades),
                "breakeven": sum(item.won is None for item in trades),
                "win_rate": (
                    round(sum(episode_win_rates) / len(episode_win_rates), 4)
                    if episode_win_rates
                    else None
                ),
                "mean_return_r": (
                    round(sum(episode_returns) / len(episode_returns), 4)
                    if episode_returns
                    else None
                ),
                "cost_adjusted_mean_return_r": (
                    round(
                        sum(episode_adjusted_returns) / len(episode_adjusted_returns),
                        4,
                    )
                    if episode_adjusted_returns
                    else None
                ),
                "integrity_conflict_runs": sum(
                    item.integrity_conflict for item in arm_outcomes
                ),
            }
        return result

    @classmethod
    def _matched_outcome_comparison(
        cls,
        rows: list[sqlite3.Row],
        *,
        per_protocol: bool,
        cost_per_trade_r: float,
    ) -> dict[str, Any]:
        selected = cls._protocol_rows(rows, per_protocol=per_protocol)
        snapshots = cls._snapshot_outcomes(
            selected,
            cost_per_trade_r=cost_per_trade_r,
        )
        episodes = cls._episode_groups(snapshots)
        complete_pairs = [
            snapshot
            for snapshot in snapshots
            if snapshot["groups"]["A"]["complete"]
            and snapshot["groups"]["B"]["complete"]
        ]
        paired: list[dict[str, float | None]] = []
        for episode in episodes:
            pairs = [
                snapshot
                for snapshot in episode
                if snapshot["groups"]["A"]["complete"]
                and snapshot["groups"]["B"]["complete"]
            ]
            if not pairs:
                continue
            a_return = sum(
                float(item["groups"]["A"]["adjusted_return_r"])
                for item in pairs
            ) / len(pairs)
            b_return = sum(
                float(item["groups"]["B"]["adjusted_return_r"])
                for item in pairs
            ) / len(pairs)
            a_raw_return = sum(
                float(item["groups"]["A"]["raw_return_r"]) for item in pairs
            ) / len(pairs)
            b_raw_return = sum(
                float(item["groups"]["B"]["raw_return_r"]) for item in pairs
            ) / len(pairs)
            win_deltas = [
                float(item["groups"]["B"]["trade_win_rate"])
                - float(item["groups"]["A"]["trade_win_rate"])
                for item in pairs
                if item["groups"]["A"]["trade_win_rate"] is not None
                and item["groups"]["B"]["trade_win_rate"] is not None
            ]
            paired.append(
                {
                    "return_delta_r": b_return - a_return,
                    "raw_return_delta_r": b_raw_return - a_raw_return,
                    "win_rate_delta": (
                        sum(win_deltas) / len(win_deltas) if win_deltas else None
                    ),
                }
            )

        tolerance = 1e-12
        improved = sum(float(item["return_delta_r"]) > tolerance for item in paired)
        worsened = sum(float(item["return_delta_r"]) < -tolerance for item in paired)
        unchanged = len(paired) - improved - worsened
        arm_outcomes = [
            cls._arm_outcome(row, cost_per_trade_r=cost_per_trade_r)
            for row in selected
        ]
        win_rate_deltas = [
            float(item["win_rate_delta"])
            for item in paired
            if item["win_rate_delta"] is not None
        ]
        return {
            "status": (
                "paired_closed_ohlc_episode_per_protocol"
                if per_protocol
                else "paired_closed_ohlc_episode_itt"
            ),
            "assigned_snapshots": len(snapshots),
            "matched_evaluable_snapshots": len(complete_pairs),
            "matched_decisive_snapshots": sum(
                item["groups"]["A"]["trade_count"] > 0
                and item["groups"]["B"]["trade_count"] > 0
                for item in complete_pairs
            ),
            "unique_matched_market_snapshots": len(
                {str(item["market_snapshot_hash"]) for item in complete_pairs}
            ),
            "matched_episodes": len(paired),
            "assigned_episodes": len(episodes),
            "paired_snapshot_coverage": (
                round(len(complete_pairs) / len(snapshots), 4) if snapshots else None
            ),
            "incomplete_snapshots": len(snapshots) - len(complete_pairs),
            "missing_runs": sum(item.state == "missing" for item in arm_outcomes),
            "censored_runs": sum(item.state == "censored" for item in arm_outcomes),
            "zero_return_runs": sum(
                item.state == "zero_return" for item in arm_outcomes
            ),
            "integrity_conflict_runs": sum(
                item.integrity_conflict for item in arm_outcomes
            ),
            "mean_return_r_delta": (
                round(
                    sum(float(item["return_delta_r"]) for item in paired) / len(paired),
                    4,
                )
                if paired
                else None
            ),
            "raw_mean_return_r_delta": (
                round(
                    sum(float(item["raw_return_delta_r"]) for item in paired)
                    / len(paired),
                    4,
                )
                if paired
                else None
            ),
            "win_rate_delta": (
                round(sum(win_rate_deltas) / len(win_rate_deltas), 4)
                if win_rate_deltas
                else None
            ),
            "matched_trade_win_rate_episodes": len(win_rate_deltas),
            "improved_snapshots": improved,
            "worsened_snapshots": worsened,
            "unchanged_snapshots": unchanged,
            "improved_episodes": improved,
            "worsened_episodes": worsened,
            "unchanged_episodes": unchanged,
            "directional_sign_test_p": _two_sided_sign_test(improved, worsened),
        }

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._db_path, timeout=5.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
        return {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS analysis_experiments (
                experiment_id TEXT PRIMARY KEY,
                protocol_version TEXT NOT NULL,
                status TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                frozen_input_hash TEXT NOT NULL,
                market_snapshot_hash TEXT NOT NULL,
                cohort_id TEXT NOT NULL,
                snapshot_hash TEXT NOT NULL,
                htf_hash TEXT NOT NULL,
                catalog_id TEXT NOT NULL,
                provider_fingerprint TEXT NOT NULL,
                settings_fingerprint TEXT NOT NULL,
                analysis_fingerprint TEXT NOT NULL,
                order_scheme TEXT NOT NULL,
                arm_sequence_json TEXT NOT NULL,
                source_run_id TEXT,
                source_record_path TEXT,
                protocol_valid INTEGER,
                protocol_errors_json TEXT NOT NULL,
                external_comparison_json TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                completed_at_ms INTEGER
            )
            """)
        existing_experiment_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(analysis_experiments)").fetchall()
        }
        for column in ("analysis_fingerprint", "market_snapshot_hash", "cohort_id"):
            if column not in existing_experiment_columns:
                connection.execute(f"ALTER TABLE analysis_experiments ADD COLUMN {column} TEXT")
        connection.execute(
            "UPDATE analysis_experiments SET market_snapshot_hash = frozen_input_hash "
            "WHERE market_snapshot_hash IS NULL OR market_snapshot_hash = ''"
        )
        legacy_cohort_rows = connection.execute(
            "SELECT experiment_id, protocol_version, provider_fingerprint, "
            "settings_fingerprint, analysis_fingerprint FROM analysis_experiments "
            "WHERE cohort_id IS NULL OR cohort_id = ''"
        ).fetchall()
        for row in legacy_cohort_rows:
            payload = json.dumps(
                {
                    "legacy_migration": True,
                    "protocol_version": row[1],
                    "provider_fingerprint": row[2],
                    "settings_fingerprint": row[3],
                    "analysis_fingerprint": row[4],
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            legacy_cohort_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            connection.execute(
                "UPDATE analysis_experiments SET cohort_id = ? WHERE experiment_id = ?",
                (legacy_cohort_id, row[0]),
            )
        connection.execute("""
            CREATE TABLE IF NOT EXISTS experiment_arms (
                experiment_id TEXT NOT NULL,
                arm_id TEXT NOT NULL,
                group_name TEXT NOT NULL,
                replicate INTEGER NOT NULL,
                sequence_position INTEGER NOT NULL,
                assigned_mode TEXT NOT NULL,
                received_path TEXT,
                run_id TEXT,
                record_path TEXT,
                run_status TEXT,
                first_pass_clean INTEGER NOT NULL,
                treatment_received INTEGER NOT NULL,
                stage1_request_hash TEXT,
                client_instance_token TEXT,
                persisted INTEGER NOT NULL,
                error TEXT,
                recorded_at_ms INTEGER NOT NULL,
                PRIMARY KEY (experiment_id, arm_id),
                FOREIGN KEY (experiment_id) REFERENCES analysis_experiments(experiment_id)
                    ON DELETE CASCADE
            )
            """)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_experiments_input "
            "ON analysis_experiments(frozen_input_hash, created_at_ms)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_experiments_market_cohort "
            "ON analysis_experiments(cohort_id, market_snapshot_hash, created_at_ms)"
        )
        connection.execute("""
            CREATE TABLE IF NOT EXISTS continuous_aabb_snapshots (
                pipeline_id TEXT NOT NULL,
                cohort_id TEXT NOT NULL,
                market_snapshot_hash TEXT NOT NULL,
                source_run_id TEXT NOT NULL,
                source_record_path TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL,
                lease_token TEXT,
                lease_until_ms INTEGER,
                next_retry_at_ms INTEGER,
                experiment_id TEXT,
                last_error TEXT,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                PRIMARY KEY (pipeline_id, cohort_id, market_snapshot_hash)
            )
            """)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_continuous_aabb_status "
            "ON continuous_aabb_snapshots(pipeline_id, status, next_retry_at_ms)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_experiment_arms_run " "ON experiment_arms(run_id)"
        )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _now_ms() -> int:
        return int(datetime.now(UTC).timestamp() * 1000)


def _two_sided_sign_test(improved: int, worsened: int) -> float | None:
    """Exact two-sided sign test over non-tied paired market snapshots."""
    positive = max(0, int(improved))
    negative = max(0, int(worsened))
    total = positive + negative
    if total == 0:
        return None
    tail = min(positive, negative)
    probability = 2.0 * sum(math.comb(total, index) for index in range(tail + 1)) / (2**total)
    return round(min(1.0, probability), 6)
