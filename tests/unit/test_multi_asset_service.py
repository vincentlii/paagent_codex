from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from pa_agent.config.settings import Settings
from pa_agent.monitoring.analysis_runner import (
    AnalysisRunResult,
    FrozenAnalysisPayload,
    IndependentAnalysisHandler,
)
from pa_agent.monitoring.audit import SchedulerAuditStore
from pa_agent.monitoring.scheduler import AnalysisTask
from pa_agent.monitoring.service import MultiAssetAnalysisService
from pa_agent.util.threading import OrchestratorEvent


def _task(
    symbol: str,
    index: int = 0,
    *,
    backend_mode: str = "deepseek_only",
) -> AnalysisTask:
    frame = SimpleNamespace(symbol=symbol, timeframe="1h")
    return AnalysisTask.create(
        source="tradingview",
        venue="OANDA",
        symbol=symbol,
        timeframe="1h",
        snapshot_hash=f"{index + 1:064x}",
        cohort_id="b" * 64,
        payload=FrozenAnalysisPayload(
            frame=frame,
            analysis_backend_mode=backend_mode,
        ),
    )


def test_handler_builds_a_fresh_orchestrator_for_every_asset() -> None:
    built: list[object] = []

    class _Orchestrator:
        def submit(self, frame, _token, on_event, **_kwargs):
            record = SimpleNamespace(
                meta=SimpleNamespace(run_id=f"run-{frame.symbol}"),
                stage2_decision={"symbol": frame.symbol},
                run_outcome={"status": "completed", "official_path": "pa_only"},
            )
            on_event(OrchestratorEvent.RecordSaved)
            return record

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


@pytest.mark.parametrize("status", ["incomplete", "unavailable_provider", "internal_error"])
@pytest.mark.parametrize("retryable", [True, False])
def test_typed_provider_retry_authority_survives_status_projection(status, retryable):
    class Orchestrator:
        def submit(self, frame, token, on_event, **kwargs):
            on_event(OrchestratorEvent.RecordSaved)
            return SimpleNamespace(run_outcome={"status": status}, exception={
                "type": "provider_error", "retryable": retryable,
                "failure_kind": "unavailable" if retryable else "invalid_request",
            })

    result = IndependentAnalysisHandler(lambda task: Orchestrator())(_task("BTCUSDT"), None)
    assert result.retryable is retryable


def test_handler_preserves_monthly_timeframe_semantics() -> None:
    handled: list[str] = []

    class _Orchestrator:
        def submit(self, frame, _token, on_event, **_kwargs):
            handled.append(frame.timeframe)
            on_event(OrchestratorEvent.RecordSaved)
            return SimpleNamespace(
                run_outcome={"status": "completed", "official_path": "pa_only"}
            )

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
        def submit(self, frame, _token, on_event, **_kwargs):
            record = SimpleNamespace(
                meta=SimpleNamespace(run_id=f"run-{frame.symbol}"),
                stage2_decision={"symbol": frame.symbol},
                run_outcome={"status": "completed", "official_path": "pa_only"},
            )
            on_event(OrchestratorEvent.RecordSaved)
            return record

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
            "queue_latency_ms, execution_latency_ms, analysis_status, "
            "analysis_success FROM monitoring_jobs"
        ).fetchall()

    assert len(rows) == 20
    assert len({row[0] for row in rows}) == 20
    assert {row[1] for row in rows} == {"accepted"}
    assert {row[2] for row in rows} == {"completed"}
    assert all(row[3] for row in rows)
    assert all(row[4] >= 0 for row in rows)
    assert all(row[5] >= 0 for row in rows)
    assert {row[6] for row in rows} == {"completed"}
    assert {row[7] for row in rows} == {1}


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
    validator_paths = []

    def create_validator(_settings, *, prompt_dir=None, take_profit_policy=None):
        validator_paths.append(prompt_dir)
        return object()

    monkeypatch.setattr(production, "JsonValidator", create_validator)
    monkeypatch.setattr(production, "TwoStageOrchestrator", create_orchestrator)

    factory = production.ProductionOrchestratorFactory(
        settings=Settings(provider={"api_key": "test-key"}),
        pending_writer=object(),
        prompt_dir=tmp_path,
        experience_dir=tmp_path,
    )
    first = factory(_task("XAUUSD"))
    second = factory(_task("EURUSD"))

    assert first is not second
    assert clients[0] is not clients[1]
    assert assemblers[0] is not assemblers[1]
    assert built[0].analysis_backend is not built[1].analysis_backend
    assert built[0].analysis_backend.client is clients[0]
    assert built[1].analysis_backend.client is clients[1]
    assert built[0].settings is not built[1].settings
    assert validator_paths == [tmp_path, tmp_path]


