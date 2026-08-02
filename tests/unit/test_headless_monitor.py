from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from pa_agent.config.paths import resolve_runtime_paths
from pa_agent.config.settings import MonitoringSettings, Settings
from pa_agent.data.base import KlineBar
from pa_agent.data.contracts import ResolvedInstrumentIdentity
from pa_agent.data.higher_timeframe import disabled_higher_timeframe_context
from pa_agent.monitoring.analysis_runner import AnalysisRunResult
from pa_agent.monitoring.headless import HeadlessMonitor
from pa_agent.monitoring.scheduler import JobCompletion, SubmissionResult


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
    source=None,
    audit=None,
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
        settings=_settings(run_on_startup=run_on_startup),
        paths=resolve_runtime_paths(
            project_root=tmp_path,
            environ={"PA_AGENT_HOME": str(tmp_path / "state")},
        ),
        scheduler_audit=audit,
        source_factory=lambda _asset: source,
        service_factory=service_factory,
        analysis_fingerprint="d" * 64,
        monotonic=clock.monotonic,
        wall_time_ms=clock.now_ms,
    )
    return monitor, source, audit, holder["service"], clock


def _completion(task, *, status: str, success: bool, retryable: bool):
    result = AnalysisRunResult(
        record=SimpleNamespace(meta=SimpleNamespace(run_id=f"run-{status}")),
        analysis_status=status,
        analysis_success=success,
        retryable=retryable,
        record_persisted=success,
        audit_ok=True,
    )
    return JobCompletion(task=task, status="completed", result=result)


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


def test_audit_read_failure_does_not_stop_current_analysis(tmp_path) -> None:
    monitor, _source, _audit, service, _clock = _monitor(
        tmp_path,
        audit=_Audit(fail_reads=True),
    )

    result = monitor.poll_once()

    assert result.accepted == 1
    assert len(service.submitted) == 1


def test_shutdown_disconnects_sources_and_closes_service(tmp_path) -> None:
    monitor, source, _audit, service, _clock = _monitor(tmp_path)
    monitor.poll_once()

    monitor.shutdown()

    assert source.disconnected == 1
    assert service.closed is True


def test_subscribe_failure_disconnects_the_partially_connected_source(tmp_path) -> None:
    source = _SubscribeFailSource()
    monitor, _source, _audit, service, _clock = _monitor(tmp_path, source=source)

    result = monitor.poll_once()

    assert result.failed == 1
    assert source.connected == 1
    assert source.disconnected == 1
    assert service.submitted == []


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
        "pa_agent.monitoring.headless.find_latest_successful_record",
        lambda **_kwargs: previous,
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
