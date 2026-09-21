from __future__ import annotations

import threading
import time
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import pa_agent.agent_runtime.spool_worker as spool_module
from pa_agent.agent_runtime.codex_worker import CodexWorkerResult
from pa_agent.agent_runtime.codex_worker import CodexWorkerUnavailable
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from pa_agent.agent_runtime.spool_worker import (
    CodexSpoolClient,
    CodexSpoolConfig,
    CodexSpoolService,
)
from pa_agent.ai.deepseek_client import AIUsage
from pa_agent.ai.codex_analysis_backend import (
    CodexAnalysisRoleBackend,
    FullChainFallbackRequired,
)
from tests.unit.test_codex_analysis_backend import _request
from tests.unit.test_frozen_agent_job import _job


@pytest.mark.parametrize("investigation", ["fixed", "agentic"])
def test_role_stop_accepts_job_selection_but_rejects_runtime_drift(investigation):
    from pa_agent.agent_runtime.codex_worker import CodexWorkerStopped, CodexWorkerOutputError
    from pa_agent.ai.role_contracts import AnalysisRoleStop

    actual = {"runtime_asset_digest": "a" * 64, "model": "gpt-5.6-luna", "reasoning_effort": "max"}
    expected = {**actual, "profile_fingerprint": "b" * 64, "investigation_policy": investigation}
    raw = {"schema_version": 1, "request_id": "request", "job_digest": "digest",
           "acceptance_key": "key", "runtime_identity": actual, "status": "error",
           "error_type": "CodexWorkerStopped", "role_stop": AnalysisRoleStop(
               role="stage1", status="incomplete", reason="wall-clock budget exceeded").model_dump(mode="json")}
    kwargs = dict(request_id="request", job_digest="digest", acceptance_key="key", runtime_identity=expected)
    with pytest.raises(CodexWorkerStopped):
        spool_module._decode_result(raw, **kwargs)
    with pytest.raises(CodexWorkerOutputError):
        spool_module._decode_result({**raw, "runtime_identity": {**actual, "runtime_asset_digest": "c" * 64}}, **kwargs)
    with pytest.raises(CodexWorkerOutputError):
        spool_module._decode_result({**raw, "acceptance_key": "wrong"}, **kwargs)


