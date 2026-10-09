from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pa_agent.agent_runtime.codex_worker import (
    CodexWorkerOutputError,
    CodexWorkerResult,
    CodexWorkerTimeout,
)
from pa_agent.agent_runtime.publisher import CodexJobPublisher
from pa_agent.ai.analysis_role_backend import AnalysisRoleRequest
from pa_agent.ai.codex_analysis_backend import (
    CodexAnalysisRoleBackend,
    CodexCircuitBreaker,
    FullChainFallbackRequired,
)
from pa_agent.ai.deepseek_client import AIUsage
from pa_agent.ai.fact_catalog import build_analysis_fact_catalog
from pa_agent.ai.provider_failures import ModelCallFailure
from pa_agent.config.settings import PromptSettings, Settings
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator, _call_attempt_entry
from tests.integration.conftest import make_frame
from tests.unit.test_frozen_agent_job import _job
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore


def _request() -> AnalysisRoleRequest:
    return AnalysisRoleRequest(
        role="stage1",
        messages=[{"role": "user", "content": "diagnose"}],
        schema={"type": "object", "required": ["cycle"]},
        thinking=True,
        reasoning_effort="high",
        snapshot_hash="a" * 64,
        cutoff_ts_open=1_725_000_000_000,
    )


def _result(*, thread_id: str = "thread-1") -> CodexWorkerResult:
    return CodexWorkerResult(
        content='{"cycle":"trading_range"}',
        thread_id=thread_id,
        usage=AIUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        latency_ms=25.0,
        evidence_events=(
            {"operation": "get_facts", "status": "ok", "response_bytes": 20},
        ),
        outbox_path=Path("outbox.json"),
    )


@pytest.mark.parametrize("after_worker", [False, True])
def test_child_observations_reach_failure_callback_before_and_after_normalization(tmp_path, after_worker):
    from dataclasses import replace
    runtime = {"model": "gpt-6-luna", "reasoning_effort": "xhigh"}
    published = FrozenAgentJobStore(tmp_path).publish(_job(runtime_identity=runtime))
    audit = {"job_digest": published.name, "status": "completed",
             "aggregate_usage": {"total_tokens": 17, "reported_fields": []}}
    worker = MagicMock()
    if after_worker:
        worker.run.return_value = replace(_result(), content="invalid-json", collaboration=audit,
                                          usage=AIUsage(total_tokens=17))
    else:
        error = CodexWorkerOutputError("bad parent output")
        error.collaboration = audit
        worker.run.side_effect = error
    backend = CodexAnalysisRoleBackend(worker=worker, publish_job=lambda *args: published,
                                       runtime_identity=runtime)
    failures = []
    with pytest.raises(ModelCallFailure) as failure:
        backend.run(_request(), on_reasoning_token=None, on_content_token=None,
                    cancel_token=None, on_attempt_error=failures.append)
    assert failures[0].analysis_attempt["collaboration"] == audit
    assert failures[0].analysis_attempt["usage"]["total_tokens"] == 17
    assert failure.value.attempts[0]["collaboration"] == audit


