from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
from types import SimpleNamespace

from pa_agent.config.paths import resolve_runtime_paths
from pa_agent.config.settings import MonitoringSettings, Settings
from pa_agent.data.base import KlineBar
from pa_agent.data.contracts import ResolvedInstrumentIdentity
from pa_agent.data.higher_timeframe import disabled_higher_timeframe_context
from pa_agent.data.instrument import InstrumentSpec
from pa_agent.monitoring.analysis_runner import AnalysisRunResult
from pa_agent.monitoring.headless import HeadlessMonitor
from pa_agent.monitoring.scheduler import JobCompletion, SubmissionResult
from pa_agent.operations.analysis_profile import AnalysisProfile, profile_fingerprint
from pa_agent.operations.runtime_state import RuntimeControlStore


class _Source:
    def __init__(self) -> None:
        self.connected = 0
        self.disconnected = 0
        self.subscriptions: list[tuple[str, str]] = []
        newest = 1_800_000_000_000
        self.raw = [
            KlineBar(
                seq=index + 1,
                ts_open=newest - index * 3_600_000,
                open=100.0,
                high=102.0,
                low=98.0,
                close=101.0,
                volume=1000.0 + index,
                closed=True,
            )
            for index in range(60)
        ]

    def connect(self) -> None:
        self.connected += 1

    def disconnect(self) -> None:
        self.disconnected += 1

    def set_exchange(self, _exchange: str) -> None:
        pass

    def subscribe(self, symbol: str, timeframe: str) -> None:
        self.subscriptions.append((symbol, timeframe))

    def supported_timeframes(self):
        return ["1h"]

    def latest_snapshot(self, _count: int):
        return list(self.raw)

    def current_identity(self, requested_symbol=None):
        return ResolvedInstrumentIdentity(
            provider="tradingview",
            requested_symbol=requested_symbol or "XAUUSD",
            resolved_symbol="XAUUSD",
            venue="OANDA",
            timezone_basis="UTC",
        )

    def advance(self) -> None:
        previous = self.raw[0]
        self.raw.insert(
            0,
            KlineBar(
                seq=1,
                ts_open=previous.ts_open + 3_600_000,
                open=previous.close,
                high=previous.high + 1,
                low=previous.low,
                close=previous.close + 1,
                volume=previous.volume + 10,
                closed=True,
            ),
        )
        self.raw.pop()

    def revise_latest(self) -> None:
        latest = self.raw[0]
        self.raw[0] = KlineBar(
            seq=latest.seq,
            ts_open=latest.ts_open,
            open=latest.open,
            high=latest.high,
            low=latest.low,
            close=latest.close,
            volume=latest.volume + 1,
            closed=True,
        )


class _SubscribeFailSource(_Source):
    def subscribe(self, symbol: str, timeframe: str) -> None:
        raise RuntimeError("subscription rejected")


class _PaperSource(_Source):
    def current_instrument_spec(self):
        return InstrumentSpec(
            provider="tradingview",
            venue="OANDA",
            symbol="XAUUSD",
            instrument_type="spot",
            tick_size=0.01,
            quantity_step=0.01,
            minimum_quantity=0.01,
            maximum_quantity=1000.0,
            minimum_notional=1.0,
        )


class _PaperExecution:
    enabled = True

    def __init__(self) -> None:
        self.observed = []
        self.submitted = []

    def observe_official(self, spec, timeframe, bars, *, observed_at_ms=None):
        self.observed.append((spec, timeframe, bars))

    def submit_official(self, record, spec, *, task_id):
        self.submitted.append((record, spec, task_id))


class _FailOncePaperExecution(_PaperExecution):
    def __init__(self) -> None:
        super().__init__()
        self.reconciled = 0

    def submit_official(self, record, spec, *, task_id):
        del record, spec, task_id
        raise OSError("paper database temporarily unavailable")

    def reconcile_official(self, _jobs, _records_dir):
        self.reconciled += 1


class _FailingFetchSource(_Source):
    def __init__(self) -> None:
        super().__init__()
        self.fetches = 0

    def latest_snapshot(self, _count: int):
        self.fetches += 1
        raise TimeoutError("feed timeout")


