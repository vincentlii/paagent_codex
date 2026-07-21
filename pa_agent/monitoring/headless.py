"""Qt-free polling runtime for bounded multi-asset analysis."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from pa_agent.config.paths import RuntimePaths
from pa_agent.config.settings import MonitoringAssetSettings, Settings
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
from pa_agent.monitoring.scheduler import AnalysisTask, JobCompletion
from pa_agent.monitoring.task_factory import create_frozen_analysis_task
from pa_agent.records.analysis_history import (
    compute_incremental_bar_delta,
    find_latest_successful_record,
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
        monotonic: Callable[[], float] = time.monotonic,
        wall_time_ms: Callable[[], int] | None = None,
    ) -> None:
        self._settings = settings
        self._paths = paths
        self._scheduler_audit = scheduler_audit
        self._source_factory = source_factory
        self._analysis_fingerprint = analysis_fingerprint
        self._monotonic = monotonic
        self._wall_time_ms = wall_time_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._closed = False
        self._states = [
            _AssetState(config=asset)
            for asset in settings.monitoring.assets
            if asset.enabled
        ]
        self._task_to_state: dict[str, _AssetState] = {}
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
        if self._closed:
            raise RuntimeError("headless monitor is closed")
        now = self._monotonic()
        eligible = [state for state in self._states if now >= state.next_fetch_at]
        futures = {
            self._data_executor.submit(self._prepare_task, state): state
            for state in eligible
        }
        prepared = 0
        accepted = 0
        skipped = len(self._states) - len(eligible)
        failed = 0
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
                return
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

    def shutdown(self) -> None:
        """Cancel work, wait within the configured bound, and disconnect feeds."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
        self._data_executor.shutdown(wait=True, cancel_futures=True)
        self._service.shutdown(wait=False)
        timeout = float(self._settings.monitoring.shutdown_timeout_seconds)
        idle = bool(self._service.wait_idle(timeout))
        self._service.shutdown(wait=idle)
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

    def _prepare_task(self, state: _AssetState) -> AnalysisTask:
        asset = state.config
        source = self._ensure_source(state)
        now_ms = self._wall_time_ms()
        fetch_count = (
            int(self._settings.general.analysis_bar_count)
            + INDICATOR_WARMUP_BARS
            + 5
        )
        raw = source.latest_snapshot(fetch_count)
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
            int(self._settings.general.analysis_bar_count),
            asset.symbol,
            asset.timeframe,
            now_ms=now_ms,
            data_metadata=metadata,
        )
        if frame is None:
            raise ValueError("insufficient closed bars for analysis")
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
        )
        with self._lock:
            state.fetch_failures = 0
            state.next_fetch_at = 0.0
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
            raise
        state.connected = True
        return source

    def _incremental_context(self, frame: Any) -> tuple[Any | None, int | None]:
        threshold = int(self._settings.general.incremental_max_new_bars)
        if threshold <= 0:
            return None, None
        previous = find_latest_successful_record(
            symbol=frame.symbol,
            timeframe=frame.timeframe,
            directory=self._paths.records_pending_dir,
        )
        if previous is None:
            return None, None
        delta = compute_incremental_bar_delta(frame, previous)
        if delta is None or delta.new_count > threshold:
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