def test_codex_backend_returns_compatible_reply_and_runtime_metadata(tmp_path: Path) -> None:
    runtime_identity = {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "xhigh",
        "runtime_version": "runtime-v1",
    }
    published = FrozenAgentJobStore(tmp_path).publish(
        _job(runtime_identity=runtime_identity, core_context={
            "knowledge_delivery_policy": "inline_mandatory_knowledge_v1",
        })
    )
    worker = MagicMock()
    worker.run.return_value = _result()
    attempts: list[int] = []

    def publish(_request: AnalysisRoleRequest, attempt: int) -> Path:
        attempts.append(attempt)
        return published

    backend = CodexAnalysisRoleBackend(
        worker=worker,
        publish_job=publish,
        runtime_identity=runtime_identity,
    )
    streamed: list[str] = []
    result = backend.run(
        _request(),
        on_reasoning_token=None,
        on_content_token=streamed.append,
        cancel_token=None,
    )

    assert result.backend_id == "codex_agent"
    assert result.reply.content == '{"cycle":"trading_range"}'
    assert result.reply.usage.total_tokens == 15
    assert result.runtime["job_digest"] == published.name
    assert result.runtime["evidence_call_count"] == 1
    assert result.runtime["evidence_response_bytes"] == 20
    assert result.runtime["initial_context_bytes"] > 0
    assert result.runtime["model"] == "gpt-5.6-terra"
    assert result.runtime["effective_reasoning_effort"] == "xhigh"
    assert result.runtime["requested_reasoning_effort"] == "xhigh"
    assert result.runtime["runtime_version"] == "runtime-v1"
    assert result.runtime["knowledge_delivery_policy"] == "inline_mandatory_knowledge_v1"
    assert result.runtime["thread_id"] == "thread-1"
    assert result.reply._pa_model_call_meta["analysis_backend"] == "codex_agent"
    assert streamed == ['{"cycle":"trading_range"}']
    assert attempts == [1]
    assert backend.validation_retry_limit == 1

    ledger = _call_attempt_entry(
        arm="pa",
        stage="stage1",
        attempt=1,
        messages=_request().messages,
        reply=result.reply,
    )
    assert ledger["job_digest"] == published.name
    assert ledger["codex_thread_id"] == "thread-1"
    assert ledger["evidence_call_count"] == 1
    assert ledger["evidence_response_bytes"] == 20
    assert ledger["initial_context_bytes"] == result.runtime["initial_context_bytes"]
    assert ledger["model"] == "gpt-5.6-terra"
    assert ledger["runtime_version"] == "runtime-v1"
    assert ledger["knowledge_delivery_policy"] == "inline_mandatory_knowledge_v1"


def test_backend_accepts_publisher_bounded_messages_for_original_role_request(
    tmp_path: Path,
) -> None:
    frame = make_frame()
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    runtime_identity = {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "runtime_version": "runtime-v1",
    }
    publisher = CodexJobPublisher(
        frame=frame,
        prompt_dir=prompt_dir,
        job_root=tmp_path / "jobs",
        runtime_identity=runtime_identity,
    )
    request = AnalysisRoleRequest(
        role="stage1",
        messages=[{"role": "system", "content": "x" * 200_000}],
        schema={"type": "object", "required": ["cycle"]},
        thinking=True,
        reasoning_effort="high",
        snapshot_hash=build_analysis_fact_catalog(frame).snapshot_hash,
        cutoff_ts_open=frame.bars[0].ts_open,
        agent_context={
            "task_id": "task-1",
            "higher_timeframe": {},
            "role_protocol": {"contract_version": "pa_stage1_agent_protocol_v1"},
            "allowed_knowledge_ids": [],
        },
    )
    worker = MagicMock()
    worker.run.return_value = _result()
    backend = CodexAnalysisRoleBackend(
        worker=worker,
        publish_job=publisher,
        runtime_identity=runtime_identity,
    )

    result = backend.run(
        request,
        on_reasoning_token=None,
        on_content_token=None,
        cancel_token=None,
    )

    assert result.backend_id == "codex_agent"
    worker.run.assert_called_once()


def test_backend_rejects_bounded_job_bound_to_different_source_messages(
    tmp_path: Path,
) -> None:
    frame = make_frame()
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    runtime_identity = {"model": "gpt-5.6-sol", "reasoning_effort": "high"}
    publisher = CodexJobPublisher(
        frame=frame,
        prompt_dir=prompt_dir,
        job_root=tmp_path / "jobs",
        runtime_identity=runtime_identity,
    )
    common = {
        "role": "stage1",
        "schema": {"type": "object", "required": ["cycle"]},
        "thinking": True,
        "reasoning_effort": "high",
        "snapshot_hash": build_analysis_fact_catalog(frame).snapshot_hash,
        "cutoff_ts_open": frame.bars[0].ts_open,
        "agent_context": {
            "task_id": "task-1",
            "higher_timeframe": {},
            "role_protocol": {"contract_version": "pa_stage1_agent_protocol_v1"},
            "allowed_knowledge_ids": [],
        },
    }
    original = AnalysisRoleRequest(
        messages=[{"role": "system", "content": "original"}],
        **common,
    )
    mismatched = AnalysisRoleRequest(
        messages=[{"role": "system", "content": "different"}],
        **common,
    )
    published = publisher(original, 1)
    worker = MagicMock()
    backend = CodexAnalysisRoleBackend(
        worker=worker,
        publish_job=lambda _request, _attempt: published,
        runtime_identity=runtime_identity,
    )

    with pytest.raises(ModelCallFailure, match="identity check failed"):
        backend.run(
            mismatched,
            on_reasoning_token=None,
            on_content_token=None,
            cancel_token=None,
        )

    worker.run.assert_not_called()