class _Audit:
    def __init__(self, *, fail_reads: bool = False) -> None:
        self.successful: set[str] = set()
        self.fail_reads = fail_reads

    def was_successful(self, task_id: str) -> bool:
        if self.fail_reads:
            raise OSError("audit database unavailable")
        return task_id in self.successful

    def list_successful_jobs(self):
        return [{"task_id": "task", "run_id": "run"}]


class _Service:
    def __init__(self, on_completion) -> None:
        self.on_completion = on_completion
        self.submitted = []
        self.closed = False

    def submit(self, task):
        self.submitted.append(task)
        return SubmissionResult("accepted", task.task_id, task.asset_key)

    def wait_idle(self, _timeout=None):
        return True

    def shutdown(self, *, wait=True):
        self.closed = True

    def snapshot(self):
        return {"unfinished": 0, "audit_failures": 0}


@dataclass
class _Clock:
    monotonic_value: float = 100.0
    wall_ms: int = 1_800_000_000_000 + 7_200_000

    def monotonic(self) -> float:
        return self.monotonic_value

    def now_ms(self) -> int:
        return self.wall_ms


def _settings(*, run_on_startup: bool = True) -> Settings:
    settings = Settings()
    settings.general.analysis_bar_count = 20
    settings.general.higher_timeframe_enabled = False
    settings.monitoring = MonitoringSettings(
        enabled=True,
        run_on_startup=run_on_startup,
        retry_backoff_seconds=30,
        assets=[
            {
                "source": "tradingview",
                "exchange": "OANDA",
                "symbol": "XAUUSD",
                "timeframe": "1h",
            }
        ],
    )
    return settings


def _monitor(
    tmp_path,
    *,
    run_on_startup: bool = True,
    settings=None,
    source=None,
    source_factory=None,
    audit=None,
    paper_execution=None,
    profile_applier=None,
    recovery_blocked_asset_keys=(),
):
    source = source or _Source()
    audit = audit or _Audit()
    clock = _Clock()
    holder = {}

    def service_factory(on_completion):
        service = _Service(on_completion)
        holder["service"] = service
        return service

    monitor = HeadlessMonitor(
        settings=settings or _settings(run_on_startup=run_on_startup),
        paths=resolve_runtime_paths(
            project_root=tmp_path,
            environ={"PA_AGENT_HOME": str(tmp_path / "state")},
        ),
        scheduler_audit=audit,
        source_factory=source_factory or (lambda _asset: source),
        service_factory=service_factory,
        analysis_fingerprint="d" * 64,
        monotonic=clock.monotonic,
        wall_time_ms=clock.now_ms,
        paper_execution=paper_execution,
        profile_applier=profile_applier,
        recovery_blocked_asset_keys=recovery_blocked_asset_keys,
    )
    return monitor, source, audit, holder["service"], clock


def test_unreconciled_started_task_blocks_new_snapshot_for_same_asset(tmp_path):
    key = "tradingview|OANDA|XAUUSD|1h"
    monitor, _source, _audit, service, _clock = _monitor(
        tmp_path, recovery_blocked_asset_keys=(key,),
    )
    try:
        result = monitor.poll_once()
        assert result.accepted == 0
        assert service.submitted == []
        assert key in monitor.runtime_snapshot(paused=True)["recovery_blocked_assets"]
    finally:
        monitor.shutdown()


def _completion(
    task,
    *,
    status: str,
    success: bool,
    retryable: bool,
    audit_ok: bool = True,
):
    result = AnalysisRunResult(
        record=SimpleNamespace(meta=SimpleNamespace(run_id=f"run-{status}")),
        analysis_status=status,
        analysis_success=success,
        retryable=retryable,
        record_persisted=success,
        audit_ok=audit_ok,
    )
    return JobCompletion(task=task, status="completed", result=result)


def test_synchronous_completion_is_registered_before_dispatch(tmp_path) -> None:
    monitor, _source, _audit, service, _clock = _monitor(tmp_path)

    def submit(task):
        service.submitted.append(task)
        service.on_completion(_completion(task, status="completed", success=True, retryable=False))
        return SubmissionResult("accepted", task.task_id, task.asset_key)

    service.submit = submit
    try:
        assert monitor.poll_once().accepted == 1
        state = monitor._states[0]
        assert state.in_flight_task_id is None
        assert state.successful_task_id == service.submitted[0].task_id
        assert state.attempts == 1
        assert monitor._task_to_state == {}
        assert monitor.poll_once().accepted == 0
    finally:
        monitor.shutdown()


