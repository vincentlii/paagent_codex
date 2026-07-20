from __future__ import annotations

import threading
import time

import pytest

from pa_agent.monitoring.scheduler import (
    AnalysisTask,
    MultiAssetAnalysisScheduler,
)


def _task(symbol: str, index: int = 0) -> AnalysisTask:
    return AnalysisTask.create(
        source="tradingview",
        venue="OANDA",
        symbol=symbol,
        timeframe="1h",
        snapshot_hash=f"{index + 1:064x}",
        cohort_id="a" * 64,
        payload={"symbol": symbol, "index": index},
    )


def test_twenty_assets_are_processed_once_with_bounded_parallelism() -> None:
    lock = threading.Lock()
    active = 0
    peak_active = 0
    handled: list[tuple[str, str]] = []

    def handler(task, cancel_token):
        nonlocal active, peak_active
        assert cancel_token.is_set() is False
        with lock:
            active += 1
            peak_active = max(peak_active, active)
        time.sleep(0.01)
        with lock:
            handled.append((task.asset_key, task.task_id))
            active -= 1
        return task.payload

    scheduler = MultiAssetAnalysisScheduler(
        handler,
        max_concurrency=3,
        max_pending_jobs=20,
        max_pending_per_asset=2,
    )
    submissions = [scheduler.submit(_task(f"ASSET-{index}")) for index in range(20)]

    assert all(item.accepted for item in submissions)
    assert scheduler.wait_idle(timeout=5.0)
    scheduler.shutdown()

    assert len(handled) == 20
    assert len({task_id for _, task_id in handled}) == 20
    assert peak_active <= 3
    assert scheduler.snapshot()["completed"] == 20


def test_same_asset_is_serial_even_when_global_pool_is_parallel() -> None:
    lock = threading.Lock()
    active_for_asset = 0
    peak_for_asset = 0
    order: list[int] = []

    def handler(task, _cancel_token):
        nonlocal active_for_asset, peak_for_asset
        with lock:
            active_for_asset += 1
            peak_for_asset = max(peak_for_asset, active_for_asset)
        time.sleep(0.005)
        with lock:
            order.append(task.payload["index"])
            active_for_asset -= 1
        return task.payload

    scheduler = MultiAssetAnalysisScheduler(
        handler,
        max_concurrency=4,
        max_pending_jobs=10,
        max_pending_per_asset=5,
    )
    for index in range(5):
        assert scheduler.submit(_task("XAUUSD", index)).accepted

    assert scheduler.wait_idle(timeout=5.0)
    scheduler.shutdown()

    assert peak_for_asset == 1
    assert order == [0, 1, 2, 3, 4]


def test_duplicate_and_queue_full_are_explicit_rejections() -> None:
    release = threading.Event()

    def handler(task, _cancel_token):
        release.wait(timeout=2.0)
        return task.task_id

    scheduler = MultiAssetAnalysisScheduler(
        handler,
        max_concurrency=1,
        max_pending_jobs=2,
        max_pending_per_asset=2,
    )
    first = _task("XAUUSD")
    assert scheduler.submit(first).status == "accepted"
    assert scheduler.submit(first).status == "duplicate"
    assert scheduler.submit(_task("EURUSD")).status == "accepted"
    assert scheduler.submit(_task("GBPUSD")).status == "queue_full"

    release.set()
    assert scheduler.wait_idle(timeout=5.0)
    scheduler.shutdown()


def test_failure_is_audited_and_does_not_block_next_snapshot() -> None:
    completions = []

    def handler(task, _cancel_token):
        if task.payload["index"] == 0:
            raise ValueError("bad snapshot")
        return "ok"

    scheduler = MultiAssetAnalysisScheduler(
        handler,
        max_concurrency=2,
        max_pending_jobs=4,
        max_pending_per_asset=2,
        on_completion=completions.append,
    )
    assert scheduler.submit(_task("BTCUSD", 0)).accepted
    assert scheduler.submit(_task("BTCUSD", 1)).accepted

    assert scheduler.wait_idle(timeout=5.0)
    scheduler.shutdown()

    assert [item.status for item in completions] == ["failed", "completed"]
    assert completions[0].error_type == "ValueError"
    assert scheduler.snapshot()["failed"] == 1


