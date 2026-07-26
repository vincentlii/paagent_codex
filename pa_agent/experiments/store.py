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


def _is_executable_order(value: object) -> bool:
    return str(value or "").strip().lower() in {item.lower() for item in _EXECUTABLE_ORDER_TYPES}


@dataclass(frozen=True)
class ExperimentWriteResult:
    ok: bool
    error: str | None = None


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
        )
        if isinstance(rows, dict):
            return {**rows, "ready_for_soft": False}
        itt = self._frequency_summary(rows, per_protocol=False)
        per_protocol = self._frequency_summary(rows, per_protocol=True)
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
            "protocol_version": "pa_vpa_aabb_v1",
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
        decisive_by_input: dict[str, dict[str, set[str]]] = {}
        for row in self._protocol_rows(rows, per_protocol=True):
            if row["outcome"] not in {"win", "loss"}:
                continue
            market_hash = str(row["market_snapshot_hash"])
            row_cohort = str(row["cohort_id"] or "")
            decisive_by_input.setdefault(market_hash, {}).setdefault(row_cohort, set()).add(
                str(row["group_name"])
            )
        matched = sum(
            any(groups == {"A", "B"} for groups in by_cohort.values())
            for by_cohort in decisive_by_input.values()
        )
        if len(cohort_ids) == 1:
            matched_comparison = self._matched_outcome_comparison(
                rows,
                per_protocol=True,
                cost_per_trade_r=cost_per_trade_r,
            )
            matched = int(matched_comparison["matched_decisive_snapshots"])
        else:
            matched_comparison = {
                "status": "mixed_cohorts_not_comparable",
                "matched_decisive_snapshots": matched,
                "mean_return_r_delta": None,
                "win_rate_delta": None,
                "directional_sign_test_p": None,
            }
        return {
            "status": "counterfactual_only",
            "protocol_version": "pa_vpa_aabb_v1",
            "statistically_reviewable": (
                len(cohort_ids) == 1 and matched >= max(1, int(min_decisive_snapshots))
            ),
            "matched_decisive_snapshots": matched,
            "minimum_matched_snapshots": max(1, int(min_decisive_snapshots)),
            "cost_per_trade_r": max(0.0, float(cost_per_trade_r)),
            "assigned_arms": len(rows),
            "unpersisted_arms": sum(not bool(row["persisted"]) for row in rows),
            "cohort_count": len(cohort_ids),
            "cohort_ids": cohort_ids,
            "intention_to_treat": itt,
            "per_protocol": per_protocol,
            "matched_comparison": matched_comparison,
            "ready_for_soft": False,
            "limitations": [
                "Closed OHLC evaluates plans, not broker fills or live PnL.",
                "Ambiguous same-bar paths and unsupported market fills remain excluded.",
                "Repeated runs on one market snapshot are not independent samples.",
                "Fees and slippage are represented only by the explicit cost_per_trade_r input.",
                "Matched return deltas condition on both groups having decisive replay outcomes; "
                "trade-frequency effects remain a separate report.",
            ],
        }

    def _joined_arm_rows(
        self,
        *,
        symbol: str | None,
        timeframe: str | None,
        cohort_id: str | None,
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
                    "e.experiment_id, e.status AS experiment_status, e.protocol_valid, "
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
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.row_factory = sqlite3.Row
                has_outcomes = self._table_exists(connection, "counterfactual_outcomes")
                outcome_select = (
                    "c.status AS cf_status, c.entry_status, c.outcome, " "c.realized_return_r"
                    if has_outcomes
                    else "NULL AS cf_status, NULL AS entry_status, NULL AS outcome, "
                    "NULL AS realized_return_r"
                )
                outcome_join = (
                    " LEFT JOIN counterfactual_outcomes c ON c.run_id = a.run_id "
                    "AND c.arm = 'official'"
                    if has_outcomes
                    else ""
                )
                query = (
                    "SELECT e.frozen_input_hash, e.market_snapshot_hash, e.cohort_id, "
                    "e.experiment_id, e.status AS experiment_status, e.protocol_valid, "
                    f"a.*, {outcome_select} FROM experiment_arms a "
                    "JOIN analysis_experiments e ON e.experiment_id = a.experiment_id"
                    f"{outcome_join}" + (" WHERE " + " AND ".join(clauses) if clauses else "")
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
    def _outcome_summary(
        cls,
        rows: list[sqlite3.Row],
        *,
        per_protocol: bool,
        cost_per_trade_r: float,
    ) -> dict[str, Any]:
        selected = cls._protocol_rows(rows, per_protocol=per_protocol)
        result: dict[str, Any] = {}
        cost = max(0.0, float(cost_per_trade_r))
        for group in ("A", "B"):
            group_rows = [row for row in selected if str(row["group_name"]) == group]
            decisive = [row for row in group_rows if row["outcome"] in {"win", "loss"}]
            decisive_by_snapshot: dict[str, list[sqlite3.Row]] = {}
            for row in decisive:
                decisive_by_snapshot.setdefault(str(row["market_snapshot_hash"]), []).append(row)
            snapshot_returns = [
                sum(float(row["realized_return_r"]) for row in values) / len(values)
                for values in decisive_by_snapshot.values()
            ]
            snapshot_win_rates = [
                sum(row["outcome"] == "win" for row in values) / len(values)
                for values in decisive_by_snapshot.values()
            ]
            adjusted = [value - cost for value in snapshot_returns]
            result[group] = {
                "runs": len(group_rows),
                "decisive_runs": len(decisive),
                "decisive_snapshots": len(decisive_by_snapshot),
                "unresolved_runs": len(group_rows) - len(decisive),
                "wins": sum(row["outcome"] == "win" for row in decisive),
                "losses": sum(row["outcome"] == "loss" for row in decisive),
                "win_rate": (
                    round(sum(snapshot_win_rates) / len(snapshot_win_rates), 4)
                    if snapshot_win_rates
                    else None
                ),
                "mean_return_r": (
                    round(sum(snapshot_returns) / len(snapshot_returns), 4)
                    if snapshot_returns
                    else None
                ),
                "cost_adjusted_mean_return_r": (
                    round(sum(adjusted) / len(adjusted), 4) if adjusted else None
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
        grouped: dict[str, dict[str, list[sqlite3.Row]]] = {}
        for row in selected:
            if row["outcome"] not in {"win", "loss"}:
                continue
            grouped.setdefault(str(row["market_snapshot_hash"]), {}).setdefault(
                str(row["group_name"]), []
            ).append(row)

        cost = max(0.0, float(cost_per_trade_r))
        paired: list[dict[str, float]] = []
        for market_hash, groups in grouped.items():
            if not groups.get("A") or not groups.get("B"):
                continue
            a_return = (
                sum(float(row["realized_return_r"]) for row in groups["A"]) / len(groups["A"])
                - cost
            )
            b_return = (
                sum(float(row["realized_return_r"]) for row in groups["B"]) / len(groups["B"])
                - cost
            )
            a_win_rate = sum(row["outcome"] == "win" for row in groups["A"]) / len(groups["A"])
            b_win_rate = sum(row["outcome"] == "win" for row in groups["B"]) / len(groups["B"])
            paired.append(
                {
                    "market_snapshot_hash": market_hash,
                    "a_return_r": a_return,
                    "b_return_r": b_return,
                    "return_delta_r": b_return - a_return,
                    "win_rate_delta": b_win_rate - a_win_rate,
                }
            )

        tolerance = 1e-12
        improved = sum(item["return_delta_r"] > tolerance for item in paired)
        worsened = sum(item["return_delta_r"] < -tolerance for item in paired)
        unchanged = len(paired) - improved - worsened
        return {
            "status": "paired_closed_ohlc",
            "matched_decisive_snapshots": len(paired),
            "mean_return_r_delta": (
                round(sum(item["return_delta_r"] for item in paired) / len(paired), 4)
                if paired
                else None
            ),
            "win_rate_delta": (
                round(sum(item["win_rate_delta"] for item in paired) / len(paired), 4)
                if paired
                else None
            ),
            "improved_snapshots": improved,
            "worsened_snapshots": worsened,
            "unchanged_snapshots": unchanged,
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