def test_rejected_dispatch_rolls_back_reserved_task_state(tmp_path) -> None:
    monitor, _source, _audit, service, _clock = _monitor(tmp_path)
    service.submit = lambda task: SubmissionResult("queue_full", task.task_id, task.asset_key)
    try:
        assert monitor.poll_once().accepted == 0
        assert monitor._states[0].in_flight_task_id is None
        assert monitor._states[0].attempts == 0
        assert monitor._task_to_state == {}
    finally:
        monitor.shutdown()


def test_provider_failure_retries_same_snapshot_after_backoff_then_dedupes_success(tmp_path) -> None:
    monitor, source, _audit, service, clock = _monitor(tmp_path)

    monitor.poll_once()
    first = service.submitted[-1]
    service.on_completion(
        _completion(
            first,
            status="unavailable_provider",
            success=False,
            retryable=True,
        )
    )
    monitor.poll_once()
    assert len(service.submitted) == 1

    clock.monotonic_value += 31
    monitor.poll_once()
    assert service.submitted[-1].task_id == first.task_id
    assert len(service.submitted) == 2

    service.on_completion(
        _completion(
            first,
            status="completed",
            success=True,
            retryable=False,
        )
    )
    monitor.poll_once()
    assert len(service.submitted) == 2
    assert source.connected == 1


def test_paper_execution_observes_market_then_submits_only_persisted_success(tmp_path) -> None:
    paper = _PaperExecution()
    monitor, _source, _audit, service, _clock = _monitor(
        tmp_path,
        source=_PaperSource(),
        paper_execution=paper,
    )

    monitor.poll_once()
    task = service.submitted[-1]
    assert len(paper.observed) == 1
    service.on_completion(
        _completion(task, status="completed", success=True, retryable=False)
    )

    assert len(paper.submitted) == 1
    assert paper.submitted[0][2] == task.task_id


def test_same_snapshot_does_not_repeat_paper_market_observation(tmp_path) -> None:
    paper = _PaperExecution()
    monitor, _source, _audit, service, _clock = _monitor(
        tmp_path,
        source=_PaperSource(),
        paper_execution=paper,
    )

    monitor.poll_once()
    task = service.submitted[-1]
    service.on_completion(
        _completion(task, status="completed", success=True, retryable=False)
    )
    monitor.poll_once()

    assert len(paper.observed) == 1


def test_paper_expiry_runs_on_cached_snapshot_and_feed_failure(tmp_path, monkeypatch) -> None:
    paper = _PaperExecution()
    sweeps = []
    paper.expire_pending = lambda: sweeps.append(True)
    monitor, source, _audit, _service, _clock = _monitor(
        tmp_path, source=_PaperSource(), paper_execution=paper,
    )
    monitor.poll_once()
    monitor.poll_once()
    def failed_fetch(*args):
        raise OSError("feed unavailable")
    monkeypatch.setattr(source, "latest_snapshot", failed_fetch)
    assert monitor.poll_once().failed == 1
    assert len(sweeps) == 3
    assert len(paper.observed) == 1


def test_paper_execution_does_not_submit_when_analysis_audit_failed(tmp_path) -> None:
    paper = _PaperExecution()
    monitor, _source, _audit, service, _clock = _monitor(
        tmp_path,
        source=_PaperSource(),
        paper_execution=paper,
    )
    monitor.poll_once()
    task = service.submitted[-1]

    service.on_completion(
        _completion(
            task,
            status="completed",
            success=True,
            retryable=False,
            audit_ok=False,
        )
    )

    assert paper.submitted == []


def test_paper_failure_reconciles_on_next_poll_without_resubmitting_analysis(tmp_path) -> None:
    paper = _FailOncePaperExecution()
    monitor, _source, _audit, service, _clock = _monitor(
        tmp_path,
        source=_PaperSource(),
        paper_execution=paper,
    )
    monitor.poll_once()
    task = service.submitted[-1]
    service.on_completion(
        _completion(task, status="completed", success=True, retryable=False)
    )

    monitor.poll_once()

    assert paper.reconciled == 2
    assert len(service.submitted) == 1


