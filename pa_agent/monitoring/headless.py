"""Qt-free polling runtime for bounded multi-asset analysis."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass
from typing import Any

from pa_agent.config.paths import RuntimePaths
from pa_agent.config.settings import MonitoringAssetSettings, Settings
from pa_agent.config.vpa import vpa_is_enabled
from pa_agent.data.factory import (
    resolved_identity_for_source,
    volume_metadata_for_source,
)
from pa_agent.data.higher_timeframe import (
    disabled_higher_timeframe_context,
    fetch_higher_timeframe_context,
)
from pa_agent.data.snapshot import INDICATOR_WARMUP_BARS, build_display_frame
from pa_agent.data.snapshot_bundle import build_snapshot_bundle
from pa_agent.data.volume_context import merge_bar_history, snapshot_fetch_count
from pa_agent.monitoring.scheduler import AnalysisTask, JobCompletion
from pa_agent.monitoring.task_factory import create_frozen_analysis_task, frame_snapshot_hash
from pa_agent.records.analysis_history import (
    compute_incremental_bar_delta,
    find_latest_successful_record_for_frame,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PollCycleResult:
    prepared: int = 0
    accepted: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass
class _AssetState:
    config: MonitoringAssetSettings
    source: Any | None = None
    connected: bool = False
    startup_observed: bool = False
    startup_closed_bar_ts: int | None = None
    observed_task_id: str | None = None
    in_flight_task_id: str | None = None
    successful_task_id: str | None = None
    attempts: int = 0
    next_retry_at: float = 0.0
    fetch_failures: int = 0
    next_fetch_at: float = 0.0
    last_analysis_status: str | None = None
    prepared_task: AnalysisTask | None = None
    prepared_closed_bar_ts: int | None = None
    prepared_frame_hash: str | None = None
    history_backfill_done: bool = False
    history_cache: list[Any] | None = None


class HeadlessMonitor:
    """Poll closed bars and submit each immutable snapshot at most once."""

    def __init__(
        self,
        *,
        settings: Settings,
        paths: RuntimePaths,
        scheduler_audit: Any,
        source_factory: Callable[[MonitoringAssetSettings], Any],
        service_factory: Callable[[Callable[[JobCompletion], None]], Any],
        analysis_fingerprint: str,
        paper_execution: Any | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time_ms: Callable[[], int] | None = None,
    ) -> None:
        self._settings = settings
        self._paths = paths
        self._scheduler_audit = scheduler_audit
        self._source_factory = source_factory
        self._analysis_fingerprint = analysis_fingerprint
        self._paper_execution = paper_execution
        self._monotonic = monotonic
        self._wall_time_ms = wall_time_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._closed = False
        self._paper_reconcile_needed = bool(
            paper_execution is not None
            and getattr(paper_execution, "enabled", False)
        )
        self._states = [
            _AssetState(config=asset)
            for asset in settings.monitoring.assets
            if asset.enabled
        ]
        self._task_to_state: dict[str, _AssetState] = {}
        self._data_futures: set[Future[Any]] = set()
        self._data_executor = ThreadPoolExecutor(
            max_workers=settings.monitoring.max_data_concurrency,
            thread_name_prefix="pa-market-data",
        )
        self._service = service_factory(self.handle_completion)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def request_stop(self) -> None:
        self._stop.set()

    def poll_once(self) -> PollCycleResult:
        """Fetch every eligible asset once and enqueue accepted snapshots."""
        with self._lock:
            if self._closed:
                raise RuntimeError("headless monitor is closed")
        self._retry_paper_reconciliation()
        now = self._monotonic()
        eligible = [state for state in self._states if now >= state.next_fetch_at]
        with self._lock:
            if self._closed:
                raise RuntimeError("headless monitor is closed")
            futures = {
                self._data_executor.submit(self._prepare_task, state): state
                for state in eligible
            }
            self._data_futures.update(futures)
        prepared = 0
        accepted = 0
        skipped = len(self._states) - len(eligible)
        failed = 0
        try:
            for future in as_completed(futures):
                state = futures[future]
                try:
                    task = future.result()
                except Exception as exc:
                    failed += 1
                    self._record_fetch_failure(state, exc)
                    continue
                prepared += 1
                outcome = self._submit_prepared(state, task)
                if outcome == "accepted":
                    accepted += 1
                else:
                    skipped += 1
        finally:
            with self._lock:
                self._data_futures.difference_update(futures)
        return PollCycleResult(
            prepared=prepared,
            accepted=accepted,
            skipped=skipped,
            failed=failed,
        )

    def run_forever(self) -> None:
        """Poll until a signal or caller requests cooperative shutdown."""
        interval = float(self._settings.monitoring.poll_interval_seconds)
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(interval)

    def wait_idle(self, timeout: float | None = None) -> bool:
        return bool(self._service.wait_idle(timeout))

    def latest_snapshots_successful(self) -> bool:
        """Whether every configured asset's latest observed task succeeded."""
        with self._lock:
            return bool(self._states) and all(
                state.observed_task_id is not None
                and state.successful_task_id == state.observed_task_id
                and state.in_flight_task_id is None
                for state in self._states
            )

    def handle_completion(self, completion: JobCompletion) -> None:
        """Update retry state after scheduler and analysis outcomes are known."""
        paper_submission: tuple[Any, Any, str] | None = None
        with self._lock:
            state = self._task_to_state.pop(completion.task.task_id, None)
            if state is None:
                return
            if state.in_flight_task_id == completion.task.task_id:
                state.in_flight_task_id = None
            result = completion.result
            state.last_analysis_status = str(
                getattr(result, "analysis_status", completion.status) or completion.status
            )
            success = (
                completion.status == "completed"
                and bool(getattr(result, "analysis_success", True))
            )
            if success:
                state.successful_task_id = completion.task.task_id
                state.next_retry_at = float("inf")
                payload = getattr(completion.task, "payload", None)
                frozen_spec = getattr(payload, "instrument_spec", None)
                if (
                    self._paper_execution is not None
                    and bool(getattr(result, "record_persisted", False))
                    and bool(getattr(result, "audit_ok", False))
                    and frozen_spec is not None
                ):
                    paper_submission = (
                        result.record,
                        frozen_spec,
                        completion.task.task_id,
                    )
            else:
                retryable = completion.status == "failed" or bool(
                    getattr(result, "retryable", False)
                )
                maximum_attempts = 1 + int(self._settings.monitoring.max_analysis_retries)
                if retryable and state.attempts < maximum_attempts:
                    exponent = max(0, state.attempts - 1)
                    state.next_retry_at = self._monotonic() + min(
                        float(self._settings.monitoring.retry_backoff_seconds) * (2**exponent),
                        3600.0,
                    )
                else:
                    state.next_retry_at = float("inf")
        if paper_submission is not None:
            record, spec, task_id = paper_submission
            try:
                self._paper_execution.submit_official(
                    record,
                    spec,
                    task_id=task_id,
                )
            except Exception:
                self._paper_reconcile_needed = True
                logger.exception(
                    "Paper execution failed after persisted analysis %s",
                    task_id,
                )

    def _retry_paper_reconciliation(self) -> None:
        if not self._paper_reconcile_needed or self._paper_execution is None:
            return
        list_jobs = getattr(self._scheduler_audit, "list_successful_jobs", None)
        reconcile = getattr(self._paper_execution, "reconcile_official", None)
        if not callable(list_jobs) or not callable(reconcile):
            return
        try:
            reconcile(list_jobs(), self._paths.records_pending_dir)
        except Exception:
            logger.exception("Paper reconciliation retry failed; will retry next poll")
            return
        self._paper_reconcile_needed = False

    def shutdown(self) -> None:
        """Cancel work, wait within the configured bound, and disconnect feeds."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            data_futures = tuple(self._data_futures)
        timeout = float(self._settings.monitoring.shutdown_timeout_seconds)
        deadline = self._monotonic() + timeout
        self._service.shutdown(wait=False)
        for future in data_futures:
            future.cancel()
        if data_futures:
            _done, unfinished = wait(
                data_futures,
                timeout=max(0.0, deadline - self._monotonic()),
            )
            if unfinished:
                logger.warning(
                    "Market-data shutdown deadline reached with %d task(s) still active",
                    len(unfinished),
                )
        self._data_executor.shutdown(wait=False, cancel_futures=True)
        for state in self._states:
            source = state.source
            if source is None or not state.connected:
                continue
            try:
                source.disconnect()
            except Exception:
                logger.exception(
                    "Data source disconnect failed for %s %s",
                    state.config.symbol,
                    state.config.timeframe,
                )
            finally:
                state.connected = False
        idle = bool(self._service.wait_idle(max(0.0, deadline - self._monotonic())))
        self._service.shutdown(wait=idle)

    def _prepare_task(self, state: _AssetState) -> AnalysisTask:
        asset = state.config
        source = self._ensure_source(state)
        now_ms = self._wall_time_ms()
        analysis_bars = int(self._settings.general.analysis_bar_count)
        steady_fetch_count = snapshot_fetch_count(
            analysis_bars,
            timeframe=asset.timeframe,
            indicator_warmup_bars=INDICATOR_WARMUP_BARS,
            vpa_enabled=False,
        )
        initial_fetch_count = snapshot_fetch_count(
            analysis_bars,
            timeframe=asset.timeframe,
            indicator_warmup_bars=INDICATOR_WARMUP_BARS,
            vpa_enabled=vpa_is_enabled(self._settings.prompt),
        )
        fetch_count = steady_fetch_count if state.history_backfill_done else initial_fetch_count
        raw = source.latest_snapshot(fetch_count)
        if initial_fetch_count > steady_fetch_count and raw:
            raw = merge_bar_history(
                raw,
                state.history_cache or [],
                limit=initial_fetch_count,
            )
            state.history_cache = raw
            state.history_backfill_done = True
        identity = resolved_identity_for_source(
            asset.source,
            source,
            symbol=asset.symbol,
        )
        metadata = volume_metadata_for_source(
            asset.source,
            source,
            identity=identity,
            symbol=asset.symbol,
        )
        frame = build_display_frame(
            raw,
            analysis_bars,
            asset.symbol,
            asset.timeframe,
            now_ms=now_ms,
            data_metadata=metadata,
        )
        if frame is None:
            raise ValueError("insufficient closed bars for analysis")
        latest_closed_bar_ts = max(int(bar.ts_open) for bar in frame.bars)
        current_frame_hash = frame_snapshot_hash(frame)
        with self._lock:
            if (
                state.prepared_task is not None
                and state.prepared_closed_bar_ts == latest_closed_bar_ts
                and state.prepared_frame_hash == current_frame_hash
            ):
                state.fetch_failures = 0
                state.next_fetch_at = 0.0
                return state.prepared_task
        paper_enabled = self._paper_execution is not None and bool(
            getattr(self._paper_execution, "enabled", False)
        )
        current_spec = getattr(source, "current_instrument_spec", None)
        spec = current_spec() if paper_enabled and callable(current_spec) else None
        if paper_enabled:
            if spec is None:
                logger.error(
                    "Paper execution unavailable for %s %s: provider constraints missing; "
                    "PA analysis continues",
                    frame.symbol,
                    frame.timeframe,
                )
            else:
                try:
                    self._paper_execution.observe_official(spec, frame.timeframe, frame.bars)
                except Exception:
                    logger.exception(
                        "Paper market observation failed for %s %s; PA analysis continues",
                        frame.symbol,
                        frame.timeframe,
                    )
        if self._settings.general.higher_timeframe_enabled:
            htf_context = fetch_higher_timeframe_context(
                source=source,
                source_kind=asset.source,
                symbol=asset.symbol,
                current_timeframe=asset.timeframe,
                bar_count=self._settings.general.higher_timeframe_bar_count,
                override=self._settings.general.higher_timeframe_override,
                now_ms=now_ms,
                resolved_identity=identity,
            )
        else:
            htf_context = disabled_higher_timeframe_context(asset.timeframe)
        bundle = build_snapshot_bundle(
            frame,
            htf_context,
            expected_identity=identity,
            captured_at_ms=now_ms,
        )
        if not bundle.data_quality.ok:
            raise ValueError(
                "snapshot quality rejected: "
                f"{bundle.data_quality.failed_check or 'unknown'}"
            )
        previous, new_count = self._incremental_context(frame)
        task_venue = identity.venue if identity is not None else asset.exchange
        task = create_frozen_analysis_task(
            frame,
            htf_context,
            self._settings,
            source=asset.source,
            venue=task_venue or None,
            analysis_fingerprint=self._analysis_fingerprint,
            previous_record=previous,
            incremental_new_bar_count=new_count,
            created_at_ms=now_ms,
            instrument_spec=spec,
        )
        with self._lock:
            state.fetch_failures = 0
            state.next_fetch_at = 0.0
            state.prepared_task = task
            state.prepared_closed_bar_ts = latest_closed_bar_ts
            state.prepared_frame_hash = current_frame_hash
        return task

    def _ensure_source(self, state: _AssetState) -> Any:
        if state.source is None:
            state.source = self._source_factory(state.config)
        if state.connected:
            return state.source
        source = state.source
        set_exchange = getattr(source, "set_exchange", None)
        if callable(set_exchange):
            set_exchange(state.config.exchange)
        source.connect()
        try:
            source.subscribe(state.config.symbol, state.config.timeframe)
        except Exception:
            try:
                source.disconnect()
            except Exception:
                logger.debug("Partially connected source cleanup failed", exc_info=True)
            state.source = None
            raise
        state.connected = True
        return source

    def _incremental_context(self, frame: Any) -> tuple[Any | None, int | None]:
        threshold = int(self._settings.general.incremental_max_new_bars)
        if threshold <= 0:
            return None, None
        previous = find_latest_successful_record_for_frame(
            frame,
            directory=self._paths.records_pending_dir,
        )
        if previous is None:
            return None, None
        delta = compute_incremental_bar_delta(frame, previous)
        if delta is None or not 1 <= delta.new_count <= threshold:
            return None, None
        return previous, delta.new_count

    def _submit_prepared(self, state: _AssetState, task: AnalysisTask) -> str:
        now = self._monotonic()
        latest_closed_bar_ts = self._latest_closed_bar_ts(task)
        with self._lock:
            if state.in_flight_task_id is not None:
                return "in_flight"
            is_new = state.observed_task_id != task.task_id
            if is_new:
                state.observed_task_id = task.task_id
                state.successful_task_id = None
                state.attempts = 0
                state.next_retry_at = 0.0
            if not state.startup_observed:
                state.startup_observed = True
                state.startup_closed_bar_ts = latest_closed_bar_ts
                if not self._settings.monitoring.run_on_startup:
                    return "initial_observation"
            elif (
                not self._settings.monitoring.run_on_startup
                and state.startup_closed_bar_ts is not None
                and latest_closed_bar_ts <= state.startup_closed_bar_ts
            ):
                return "initial_observation"
            if state.successful_task_id == task.task_id:
                return "successful"
            try:
                persisted_success = self._scheduler_audit.was_successful(task.task_id)
            except Exception as exc:
                persisted_success = False
                logger.warning(
                    "Persistent monitoring dedupe is unavailable for %s %s: %s",
                    state.config.symbol,
                    state.config.timeframe,
                    exc,
                )
            if persisted_success:
                state.successful_task_id = task.task_id
                state.last_analysis_status = "completed"
                return "persisted_success"
            if now < state.next_retry_at:
                return "backoff"
            maximum_attempts = 1 + int(self._settings.monitoring.max_analysis_retries)
            if state.attempts >= maximum_attempts:
                return "attempts_exhausted"
            submission = self._service.submit(task)
            if not submission.accepted:
                return submission.status
            state.attempts += 1
            state.in_flight_task_id = task.task_id
            self._task_to_state[task.task_id] = state
            return "accepted"

    def _record_fetch_failure(self, state: _AssetState, exc: Exception) -> None:
        with self._lock:
            state.fetch_failures += 1
            delay = min(
                float(self._settings.monitoring.retry_backoff_seconds)
                * (2 ** max(0, state.fetch_failures - 1)),
                3600.0,
            )
            state.next_fetch_at = self._monotonic() + delay
            source = state.source
            if source is not None and state.connected:
                try:
                    source.disconnect()
                except Exception:
                    logger.debug("Data source reset failed", exc_info=True)
                state.connected = False
                state.source = None
        logger.warning(
            "Market data unavailable for %s %s: %s",
            state.config.symbol,
            state.config.timeframe,
            exc,
        )

    @staticmethod
    def _latest_closed_bar_ts(task: AnalysisTask) -> int:
        frame = getattr(task.payload, "frame", None)
        bars = getattr(frame, "bars", None)
        timestamps = [
            int(bar.ts_open)
            for bar in (bars or ())
            if getattr(bar, "ts_open", None) is not None
        ]
        if not timestamps:
            raise ValueError("frozen analysis task has no closed bar timestamp")
        return max(timestamps)