def test_auto_mode_requests_full_chain_fallback_only_for_availability(
    tmp_path: Path,
) -> None:
    published = FrozenAgentJobStore(tmp_path).publish(_job())
    worker = MagicMock()
    worker.run.side_effect = CodexWorkerTimeout("timeout")
    backend = CodexAnalysisRoleBackend(
        worker=worker,
        publish_job=lambda _request, _attempt: published,
        request_full_chain_fallback=True,
    )

    with pytest.raises(FullChainFallbackRequired) as caught:
        backend.run(
            _request(),
            on_reasoning_token=None,
            on_content_token=None,
            cancel_token=None,
        )
    assert caught.value.failure.kind == "timeout"
    assert caught.value.failure.retryable is True


def test_backend_rejects_role_when_full_cycle_budget_is_already_exhausted() -> None:
    worker = MagicMock()
    publisher = MagicMock()
    monotonic = MagicMock(side_effect=[100.0, 701.0])
    backend = CodexAnalysisRoleBackend(
        worker=worker,
        publish_job=publisher,
        cycle_wall_seconds=600,
        monotonic=monotonic,
    )

    with pytest.raises(ModelCallFailure) as caught:
        backend.run(
            _request(),
            on_reasoning_token=None,
            on_content_token=None,
            cancel_token=None,
        )

    assert caught.value.failure.kind == "timeout"
    publisher.assert_not_called()
    worker.run.assert_not_called()


def test_failed_call_ledger_keeps_selected_backend_without_inventing_usage():
    entry = _call_attempt_entry(
        arm="pa", stage="stage1", attempt=2, messages=[],
        status="request_failed", backend_id="codex_agent",
    )
    assert entry["analysis_backend"] == "codex_agent"
    assert entry["response_hash"] is None
    assert entry["metrics"]["values"]["prompt_tokens"]["status"] == "unknown"


@pytest.mark.parametrize("after_worker", [False, True])
def test_single_agent_failed_usage_reaches_ledger_without_child_collaboration(tmp_path, after_worker):
    from dataclasses import replace

    runtime = {"model": "gpt-6-luna", "reasoning_effort": "xhigh"}
    published = FrozenAgentJobStore(tmp_path).publish(_job(runtime_identity=runtime))
    error = CodexWorkerOutputError("Codex failed without an availability signal (exit status 1)")
    error.observed_usage = AIUsage(total_tokens=0, reported_fields=())
    worker = MagicMock()
    if after_worker:
        worker.run.return_value = replace(_result(), content="invalid-json", usage=AIUsage())
    else:
        worker.run.side_effect = error
    backend = CodexAnalysisRoleBackend(worker=worker, publish_job=lambda *args: published, runtime_identity=runtime)
    entries = []
    def on_error(exc):
        entries.append(_call_attempt_entry(arm="pa", stage="stage1", attempt=1, messages=[], status="request_failed", backend_id="codex_agent", failure_metadata=exc.analysis_attempt))
    with pytest.raises(ModelCallFailure):
        backend.run(_request(), on_reasoning_token=None, on_content_token=None, cancel_token=None, on_attempt_error=on_error)
    call = entries[0]
    assert call["latency_ms"] is not None
    assert call["requested_model"] == "gpt-6-luna"
    assert call["effective_model"] == ("gpt-6-luna" if after_worker else None)
    assert call["requested_reasoning_effort"] == "xhigh"
    assert call["metrics"]["values"]["total_tokens"]["status"] == "unknown"
    assert call["metrics"]["values"]["backend_wall_ms"]["status"] == "actual"
    assert call["usage_observed_without_response"] is True