def test_new_closed_bar_is_submitted_after_previous_snapshot_succeeds(tmp_path) -> None:
    monitor, source, _audit, service, _clock = _monitor(tmp_path)
    monitor.poll_once()
    first = service.submitted[-1]
    service.on_completion(
        _completion(first, status="completed", success=True, retryable=False)
    )

    source.advance()
    monitor.poll_once()

    assert len(service.submitted) == 2
    assert service.submitted[-1].task_id != first.task_id


def test_run_on_startup_false_waits_for_new_close_not_snapshot_revision(tmp_path) -> None:
    monitor, source, _audit, service, _clock = _monitor(
        tmp_path,
        run_on_startup=False,
    )

    monitor.poll_once()
    assert service.submitted == []

    source.revise_latest()
    monitor.poll_once()
    assert service.submitted == []

    source.advance()
    monitor.poll_once()
    assert len(service.submitted) == 1


def test_same_closed_bar_reuses_frozen_task_without_refetching_htf(
    tmp_path,
    monkeypatch,
) -> None:
    monitor, _source, _audit, service, _clock = _monitor(tmp_path)
    monitor._settings.general.higher_timeframe_enabled = True
    calls: list[int] = []
    monkeypatch.setattr(
        "pa_agent.monitoring.headless.fetch_higher_timeframe_context",
        lambda **_kwargs: (
            calls.append(1)
            or disabled_higher_timeframe_context("1h")
        ),
    )

    monitor.poll_once()
    first = service.submitted[-1]
    service.on_completion(
        _completion(first, status="completed", success=True, retryable=False)
    )
    monitor.poll_once()

    assert calls == [1]
    assert len(service.submitted) == 1


def test_same_timestamp_market_revision_invalidates_prepared_task(
    tmp_path,
    monkeypatch,
) -> None:
    monitor, source, _audit, service, _clock = _monitor(tmp_path)
    monitor._settings.general.higher_timeframe_enabled = True
    calls: list[int] = []
    monkeypatch.setattr(
        "pa_agent.monitoring.headless.fetch_higher_timeframe_context",
        lambda **_kwargs: (
            calls.append(1)
            or disabled_higher_timeframe_context("1h")
        ),
    )

    monitor.poll_once()
    first = service.submitted[-1]
    service.on_completion(
        _completion(first, status="completed", success=True, retryable=False)
    )
    source.revise_latest()
    monitor.poll_once()

    assert calls == [1, 1]
    assert len(service.submitted) == 2
    assert service.submitted[-1].task_id != first.task_id


def test_restart_uses_persistent_success_dedupe(tmp_path) -> None:
    first_monitor, _source, audit, first_service, _clock = _monitor(tmp_path)
    first_monitor.poll_once()
    task = first_service.submitted[-1]
    audit.successful.add(task.task_id)
    first_monitor.shutdown()

    second_monitor, _source, _audit, second_service, _clock = _monitor(
        tmp_path,
        audit=audit,
    )
    second_monitor.poll_once()
    second_monitor.shutdown()


    assert second_service.submitted == []


def test_audit_read_failure_blocks_new_analysis(tmp_path) -> None:
    monitor, _source, _audit, service, _clock = _monitor(
        tmp_path,
        audit=_Audit(fail_reads=True),
    )

    result = monitor.poll_once()

    assert result.accepted == 0
    assert service.submitted == []
    monitor.shutdown()


def test_shutdown_disconnects_sources_and_closes_service(tmp_path) -> None:
    monitor, source, _audit, service, _clock = _monitor(tmp_path)
    monitor.poll_once()

    monitor.shutdown()

    assert source.disconnected == 1
    assert service.closed is True


def test_shutdown_waits_for_data_workers_before_closing_sources(tmp_path) -> None:
    monitor, source, _audit, _service, _clock = _monitor(tmp_path)
    monitor.poll_once()
    observed: list[int] = []

    class _Executor:
        def shutdown(self, *, wait, cancel_futures):
            assert wait is False
            assert cancel_futures is True
            observed.append(source.disconnected)

    monitor._data_executor = _Executor()

    monitor.shutdown()

    assert observed == [0]
    assert source.disconnected == 1


