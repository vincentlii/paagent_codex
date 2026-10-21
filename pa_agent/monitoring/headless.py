"""Qt-free polling runtime for bounded multi-asset analysis."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, replace
from typing import Any

from pa_agent.config.analysis_pipeline import pipeline_from_legacy
from pa_agent.config.paths import RuntimePaths
from pa_agent.config.settings import MonitoringAssetSettings, Settings, provider_api_key_configured
from pa_agent.config.vpa import vpa_is_enabled
from pa_agent.data.bar_close_wait import timeframe_to_seconds
from pa_agent.data.market_context import market_context_for_snapshot
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
from pa_agent.operations.analysis_profile import (
    AnalysisProfile,
    MARKET_SYMBOLS,
    profile_fingerprint,
    validate_analysis_profile,
)
from pa_agent.operations.projection import build_review_projection
from pa_agent.operations.runtime_state import RuntimeControlStore, RuntimeStatusStore
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
        control_store: RuntimeControlStore | None = None,
        status_store: RuntimeStatusStore | None = None,
        backend_switcher: Callable[[str], bool] | None = None,
        backend_runtime_identity: Mapping[str, Any] | None = None,
        profile_applier: Callable[[AnalysisProfile], bool] | None = None,
        codex_worker_status_path: Any | None = None,
        recovery_blocked_asset_keys: tuple[str, ...] = (),
        recovery_tasks: tuple[AnalysisTask, ...] = (),
    ) -> None:
        self._settings = settings
        self._paths = paths
        self._scheduler_audit = scheduler_audit
        self._source_factory = source_factory
        self._service_factory = service_factory
        self._scheduler_concurrency = settings.monitoring.max_concurrency
        self._analysis_fingerprint = analysis_fingerprint
        self._paper_execution = paper_execution
        self._monotonic = monotonic
        self._wall_time_ms = wall_time_ms or (lambda: int(time.time() * 1000))
        self._control_store = control_store or RuntimeControlStore(paths.runtime_control_path)
        self._status_store = status_store or RuntimeStatusStore(paths.runtime_status_path)
        self._started_at_ms = int(self._wall_time_ms())
        self._review_projection: dict[str, Any] | None = None
        self._review_projection_at_ms = 0
        self._applied_control_generation = 0
        self._applied_backend = "deepseek_only"
        self._applied_codex_fast_mode = False
        from pa_agent.operations.demo_settings import DemoExecutionPolicy
        self._applied_demo_execution_policy = DemoExecutionPolicy()
        self._applied_profile = AnalysisProfile()
        self._applied_profile_fingerprint = profile_fingerprint(self._applied_profile)
        self._applied_profile_generation = 0
        persisted_status = self._status_store.read() or {}
        persisted_consumed = persisted_status.get(
            "run_once_consumed_generation",
            persisted_status.get("consumed_run_once_generation", 0),
        )
        self._consumed_run_once_generation = (
            int(persisted_consumed)
            if isinstance(persisted_consumed, int)
            and not isinstance(persisted_consumed, bool)
            and persisted_consumed >= 0
            else 0
        )
        self._run_once_in_flight_generation: int | None = None
        self._backend_switcher = backend_switcher or (
            lambda mode: mode == "deepseek_only"
        )
        self._backend_runtime_identity = dict(backend_runtime_identity or {})
        self._profile_applier = profile_applier or (lambda _profile: True)
        self._codex_worker_status_path = codex_worker_status_path
        self._recovery_blocked_assets = set(recovery_blocked_asset_keys)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._closed = False
        self._paper_reconcile_needed = bool(
            paper_execution is not None
            and getattr(paper_execution, "enabled", False)
        )
        self._states = [
            _AssetState(config=asset.model_copy(deep=True))
            for asset in settings.monitoring.assets
            if asset.enabled
        ]
        # Dashboard scope controls only the allowlisted VPS markets; generic
        # monitors retain their configured providers and instruments.
        self._profile_market_scope = bool(self._states) and all(
            state.config.source == "binance_usdm" and state.config.exchange == "BINANCE"
            and state.config.symbol in MARKET_SYMBOLS and state.config.timeframe == "1h"
            for state in self._states
        )
        if self._profile_market_scope:
            existing = {state.config.symbol for state in self._states}
            self._states.extend(_AssetState(config=MonitoringAssetSettings(
                source="binance_usdm", exchange="BINANCE", symbol=symbol, timeframe="1h", enabled=False,
            )) for symbol in MARKET_SYMBOLS if symbol not in existing)
        self._task_to_state: dict[str, _AssetState] = {}
        self._data_futures: set[Future[Any]] = set()
        self._data_executor = ThreadPoolExecutor(
            max_workers=settings.monitoring.max_data_concurrency,
            thread_name_prefix="pa-market-data",
        )
        self._service = service_factory(self.handle_completion)
        self._resume_accepted_tasks(recovery_tasks)

    def _resume_accepted_tasks(self, tasks: tuple[AnalysisTask, ...]) -> None:
        recover = getattr(self._service, "recover_unstarted", None)
        for task in tasks:
            matches = [
                state for state in self._states
                if state.config.source == task.source
                and state.config.symbol.upper() == task.symbol
                and state.config.timeframe == task.timeframe
                and state.config.exchange.upper() in {task.venue, "AUTO"}
            ]
            if task.payload.analysis_backend_mode != "codex_only" or len(matches) != 1 or not callable(recover):
                self._recovery_blocked_assets.add(task.asset_key)
                continue
            state = matches[0]
            state.observed_task_id = task.task_id
            state.in_flight_task_id = task.task_id
            state.attempts = 1
            self._task_to_state[task.task_id] = state
            result = recover(task)
            if not result.accepted:
                self._task_to_state.pop(task.task_id, None)
                state.in_flight_task_id = None
                self._recovery_blocked_assets.add(task.asset_key)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def request_stop(self) -> None:
        self._stop.set()

    def poll_once(
        self,
        *,
        submission_allowed: Callable[[], bool] | None = None,
        max_submissions: int | None = None,
        allow_startup_snapshot: bool = False,
    ) -> PollCycleResult:
        """Fetch every eligible asset once and enqueue accepted snapshots."""
        with self._lock:
            if self._closed:
                raise RuntimeError("headless monitor is closed")
        self._retry_paper_reconciliation()
        now = self._monotonic()
        active_markets = self._active_paper_markets()
        eligible = [state for state in self._states if now >= state.next_fetch_at and (
            state.config.enabled or self._market_key(state) in active_markets
        )]
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
                if task is None:
                    skipped += 1
                    continue
                if max_submissions is not None and accepted >= max_submissions:
                    skipped += 1
                    continue
                if submission_allowed is not None and not submission_allowed():
                    skipped += 1
                    continue
                outcome = self._submit_prepared(
                    state,
                    task,
                    allow_startup_snapshot=allow_startup_snapshot,
                )
                if outcome == "accepted":
                    accepted += 1
                else:
                    skipped += 1
        finally:
            with self._lock:
                self._data_futures.difference_update(futures)
            self._expire_paper_pending()
        return PollCycleResult(
            prepared=prepared,
            accepted=accepted,
            skipped=skipped,
            failed=failed,
        )

    def run_forever(self) -> None:
        """Poll until a signal or caller requests cooperative shutdown."""
        interval = float(self._settings.monitoring.poll_interval_seconds)
        heartbeat_interval = min(5.0, interval)
        next_poll_at = self._monotonic()
        while not self._stop.is_set():
            now = self._monotonic()
            control = self._control_store.read()
            paused, control_error = self._apply_control(
                control,
                verify_latest=True,
            )
            if not paused and now >= next_poll_at:
                self.poll_once(
                    submission_allowed=lambda: (
                        self._submission_matches_applied_control()
                    )
                )
                next_poll_at = self._monotonic() + interval
                control = self._control_store.read()
                paused, control_error = self._apply_control(
                    control,
                    verify_latest=True,
                )
            if paused:
                self._expire_paper_pending()
            self._publish_runtime_status(paused=paused, control_error=control_error)
            wait_seconds = heartbeat_interval
            if not paused:
                wait_seconds = min(wait_seconds, max(0.05, next_poll_at - self._monotonic()))
            self._stop.wait(wait_seconds)

    def _expire_paper_pending(self) -> None:
        expire = getattr(self._paper_execution, "expire_pending", None)
        if callable(expire):
            try:
                expire()
            except Exception:
                logger.exception("Paper expiry reconciliation failed; analysis remains isolated")

    def _apply_control(
        self,
        control: Any,
        *,
        verify_latest: bool = False,
    ) -> tuple[bool, str | None]:
        """Apply backend/profile controls and one-shots at an idle boundary."""
        desired_state = str(getattr(control, "desired_state", "paused"))
        desired_backend = str(
            getattr(control, "desired_backend", "deepseek_only")
            or "deepseek_only"
        )
        desired_fast_mode = bool(getattr(control, "codex_fast_mode", False))
        error = getattr(control, "error", None)
        if error:
            return True, str(error)
        desired_demo_policy = getattr(control, "demo_execution_policy", self._applied_demo_execution_policy)
        demo_policy_pending = desired_demo_policy != self._applied_demo_execution_policy
        desired_profile = self._desired_profile(control)
        profile_pending = desired_profile is not None and (
            int(getattr(control, "profile_generation", 0))
            != self._applied_profile_generation
            or str(
                getattr(control, "profile_fingerprint", "")
                or profile_fingerprint(desired_profile)
            ).strip().lower()
            != self._applied_profile_fingerprint
        )
        backend_pending = desired_backend != self._applied_backend
        fast_mode_pending = desired_fast_mode != self._applied_codex_fast_mode
        identity_pending = profile_pending or backend_pending or fast_mode_pending or demo_policy_pending
        profile_applied = False
        backend_applied = False

        if identity_pending:
            if not self._scheduler_is_idle():
                return True, (
                    "analysis_profile_pending"
                    if profile_pending
                    else "demo_execution_settings_pending" if demo_policy_pending else "backend_switch_draining"
                )
            if verify_latest and not self._control_is_latest(
                control,
                desired_backend=desired_backend,
                desired_profile=desired_profile,
                desired_fast_mode=desired_fast_mode,
            ):
                return True, (
                    "analysis_profile_pending"
                    if profile_pending
                    else "demo_execution_settings_pending" if demo_policy_pending else "backend_switch_pending"
                )
            if profile_pending:
                assert desired_profile is not None
                if not self._profile_market_scope and desired_profile.enabled_symbols != ("XAUUSDT",):
                    return True, "analysis_market_scope_unavailable"
                try:
                    applied = bool(self._profile_applier(desired_profile))
                except Exception:
                    logger.exception("Analysis profile application failed")
                    applied = False
                if not applied:
                    return True, "analysis_profile_unavailable"
                try:
                    self._apply_profile_runtime(desired_profile)
                except Exception:
                    logger.exception("Analysis scope application failed")
                    return True, "analysis_profile_unavailable"
                self._applied_profile = desired_profile
                self._applied_profile_fingerprint = profile_fingerprint(
                    desired_profile
                )
                self._applied_profile_generation = int(
                    getattr(control, "profile_generation", 0)
                )
                profile_applied = True
                self._clear_prepared_tasks()
            if backend_pending:
                try:
                    switched = bool(self._backend_switcher(desired_backend))
                except Exception:
                    logger.exception("Analysis backend switch failed")
                    switched = False
                if not switched:
                    return True, "backend_unavailable"
                self._applied_backend = desired_backend
                backend_applied = True
                self._clear_prepared_tasks()
            if fast_mode_pending:
                self._applied_codex_fast_mode = desired_fast_mode
                self._clear_prepared_tasks()
            if demo_policy_pending:
                apply_settings = getattr(self._paper_execution, "apply_execution_settings", None)
                if callable(apply_settings):
                    apply_settings(desired_demo_policy)
                self._applied_demo_execution_policy = desired_demo_policy
                self._clear_prepared_tasks()
        self._applied_control_generation = int(getattr(control, "generation", 0))
        run_once_generation = int(getattr(control, "run_once_generation", 0) or 0)
        if run_once_generation > self._consumed_run_once_generation:
            if profile_applied or backend_applied:
                return True, (
                    "analysis_profile_pending"
                    if profile_applied
                    else "backend_switch_pending"
                )
            if desired_state != "paused":
                return True, "run_once_requires_paused"
            if not self._scheduler_is_healthy_and_idle():
                return True, "run_once_in_flight"
            if verify_latest and not self._control_is_latest(
                control,
                desired_backend=desired_backend,
                desired_profile=desired_profile,
                desired_fast_mode=desired_fast_mode,
            ):
                return True, "run_once_pending"
            self._run_once_in_flight_generation = run_once_generation
            try:
                result = self.poll_once(
                    submission_allowed=lambda: True,
                    max_submissions=1,
                    allow_startup_snapshot=True,
                )
            except Exception:
                logger.exception("Run-once analysis opportunity failed")
                self._run_once_in_flight_generation = None
                return True, "run_once_failed"
            if result.accepted != 1:
                self._run_once_in_flight_generation = None
                return True, "run_once_not_submitted"
            self._consumed_run_once_generation = run_once_generation
            self._run_once_in_flight_generation = None
            # Persist the acknowledgement immediately, before the next
            # heartbeat, so a service restart cannot replay the generation.
            self._publish_runtime_status(paused=True)
        return desired_state == "paused", None

    @staticmethod
    def _desired_profile(control: Any) -> AnalysisProfile | None:
        raw = getattr(control, "analysis_profile", None)
        if raw is None:
            return None
        try:
            return validate_analysis_profile(raw)
        except (TypeError, ValueError):
            return None

    def _scheduler_is_idle(self) -> bool:
        try:
            if not bool(self._service.wait_idle(0)):
                return False
        except Exception:
            return False
        with self._lock:
            return not self._data_futures

    def _apply_profile_runtime(self, profile: AnalysisProfile) -> None:
        """Caller guarantees idle; existing clients and frozen tasks stay intact."""
        if self._scheduler_concurrency != profile.max_concurrency:
            previous = self._settings.monitoring.max_concurrency
            self._settings.monitoring.max_concurrency = profile.max_concurrency
            try:
                candidate = self._service_factory(self.handle_completion)
            except Exception:
                self._settings.monitoring.max_concurrency = previous
                raise
            self._service.shutdown(wait=True)
            self._service = candidate
            self._scheduler_concurrency = profile.max_concurrency
        if self._profile_market_scope:
            for state in self._states:
                state.config.enabled = state.config.symbol in profile.enabled_symbols
            self._settings.monitoring.assets = [state.config.model_copy(deep=True) for state in self._states]
        self._settings.general.decision_stance = profile.decision_stance

    @staticmethod
    def _market_key(state: _AssetState) -> tuple[str, str, str, str]:
        asset = state.config
        return asset.source, asset.exchange, asset.symbol, asset.timeframe

    def _active_paper_markets(self) -> set[tuple[str, str, str, str]]:
        getter = getattr(self._paper_execution, "active_market_keys", None)
        if not callable(getter):
            return set()
        try:
            return set(getter())
        except Exception:
            logger.exception("Paper exposure lookup unavailable; retain market observations")
            return {self._market_key(state) for state in self._states}

    def _scheduler_is_healthy_and_idle(self) -> bool:
        if not self._scheduler_is_idle():
            return False
        with self._lock:
            if any(state.in_flight_task_id is not None for state in self._states):
                return False
        try:
            snapshot = self._service.snapshot()
        except Exception:
            return False
        if not isinstance(snapshot, Mapping):
            return True
        return not bool(snapshot.get("closed")) and not bool(
            snapshot.get("error")
        )

    def _control_is_latest(
        self,
        control: Any,
        *,
        desired_backend: str,
        desired_profile: AnalysisProfile | None,
        desired_fast_mode: bool,
    ) -> bool:
        latest = self._control_store.read()
        if int(getattr(latest, "generation", 0)) != int(
            getattr(control, "generation", 0)
        ):
            return False
        if str(
            getattr(latest, "desired_backend", "deepseek_only")
            or "deepseek_only"
        ) != desired_backend:
            return False
        if bool(getattr(latest, "codex_fast_mode", False)) != desired_fast_mode:
            return False
        if desired_profile is not None:
            latest_profile = self._desired_profile(latest)
            if latest_profile is None or latest_profile != desired_profile:
                return False
            if int(getattr(latest, "profile_generation", 0)) != int(
                getattr(control, "profile_generation", 0)
            ):
                return False
            latest_fp = str(
                getattr(latest, "profile_fingerprint", "")
                or profile_fingerprint(latest_profile)
            ).strip().lower()
            expected_fp = str(
                getattr(control, "profile_fingerprint", "")
                or profile_fingerprint(desired_profile)
            ).strip().lower()
            if latest_fp != expected_fp:
                return False
        return True

    def _clear_prepared_tasks(self) -> None:
        with self._lock:
            for state in self._states:
                state.prepared_task = None
                state.prepared_closed_bar_ts = None
                state.prepared_frame_hash = None

    def _submission_matches_applied_control(self) -> bool:
        control = self._control_store.read()
        backend_matches = (
            getattr(control, "desired_state", "paused") == "running"
            and str(
                getattr(control, "desired_backend", "deepseek_only")
                or "deepseek_only"
            )
            == self._applied_backend
        )
        if not backend_matches:
            return False
        if getattr(control, "demo_execution_policy", self._applied_demo_execution_policy) != self._applied_demo_execution_policy:
            return False
        if bool(getattr(control, "codex_fast_mode", False)) != self._applied_codex_fast_mode:
            return False
        desired_profile = self._desired_profile(control)
        if desired_profile is None:
            return not hasattr(control, "analysis_profile")
        return (
            int(getattr(control, "profile_generation", 0))
            == self._applied_profile_generation
            and str(
                getattr(control, "profile_fingerprint", "")
                or profile_fingerprint(desired_profile)
            ).strip().lower()
            == self._applied_profile_fingerprint
        )

    def runtime_snapshot(
        self,
        *,
        paused: bool,
        control_error: str | None = None,
        service_state: str | None = None,
    ) -> dict[str, Any]:
        """Return the credential-free operational projection for the dashboard."""
        with self._lock:
            assets = [
                {
                    "source": state.config.source,
                    "venue": state.config.exchange,
                    "symbol": state.config.symbol,
                    "timeframe": state.config.timeframe,
                    "analysis_enabled": state.config.enabled,
                    "market_context": dict(state.prepared_task.payload.frame.data_metadata.get("market_context", {}))
                        if state.prepared_task is not None else {},
                    "connected": state.connected,
                    "in_flight": state.in_flight_task_id is not None,
                    "last_analysis_status": state.last_analysis_status,
                    "fetch_failures": state.fetch_failures,
                    "latest_closed_bar_ts": state.prepared_closed_bar_ts,
                }
                for state in self._states
            ]
        return {
            "service_state": service_state or ("paused" if paused else "running"),
            "started_at_ms": self._started_at_ms,
            "heartbeat_stale_after_seconds": max(
                30,
                int(float(self._settings.monitoring.poll_interval_seconds) * 2 + 15),
            ),
            "applied_control_generation": self._applied_control_generation,
            "applied_codex_fast_mode": self._applied_codex_fast_mode,
            "applied_demo_execution_policy": self._applied_demo_execution_policy.model_dump(mode="json"),
            "applied_profile": self._applied_profile.model_dump(mode="json"),
            "applied_profile_fingerprint": self._applied_profile_fingerprint,
            "applied_profile_generation": self._applied_profile_generation,
            "take_profit_policy_version": "fixed_rr_v1",
            "run_once_consumed_generation": self._consumed_run_once_generation,
            "consumed_run_once_generation": self._consumed_run_once_generation,
            "mode": {
                "vpa": self._applied_profile.vpa_mode,
                "analysis_pipeline": (
                    self._settings.analysis_pipeline.pipeline
                    if self._settings.analysis_pipeline is not None
                    else pipeline_from_legacy(self._settings.prompt).pipeline
                ),
                "direct_available": provider_api_key_configured(self._settings),
                "paper_trading": bool(self._settings.paper_trading.enabled),
                "decision_stance": self._settings.general.decision_stance,
                "analysis_backend": self._applied_backend,
            },
            "scheduler": self._service.snapshot(),
            "recovery_blocked_assets": sorted(self._recovery_blocked_assets),
            "assets": assets,
            "control_error": control_error,
        }

    def _publish_runtime_status(
        self,
        *,
        paused: bool,
        control_error: str | None = None,
        service_state: str | None = None,
    ) -> None:
        try:
            now_ms = int(self._wall_time_ms())
            if (
                self._review_projection is None
                or now_ms - self._review_projection_at_ms >= 30_000
            ):
                self._review_projection = build_review_projection(
                    self._paths.analysis_audit_db_path,
                    self._paths.paper_trading_db_path,
                    monitoring_db_path=self._paths.monitoring_audit_db_path,
                    codex_circuit_path=self._paths.state_root / "codex-circuit.json",
                    codex_worker_status_path=self._codex_worker_status_path,
                )
                self._review_projection_at_ms = now_ms
            publish_control = getattr(self._paper_execution, "publish_control", None)
            if callable(publish_control):
                publish_control(running=not paused and service_state not in {"stopping", "stopped"})
            review = dict(self._review_projection)
            compose = getattr(self._paper_execution, "compose_projection", None)
            if callable(compose):
                review["trading"] = compose(review.get("trading", {}))
            self._status_store.write({
                **self.runtime_snapshot(
                    paused=paused,
                    control_error=control_error,
                    service_state=service_state,
                ),
                **review,
            })
        except (OSError, TypeError, ValueError):
            logger.exception("Runtime status publication failed")

    def wait_idle(self, timeout: float | None = None) -> bool:
        return bool(self._service.wait_idle(timeout))

    def latest_snapshots_successful(self) -> bool:
        """Whether every configured asset's latest observed task succeeded."""
        with self._lock:
            return bool(self._states) and all(
                state.observed_task_id is not None
                and state.successful_task_id == state.observed_task_id
                and state.in_flight_task_id is None
                for state in self._states if state.config.enabled
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
                extra = ({"execution_payload": completion.task.payload,
                          "execution_snapshot_hash": completion.task.snapshot_hash}
                         if callable(getattr(self._paper_execution, "bind_task", None)) else {})
                self._paper_execution.submit_official(
                    record,
                    spec,
                    task_id=task_id,
                    **extra,
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
        self._publish_runtime_status(
            paused=True,
            control_error=None,
            service_state="stopping",
        )
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

    def _prepare_task(self, state: _AssetState) -> AnalysisTask | None:
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
        metadata["analysis_as_of_ms"] = now_ms
        if asset.enabled and self._settings.prompt.experience_max_entries > 0:
            from pa_agent.records.experience_reader import ExperienceReader

            metadata["experience_pool"] = ExperienceReader(self._paths.experience_dir).freeze_pool()
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
        duration = timeframe_to_seconds(frame.timeframe)
        if identity is not None and duration is not None:
            market_context = market_context_for_snapshot(identity, latest_closed_bar_ts + duration * 1000)
            if market_context:
                frame = replace(frame, data_metadata={**frame.data_metadata, "market_context": market_context})
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
                    self._paper_execution.observe_official(
                        spec, frame.timeframe, frame.bars, observed_at_ms=now_ms
                    )
                except Exception:
                    logger.exception(
                        "Paper market observation failed for %s %s; PA analysis continues",
                        frame.symbol,
                        frame.timeframe,
                    )
        if not asset.enabled:
            with self._lock:
                state.fetch_failures = 0
                state.next_fetch_at = 0.0
            return None
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
            analysis_backend_mode=self._applied_backend,
            analysis_backend_identity=(
                {
                    **self._backend_runtime_identity,
                    "model": self._applied_profile.codex_model,
                    "reasoning_effort": self._applied_profile.codex_reasoning_effort,
                    "profile_fingerprint": self._applied_profile_fingerprint,
                    **({"service_tier": "fast"} if self._applied_codex_fast_mode else {}),
                }
                if self._applied_backend != "deepseek_only"
                else None
            ),
            analysis_profile=self._applied_profile,
            analysis_profile_fingerprint=self._applied_profile_fingerprint,
            take_profit_tick_size=(
                self._paper_execution.take_profit_tick_size(frame.symbol)
                if self._applied_profile.take_profit_mode == "fixed_rr"
                and callable(getattr(self._paper_execution, "take_profit_tick_size", None))
                else None
            ),
            execution_policy=(
                "limit_3bar_v1" if frame.timeframe == "1h"
                and self._settings.analysis_pipeline is not None else None
            ),
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

    def _submit_prepared(
        self,
        state: _AssetState,
        task: AnalysisTask,
        *,
        allow_startup_snapshot: bool = False,
    ) -> str:
        now = self._monotonic()
        if task.asset_key in self._recovery_blocked_assets:
            return "recovery_blocked"
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
                if (
                    not self._settings.monitoring.run_on_startup
                    and not allow_startup_snapshot
                ):
                    return "initial_observation"
            elif (
                not self._settings.monitoring.run_on_startup
                and not allow_startup_snapshot
                and state.startup_closed_bar_ts is not None
                and latest_closed_bar_ts <= state.startup_closed_bar_ts
            ):
                return "initial_observation"
            if state.successful_task_id == task.task_id:
                return "successful"
            try:
                persisted_success = self._scheduler_audit.was_successful(task.task_id)
            except Exception as exc:
                logger.warning(
                    "Persistent monitoring dedupe is unavailable for %s %s: %s",
                    state.config.symbol,
                    state.config.timeframe,
                    exc,
                )
                return "audit_unavailable"
            if persisted_success:
                state.successful_task_id = task.task_id
                state.last_analysis_status = "completed"
                return "persisted_success"
            if now < state.next_retry_at:
                return "backoff"
            maximum_attempts = 1 + int(self._settings.monitoring.max_analysis_retries)
            if state.attempts >= maximum_attempts:
                return "attempts_exhausted"
            state.attempts += 1
            state.in_flight_task_id = task.task_id
            self._task_to_state[task.task_id] = state
            # submit may deliver completion synchronously. Reserve ownership first,
            # as recovery does, so that callback cannot be lost or re-registered.
            try:
                bind = getattr(self._paper_execution, "bind_task", None)
                if callable(bind):
                    task = bind(task)
                submission = self._service.submit(task)
            except Exception:
                if self._task_to_state.get(task.task_id) is state:
                    self._task_to_state.pop(task.task_id)
                    state.in_flight_task_id = None
                    state.attempts -= 1
                raise
            if not submission.accepted:
                if self._task_to_state.get(task.task_id) is state:
                    self._task_to_state.pop(task.task_id)
                    state.in_flight_task_id = None
                    state.attempts -= 1
                return submission.status
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
