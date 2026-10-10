from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from pa_agent.monitoring.analysis_runner import AnalysisRunResult
from pa_agent.monitoring.audit import SchedulerAuditStore
from pa_agent.monitoring.scheduler import JobCompletion
from tests.unit.test_monitoring_recovery import _task


@pytest.mark.parametrize("status", ["incomplete", "review_required", "unavailable_provider"])
def test_completed_callback_preserves_failed_analysis_details(tmp_path, status):
    task = _task()
    record = SimpleNamespace(
        meta=SimpleNamespace(run_id="failed-run"),
        exception={"type": "provider_error", "message": "Codex output rejected before PA validation"},
        run_outcome={"status": status, "reason": "Codex output rejected before PA validation"},
    )
    result = AnalysisRunResult(record, status, False, False, True, True)
    store = SchedulerAuditStore(tmp_path / "monitoring.sqlite3")
    assert store.record_completion(JobCompletion(task=task, status="completed", result=result))
    with sqlite3.connect(store.db_path) as db:
        row = db.execute(
            "SELECT completion_status, analysis_success, analysis_status, error_type, error_message "
            "FROM monitoring_jobs"
        ).fetchone()
    assert row == ("completed", 0, status, "provider_error", record.exception["message"])
    assert store.was_successful(task.task_id) is False


def test_successful_analysis_does_not_promote_sidecar_error(tmp_path):
    task = _task()
    record = SimpleNamespace(
        meta=SimpleNamespace(run_id="successful-run"),
        exception={"type": "provider_error", "message": "non-official sidecar"},
        run_outcome={"status": "completed_with_warnings"},
    )
    result = AnalysisRunResult(record, "completed_with_warnings", True, False, True, True)
    store = SchedulerAuditStore(tmp_path / "monitoring.sqlite3")
    assert store.record_completion(JobCompletion(task=task, status="completed", result=result))
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT error_type,error_message FROM monitoring_jobs").fetchone() == (None, None)


def test_explicit_scheduler_failure_remains_authoritative(tmp_path):
    task = _task()
    store = SchedulerAuditStore(tmp_path / "monitoring.sqlite3")
    assert store.record_completion(JobCompletion(
        task=task, status="failed", error_type="OSError", error_message="scheduler error",
    ))
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT error_type,error_message FROM monitoring_jobs").fetchone() == (
            "OSError", "scheduler error",
        )


def test_failed_analysis_preserves_bounded_worker_reason(tmp_path):
    record = SimpleNamespace(
        meta=SimpleNamespace(run_id="resource-failure"),
        exception={"type": "provider_error", "message": "Codex execution unavailable", "attempts": [
            {"worker_failure_reason": "Codex process could not be started (errno 11)"},
        ]},
        run_outcome={"status": "incomplete"},
    )
    result = AnalysisRunResult(record, "incomplete", False, False, True, True)
    store = SchedulerAuditStore(tmp_path / "monitoring.sqlite3")
    assert store.record_completion(JobCompletion(task=_task(), status="completed", result=result))
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT error_message FROM monitoring_jobs").fetchone()[0] == (
            "Codex execution unavailable: Codex process could not be started (errno 11)"
        )


def test_role_stop_without_exception_preserves_outcome_reason(tmp_path):
    record = SimpleNamespace(
        meta=SimpleNamespace(run_id="stopped-run"), exception=None,
        run_outcome={"status": "incomplete", "reason": "required evidence unavailable"},
    )
    result = AnalysisRunResult(record, "incomplete", False, False, True, True)
    store = SchedulerAuditStore(tmp_path / "monitoring.sqlite3")
    assert store.record_completion(JobCompletion(task=_task(), status="completed", result=result))
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT error_type,error_message FROM monitoring_jobs").fetchone() == (
            "incomplete", "required evidence unavailable",
        )