def test_shutdown_bounds_active_data_future_before_forced_disconnect(
    tmp_path,
    monkeypatch,
) -> None:
    monitor, source, _audit, _service, _clock = _monitor(tmp_path)
    monitor.poll_once()
    active = Future()
    monitor._data_futures.add(active)
    observed: dict[str, object] = {}

    def bounded_wait(futures, *, timeout):
        observed["futures"] = tuple(futures)
        observed["timeout"] = timeout
        return set(), set(futures)

    monkeypatch.setattr("pa_agent.monitoring.headless.wait", bounded_wait)

    monitor.shutdown()

    assert observed["futures"] == (active,)
    assert observed["timeout"] == 60.0
    assert source.disconnected == 1


def test_subscribe_failure_disconnects_the_partially_connected_source(tmp_path) -> None:
    source = _SubscribeFailSource()
    monitor, _source, _audit, service, _clock = _monitor(tmp_path, source=source)

    result = monitor.poll_once()

    assert result.failed == 1
    assert source.connected == 1
    assert source.disconnected == 1
    assert service.submitted == []


def test_fetch_failure_discards_closed_source_and_recreates_it_after_backoff(
    tmp_path,
) -> None:
    failed = _FailingFetchSource()
    recovered = _Source()
    sources = iter((failed, recovered))
    monitor, _source, _audit, service, clock = _monitor(
        tmp_path,
        source=failed,
        source_factory=lambda _asset: next(sources),
    )

    first = monitor.poll_once()
    clock.monotonic_value += 31
    second = monitor.poll_once()

    assert first.failed == 1
    assert failed.disconnected == 1
    assert second.accepted == 1
    assert recovered.connected == 1
    assert len(service.submitted) == 1


def test_repeated_market_data_failures_use_exponential_backoff(tmp_path) -> None:
    source = _FailingFetchSource()
    monitor, _source, _audit, _service, clock = _monitor(tmp_path, source=source)

    monitor.poll_once()
    assert source.fetches == 1

    clock.monotonic_value = 131
    monitor.poll_once()
    assert source.fetches == 2

    clock.monotonic_value = 170
    monitor.poll_once()
    assert source.fetches == 2

    clock.monotonic_value = 192
    monitor.poll_once()
    assert source.fetches == 3


def test_zero_new_bars_never_reuses_incremental_context(tmp_path, monkeypatch) -> None:
    monitor, _source, _audit, _service, _clock = _monitor(tmp_path)
    previous = SimpleNamespace()
    monkeypatch.setattr(
        "pa_agent.monitoring.headless.find_latest_successful_record_for_frame",
        lambda *_args, **_kwargs: previous,
    )
    monkeypatch.setattr(
        "pa_agent.monitoring.headless.compute_incremental_bar_delta",
        lambda *_args: SimpleNamespace(new_count=0),
    )

    found, new_count = monitor._incremental_context(
        SimpleNamespace(symbol="XAUUSD", timeframe="1h")
    )

    assert found is None
    assert new_count is None


def test_paused_runtime_publishes_status_without_polling_market(tmp_path) -> None:
    monitor, source, _audit, _service, _clock = _monitor(tmp_path)
    published = []

    class _Control:
        @staticmethod
        def read():
            return SimpleNamespace(desired_state="paused", error=None)

    class _Status:
        @staticmethod
        def write(payload):
            published.append(payload)

    class _OneIteration:
        checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks > 1

        @staticmethod
        def wait(_timeout):
            return True

    original_stop = monitor._stop
    monitor._control_store = _Control()
    monitor._status_store = _Status()
    monitor._stop = _OneIteration()
    try:
        monitor.run_forever()
    finally:
        monitor._stop = original_stop
        monitor.shutdown()

    assert len(published) >= 1
    assert published[0]["service_state"] == "paused"
    assert published[0]["applied_control_generation"] == 0
    assert published[0]["scheduler"]["unfinished"] == 0
    assert source.connected == 0


def test_pause_during_snapshot_preparation_blocks_submission(tmp_path) -> None:
    monitor, source, _audit, service, _clock = _monitor(tmp_path)

    result = monitor.poll_once(submission_allowed=lambda: False)
    monitor.shutdown()

    assert result.prepared == 1
    assert result.accepted == 0
    assert result.skipped == 1
    assert service.submitted == []
    assert source.connected == 1