def test_environment_worker_identity_provider_passes_legacy_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured = {}

    def identity(**kwargs):
        captured.update(kwargs)
        return {"runtime_version": "test"}

    monkeypatch.setenv("PA_AGENT_CODEX_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PA_AGENT_CODEX_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("PA_AGENT_CODEX_REASONING", "high")
    monkeypatch.setattr(spool_module, "codex_runtime_identity", identity)

    service = spool_module._service_from_environment()
    assert service._runtime_identity_provider() == {"runtime_version": "test"}
    assert captured == {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "codex_binary": "codex",
        "model_catalog": None,
    }


def test_worker_health_projection_contains_no_auth_material(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    worker = MagicMock()
    worker.login_health.return_value = "healthy"
    service = CodexSpoolService(
        spool=CodexSpoolConfig(
            job_root=tmp_path / "jobs",
            inbox_root=tmp_path / "inbox",
            result_root=results,
        ),
        worker=worker,
        runtime_identity_provider=lambda: {
            "runtime_version": "runtime-v1",
            "runtime_asset_digest": "a" * 64,
            "codex_binary_digest": "b" * 64,
            "access_token": "must-not-leak",
        },
    )

    service._publish_health()

    status = json.loads((results / ".worker-status.json").read_text(encoding="utf-8"))
    assert status["auth_state"] == "healthy"
    assert status["runtime_version"] == "runtime-v1"
    assert "access_token" not in status


def test_spool_bridge_exchanges_only_job_identity_and_bounded_result(
    tmp_path: Path,
) -> None:
    jobs = tmp_path / "jobs"
    inbox = tmp_path / "inbox"
    results = tmp_path / "results"
    inbox.mkdir()
    results.mkdir()
    runtime_identity = {
        "model": "gpt-test",
        "reasoning_effort": "high",
        "runtime_version": "runtime-v1",
        "codex_binary_digest": "b" * 64,
    }
    published = FrozenAgentJobStore(jobs).publish(
        _job(runtime_identity=runtime_identity)
    )
    worker = MagicMock()
    worker.run.return_value = CodexWorkerResult(
        content='{"cycle":"trading_range"}',
        thread_id="thread-1",
        usage=AIUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5),
        latency_ms=12.0,
        evidence_events=(),
        outbox_path=tmp_path / "isolated" / "outbox.json",
    )
    config = CodexSpoolConfig(
        job_root=jobs,
        inbox_root=inbox,
        result_root=results,
        poll_seconds=0.01,
        response_grace_seconds=1.0,
    )
    client = CodexSpoolClient(config)
    service = CodexSpoolService(
        spool=config,
        worker=worker,
        max_workers=1,
        runtime_identity_provider=lambda: runtime_identity,
    )
    captured: list[CodexWorkerResult] = []
    thread = threading.Thread(target=lambda: captured.append(client.run(published)))
    thread.start()
    deadline = time.monotonic() + 2
    dispatches: list[Path] = []
    while time.monotonic() < deadline and not dispatches:
        dispatches = list(inbox.glob("*.json"))
        time.sleep(0.01)
    assert len(dispatches) == 1
    dispatch_text = dispatches[0].read_text(encoding="utf-8")
    assert "database" not in dispatch_text
    assert "credential" not in dispatch_text
    claimed = dispatches[0].with_suffix(".running")
    dispatches[0].replace(claimed)
    service._execute(claimed)
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert captured[0].content == '{"cycle":"trading_range"}'
    worker.run.assert_called_once()
    called_path = worker.run.call_args.args[0]
    called_timeout = worker.run.call_args.kwargs["timeout_seconds"]
    assert called_path == published
    assert 0 < called_timeout <= _job().budget.max_wall_seconds
    assert not list(results.glob("*.json"))


def test_isolated_worker_rejects_runtime_identity_drift(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    inbox = tmp_path / "inbox"
    results = tmp_path / "results"
    inbox.mkdir()
    results.mkdir()
    expected = {"model": "gpt-frozen", "codex_binary_digest": "a" * 64}
    job = _job(runtime_identity=expected)
    FrozenAgentJobStore(jobs).publish(job)
    dispatch = inbox / "request-1.running"
    dispatch.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request_id": "request-1",
                "job_digest": job.manifest.job_digest,
                "expected_runtime_identity": expected,
            }
        ),
        encoding="utf-8",
    )
    worker = MagicMock()
    service = CodexSpoolService(
        spool=CodexSpoolConfig(
            job_root=jobs,
            inbox_root=inbox,
            result_root=results,
        ),
        worker=worker,
        runtime_identity_provider=lambda: {
            "model": "gpt-drifted",
            "codex_binary_digest": "b" * 64,
        },
    )

    service._execute(dispatch)

    envelope = json.loads((results / "request-1.json").read_text(encoding="utf-8"))
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "CodexWorkerOutputError"
    worker.run.assert_not_called()


def test_spool_io_oserror_is_classified_as_availability(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_identity = {"model": "gpt-test", "codex_binary_digest": "a" * 64}
    jobs = tmp_path / "jobs"
    published = FrozenAgentJobStore(jobs).publish(
        _job(runtime_identity=runtime_identity)
    )
    inbox = tmp_path / "inbox"
    results = tmp_path / "results"
    inbox.mkdir()
    results.mkdir()
    monkeypatch.setattr(
        spool_module,
        "_atomic_json",
        MagicMock(side_effect=OSError("disk full")),
    )
    client = CodexSpoolClient(
        CodexSpoolConfig(
            job_root=jobs,
            inbox_root=inbox,
            result_root=results,
        )
    )

    with pytest.raises(CodexWorkerUnavailable):
        client.run(published)


def test_recovery_requeues_expired_dispatch_even_while_lease_is_active(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    claimed = inbox / "request-1.running"
    claimed.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "request_id": "request-1",
                "lease_seconds": 3600,
                "deadline_epoch": time.time() - 1,
            }
        ),
        encoding="utf-8",
    )
    service = CodexSpoolService(
        spool=CodexSpoolConfig(
            job_root=tmp_path / "jobs",
            inbox_root=inbox,
            result_root=tmp_path / "results",
        ),
        worker=MagicMock(),
    )

    service._recover_claims(recover_all=False)

    assert not claimed.exists()
    assert (inbox / "request-1.json").is_file()