def test_cancel_asset_cancels_running_and_queued_work() -> None:
    started = threading.Event()
    completions = []

    def handler(_task, cancel_token):
        started.set()
        cancel_token.wait(timeout=2.0)
        return "stopped"

    scheduler = MultiAssetAnalysisScheduler(
        handler,
        max_concurrency=1,
        max_pending_jobs=4,
        max_pending_per_asset=3,
        on_completion=completions.append,
    )
    assert scheduler.submit(_task("ETHUSD", 0)).accepted
    assert scheduler.submit(_task("ETHUSD", 1)).accepted
    assert started.wait(timeout=1.0)

    cancelled = scheduler.cancel_asset(_task("ETHUSD").asset_key)

    assert cancelled == 2
    assert scheduler.wait_idle(timeout=5.0)
    scheduler.shutdown()
    assert {item.status for item in completions} == {"cancelled"}


def test_shutdown_rejects_new_submissions() -> None:
    scheduler = MultiAssetAnalysisScheduler(lambda task, _token: task)
    scheduler.shutdown()

    result = scheduler.submit(_task("XAUUSD"))

    assert result.accepted is False
    assert result.status == "scheduler_closed"


def test_queue_and_execution_latency_are_measured_separately() -> None:
    release = threading.Event()
    started = threading.Event()
    completions = []

    def handler(task, _cancel_token):
        if task.symbol == "FIRST":
            started.set()
            release.wait(timeout=2.0)
        return task.symbol

    scheduler = MultiAssetAnalysisScheduler(
        handler,
        max_concurrency=1,
        max_pending_jobs=2,
        on_completion=completions.append,
    )
    assert scheduler.submit(_task("FIRST")).accepted
    assert started.wait(timeout=1.0)
    assert scheduler.submit(_task("SECOND")).accepted
    time.sleep(0.08)
    release.set()

    assert scheduler.wait_idle(timeout=5.0)
    scheduler.shutdown()

    second = next(item for item in completions if item.task.symbol == "SECOND")
    assert second.queue_latency_ms >= 40.0
    assert second.execution_latency_ms < second.queue_latency_ms


def test_shutdown_can_be_awaited_after_initial_nonblocking_request() -> None:
    started = threading.Event()
    release = threading.Event()

    def handler(_task, _cancel_token):
        started.set()
        release.wait(timeout=2.0)

    scheduler = MultiAssetAnalysisScheduler(handler, max_concurrency=1)
    assert scheduler.submit(_task("XAUUSD")).accepted
    assert started.wait(timeout=1.0)

    scheduler.shutdown(wait=False)
    release_timer = threading.Timer(0.05, release.set)
    release_timer.start()
    started_wait = time.monotonic()
    scheduler.shutdown(wait=True)
    waited = time.monotonic() - started_wait
    release_timer.join(timeout=1.0)

    assert waited >= 0.04
    assert scheduler.wait_idle(timeout=1.0)
    assert scheduler.snapshot()["cancelled"] == 1


def test_minute_and_monthly_timeframes_have_distinct_task_identity() -> None:
    minute = AnalysisTask.create(
        source="tradingview",
        venue="OANDA",
        symbol="XAUUSD",
        timeframe="1m",
        snapshot_hash="1" * 64,
        cohort_id="2" * 64,
        payload={"timeframe": "1m"},
    )
    monthly = AnalysisTask.create(
        source="tradingview",
        venue="OANDA",
        symbol="XAUUSD",
        timeframe="1M",
        snapshot_hash=minute.snapshot_hash,
        cohort_id=minute.cohort_id,
        payload={"timeframe": "1M"},
    )

    assert minute.timeframe == "1m"
    assert monthly.timeframe == "1M"
    assert monthly.asset_key != minute.asset_key
    assert monthly.task_id != minute.task_id


def test_analysis_task_cannot_be_constructed_with_forged_identity() -> None:
    with pytest.raises(ValueError, match="task_id"):
        AnalysisTask(
            task_id="0" * 64,
            asset_key="tradingview|OANDA|XAUUSD|1h",
            source="tradingview",
            venue="OANDA",
            symbol="XAUUSD",
            timeframe="1h",
            snapshot_hash="1" * 64,
            cohort_id="2" * 64,
            payload={},
            created_at_ms=1,
        )