def test_production_factory_applies_profile_only_to_future_direct_clients(
    tmp_path,
    monkeypatch,
) -> None:
    from pa_agent.monitoring import production
    from pa_agent.operations.analysis_profile import AnalysisProfile

    settings = Settings()
    settings.provider.api_key = "secret-token"
    settings.provider.base_url = "https://provider.example/v1"
    settings.general.decision_stance = "conservative"
    providers: list[object] = []
    built: list[SimpleNamespace] = []

    def create_client(provider, **_kwargs):
        providers.append(provider)
        return SimpleNamespace(provider=provider)

    monkeypatch.setattr(production, "create_ai_client", create_client)
    monkeypatch.setattr(production, "ExperienceReader", lambda **_kwargs: object())
    monkeypatch.setattr(production, "PromptAssembler", lambda **_kwargs: object())
    monkeypatch.setattr(production, "JsonValidator", lambda *_args, **_kwargs: object())

    class _Orchestrator:
        def __init__(self, **kwargs):
            built.append(SimpleNamespace(**kwargs))

    monkeypatch.setattr(production, "TwoStageOrchestrator", _Orchestrator)
    factory = production.ProductionOrchestratorFactory(
        settings=settings,
        pending_writer=object(),
        prompt_dir=tmp_path,
        experience_dir=tmp_path,
    )

    factory(_task("XAUUSD"))
    profile = AnalysisProfile(
        deepseek_model="deepseek-v4-pro",
        deepseek_thinking=False,
        deepseek_reasoning_effort="low",
        decision_stance="aggressive",
    )
    assert factory.apply_analysis_profile(profile) is True
    factory(_task("EURUSD", 1))
    from dataclasses import replace
    from pa_agent.operations.analysis_profile import profile_fingerprint
    legacy_identity = AnalysisProfile().model_dump(mode="json")
    legacy_identity.pop("symbols")
    legacy_identity.pop("decision_stance")
    legacy_identity["profile_fingerprint"] = profile_fingerprint(legacy_identity)
    legacy = _task("XAUUSD", 2)
    factory(replace(legacy, payload=replace(legacy.payload, analysis_profile_identity=legacy_identity)))

    assert providers[0].model == "deepseek-v4-flash"
    assert providers[1].model == "deepseek-v4-pro"
    assert providers[1].thinking is False
    assert providers[1].reasoning_effort == "low"
    assert built[0].settings.provider.model == "deepseek-v4-flash"
    assert built[1].settings.provider.model == "deepseek-v4-pro"
    assert built[1].settings.prompt.vpa_mode == "shadow"
    assert built[1].settings.general.higher_timeframe_enabled is True
    assert built[1].settings.general.higher_timeframe_override == "4h"
    assert built[1].settings.general.higher_timeframe_bar_count == 60
    assert built[1].settings.general.decision_stance == "aggressive"
    assert built[0].settings.general.decision_stance == "balanced"
    assert built[2].settings.general.decision_stance == "conservative"
    assert providers[2].model == "deepseek-v4-flash"
    assert built[2].profile_identity == legacy_identity
    assert settings.provider.api_key == "secret-token"
    assert settings.provider.base_url == "https://provider.example/v1"
    assert settings.provider.model == "deepseek-v4-flash"