def test_backend_control_applies_only_after_scheduler_is_idle(tmp_path) -> None:
    applied = []
    monitor, _source, _audit, service, _clock = _monitor(tmp_path)
    monitor._backend_switcher = lambda mode: applied.append(mode) or True
    service.wait_idle = lambda _timeout=None: False
    control = SimpleNamespace(
        desired_state="running",
        desired_backend="auto",
        generation=4,
        error=None,
    )

    paused, error = monitor._apply_control(control)
    assert paused is True
    assert error == "backend_switch_draining"
    assert applied == []
    assert monitor._applied_control_generation == 0

    service.wait_idle = lambda _timeout=None: True
    paused, error = monitor._apply_control(control)
    assert paused is False
    assert error is None
    assert applied == ["auto"]
    assert monitor._applied_control_generation == 4
    assert monitor.runtime_snapshot(paused=False)["mode"]["analysis_backend"] == "auto"


def test_fast_control_waits_for_idle_and_freezes_tier_into_new_codex_task(tmp_path) -> None:
    monitor, _source, _audit, service, _clock = _monitor(tmp_path)
    monitor._backend_switcher = lambda mode: mode == "codex_only"
    store = RuntimeControlStore(monitor._paths.runtime_control_path)
    backend = store.set_desired_backend("codex_only", actor="test")
    control = store.set_codex_fast_mode(True, expected_generation=backend.generation, actor="test")
    service.wait_idle = lambda _timeout=None: False
    paused, _error = monitor._apply_control(control, verify_latest=True)
    assert paused is True
    assert monitor.runtime_snapshot(paused=True)["applied_codex_fast_mode"] is False

    service.wait_idle = lambda _timeout=None: True
    paused, error = monitor._apply_control(control, verify_latest=True)
    assert (paused, error) == (False, None)
    assert monitor.runtime_snapshot(paused=False)["applied_codex_fast_mode"] is True
    result = monitor.poll_once()
    assert result.accepted == 1
    assert service.submitted[0].payload.analysis_backend_identity["service_tier"] == "fast"
    monitor.shutdown()


def test_fast_roundtrip_preserves_frozen_tasks_and_rejects_stale_control(tmp_path) -> None:
    monitor, _source, _audit, service, _clock = _monitor(tmp_path)
    monitor._backend_switcher = lambda mode: mode == "codex_only"
    store = RuntimeControlStore(monitor._paths.runtime_control_path)
    control = store.set_desired_backend("codex_only", actor="test")
    enabled = store.set_codex_fast_mode(True, expected_generation=control.generation, actor="test")
    assert monitor._apply_control(enabled, verify_latest=True) == (False, None)
    assert monitor.poll_once().accepted == 1
    original = service.submitted[0]
    assert original.payload.analysis_backend_identity["service_tier"] == "fast"

    disabled = store.set_codex_fast_mode(False, expected_generation=enabled.generation, actor="test")
    service.wait_idle = lambda _timeout=None: False
    assert monitor._apply_control(disabled, verify_latest=True)[0] is True
    assert monitor._applied_codex_fast_mode is True
    enabled_again = store.set_codex_fast_mode(True, expected_generation=disabled.generation, actor="test")
    service.wait_idle = lambda _timeout=None: True
    assert monitor._apply_control(disabled, verify_latest=True)[0] is True
    assert monitor._apply_control(enabled_again, verify_latest=True) == (False, None)
    assert monitor._applied_control_generation == enabled_again.generation
    assert original.payload.analysis_backend_identity["service_tier"] == "fast"
    assert monitor._submission_matches_applied_control() is True
    monitor.shutdown()


def test_applied_fast_off_then_on_changes_only_future_task_cohort(tmp_path):
    monitor, source, _audit, service, clock = _monitor(tmp_path)
    monitor._backend_switcher = lambda mode: mode == "codex_only"
    store = RuntimeControlStore(monitor._paths.runtime_control_path)
    control = store.set_desired_backend("codex_only", actor="test")
    for enabled in (True, False, True):
        control = store.set_codex_fast_mode(enabled, expected_generation=control.generation, actor="test")
        assert monitor._apply_control(control, verify_latest=True) == (False, None)
        assert monitor.poll_once().accepted == 1
        task = service.submitted[-1]
        assert task.payload.analysis_backend_identity.get("service_tier") == ("fast" if enabled else None)
        service.on_completion(_completion(task, status="completed", success=True, retryable=False))
        source.advance()
        clock.wall_ms += 3_600_000
        clock.monotonic_value += 60
    first, normal, fast_again = service.submitted
    assert first.cohort_id == fast_again.cohort_id
    assert first.cohort_id != normal.cohort_id
    assert len({task.task_id for task in service.submitted}) == 3
    assert first.payload.analysis_backend_identity["service_tier"] == "fast"
    monitor.shutdown()


