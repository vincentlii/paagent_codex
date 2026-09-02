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
from pa_agent.ai.analysis_role_backend import AnalysisRoleRequest
from pa_agent.ai.codex_analysis_backend import (
    CodexAnalysisRoleBackend,
    CodexCircuitBreaker,
    FullChainFallbackRequired,
)
from pa_agent.ai.deepseek_client import AIUsage
from pa_agent.ai.provider_failures import ModelCallFailure
from pa_agent.config.settings import PromptSettings, Settings
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator, _call_attempt_entry
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


def test_codex_backend_returns_compatible_reply_and_runtime_metadata(tmp_path: Path) -> None:
    runtime_identity = {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "xhigh",
        "runtime_version": "runtime-v1",
    }
    published = FrozenAgentJobStore(tmp_path).publish(
        _job(runtime_identity=runtime_identity)
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
    assert result.runtime["runtime_version"] == "runtime-v1"
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
            stderr="401 token-secret should not escape",
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


def test_codex_backend_can_own_pa_roles_while_vpa_remains_independent() -> None:
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
