"""Bounded, identity-safe scheduling for independent asset analyses."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

from pa_agent.monitoring.identity import canonical_timeframe
from pa_agent.util.threading import CancelToken

logger = logging.getLogger(__name__)

SubmissionStatus = Literal[
    "accepted",
    "duplicate",
    "queue_full",
    "asset_queue_full",
    "scheduler_closed",
]
CompletionStatus = Literal["completed", "failed", "cancelled"]


def _sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _digest(value: str, name: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    return normalized


@dataclass(frozen=True)
class AnalysisTask:
    """One immutable market snapshot assigned to one analysis cohort."""

    task_id: str
    asset_key: str
    source: str
    venue: str | None
    symbol: str
    timeframe: str
    snapshot_hash: str
    cohort_id: str
    payload: Any = field(compare=False, repr=False)
    created_at_ms: int = field(compare=False)

    def __post_init__(self) -> None:
        source = str(self.source or "").strip().lower()
        venue = str(self.venue or "").strip().upper() or None
        symbol = str(self.symbol or "").strip().upper()
        timeframe = canonical_timeframe(self.timeframe)
        snapshot_hash = _digest(self.snapshot_hash, "snapshot_hash")
        cohort_id = _digest(self.cohort_id, "cohort_id")
        expected_asset_key = "|".join(
            (source, venue or "-", symbol, timeframe)
        )
        expected_task_id = _sha256(
            {
                "asset": {
                    "source": source,
                    "venue": venue,
                    "symbol": symbol,
                    "timeframe": timeframe,
                },
                "snapshot_hash": snapshot_hash,
                "cohort_id": cohort_id,
            }
        )
        if not source or not symbol or not timeframe:
            raise ValueError("task source, symbol, and timeframe are required")
        if (
            self.source != source
            or self.venue != venue
            or self.symbol != symbol
            or self.timeframe != timeframe
            or self.snapshot_hash != snapshot_hash
            or self.cohort_id != cohort_id
        ):
            raise ValueError("task identity fields must be canonical")
        if self.asset_key != expected_asset_key:
            raise ValueError("asset_key does not match task identity")
        if self.task_id != expected_task_id:
            raise ValueError("task_id does not match task identity")
        if int(self.created_at_ms) < 0:
            raise ValueError("created_at_ms must be non-negative")

    @classmethod
    def create(
        cls,
        *,
        source: str,
        symbol: str,
        timeframe: str,
        snapshot_hash: str,
        cohort_id: str,
        payload: Any,
        venue: str | None = None,
        created_at_ms: int | None = None,
    ) -> AnalysisTask:
        normalized_source = str(source or "").strip().lower()
        normalized_symbol = str(symbol or "").strip().upper()
        normalized_timeframe = canonical_timeframe(timeframe)
        normalized_venue = str(venue or "").strip().upper() or None
        if not normalized_source:
            raise ValueError("source is required")
        if not normalized_symbol:
            raise ValueError("symbol is required")
        if not normalized_timeframe:
            raise ValueError("timeframe is required")
        snapshot = _digest(snapshot_hash, "snapshot_hash")
        cohort = _digest(cohort_id, "cohort_id")
        identity = {
            "source": normalized_source,
            "venue": normalized_venue,
            "symbol": normalized_symbol,
            "timeframe": normalized_timeframe,
        }
        asset_key = "|".join(
            (
                normalized_source,
                normalized_venue or "-",
                normalized_symbol,
                normalized_timeframe,
            )
        )
        task_id = _sha256(
            {
                "asset": identity,
                "snapshot_hash": snapshot,
                "cohort_id": cohort,
            }
        )
        return cls(
            task_id=task_id,
            asset_key=asset_key,
            source=normalized_source,
            venue=normalized_venue,
            symbol=normalized_symbol,
            timeframe=normalized_timeframe,
            snapshot_hash=snapshot,
            cohort_id=cohort,
            payload=payload,
            created_at_ms=int(created_at_ms if created_at_ms is not None else time.time() * 1000),
        )


@dataclass(frozen=True)
class SubmissionResult:
    status: SubmissionStatus
    task_id: str
    asset_key: str

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


@dataclass(frozen=True)
class JobCompletion:
    task: AnalysisTask
    status: CompletionStatus
    result: Any = field(default=None, compare=False, repr=False)
    error_type: str | None = None
    error_message: str | None = None
    queue_latency_ms: float = 0.0
    execution_latency_ms: float = 0.0


@dataclass(frozen=True)
class _QueuedTask:
    task: AnalysisTask
    submitted_at: float


@dataclass(frozen=True)
class _RunningTask:
    queued: _QueuedTask
    cancel_token: CancelToken
    future: Future[Any]
    dispatched_at: float


@dataclass(frozen=True)
class _HandlerOutcome:
    started_at: float
    finished_at: float
    result: Any = field(default=None, compare=False, repr=False)
    error_type: str | None = None
    error_message: str | None = None


class MultiAssetAnalysisScheduler:
    """Run at most one job per asset and a bounded number globally.

    The handler owns market interpretation and persistence. The scheduler owns
    only deterministic identity, dispatch, cancellation, and lifecycle state.
    """

    def __init__(
        self,
        handler: Callable[[AnalysisTask, CancelToken], Any],
        *,
        max_concurrency: int = 2,
        max_pending_jobs: int = 100,
        max_pending_per_asset: int = 2,
        dedupe_history: int = 10_000,
        on_completion: Callable[[JobCompletion], None] | None = None,
    ) -> None:
        if not callable(handler):
            raise TypeError("handler must be callable")
        if not 1 <= int(max_concurrency) <= 20:
            raise ValueError("max_concurrency must be between 1 and 20")
        if int(max_pending_jobs) < int(max_concurrency):
            raise ValueError("max_pending_jobs must be at least max_concurrency")
        if int(max_pending_per_asset) < 1:
            raise ValueError("max_pending_per_asset must be positive")
        if int(dedupe_history) < 1:
            raise ValueError("dedupe_history must be positive")
        self._handler = handler
        self._max_concurrency = int(max_concurrency)
        self._max_pending_jobs = int(max_pending_jobs)
        self._max_pending_per_asset = int(max_pending_per_asset)
        self._dedupe_history = int(dedupe_history)
        self._on_completion = on_completion
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_concurrency,
            thread_name_prefix="pa-asset-analysis",
        )
        self._condition = threading.Condition(threading.RLock())
        self._queues: dict[str, deque[_QueuedTask]] = {}
        self._running: dict[str, _RunningTask] = {}
        self._active_task_ids: set[str] = set()
        self._recent_task_ids: OrderedDict[str, None] = OrderedDict()
        self._closed = False
        self._metrics = {
            "accepted": 0,
            "duplicates": 0,
            "rejected": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        }

    def submit(self, task: AnalysisTask) -> SubmissionResult:
        if not isinstance(task, AnalysisTask):
            raise TypeError("task must be an AnalysisTask")
        with self._condition:
            if self._closed:
                return self._submission(task, "scheduler_closed")
            if task.task_id in self._active_task_ids or task.task_id in self._recent_task_ids:
                self._metrics["duplicates"] += 1
                return self._submission(task, "duplicate")
            if self._unfinished_count_locked() >= self._max_pending_jobs:
                self._metrics["rejected"] += 1
                return self._submission(task, "queue_full")
            queue = self._queues.setdefault(task.asset_key, deque())
            if len(queue) >= self._max_pending_per_asset:
                self._metrics["rejected"] += 1
                return self._submission(task, "asset_queue_full")
            queue.append(_QueuedTask(task=task, submitted_at=time.monotonic()))
            self._active_task_ids.add(task.task_id)
            self._metrics["accepted"] += 1
            self._schedule_next_locked(task.asset_key)
            self._condition.notify_all()
            return self._submission(task, "accepted")

    def cancel_asset(self, asset_key: str) -> int:
        completions: list[JobCompletion] = []
        future: Future[Any] | None = None
        with self._condition:
            queue = self._queues.pop(str(asset_key), deque())
            while queue:
                queued = queue.popleft()
                self._finish_task_id_locked(queued.task.task_id)
                self._metrics["cancelled"] += 1
                completions.append(
                    JobCompletion(
                        task=queued.task,
                        status="cancelled",
                        queue_latency_ms=max(0.0, (time.monotonic() - queued.submitted_at) * 1000),
                    )
                )
            running = self._running.get(str(asset_key))
            if running is not None:
                running.cancel_token.set()
                future = running.future
            self._condition.notify_all()
        for completion in completions:
            self._notify_completion(completion)
        if future is not None:
            future.cancel()
        return len(completions) + int(future is not None)

    def wait_idle(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._unfinished_count_locked() > 0:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def shutdown(self, *, wait: bool = True) -> None:
        completions: list[JobCompletion] = []
        running: list[_RunningTask] = []
        with self._condition:
            if not self._closed:
                self._closed = True
                for queue in self._queues.values():
                    while queue:
                        queued = queue.popleft()
                        self._finish_task_id_locked(queued.task.task_id)
                        self._metrics["cancelled"] += 1
                        completions.append(
                            JobCompletion(
                                task=queued.task,
                                status="cancelled",
                                queue_latency_ms=max(
                                    0.0,
                                    (time.monotonic() - queued.submitted_at) * 1000,
                                ),
                            )
                        )
                self._queues.clear()
                running = list(self._running.values())
                for item in running:
                    item.cancel_token.set()
                self._condition.notify_all()
        for completion in completions:
            self._notify_completion(completion)
        for item in running:
            item.future.cancel()
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            queued = sum(len(queue) for queue in self._queues.values())
            return {
                **self._metrics,
                "closed": self._closed,
                "queued": queued,
                "running": len(self._running),
                "unfinished": queued + len(self._running),
                "active_assets": sorted(self._running),
                "max_concurrency": self._max_concurrency,
                "max_pending_jobs": self._max_pending_jobs,
                "max_pending_per_asset": self._max_pending_per_asset,
                "dedupe_entries": len(self._recent_task_ids),
            }

    @staticmethod
    def _submission(task: AnalysisTask, status: SubmissionStatus) -> SubmissionResult:
        return SubmissionResult(status=status, task_id=task.task_id, asset_key=task.asset_key)

    def _unfinished_count_locked(self) -> int:
        return len(self._running) + sum(len(queue) for queue in self._queues.values())

    def _schedule_next_locked(self, asset_key: str) -> None:
        if self._closed or asset_key in self._running:
            return
        queue = self._queues.get(asset_key)
        if not queue:
            self._queues.pop(asset_key, None)
            return
        queued = queue.popleft()
        if not queue:
            self._queues.pop(asset_key, None)
        cancel_token = CancelToken()
        dispatched_at = time.monotonic()
        future = self._executor.submit(self._execute, queued.task, cancel_token)
        self._running[asset_key] = _RunningTask(
            queued=queued,
            cancel_token=cancel_token,
            future=future,
            dispatched_at=dispatched_at,
        )
        future.add_done_callback(
            lambda completed, key=asset_key, task_id=queued.task.task_id: self._future_done(
                key,
                task_id,
                completed,
            )
        )

    def _future_done(
        self,
        asset_key: str,
        task_id: str,
        future: Future[Any],
    ) -> None:
        with self._condition:
            running = self._running.get(asset_key)
            if running is None or running.queued.task.task_id != task_id:
                return
            cancelled = future.cancelled() or running.cancel_token.is_set()
            result: Any = None
            error_type: str | None = None
            error_message: str | None = None
            started_at = running.dispatched_at
            finished_at = time.monotonic()
            if not cancelled:
                try:
                    outcome = future.result()
                    result = outcome.result
                    error_type = outcome.error_type
                    error_message = outcome.error_message
                    started_at = outcome.started_at
                    finished_at = outcome.finished_at
                except Exception as exc:
                    error_type = type(exc).__name__
                    error_message = str(exc)
            if cancelled:
                status: CompletionStatus = "cancelled"
            elif error_type is not None:
                status = "failed"
            else:
                status = "completed"
            completion = JobCompletion(
                task=running.queued.task,
                status=status,
                result=result,
                error_type=error_type,
                error_message=error_message,
                queue_latency_ms=max(0.0, (started_at - running.queued.submitted_at) * 1000),
                execution_latency_ms=max(0.0, (finished_at - started_at) * 1000),
            )
            self._metrics[status] += 1

        self._notify_completion(completion)

        with self._condition:
            current = self._running.get(asset_key)
            if current is not None and current.queued.task.task_id == task_id:
                self._running.pop(asset_key, None)
                self._finish_task_id_locked(task_id)
                self._schedule_next_locked(asset_key)
            self._condition.notify_all()

    def _execute(
        self,
        task: AnalysisTask,
        cancel_token: CancelToken,
    ) -> _HandlerOutcome:
        started_at = time.monotonic()
        result: Any = None
        error_type: str | None = None
        error_message: str | None = None
        try:
            result = self._handler(task, cancel_token)
        except Exception as exc:
            error_type = type(exc).__name__
            error_message = str(exc)
        return _HandlerOutcome(
            started_at=started_at,
            finished_at=time.monotonic(),
            result=result,
            error_type=error_type,
            error_message=error_message,
        )

    def _finish_task_id_locked(self, task_id: str) -> None:
        self._active_task_ids.discard(task_id)
        self._recent_task_ids[task_id] = None
        self._recent_task_ids.move_to_end(task_id)
        while len(self._recent_task_ids) > self._dedupe_history:
            self._recent_task_ids.popitem(last=False)

    def _notify_completion(self, completion: JobCompletion) -> None:
        if self._on_completion is None:
            return
        try:
            self._on_completion(completion)
        except Exception:
            logger.exception("Multi-asset completion callback failed")
