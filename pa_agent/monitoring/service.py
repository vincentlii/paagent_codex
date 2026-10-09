"""Audited facade around the bounded multi-asset scheduler."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from pa_agent.monitoring.audit import SchedulerAuditStore
from pa_agent.monitoring.scheduler import (
    AnalysisTask,
    JobCompletion,
    MultiAssetAnalysisScheduler,
    SubmissionResult,
)


class SchedulerStartUnavailable(RuntimeError):
    """No model may run when the durable start boundary cannot be recorded."""


class MultiAssetAnalysisService:
    """Submit frozen asset snapshots with lifecycle and audit guarantees."""

    def __init__(
        self,
        handler: Callable[[AnalysisTask, Any], Any],
        *,
        max_concurrency: int = 2,
        max_pending_jobs: int = 100,
        max_pending_per_asset: int = 2,
        dedupe_history: int = 10_000,
        audit_store: SchedulerAuditStore | None = None,
        on_completion: Callable[[JobCompletion], None] | None = None,
    ) -> None:
        self._audit_store = audit_store
        self._on_completion = on_completion
        self._audit_failure_lock = threading.Lock()
        self._audit_failures = 0
        self._audit_unhealthy = False
        self._recovery_ids: set[str] = set()
        self._scheduler = MultiAssetAnalysisScheduler(
            self._guarded_handler(handler),
            max_concurrency=max_concurrency,
            max_pending_jobs=max_pending_jobs,
            max_pending_per_asset=max_pending_per_asset,
            dedupe_history=dedupe_history,
            on_completion=self._handle_completion,
            remember_completion=self._remember_completion,
            before_accept=self._audit_accepted_submission,
        )

    def submit(self, task: AnalysisTask) -> SubmissionResult:
        result = self._scheduler.submit(task)
        if result.accepted or result.status == "audit_unavailable":
            return result
        if self._audit_store is not None and not self._audit_store.record_submission(
            task,
            result,
        ):
            self._record_audit_failure()
        return result

    def recover_unstarted(self, task: AnalysisTask) -> SubmissionResult:
        """Dispatch a previously accepted task without treating it as a new one."""
        if self._audit_store is None or not getattr(
            self._audit_store, "_require_frozen_payload", False
        ):
            raise RuntimeError("durable recovery requires a frozen audit store")
        with self._audit_failure_lock:
            self._recovery_ids.add(task.task_id)
        try:
            return self.submit(task)
        finally:
            with self._audit_failure_lock:
                self._recovery_ids.discard(task.task_id)

    def _guarded_handler(self, handler: Callable[[AnalysisTask, Any], Any]):
        def run(task: AnalysisTask, cancel_token: Any) -> Any:
            if self._audit_store is not None and getattr(
                self._audit_store, "_require_frozen_payload", False
            ):
                if not self._audit_store.mark_started(task.task_id):
                    raise SchedulerStartUnavailable("durable model start was not recorded")
            return handler(task, cancel_token)

        return run

    def _audit_accepted_submission(self, task: AnalysisTask) -> bool:
        if self._audit_store is None:
            return True
        with self._audit_failure_lock:
            if self._audit_unhealthy:
                return False
        accepted = SubmissionResult("accepted", task.task_id, task.asset_key)
        with self._audit_failure_lock:
            resume = task.task_id in self._recovery_ids
        if (
            self._audit_store.record_submission(task, accepted, resume_unstarted=resume)
            if getattr(self._audit_store, "_require_frozen_payload", False)
            else self._audit_store.record_submission(task, accepted)
        ):
            return True
        self._record_audit_failure()
        return False

    def cancel_asset(self, asset_key: str) -> int:
        return self._scheduler.cancel_asset(asset_key)

    def wait_idle(self, timeout: float | None = None) -> bool:
        return self._scheduler.wait_idle(timeout)

    def shutdown(self, *, wait: bool = True) -> None:
        self._scheduler.shutdown(wait=wait)

    def snapshot(self) -> dict[str, Any]:
        with self._audit_failure_lock:
            audit_failures = self._audit_failures
            audit_unhealthy = self._audit_unhealthy
        return {
            **self._scheduler.snapshot(),
            "audit_failures": audit_failures,
            "error": "audit_unavailable" if audit_unhealthy else None,
        }

    def _handle_completion(self, completion: JobCompletion) -> None:
        if completion.error_type == "SchedulerStartUnavailable":
            self._record_audit_failure()
            if self._on_completion is not None:
                self._on_completion(completion)
            return
        if self._audit_store is not None and not self._audit_store.record_completion(
            completion
        ):
            self._record_audit_failure()
            completion = replace(
                completion,
                status="failed",
                error_type="SchedulerAuditUnavailable",
                error_message="scheduler completion audit is unavailable",
            )
        if self._on_completion is not None:
            self._on_completion(completion)

    def _record_audit_failure(self) -> None:
        with self._audit_failure_lock:
            self._audit_failures += 1
            self._audit_unhealthy = True

    def _remember_completion(self, completion: JobCompletion) -> bool:
        with self._audit_failure_lock:
            if self._audit_unhealthy:
                return False
        if completion.status != "completed":
            return False
        return bool(getattr(completion.result, "analysis_success", True))
