"""SQLite audit ledger for bounded multi-asset scheduling."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pa_agent.monitoring.scheduler import (
    AnalysisTask,
    JobCompletion,
    SubmissionResult,
)

logger = logging.getLogger(__name__)


class SchedulerAuditStore:
    """Persist every task assignment and terminal scheduler outcome once."""

    def __init__(self, db_path: Path, *, require_frozen_payload: bool = False) -> None:
        self._db_path = Path(db_path)
        self._require_frozen_payload = require_frozen_payload
        self._lock = threading.RLock()
        self._initialize()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def record_submission(
        self,
        task: AnalysisTask,
        result: SubmissionResult,
        *,
        resume_unstarted: bool = False,
    ) -> bool:
        now_ms = int(time.time() * 1000)
        values = self._task_fields(task)
        try:
            with self._lock, self._connect() as connection:
                if result.accepted and self._require_frozen_payload:
                    from pa_agent.monitoring.frozen_task_codec import encode_task

                    payload_json, payload_digest = encode_task(task)
                    existing = connection.execute(
                        "SELECT completion_status, analysis_success FROM monitoring_jobs "
                        "WHERE task_id = ?",
                        (task.task_id,),
                    ).fetchone()
                    if existing is not None and existing == ("completed", 1):
                        return False
                    if existing is not None:
                        started = connection.execute(
                            "SELECT started_at_ms, active FROM monitoring_frozen_tasks "
                            "WHERE task_id = ?", (task.task_id,),
                        ).fetchone()
                        if started is not None and started[1] and (
                            not resume_unstarted or started[0] is not None
                        ):
                            return False
                        if started is None and existing[0] is None:
                            return False
                    connection.execute(
                        "INSERT INTO monitoring_frozen_tasks "
                        "(task_id, payload_json, payload_digest, active) "
                        "VALUES (?, ?, ?, 1) "
                        "ON CONFLICT(task_id) DO UPDATE SET "
                        "payload_json = excluded.payload_json, "
                        "payload_digest = excluded.payload_digest, "
                        "active = 1, "
                        "started_at_ms = NULL",
                        (task.task_id, payload_json, payload_digest),
                    )
                connection.execute(
                    """
                    INSERT INTO monitoring_jobs (
                        task_id, asset_key, source, venue, symbol, timeframe,
                        snapshot_hash, cohort_id, task_created_at_ms,
                        first_submission_status, submission_status,
                        submission_count, submitted_at_ms, updated_at_ms
                    ) VALUES (
                        :task_id, :asset_key, :source, :venue, :symbol, :timeframe,
                        :snapshot_hash, :cohort_id, :task_created_at_ms,
                        :submission_status, :submission_status,
                        1, :submitted_at_ms, :updated_at_ms
                    )
                    ON CONFLICT(task_id) DO UPDATE SET
                        submission_status = excluded.submission_status,
                        submission_count = monitoring_jobs.submission_count + 1,
                        submitted_at_ms = excluded.submitted_at_ms,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    {
                        **values,
                        "submission_status": result.status,
                        "submitted_at_ms": now_ms,
                        "updated_at_ms": now_ms,
                    },
                )
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("Scheduler submission audit failed: %s", exc)
            return False

    def record_completion(self, completion: JobCompletion) -> bool:
        now_ms = int(time.time() * 1000)
        task = completion.task
        values = self._task_fields(task)
        run_id = self._run_id(completion.result)
        analysis_status = self._analysis_status(completion)
        analysis_success = self._analysis_success(completion)
        try:
            with self._lock, self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO monitoring_jobs (
                        task_id, asset_key, source, venue, symbol, timeframe,
                        snapshot_hash, cohort_id, task_created_at_ms,
                        first_submission_status, submission_status,
                        submission_count, submitted_at_ms, completion_status,
                        run_id, error_type, error_message, queue_latency_ms,
                        execution_latency_ms, completed_at_ms, updated_at_ms,
                        analysis_status, analysis_success
                    ) VALUES (
                        :task_id, :asset_key, :source, :venue, :symbol, :timeframe,
                        :snapshot_hash, :cohort_id, :task_created_at_ms,
                        'accepted', 'accepted', 0, :submitted_at_ms,
                        :completion_status, :run_id, :error_type, :error_message,
                        :queue_latency_ms, :execution_latency_ms,
                        :completed_at_ms, :updated_at_ms,
                        :analysis_status, :analysis_success
                    )
                    ON CONFLICT(task_id) DO UPDATE SET
                        completion_status = excluded.completion_status,
                        run_id = excluded.run_id,
                        error_type = excluded.error_type,
                        error_message = excluded.error_message,
                        queue_latency_ms = excluded.queue_latency_ms,
                        execution_latency_ms = excluded.execution_latency_ms,
                        completed_at_ms = excluded.completed_at_ms,
                        analysis_status = excluded.analysis_status,
                        analysis_success = excluded.analysis_success,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    {
                        **values,
                        "submitted_at_ms": task.created_at_ms,
                        "completion_status": completion.status,
                        "run_id": run_id,
                        "error_type": completion.error_type,
                        "error_message": (completion.error_message or "")[:2_000] or None,
                        "queue_latency_ms": completion.queue_latency_ms,
                        "execution_latency_ms": completion.execution_latency_ms,
                        "completed_at_ms": now_ms,
                        "analysis_status": analysis_status,
                        "analysis_success": int(analysis_success),
                        "updated_at_ms": now_ms,
                    },
                )
                if self._require_frozen_payload:
                    connection.execute(
                        "UPDATE monitoring_frozen_tasks SET active = 0 "
                        "WHERE task_id = ? AND NOT "
                        "(? = 'cancelled' AND started_at_ms IS NOT NULL)",
                        (task.task_id, completion.status),
                    )
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("Scheduler completion audit failed: %s", exc)
            return False

    def _initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS monitoring_jobs (
                    task_id TEXT PRIMARY KEY,
                    asset_key TEXT NOT NULL,
                    source TEXT NOT NULL,
                    venue TEXT,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    cohort_id TEXT NOT NULL,
                    task_created_at_ms INTEGER NOT NULL,
                    first_submission_status TEXT NOT NULL,
                    submission_status TEXT NOT NULL,
                    submission_count INTEGER NOT NULL,
                    submitted_at_ms INTEGER NOT NULL,
                    completion_status TEXT,
                    run_id TEXT,
                    error_type TEXT,
                    error_message TEXT,
                    queue_latency_ms REAL,
                    execution_latency_ms REAL,
                    completed_at_ms INTEGER,
                    analysis_status TEXT,
                    analysis_success INTEGER,
                    updated_at_ms INTEGER NOT NULL
                )
                """)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_monitoring_jobs_asset_time "
                "ON monitoring_jobs(asset_key, task_created_at_ms DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_monitoring_jobs_completion "
                "ON monitoring_jobs(completion_status, completed_at_ms DESC)"
            )
            self._ensure_column(connection, "analysis_status", "TEXT")
            self._ensure_column(connection, "analysis_success", "INTEGER")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS monitoring_frozen_tasks ("
                "task_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, "
                "payload_digest TEXT NOT NULL, started_at_ms INTEGER, "
                "active INTEGER NOT NULL DEFAULT 1)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_monitoring_jobs_success "
                "ON monitoring_jobs(task_id, analysis_success)"
            )

    def mark_started(self, task_id: str) -> bool:
        """Cross the no-automatic-retry boundary before model work."""
        try:
            with self._lock, self._connect() as connection:
                cursor = connection.execute(
                    "UPDATE monitoring_frozen_tasks SET started_at_ms = ? "
                    "WHERE task_id = ? AND active = 1 AND started_at_ms IS NULL",
                    (int(time.time() * 1000), task_id),
                )
                return cursor.rowcount == 1
        except (OSError, sqlite3.Error):
            logger.exception("Scheduler start audit failed")
            return False

    def list_unresolved_tasks(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT j.task_id, j.asset_key, f.started_at_ms, "
                "f.payload_json, f.payload_digest FROM monitoring_jobs AS j "
                "LEFT JOIN monitoring_frozen_tasks AS f ON f.task_id = j.task_id "
                "WHERE (f.active = 1 OR (f.task_id IS NULL AND "
                "j.first_submission_status = 'accepted' AND j.completion_status IS NULL)) "
                "ORDER BY j.task_created_at_ms"
            ).fetchall()
        return [dict(row) for row in rows]

    def load_incomplete_tasks(self) -> list[AnalysisTask]:
        """Load never-started accepted tasks; started work needs reconciliation."""
        from pa_agent.monitoring.frozen_task_codec import decode_task

        tasks = []
        for row in self.list_unresolved_tasks():
            if row["started_at_ms"] is not None:
                continue
            if not row["payload_json"] or not row["payload_digest"]:
                raise ValueError(f"accepted task has no frozen payload: {row['task_id']}")
            task = decode_task(row["payload_json"], row["payload_digest"])
            if task.task_id != row["task_id"] or task.asset_key != row["asset_key"]:
                raise ValueError("accepted task identity differs from frozen payload")
            tasks.append(task)
        return tasks

    def record_reconciled_success(self, task_id: str, run_id: str, status: str) -> bool:
        """Link an already-indexed successful record to its accepted task."""
        if status not in {"completed", "completed_with_warnings", "degraded_pa_only"}:
            return False
        try:
            with self._lock, self._connect() as connection:
                active = connection.execute(
                    "SELECT started_at_ms FROM monitoring_frozen_tasks "
                    "WHERE task_id = ? AND active = 1", (task_id,),
                ).fetchone()
                if active is None or active[0] is None:
                    return False
                cursor = connection.execute(
                    "UPDATE monitoring_jobs SET completion_status = 'completed', "
                    "run_id = ?, analysis_status = ?, analysis_success = 1, "
                    "error_type = NULL, error_message = NULL, "
                    "queue_latency_ms = NULL, execution_latency_ms = NULL, "
                    "completed_at_ms = ?, updated_at_ms = ? WHERE task_id = ?",
                    (run_id, status, int(time.time() * 1000), int(time.time() * 1000), task_id),
                )
                if cursor.rowcount != 1:
                    return False
                connection.execute(
                    "UPDATE monitoring_frozen_tasks SET active = 0 WHERE task_id = ?",
                    (task_id,),
                )
                return True
        except (OSError, sqlite3.Error):
            logger.exception("Scheduler persisted-result reconciliation failed")
            return False

    def was_successful(self, task_id: str) -> bool:
        """Return whether this exact frozen task completed successfully before."""
        try:
            with self._lock, self._connect() as connection:
                row = connection.execute(
                    "SELECT completion_status, analysis_success "
                    "FROM monitoring_jobs WHERE task_id = ?",
                    (str(task_id),),
                ).fetchone()
            return bool(row and row[0] == "completed" and row[1] == 1)
        except (OSError, sqlite3.Error, TypeError, ValueError, KeyError) as exc:
            logger.warning("Scheduler success lookup failed: %s", exc)
            raise

    def list_successful_jobs(self, *, limit: int = 10_000) -> list[dict[str, Any]]:
        """Return persisted task/run links for non-LLM execution reconciliation."""
        try:
            with self._lock, self._connect() as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT task_id, run_id FROM monitoring_jobs "
                    "WHERE completion_status = 'completed' AND analysis_success = 1 "
                    "AND run_id IS NOT NULL "
                    "ORDER BY task_created_at_ms ASC, completed_at_ms ASC LIMIT ?",
                    (max(1, min(int(limit), 1_000_000)),),
                ).fetchall()
            return [dict(row) for row in rows]
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("Scheduler reconciliation query failed: %s", exc)
            return []

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._db_path, timeout=5.0)
        try:
            connection.execute("PRAGMA busy_timeout = 5000")
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _task_fields(task: AnalysisTask) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "asset_key": task.asset_key,
            "source": task.source,
            "venue": task.venue,
            "symbol": task.symbol,
            "timeframe": task.timeframe,
            "snapshot_hash": task.snapshot_hash,
            "cohort_id": task.cohort_id,
            "task_created_at_ms": task.created_at_ms,
        }

    @staticmethod
    def _run_id(result: Any) -> str | None:
        result = getattr(result, "record", result)
        meta = getattr(result, "meta", None)
        value = str(getattr(meta, "run_id", "") or "").strip()
        return value or None

    @staticmethod
    def _analysis_status(completion: JobCompletion) -> str:
        value = str(getattr(completion.result, "analysis_status", "") or "").strip()
        return value or completion.status

    @staticmethod
    def _analysis_success(completion: JobCompletion) -> bool:
        if completion.status != "completed":
            return False
        return bool(getattr(completion.result, "analysis_success", True))

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        name: str,
        declaration: str,
    ) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(monitoring_jobs)").fetchall()
        }
        if name not in columns:
            connection.execute(
                f"ALTER TABLE monitoring_jobs ADD COLUMN {name} {declaration}"
            )
