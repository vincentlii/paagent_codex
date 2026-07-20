from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from pa_agent.config.settings import Settings
from pa_agent.monitoring.analysis_runner import (
    FrozenAnalysisPayload,
    IndependentAnalysisHandler,
)
from pa_agent.monitoring.audit import SchedulerAuditStore
from pa_agent.monitoring.scheduler import AnalysisTask
from pa_agent.monitoring.service import MultiAssetAnalysisService


def _task(symbol: str, index: int = 0) -> AnalysisTask:
    frame = SimpleNamespace(symbol=symbol, timeframe="1h")
    return AnalysisTask.create(
        source="tradingview",
        venue="OANDA",
        symbol=symbol,
        timeframe="1h",
        snapshot_hash=f"{index + 1:064x}",
        cohort_id="b" * 64,
        payload=FrozenAnalysisPayload(frame=frame),
    )


def test_handler_builds_a_fresh_orchestrator_for_every_asset() -> None:
    built: list[object] = []

    class _Orchestrator:
        def submit(self, frame, _token, _event, **_kwargs):
            return SimpleNamespace(
                meta=SimpleNamespace(run_id=f"run-{frame.symbol}"),
                stage2_decision={"symbol": frame.symbol},
            )

    def factory(_task):
        orchestrator = _Orchestrator()
        built.append(orchestrator)
        return orchestrator

    handler = IndependentAnalysisHandler(factory)
    service = MultiAssetAnalysisService(
        handler,
        max_concurrency=2,
        max_pending_jobs=4,
    )

    assert service.submit(_task("XAUUSD")).accepted
    assert service.submit(_task("EURUSD")).accepted
    assert service.wait_idle(timeout=2.0)
    service.shutdown()

    assert len(built) == 2
    assert built[0] is not built[1]


def test_handler_rejects_cross_asset_frame_before_model_call() -> None:
    factory_called = False

    def factory(_task):
        nonlocal factory_called
        factory_called = True
        return object()

    handler = IndependentAnalysisHandler(factory)
    task = _task("XAUUSD")
    task = AnalysisTask.create(
        source=task.source,
        venue=task.venue,
        symbol=task.symbol,
        timeframe=task.timeframe,
        snapshot_hash=task.snapshot_hash,
        cohort_id=task.cohort_id,
        payload=FrozenAnalysisPayload(frame=SimpleNamespace(symbol="EURUSD", timeframe="1h")),
    )

    with pytest.raises(ValueError, match="symbol"):
        handler(task, SimpleNamespace())

    assert factory_called is False


def test_handler_preserves_monthly_timeframe_semantics() -> None:
    handled: list[str] = []

    class _Orchestrator:
        def submit(self, frame, _token, _event, **_kwargs):
            handled.append(frame.timeframe)
            return object()

    task = AnalysisTask.create(
        source="tradingview",
        symbol="XAUUSD",
        timeframe="1M",
        snapshot_hash="1" * 64,
        cohort_id="2" * 64,
        payload=FrozenAnalysisPayload(
            frame=SimpleNamespace(symbol="XAUUSD", timeframe="1M")
        ),
    )

    IndependentAnalysisHandler(lambda _task: _Orchestrator())(
        task,
        SimpleNamespace(),
    )

    assert task.timeframe == "1M"
    assert handled == ["1M"]


def test_twenty_assets_are_audited_once_with_terminal_status(tmp_path) -> None:
    class _Orchestrator:
        def submit(self, frame, _token, _event, **_kwargs):
            return SimpleNamespace(
                meta=SimpleNamespace(run_id=f"run-{frame.symbol}"),
                stage2_decision={"symbol": frame.symbol},
            )

    audit_path = tmp_path / "monitoring.sqlite3"
    service = MultiAssetAnalysisService(
        IndependentAnalysisHandler(lambda _task: _Orchestrator()),
        max_concurrency=4,
        max_pending_jobs=20,
        audit_store=SchedulerAuditStore(audit_path),
    )

    submissions = [service.submit(_task(f"ASSET-{index}")) for index in range(20)]
    assert all(result.accepted for result in submissions)
    assert service.wait_idle(timeout=5.0)
    service.shutdown()

    with sqlite3.connect(audit_path) as connection:
        rows = connection.execute(
            "SELECT task_id, submission_status, completion_status, run_id, "
            "queue_latency_ms, execution_latency_ms FROM monitoring_jobs"
        ).fetchall()

    assert len(rows) == 20
    assert len({row[0] for row in rows}) == 20
    assert {row[1] for row in rows} == {"accepted"}
    assert {row[2] for row in rows} == {"completed"}
    assert all(row[3] for row in rows)
    assert all(row[4] >= 0 for row in rows)
    assert all(row[5] >= 0 for row in rows)


def test_production_factory_does_not_share_mutable_model_state(
    tmp_path,
    monkeypatch,
) -> None:
    from pa_agent.monitoring import production

    clients: list[object] = []
    assemblers: list[object] = []
    built: list[SimpleNamespace] = []

    def create_client(_provider, **_kwargs):
        client = object()
        clients.append(client)
        return client

    def create_assembler(**_kwargs):
        assembler = object()
        assemblers.append(assembler)
        return assembler

    def create_orchestrator(**kwargs):
        value = SimpleNamespace(**kwargs)
        built.append(value)
        return value

    monkeypatch.setattr(production, "create_ai_client", create_client)
    monkeypatch.setattr(production, "ExperienceReader", lambda **_kwargs: object())
    monkeypatch.setattr(production, "PromptAssembler", create_assembler)
    monkeypatch.setattr(production, "JsonValidator", lambda _settings: object())
    monkeypatch.setattr(production, "TwoStageOrchestrator", create_orchestrator)

    factory = production.ProductionOrchestratorFactory(
        settings=Settings(),
        pending_writer=object(),
        prompt_dir=tmp_path,
        experience_dir=tmp_path,
    )
    first = factory(_task("XAUUSD"))
    second = factory(_task("EURUSD"))

    assert first is not second
    assert clients[0] is not clients[1]
    assert assemblers[0] is not assemblers[1]
    assert built[0].settings is not built[1].settings


def test_scheduler_audit_failure_is_visible_in_service_metrics() -> None:
    class _BrokenAudit:
        def record_submission(self, _task, _result):
            return False

        def record_completion(self, _completion):
            return False

    class _Orchestrator:
        def submit(self, *_args, **_kwargs):
            return SimpleNamespace(meta=SimpleNamespace(run_id="run-audit-failed"))

    service = MultiAssetAnalysisService(
        IndependentAnalysisHandler(lambda _task: _Orchestrator()),
        audit_store=_BrokenAudit(),
    )
    assert service.submit(_task("XAUUSD")).accepted
    assert service.wait_idle(timeout=2.0)
    service.shutdown()

    assert service.snapshot()["audit_failures"] == 2