def test_current_role_cycle_budget_stops_without_availability_fallback():
    from dataclasses import replace
    from pa_agent.agent_runtime.codex_worker import CodexWorkerStopped

    backend = CodexAnalysisRoleBackend(
        worker=MagicMock(), publish_job=MagicMock(), request_full_chain_fallback=True,
        cycle_wall_seconds=600, monotonic=MagicMock(side_effect=[100.0, 701.0]),
    )
    request = replace(_request(), agent_context={
        "role_protocol": {"contract_version": "pa_stage1_agent_protocol_v1"},
    })
    with pytest.raises(ModelCallFailure) as caught:
        backend.run(request, on_reasoning_token=None, on_content_token=None, cancel_token=None)
    assert not isinstance(caught.value, FullChainFallbackRequired)
    assert caught.value.failure.kind == "role_stopped"
    assert isinstance(caught.value.original, CodexWorkerStopped)
    assert caught.value.original.stop.status == "incomplete"
    assert backend._circuit.snapshot()["failures"] == 0


def test_worker_role_stop_is_not_misclassified_as_invalid_output(tmp_path: Path) -> None:
    from pa_agent.agent_runtime.codex_worker import CodexWorkerStopped
    from pa_agent.ai.role_contracts import AnalysisRoleStop

    published = FrozenAgentJobStore(tmp_path).publish(_job())
    stop = AnalysisRoleStop(
        role="stage1", status="incomplete", reason="wall-clock budget exceeded",
    )
    worker = MagicMock()
    worker.run.side_effect = CodexWorkerStopped(stop)
    backend = CodexAnalysisRoleBackend(
        worker=worker,
        publish_job=lambda _request, _attempt: published,
        request_full_chain_fallback=True,
    )

    with pytest.raises(ModelCallFailure) as caught:
        backend.run(
            _request(), on_reasoning_token=None, on_content_token=None, cancel_token=None,
        )

    assert not isinstance(caught.value, FullChainFallbackRequired)
    assert caught.value.failure.kind == "role_stopped"
    assert caught.value.failure.message == stop.reason
    assert caught.value.failure.retryable is False


def test_role_stop_attempt_status_is_not_recorded_as_request_failure() -> None:
    from pa_agent.agent_runtime.codex_worker import CodexWorkerStopped
    from pa_agent.ai.role_contracts import AnalysisRoleStop
    from pa_agent.ai.provider_failures import ProviderFailure
    from pa_agent.orchestrator.two_stage import _attempt_failure_status

    stop = CodexWorkerStopped(AnalysisRoleStop(
        role="stage1", status="incomplete", reason="wall-clock budget exceeded",
    ))
    assert _attempt_failure_status(stop) == "role_stopped"
    wrapped = ModelCallFailure(
        ProviderFailure(kind="role_stopped", message=stop.stop.reason), original=stop,
    )
    assert _attempt_failure_status(wrapped) == "role_stopped"
    assert _attempt_failure_status(ValueError("bad output")) == "request_failed"


def test_auto_mode_treats_job_spool_oserror_as_full_chain_availability() -> None:
    def unavailable(_request: AnalysisRoleRequest, _attempt: int) -> Path:
        raise OSError("spool is read-only")

    backend = CodexAnalysisRoleBackend(
        worker=MagicMock(),
        publish_job=unavailable,
        request_full_chain_fallback=True,
    )

    with pytest.raises(FullChainFallbackRequired) as caught:
        backend.run(
            _request(),
            on_reasoning_token=None,
            on_content_token=None,
            cancel_token=None,
        )

    assert caught.value.failure.kind == "unavailable"


def test_output_failure_never_requests_fallback(tmp_path: Path) -> None:
    published = FrozenAgentJobStore(tmp_path).publish(_job())
    worker = MagicMock()
    worker.run.side_effect = CodexWorkerOutputError("schema invalid")
    backend = CodexAnalysisRoleBackend(
        worker=worker,
        publish_job=lambda _request, _attempt: published,
        request_full_chain_fallback=True,
    )

    with pytest.raises(ModelCallFailure) as caught:
        backend.run(
            _request(),
            on_reasoning_token=None,
            on_content_token=None,
            cancel_token=None,
        )

    assert not isinstance(caught.value, FullChainFallbackRequired)
    assert caught.value.failure.kind == "invalid_request"
    assert caught.value.failure.retryable is False