def test_pending_profile_blocks_while_scheduler_is_active(tmp_path) -> None:
    applied: list[AnalysisProfile] = []
    monitor, _source, _audit, service, _clock = _monitor(
        tmp_path,
        profile_applier=lambda profile: applied.append(profile) or True,
    )
    service.wait_idle = lambda _timeout=None: False
    profile = AnalysisProfile(deepseek_model="deepseek-v4-pro")
    control = SimpleNamespace(
        desired_state="running",
        desired_backend="deepseek_only",
        analysis_profile=profile,
        profile_fingerprint=profile_fingerprint(profile),
        profile_generation=1,
        run_once_generation=0,
        generation=1,
        error=None,
    )

    paused, error = monitor._apply_control(control)

    assert paused is True
    assert error == "analysis_profile_pending"
    assert applied == []
    assert monitor.runtime_snapshot(paused=True)["applied_profile_generation"] == 0
    monitor.shutdown()


def test_stale_profile_generation_is_not_applied(tmp_path) -> None:
    applied: list[AnalysisProfile] = []
    monitor, _source, _audit, _service, _clock = _monitor(
        tmp_path,
        profile_applier=lambda profile: applied.append(profile) or True,
    )
    profile = AnalysisProfile(deepseek_model="deepseek-v4-pro")

    class _ControlStore:
        @staticmethod
        def read():
            return SimpleNamespace(
                desired_state="paused",
                desired_backend="deepseek_only",
                analysis_profile=profile,
                profile_fingerprint=profile_fingerprint(profile),
                profile_generation=2,
                run_once_generation=0,
                generation=2,
                error=None,
            )

    monitor._control_store = _ControlStore()
    control = SimpleNamespace(
        desired_state="paused",
        desired_backend="deepseek_only",
        analysis_profile=profile,
        profile_fingerprint=profile_fingerprint(profile),
        profile_generation=1,
        run_once_generation=0,
        generation=1,
        error=None,
    )

    paused, error = monitor._apply_control(control, verify_latest=True)

    assert paused is True
    assert error == "analysis_profile_pending"
    assert applied == []
    monitor.shutdown()


def test_run_once_submits_once_while_paused_and_projects_consumed_generation(
    tmp_path,
) -> None:
    monitor, _source, _audit, service, _clock = _monitor(
        tmp_path,
        run_on_startup=False,
    )
    profile = AnalysisProfile()
    control = SimpleNamespace(
        desired_state="paused",
        desired_backend="deepseek_only",
        analysis_profile=profile,
        profile_fingerprint=profile_fingerprint(profile),
        profile_generation=0,
        run_once_generation=1,
        generation=1,
        error=None,
    )

    paused, error = monitor._apply_control(control)
    assert paused is True
    assert error is None
    assert len(service.submitted) == 1
    assert monitor.runtime_snapshot(paused=True)["run_once_consumed_generation"] == 1

    paused, error = monitor._apply_control(control)
    assert paused is True
    assert error is None
    assert len(service.submitted) == 1
    monitor.shutdown()


def test_run_once_rejects_duplicate_and_inflight_requests(tmp_path) -> None:
    monitor, _source, _audit, service, _clock = _monitor(tmp_path)
    profile = AnalysisProfile()
    base = {
        "desired_state": "paused",
        "desired_backend": "deepseek_only",
        "analysis_profile": profile,
        "profile_fingerprint": profile_fingerprint(profile),
        "profile_generation": 0,
        "error": None,
    }
    first = SimpleNamespace(**base, run_once_generation=1, generation=1)
    monitor._apply_control(first)
    before = len(service.submitted)

    service.wait_idle = lambda _timeout=None: False
    pending = SimpleNamespace(**base, run_once_generation=2, generation=2)
    paused, error = monitor._apply_control(pending)

    assert paused is True
    assert error == "run_once_in_flight"
    assert len(service.submitted) == before
    monitor.shutdown()


