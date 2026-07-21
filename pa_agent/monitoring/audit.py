"""SQLite audit ledger for bounded multi-asset scheduling."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
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

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._lock = threading.RLock()
        self._initialize()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def record_submission(
        self,
        task: AnalysisTask,
        result: SubmissionResult,
    ) -> bool:
        now_ms = int(time.time() * 1000)
        values = self._task_fields(task)
        try:
            with self._lock, self._connect() as connection:
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
                "CREATE INDEX IF NOT EXISTS idx_monitoring_jobs_success "
                "ON monitoring_jobs(task_id, analysis_success)"
            )

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
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("Scheduler success lookup failed: %s", exc)
            return False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

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
