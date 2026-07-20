"""Audited facade around the bounded multi-asset scheduler."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from pa_agent.monitoring.audit import SchedulerAuditStore
from pa_agent.monitoring.scheduler import (
    AnalysisTask,
    JobCompletion,
    MultiAssetAnalysisScheduler,
    SubmissionResult,
)


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
        self._scheduler = MultiAssetAnalysisScheduler(
            handler,
            max_concurrency=max_concurrency,
            max_pending_jobs=max_pending_jobs,
            max_pending_per_asset=max_pending_per_asset,
            dedupe_history=dedupe_history,
            on_completion=self._handle_completion,
        )

    def submit(self, task: AnalysisTask) -> SubmissionResult:
        result = self._scheduler.submit(task)
        if self._audit_store is not None and not self._audit_store.record_submission(
            task,
            result,
        ):
            self._record_audit_failure()
        return result

    def cancel_asset(self, asset_key: str) -> int:
        return self._scheduler.cancel_asset(asset_key)

    def wait_idle(self, timeout: float | None = None) -> bool:
        return self._scheduler.wait_idle(timeout)

    def shutdown(self, *, wait: bool = True) -> None:
        self._scheduler.shutdown(wait=wait)

    def snapshot(self) -> dict[str, Any]:
        with self._audit_failure_lock:
            audit_failures = self._audit_failures
        return {
            **self._scheduler.snapshot(),
            "audit_failures": audit_failures,
        }

    def _handle_completion(self, completion: JobCompletion) -> None:
        if self._audit_store is not None and not self._audit_store.record_completion(
            completion
        ):
            self._record_audit_failure()
        if self._on_completion is not None:
            self._on_completion(completion)

    def _record_audit_failure(self) -> None:
        with self._audit_failure_lock:
            self._audit_failures += 1