def test_production_factory_honors_backend_frozen_on_task(
    tmp_path,
    monkeypatch,
) -> None:
    from pa_agent.monitoring import production
    from pa_agent.orchestrator.backend_fallback import FullChainFallbackOrchestrator

    direct_clients: list[object] = []

    def create_client(*_args, **_kwargs):
        client = object()
        direct_clients.append(client)
        return client

    monkeypatch.setattr(production, "create_ai_client", create_client)
    monkeypatch.setattr(production, "ExperienceReader", lambda **_kwargs: object())
    monkeypatch.setattr(production, "PromptAssembler", lambda **_kwargs: object())
    monkeypatch.setattr(production, "JsonValidator", lambda *_args, **_kwargs: object())
    built = []

    class _Orchestrator:
        def __init__(self, **kwargs):
            self.analysis_backend = kwargs["analysis_backend"]
            built.append(self)

        def submit(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(production, "TwoStageOrchestrator", _Orchestrator)
    codex_backends = []

    def codex_factory(_task, _settings):
        backend = SimpleNamespace(backend_id="codex_agent")
        codex_backends.append(backend)
        return backend

    factory = production.ProductionOrchestratorFactory(
        settings=Settings(provider={"api_key": "secret-token"}),
        pending_writer=object(),
        prompt_dir=tmp_path,
        experience_dir=tmp_path,
        codex_backend_factory=codex_factory,
    )

    codex_only = factory(_task("XAUUSD", backend_mode="codex_only"))
    automatic = factory(_task("EURUSD", 1, backend_mode="auto"))

    assert codex_only.analysis_backend is codex_backends[0]
    assert isinstance(automatic, FullChainFallbackOrchestrator)
    # codex_only and the auto primary path require no Direct credential. Only
    # the explicit whole-chain cold fallback owns one mutable Direct client.
    assert len(direct_clients) == 1
    assert factory.activate_backend_mode("auto") is True


def test_production_factory_without_direct_key_exposes_only_codex(tmp_path) -> None:
    from pa_agent.monitoring.production import ProductionOrchestratorFactory

    factory = ProductionOrchestratorFactory(
        settings=Settings(),
        pending_writer=object(),
        prompt_dir=tmp_path,
        experience_dir=tmp_path,
        codex_backend_factory=lambda *_args: object(),
    )

    assert factory.activate_backend_mode("codex_only") is True
    assert factory.activate_backend_mode("deepseek_only") is False
    assert factory.activate_backend_mode("auto") is False


def test_scheduler_audit_failure_prevents_execution() -> None:
    class _BrokenAudit:
        def record_submission(self, _task, _result):
            return False

        def record_completion(self, _completion):
            return False

    class _Orchestrator:
        def submit(self, _frame, _token, on_event, **_kwargs):
            on_event(OrchestratorEvent.RecordSaved)
            return SimpleNamespace(
                meta=SimpleNamespace(run_id="run-audit-failed"),
                run_outcome={"status": "completed", "official_path": "pa_only"},
            )

    calls = []
    service = MultiAssetAnalysisService(
        IndependentAnalysisHandler(lambda _task: calls.append(1) or _Orchestrator()),
        audit_store=_BrokenAudit(),
    )
    assert service.submit(_task("XAUUSD")).status == "audit_unavailable"
    assert service.wait_idle(timeout=2.0)
    service.shutdown()

    assert service.snapshot()["audit_failures"] == 1
    assert calls == []


def test_completion_audit_failure_blocks_further_tasks() -> None:
    class _CompletionAuditFailure:
        def record_submission(self, _task, _result):
            return True

        def record_completion(self, _completion):
            return False

    completions = []
    service = MultiAssetAnalysisService(
        lambda task, _cancel: task.task_id,
        audit_store=_CompletionAuditFailure(),
        on_completion=completions.append,
    )

    assert service.submit(_task("XAUUSD")).accepted
    assert service.wait_idle(timeout=2.0)
    assert completions[0].status == "failed"
    assert completions[0].error_type == "SchedulerAuditUnavailable"
    assert service.submit(_task("EURUSD")).status == "audit_unavailable"
    service.shutdown()


def test_handler_classifies_provider_failure_without_calling_it_success() -> None:
    class _Orchestrator:
        def submit(self, *_args, **_kwargs):
            return SimpleNamespace(
                meta=SimpleNamespace(run_id="run-provider-failed"),
                run_outcome={
                    "status": "unavailable_provider",
                    "official_path": "none",
                },
            )

    result = IndependentAnalysisHandler(lambda _task: _Orchestrator())(
        _task("XAUUSD"),
        SimpleNamespace(),
    )

    assert isinstance(result, AnalysisRunResult)
    assert result.analysis_status == "unavailable_provider"
    assert result.analysis_success is False
    assert result.retryable is True


def test_handler_does_not_retry_exhausted_model_contract_failure() -> None:
    class _Orchestrator:
        def submit(self, *_args, **_kwargs):
            return SimpleNamespace(
                meta=SimpleNamespace(run_id="run-invalid-model-output"),
                run_outcome={
                    "status": "invalid_model_output",
                    "official_path": "none",
                },
            )

    result = IndependentAnalysisHandler(lambda _task: _Orchestrator())(
        _task("XAUUSD"),
        SimpleNamespace(),
    )

    assert result.analysis_status == "invalid_model_output"
    assert result.analysis_success is False
    assert result.retryable is False


def test_handler_does_not_mark_analysis_success_when_record_audit_failed() -> None:
    class _Orchestrator:
        def submit(self, _frame, _token, on_event, **_kwargs):
            on_event(OrchestratorEvent.RecordAuditFailed)
            on_event(OrchestratorEvent.RecordSaved)
            return SimpleNamespace(
                meta=SimpleNamespace(run_id="run-record-audit-failed"),
                run_outcome={"status": "completed", "official_path": "pa_only"},
            )

    result = IndependentAnalysisHandler(lambda _task: _Orchestrator())(
        _task("XAUUSD"),
        SimpleNamespace(),
    )

    assert result.analysis_success is False
    assert result.audit_ok is False
    assert result.retryable is True


def test_logical_analysis_failure_can_retry_and_success_is_restart_deduped(tmp_path) -> None:
    outcomes = iter(("unavailable_provider", "degraded_pa_only"))

    class _Orchestrator:
        def submit(self, _frame, _token, on_event, **_kwargs):
            status = next(outcomes)
            if status == "degraded_pa_only":
                on_event(OrchestratorEvent.RecordSaved)
            return SimpleNamespace(
                meta=SimpleNamespace(run_id=f"run-{status}"),
                run_outcome={
                    "status": status,
                    "official_path": "none" if status == "unavailable_provider" else "pa_only",
                },
            )

    task = _task("XAUUSD")
    store = SchedulerAuditStore(tmp_path / "monitoring.sqlite3")
    service = MultiAssetAnalysisService(
        IndependentAnalysisHandler(lambda _task: _Orchestrator()),
        audit_store=store,
    )

    assert service.submit(task).accepted
    assert service.wait_idle(timeout=2.0)
    assert store.was_successful(task.task_id) is False
    assert service.submit(task).accepted
    assert service.wait_idle(timeout=2.0)
    assert store.was_successful(task.task_id) is True
    assert service.submit(task).status == "duplicate"
    service.shutdown()

    with sqlite3.connect(store.db_path) as connection:
        row = connection.execute(
            "SELECT submission_count, analysis_status, analysis_success "
            "FROM monitoring_jobs WHERE task_id = ?",
            (task.task_id,),
        ).fetchone()
    assert row == (3, "degraded_pa_only", 1)
    assert store.list_successful_jobs() == [
        {"task_id": task.task_id, "run_id": "run-degraded_pa_only"}
    ]


def test_scheduler_success_lookup_failure_is_fail_closed(tmp_path, monkeypatch) -> None:
    store = SchedulerAuditStore(tmp_path / "monitoring.sqlite3")

    def broken_connect():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_connect", broken_connect)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        store.was_successful("missing-task")


def test_scheduler_reconciliation_lists_snapshots_oldest_first(tmp_path) -> None:
    store = SchedulerAuditStore(tmp_path / "monitoring.sqlite3")
    with sqlite3.connect(store.db_path) as connection:
        for task_id, run_id, created, completed in (
            ("new", "run-new", 200, 400),
            ("old", "run-old", 100, 300),
        ):
            connection.execute(
                "INSERT INTO monitoring_jobs("
                "task_id, asset_key, source, symbol, timeframe, snapshot_hash, cohort_id, "
                "task_created_at_ms, first_submission_status, submission_status, "
                "submission_count, submitted_at_ms, completion_status, run_id, "
                "completed_at_ms, analysis_status, analysis_success, updated_at_ms"
                ") VALUES (?, 'asset', 'source', 'XAUUSD', '1h', ?, ?, ?, 'accepted', "
                "'accepted', 1, ?, 'completed', ?, ?, 'completed', 1, ?)",
                (task_id, task_id * 64, "c" * 64, created, created, run_id, completed, completed),
            )

    assert store.list_successful_jobs() == [
        {"task_id": "old", "run_id": "run-old"},
        {"task_id": "new", "run_id": "run-new"},
    ]