def test_backend_rejects_published_job_not_bound_to_current_request(
    tmp_path: Path,
) -> None:
    published = FrozenAgentJobStore(tmp_path).publish(_job(role="stage1"))
    worker = MagicMock()
    request = AnalysisRoleRequest(
        role="stage2",
        messages=[{"role": "user", "content": "decide"}],
        schema={"type": "object"},
        thinking=True,
        reasoning_effort="high",
        snapshot_hash="b" * 64,
        cutoff_ts_open=1_725_000_000_001,
    )
    backend = CodexAnalysisRoleBackend(
        worker=worker,
        publish_job=lambda _request, _attempt: published,
        request_full_chain_fallback=True,
    )

    with pytest.raises(ModelCallFailure) as caught:
        backend.run(
            request,
            on_reasoning_token=None,
            on_content_token=None,
            cancel_token=None,
        )

    assert caught.value.failure.kind == "invalid_request"
    assert caught.value.failure.retryable is False
    assert not isinstance(caught.value, FullChainFallbackRequired)
    worker.run.assert_not_called()


def test_job_publication_oserror_is_sanitized_availability() -> None:
    backend = CodexAnalysisRoleBackend(
        worker=MagicMock(),
        publish_job=MagicMock(side_effect=OSError("disk-secret")),
        request_full_chain_fallback=True,
    )

    with pytest.raises(FullChainFallbackRequired) as caught:
        backend.run(
            _request(),
            on_reasoning_token=None,
            on_content_token=None,
            cancel_token=None,
        )

    assert caught.value.failure.kind == "unavailable"
    assert "disk-secret" not in str(caught.value)


def test_circuit_counts_only_availability_failures(tmp_path: Path) -> None:
    store = FrozenAgentJobStore(tmp_path)
    clock = MagicMock(return_value=100.0)
    circuit = CodexCircuitBreaker(failure_threshold=2, cooldown_seconds=30, clock=clock)
    worker = MagicMock()
    worker.run.side_effect = [
        CodexWorkerTimeout("one"),
        CodexWorkerTimeout("two"),
    ]
    backend = CodexAnalysisRoleBackend(
        worker=worker,
        publish_job=lambda _request, attempt: store.publish(_job(attempt=attempt)),
        circuit_breaker=circuit,
    )

    for _ in range(2):
        with pytest.raises(ModelCallFailure):
            backend.run(
                _request(),
                on_reasoning_token=None,
                on_content_token=None,
                cancel_token=None,
            )
    with pytest.raises(ModelCallFailure, match="circuit") as caught:
        backend.run(
            _request(),
            on_reasoning_token=None,
            on_content_token=None,
            cancel_token=None,
        )

    assert caught.value.failure.kind == "unavailable"
    assert worker.run.call_count == 2


def test_worker_nonzero_exit_is_classified_without_exposing_stderr(tmp_path: Path) -> None:
    from pa_agent.agent_runtime.codex_worker import CodexCliConfig, CodexCliWorker

    published = FrozenAgentJobStore(tmp_path / "jobs").publish(_job())

    def failed(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="HTTP 401 token-secret should not escape",
        )

    worker = CodexCliWorker(
        CodexCliConfig(
            codex_home=tmp_path / "home",
            runtime_root=tmp_path / "runtime",
            outbox_root=tmp_path / "outbox",
            model="gpt-5.6-terra",
            reasoning_effort="high",
        ),
        process_runner=failed,
    )
    with pytest.raises(Exception) as caught:
        worker.run(published)

    assert type(caught.value).__name__ == "CodexWorkerAuthenticationError"
    assert "token-secret" not in str(caught.value)