def test_worker_runtime_unavailable_reaches_auto_full_chain_fallback(
    tmp_path: Path,
) -> None:
    expected = {"model": "gpt-frozen", "codex_binary_digest": "a" * 64}
    jobs = tmp_path / "jobs"
    inbox = tmp_path / "inbox"
    results = tmp_path / "results"
    inbox.mkdir()
    results.mkdir()
    published = FrozenAgentJobStore(jobs).publish(
        _job(runtime_identity=expected)
    )
    spool = CodexSpoolConfig(
        job_root=jobs,
        inbox_root=inbox,
        result_root=results,
        poll_seconds=0.01,
        response_grace_seconds=1.0,
    )
    backend = CodexAnalysisRoleBackend(
        worker=CodexSpoolClient(spool),
        publish_job=lambda _request, _attempt: published,
        request_full_chain_fallback=True,
        runtime_identity=expected,
    )
    caught: list[Exception] = []

    def run_backend() -> None:
        try:
            backend.run(
                _request(),
                on_reasoning_token=None,
                on_content_token=None,
                cancel_token=None,
            )
        except Exception as exc:
            caught.append(exc)

    thread = threading.Thread(target=run_backend)
    thread.start()
    deadline = time.monotonic() + 2
    dispatches: list[Path] = []
    while time.monotonic() < deadline and not dispatches:
        dispatches = list(inbox.glob("*.json"))
        time.sleep(0.01)
    assert len(dispatches) == 1
    claimed = dispatches[0].with_suffix(".running")
    dispatches[0].replace(claimed)
    service = CodexSpoolService(
        spool=spool,
        worker=MagicMock(),
        runtime_identity_provider=MagicMock(side_effect=OSError("binary unavailable")),
    )
    service._execute(claimed)
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert len(caught) == 1
    assert isinstance(caught[0], FullChainFallbackRequired)
    assert caught[0].failure.kind == "unavailable"


def test_spool_binds_and_forwards_pa_resume_identity(tmp_path: Path) -> None:
    from pa_agent.agent_runtime.publisher import CodexJobPublisher
    from pa_agent.ai.fact_catalog import build_analysis_fact_catalog
    from tests.integration.conftest import make_frame
    from tests.unit.test_codex_job_publisher import _request as role_request

    jobs = tmp_path / "jobs"
    inbox = tmp_path / "inbox"
    results = tmp_path / "results"
    inbox.mkdir()
    results.mkdir()
    frame = make_frame()
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    runtime_identity = {
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "runtime_version": "runtime-v1",
    }
    publisher = CodexJobPublisher(
        frame=frame,
        prompt_dir=prompt_dir,
        job_root=jobs,
        runtime_identity=runtime_identity,
    )
    common = {"task_id": "task-session", "allowed_knowledge_ids": [], "higher_timeframe": {}}
    stage1 = publisher(role_request("stage1", {
        **common,
        "role_protocol": {"contract_version": "pa_stage1_agent_protocol_v1"},
    }), 1)
    stage2 = publisher(role_request("stage2", {
        **common,
        "role_protocol": {"contract_version": "pa_stage2_agent_protocol_v1"},
        "stage1_diagnosis": {},
        "router_manifest": [],
        "pa_session_continuation": {
            "parent_job_digest": stage1.name,
            "thread_id": "thread-pa-0001",
        },
    }), 1)
    worker = MagicMock()
    worker.run.return_value = CodexWorkerResult(
        content="{}",
        thread_id="thread-pa-0001",
        usage=AIUsage(),
        latency_ms=1.0,
        evidence_events=(),
        outbox_path=tmp_path / "outbox.json",
    )
    config = CodexSpoolConfig(
        job_root=jobs,
        inbox_root=inbox,
        result_root=results,
        poll_seconds=0.01,
        response_grace_seconds=1.0,
    )
    client = CodexSpoolClient(config)
    service = CodexSpoolService(
        spool=config,
        worker=worker,
        max_workers=1,
        runtime_identity_provider=lambda: runtime_identity,
    )
    captured: list[CodexWorkerResult] = []
    thread = threading.Thread(target=lambda: captured.append(client.run(
        stage2,
        resume_thread_id="thread-pa-0001",
        parent_job_digest=stage1.name,
    )))
    thread.start()
    deadline = time.monotonic() + 2
    dispatches: list[Path] = []
    while time.monotonic() < deadline and not dispatches:
        dispatches = list(inbox.glob("*.json"))
        time.sleep(0.01)
    assert len(dispatches) == 1
    dispatch = json.loads(dispatches[0].read_text(encoding="utf-8"))
    assert dispatch["schema_version"] == 3
    assert dispatch["resume_thread_id"] == "thread-pa-0001"
    assert dispatch["parent_job_digest"] == stage1.name
    claimed = dispatches[0].with_suffix(".running")
    dispatches[0].replace(claimed)
    service._execute(claimed)
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert captured[0].thread_id == "thread-pa-0001"
    assert worker.run.call_args.kwargs["resume_thread_id"] == "thread-pa-0001"
    assert worker.run.call_args.kwargs["parent_job_digest"] == stage1.name
    assert worker.run.call_args.kwargs["persist_session"] is False