def test_run_once_allows_at_most_one_submission_across_assets(tmp_path) -> None:
    settings = _settings()
    second = settings.monitoring.assets[0].model_copy(update={"symbol": "XAGUSD"})
    settings.monitoring.assets.append(second)
    monitor, _source, _audit, service, _clock = _monitor(
        tmp_path,
        settings=settings,
    )
    profile = AnalysisProfile()
    control = SimpleNamespace(
        desired_state="paused",
        desired_backend="deepseek_only",
        analysis_profile=profile,
        profile_fingerprint=profile_fingerprint(profile),
        profile_generation=0,
        run_once_generation=1,
        generation=1,
        error=None,
    )

    paused, error = monitor._apply_control(control)

    assert paused is True
    assert error is None
    assert len(service.submitted) == 1
    monitor.shutdown()


def test_run_once_is_not_consumed_when_scheduler_rejects_submission(tmp_path) -> None:
    monitor, _source, _audit, service, _clock = _monitor(tmp_path)
    service.submit = lambda task: SubmissionResult(
        "queue_full",
        task.task_id,
        task.asset_key,
    )
    profile = AnalysisProfile()
    control = SimpleNamespace(
        desired_state="paused",
        desired_backend="deepseek_only",
        analysis_profile=profile,
        profile_fingerprint=profile_fingerprint(profile),
        profile_generation=0,
        run_once_generation=1,
        generation=1,
        error=None,
    )

    paused, error = monitor._apply_control(control)

    assert paused is True
    assert error == "run_once_not_submitted"
    assert monitor.runtime_snapshot(paused=True)["run_once_consumed_generation"] == 0
    monitor.shutdown()


def test_run_once_is_not_consumed_when_poll_raises(tmp_path) -> None:
    monitor, _source, _audit, _service, _clock = _monitor(tmp_path)
    monitor.poll_once = lambda **_kwargs: (_ for _ in ()).throw(
        RuntimeError("poll failed")
    )
    profile = AnalysisProfile()
    control = SimpleNamespace(
        desired_state="paused",
        desired_backend="deepseek_only",
        analysis_profile=profile,
        profile_fingerprint=profile_fingerprint(profile),
        profile_generation=0,
        run_once_generation=1,
        generation=1,
        error=None,
    )

    paused, error = monitor._apply_control(control)

    assert paused is True
    assert error == "run_once_failed"
    assert monitor.runtime_snapshot(paused=True)["run_once_consumed_generation"] == 0
    monitor.shutdown()


def test_run_once_consumed_generation_survives_service_restart(tmp_path) -> None:
    profile = AnalysisProfile()
    control = SimpleNamespace(
        desired_state="paused",
        desired_backend="deepseek_only",
        analysis_profile=profile,
        profile_fingerprint=profile_fingerprint(profile),
        profile_generation=0,
        run_once_generation=1,
        generation=1,
        error=None,
    )
    first, _source, _audit, first_service, _clock = _monitor(tmp_path)
    first._apply_control(control)
    assert len(first_service.submitted) == 1
    first.shutdown()

    restarted, _source, _audit, restarted_service, _clock = _monitor(tmp_path)
    paused, error = restarted._apply_control(control)

    assert paused is True
    assert error is None
    assert restarted.runtime_snapshot(paused=True)[
        "run_once_consumed_generation"
    ] == 1
    assert restarted_service.submitted == []
    restarted.shutdown()


def test_unavailable_backend_control_fails_closed(tmp_path) -> None:
    monitor, _source, _audit, _service, _clock = _monitor(tmp_path)
    monitor._backend_switcher = lambda _mode: False
    control = SimpleNamespace(
        desired_state="running",
        desired_backend="codex_only",
        generation=2,
        error=None,
    )

    paused, error = monitor._apply_control(control)

    assert paused is True
    assert error == "backend_unavailable"
    assert monitor._applied_control_generation == 0
    assert monitor.runtime_snapshot(paused=True)["mode"]["analysis_backend"] == "deepseek_only"