def test_codex_backend_clamps_validator_repairs_without_mutating_settings() -> None:
    settings = Settings()
    original_retry_max = settings.validation.retry_max
    orchestrator = object.__new__(TwoStageOrchestrator)
    orchestrator._settings = settings
    orchestrator._analysis_backend = MagicMock(validation_retry_limit=1)

    effective = orchestrator._validation_settings()

    assert effective.retry_max == 1
    assert effective.retry_max_semantic == 1
    assert settings.validation.retry_max == original_retry_max


def test_codex_backend_can_own_all_analysis_roles_without_direct_client() -> None:
    settings = Settings(prompt=PromptSettings(vpa_mode="shadow"))

    orchestrator = TwoStageOrchestrator(
        client=None,
        analysis_backend=MagicMock(backend_id="codex_agent"),
        assembler=MagicMock(prompt_dir=None),
        router=object(),
        validator=MagicMock(prompt_dir=None),
        pending_writer=object(),
        exp_reader=object(),
        settings=settings,
    )

    assert orchestrator._analysis_backend.backend_id == "codex_agent"
    assert orchestrator._clone_role_client(settings.provider) == (None, True)


def test_backend_continues_only_pa_stage2_and_keeps_vpa_ephemeral(
    tmp_path: Path,
) -> None:
    frame = make_frame()
    snapshot_hash = build_analysis_fact_catalog(frame).snapshot_hash
    runtime_identity = {
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "runtime_version": "runtime-v1",
    }
    publisher = CodexJobPublisher(
        frame=frame,
        prompt_dir=tmp_path / "prompts",
        job_root=tmp_path / "jobs",
        runtime_identity=runtime_identity,
    )
    (tmp_path / "prompts").mkdir()

    def role_request(role: str, context: dict) -> AnalysisRoleRequest:
        return AnalysisRoleRequest(
            role=role,
            messages=[{"role": "user", "content": role}],
            schema={"type": "object"},
            thinking=True,
            reasoning_effort="max",
            snapshot_hash=snapshot_hash,
            cutoff_ts_open=frame.bars[0].ts_open,
            agent_context={"task_id": "task-session", "allowed_knowledge_ids": [], **context},
        )

    worker = MagicMock()
    worker.run.side_effect = [
        _result(thread_id="thread-pa-0001"),
        _result(thread_id="thread-vpa-001"),
        _result(thread_id="thread-pa-0001"),
    ]
    backend = CodexAnalysisRoleBackend(
        worker=worker,
        publish_job=publisher,
        runtime_identity=runtime_identity,
    )
    common = dict(
        on_reasoning_token=None,
        on_content_token=None,
        cancel_token=None,
    )
    backend.run(role_request("stage1", {
        "role_protocol": {"contract_version": "pa_stage1_agent_protocol_v1"},
        "higher_timeframe": {},
    }), **common)
    backend.run(role_request("vpa_evidence", {
        "role_protocol": {"contract_version": "vpa_evidence_agent_protocol_v1"},
        "stage1_diagnosis": {},
        "pa_hypotheses": {},
    }), **common)
    stage2 = backend.run(role_request("stage2", {
        "role_protocol": {"contract_version": "pa_stage2_agent_protocol_v1"},
        "stage1_diagnosis": {},
        "router_manifest": [],
        "higher_timeframe": {},
    }), **common)

    first, vpa, second = worker.run.call_args_list
    assert first.kwargs["persist_session"] is True
    assert first.kwargs["resume_thread_id"] is None
    assert vpa.kwargs["persist_session"] is False
    assert vpa.kwargs["resume_thread_id"] is None
    assert second.kwargs["persist_session"] is False
    assert second.kwargs["resume_thread_id"] == "thread-pa-0001"
    assert second.kwargs["parent_job_digest"] == first.args[0].name
    assert stage2.runtime["session_mode"] == "pa_continuation"
    assert stage2.runtime["parent_job_digest"] == first.args[0].name
    ledger = _call_attempt_entry(
        arm="pa",
        stage="stage2",
        attempt=1,
        messages=[],
        reply=stage2.reply,
    )
    assert ledger["session_mode"] == "pa_continuation"
    assert ledger["parent_job_digest"] == first.args[0].name
