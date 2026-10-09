from __future__ import annotations

import threading
import time

import pytest

from pa_agent.agent_runtime.stage_challenge import (
    ChildExecution, StageChallengeHost, challenge_rpc,
)
from pa_agent.ai.deepseek_client import AIUsage
from tests.unit.test_frozen_agent_job import _job


def test_child_reads_scoped_evidence_in_parallel_and_cannot_use_parent_endpoint():
    started = threading.Event()
    release = threading.Event()
    tokens = AIUsage(prompt_tokens=10, completion_tokens=3, total_tokens=13,
                     reported_fields=("prompt_tokens", "completion_tokens", "total_tokens"))

    def execute(context, endpoint, token, cancel, remaining):
        started.set()
        value = challenge_rpc(endpoint, token, {"action": "read", "resource_index": 0})
        assert value["payload"]["facts"][0]["fact_id"] == "PA.BAR.K1"
        release.wait(2)
        return ChildExecution(
            {"source_digest": context["job_digest"], "evidence_refs": ["PA.BAR.K1"],
             "support": "known close", "challenge": "", "unknown": "acceptance"}, tokens,
        )

    with StageChallengeHost(_job(), execute=execute, parent_ready=lambda: None, deadline=time.monotonic()+5) as host:
        response = host.start("Check one fact", [{"operation": "get_pa_evidence", "arguments": {"fact_ids": ["PA.BAR.K1"]}}])
        assert response["status"] == "running"
        assert started.wait(2)
        assert host.collect(0)["status"] == "running"
        with pytest.raises(ValueError, match="one child"):
            host.start("again", [])
        release.set()
        result = host.collect(3)
        assert result["status"] == "completed"
        audit = host.audit()
        assert audit["collected"] is True
        assert audit["child_usage"]["total_tokens"] == 13
        assert audit["evidence_events"][0]["actor"] == "child"
        assert audit["evidence_events"][0]["operation"] == "get_pa_evidence"


def test_delegation_rejects_full_baseline_and_incomplete_parent_before_launch():
    launched = []
    def execute(*args):
        launched.append(True)
        raise AssertionError("must not launch")

    with StageChallengeHost(_job(), execute=execute, parent_ready=lambda: None, deadline=time.monotonic()+5) as host:
        with pytest.raises(ValueError, match="not permitted"):
            host.start("check", [{"operation": "get_role_baseline", "arguments": {}}])
        assert host.audit()["status"] == "start_rejected"
        assert host.audit()["start_attempts"] == host.audit()["start_rejections"] == 1
    def not_ready():
        raise ValueError("parent mandatory reads incomplete")
    with StageChallengeHost(_job(), execute=execute, parent_ready=not_ready, deadline=time.monotonic()+5) as host:
        with pytest.raises(ValueError, match="mandatory"):
            host.start("check", [{"operation": "get_pa_evidence", "arguments": {"fact_ids": ["PA.BAR.K1"]}}])
    assert not launched


def test_child_cannot_cite_unread_resource_and_close_cancels_work():
    def fabricated(context, *args):
        return ChildExecution({"source_digest": context["job_digest"], "evidence_refs": ["PA.BAR.K1"],
                               "support": "claimed", "challenge": "", "unknown": ""}, AIUsage())
    with StageChallengeHost(_job(), execute=fabricated, parent_ready=lambda: None, deadline=time.monotonic()+5) as host:
        host.start("check", [{"operation": "get_pa_evidence", "arguments": {"fact_ids": ["PA.BAR.K1"]}}])
        assert host.collect(3)["status"] == "failed"
        assert host.audit()["failure_code"] == "invalid_child_output"

    stopped = threading.Event()
    def waiting(context, endpoint, token, cancel, remaining):
        cancel.wait(3)
        stopped.set()
        return ChildExecution(None, AIUsage(), failure_code="cancelled")
    with StageChallengeHost(_job(), execute=waiting, parent_ready=lambda: None, deadline=time.monotonic()+5) as host:
        host.start("check", [{"operation": "get_pa_evidence", "arguments": {"fact_ids": ["PA.BAR.K1"]}}])
    assert stopped.is_set()
    assert host.audit()["status"] == "cancelled"


def test_child_capability_cannot_start_another_child():
    def execute(context, endpoint, token, cancel, remaining):
        with pytest.raises(ValueError, match="not authorized"):
            challenge_rpc(endpoint, token, {"action": "start", "question": "other", "resources": []})
        challenge_rpc(endpoint, token, {"action": "read", "resource_index": 0})
        return ChildExecution({"source_digest": context["job_digest"], "evidence_refs": ["PA.BAR.K1"],
                               "support": "known", "challenge": "", "unknown": ""}, AIUsage())
    with StageChallengeHost(_job(), execute=execute, parent_ready=lambda: None, deadline=time.monotonic()+5) as host:
        host.start("check", [{"operation": "get_pa_evidence", "arguments": {"fact_ids": ["PA.BAR.K1"]}}])
        assert host.collect(3)["status"] == "failed"
        assert host.audit()["child_access_denials"] == 1


def test_measurement_delivers_real_anchor_values_and_citable_ids_without_changing_parent():
    from dataclasses import replace
    from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
    from pa_agent.agent_runtime.frozen_job import AgentJobBudget
    job = replace(_job(), bars=({"seq": 1, "ts_open": 1_725_000_000_000,
                                "open": 99.0, "high": 102.0, "low": 98.0, "close": 101.0},),
                  budget=AgentJobBudget())
    parent = FrozenEvidenceGateway(job).measure_price_window(older_seq=1, newer_seq=1)
    assert "source_bars" not in parent

    def execute(context, endpoint, token, *args):
        value = challenge_rpc(endpoint, token, {"action": "read", "resource_index": 0})
        assert value["citable_ids"] == ["PA.BAR.K1"]
        receipt = value["payload"]["measurement"]
        assert receipt["source_bars"][0]["close"] == 101
        assert receipt["values"] == parent["values"]
        assert receipt["measurement_digest"] != parent["measurement_digest"]
        return ChildExecution({"source_digest": context["job_digest"], "evidence_refs": value["citable_ids"],
                               "support": "bar range 4", "challenge": "", "unknown": ""}, AIUsage())
    with StageChallengeHost(job, execute=execute, parent_ready=lambda: None, deadline=time.monotonic()+5) as host:
        host.start("measure", [{"operation": "measure_price_window", "arguments": {"older_seq": 1, "newer_seq": 1}}])
        assert host.collect(3)["status"] == "completed"


def test_child_budget_is_independent_and_denied_read_prevents_clean_result():
    def execute(context, endpoint, token, *args):
        for _ in range(8):
            challenge_rpc(endpoint, token, {"action": "read", "resource_index": 0})
        with pytest.raises(ValueError, match="budget exhausted"):
            challenge_rpc(endpoint, token, {"action": "read", "resource_index": 0})
        return ChildExecution({"source_digest": context["job_digest"], "evidence_refs": ["PA.BAR.K1"],
                               "support": "known", "challenge": "", "unknown": ""}, AIUsage())
    with StageChallengeHost(_job(), execute=execute, parent_ready=lambda: None, deadline=time.monotonic()+5) as host:
        host.start("check", [{"operation": "get_pa_evidence", "arguments": {"fact_ids": ["PA.BAR.K1"]}}])
        assert host.collect(3)["status"] == "failed"
        assert len(host.audit()["evidence_events"]) == 9
        assert host.job.budget.max_evidence_calls == 3


@pytest.mark.parametrize("role,operation,arguments", [
    ("stage2", "get_vpa_evidence", {"evidence_ids": ["VPA.BAR.K1"]}),
    ("vpa_evidence", "get_bars", {"older_seq": 1, "newer_seq": 1}),
])
def test_child_role_boundaries(role, operation, arguments):
    from dataclasses import replace
    job = _job()
    job = replace(job, manifest=replace(job.manifest, role=role))
    with StageChallengeHost(job, execute=lambda *args: None,
                            parent_ready=lambda: None, deadline=time.monotonic()+5) as host:
        with pytest.raises(ValueError, match="not permitted"):
            host.start("check", [{"operation": operation, "arguments": arguments}])


@pytest.mark.parametrize("volume_kind,ceiling", [("real", "full"), ("tick", "activity_proxy_only")])
def test_vpa_child_receives_volume_meaning_in_same_metered_receipt(volume_kind, ceiling):
    from dataclasses import replace
    job = _job()
    catalog = {"catalog_id": "catalog", "data_status": "available",
               "provenance": {"volume_kind": volume_kind, "volume_quality": "test"},
               "capabilities": {"interpretation_ceiling": ceiling},
               "evidence": [{"evidence_id": "VPA.BAR.K1", "value": {"volume": 12}}]}
    job = replace(job, manifest=replace(job.manifest, role="vpa_evidence"),
                  fact_projection={**job.fact_projection, "role_evidence": catalog})
    def execute(context, endpoint, token, *args):
        value = challenge_rpc(endpoint, token, {"action": "read", "resource_index": 0})
        assert value["payload"]["catalog_context"] == {k: catalog[k] for k in
                                                     ("catalog_id", "data_status", "provenance", "capabilities")}
        return ChildExecution({"source_digest": context["job_digest"], "evidence_refs": ["VPA.BAR.K1"],
                               "support": "conditional volume", "challenge": "", "unknown": ""}, AIUsage())
    with StageChallengeHost(job, execute=execute, parent_ready=lambda: None, deadline=time.monotonic()+5) as host:
        host.start("check volume", [{"operation": "get_vpa_evidence", "arguments": {"evidence_ids": ["VPA.BAR.K1"]}}])
        assert host.collect(3)["status"] == "completed"
        assert host.audit()["evidence_events"][0]["response_bytes"] > 200


@pytest.mark.parametrize("batch", [False, True])
def test_child_real_stdio_mcp_exposes_only_scoped_read(batch):
    import os
    import subprocess
    import sys
    from tests.unit.test_codex_evidence_mcp import _initialize, _request
    errors = []

    def execute(context, endpoint, token, *args):
        process = subprocess.Popen([sys.executable, "-B", "-m", "pa_agent.agent_runtime.challenge_mcp",
                                    "--endpoint", endpoint, *(["--batch"] if batch else [])],
                                   env={**os.environ, "PA_AGENT_CHILD_EVIDENCE_TOKEN": token},
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8")
        try:
            _initialize(process)
            listed = _request(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tool_name = "read_challenge_resources" if batch else "read_challenge_resource"
            assert [tool["name"] for tool in listed["result"]["tools"]] == [tool_name]
            value = _request(process, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": tool_name, "arguments": {} if batch else {"resource_index": 0}}})
            payload = value["result"]["structuredContent"]
            assert (payload["resources"][0] if batch else payload)["citable_ids"] == ["PA.BAR.K1"]
            denied = _request(process, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "get_role_baseline", "arguments": {}}})
            assert "error" in denied or denied["result"]["isError"]
        except Exception as exc:
            errors.append(repr(exc))
            raise
        finally:
            process.stdin.close()
            process.wait(timeout=5)
        return ChildExecution({"source_digest": context["job_digest"], "evidence_refs": ["PA.BAR.K1"],
                               "support": "known", "challenge": "", "unknown": ""}, AIUsage())
    with StageChallengeHost(_job(), execute=execute, parent_ready=lambda: None, deadline=time.monotonic()+15) as host:
        host.start("check", [{"operation": "get_pa_evidence", "arguments": {"fact_ids": ["PA.BAR.K1"]}}])
        assert host.collect(10)["status"] == "completed", errors


def test_close_revokes_endpoint_even_when_executor_misses_shutdown(monkeypatch):
    from unittest.mock import Mock
    host = StageChallengeHost(_job(), execute=lambda *args: None, parent_ready=lambda: None,
                              deadline=time.monotonic()+5)
    host.__enter__()
    binding = host.binding()
    host._thread = Mock()
    host._thread.is_alive.return_value = True
    host.close()
    host.close()
    assert host.audit()["failure_code"] == "child_shutdown_timeout"
    with pytest.raises(ValueError, match="unavailable"):
        challenge_rpc(binding["endpoint"], binding["token"], {"action": "collect"})


@pytest.mark.parametrize("stage1_active", [False, True])
def test_resumed_mcp_uses_new_stage_capability_and_old_token_cannot_cross_stage(tmp_path, monkeypatch, stage1_active):
    import json
    from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
    from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer
    from pa_agent.agent_runtime.frozen_job import FrozenAgentJobBuilder, FrozenAgentJobStore
    from pa_agent.agent_runtime.stage_challenge import CHALLENGE_PROTOCOL
    from tests.unit import test_frozen_agent_job as fixtures
    monkeypatch.setattr(fixtures, "_builder", lambda: FrozenAgentJobBuilder(runtime_version="test",
        agents_text="test", skill_documents={"stage1": "test", "stage2": "test"}))
    first = fixtures._job()
    second = fixtures._job(role="stage2", core_context={"pa_session_continuation": {
        "protocol_version": "pa_session_continuation_v1", "parent_job_digest": first.manifest.job_digest}})
    store = FrozenAgentJobStore(tmp_path / "jobs")
    store.publish(first)
    store.publish(second)
    evidence_binding, child_binding = tmp_path / "evidence.json", tmp_path / "child.json"
    def bind(job):
        evidence_binding.write_text(json.dumps({"schema_version": 1, "job_root": str(store.publish(job).parent),
            "job_digest": job.manifest.job_digest, "audit_output": str(tmp_path / "audit.json")}), encoding="utf-8")
    def execute(context, endpoint, token, *args):
        challenge_rpc(endpoint, token, {"action": "read", "resource_index": 0})
        return ChildExecution({"source_digest": context["job_digest"], "evidence_refs": ["PA.BAR.K1"],
                               "support": "known", "challenge": "", "unknown": ""}, AIUsage())
    bind(first)
    server = EvidenceMcpServer(FrozenEvidenceGateway(first), job_digest=first.manifest.job_digest,
        audit_output=tmp_path / "audit.json", binding_file=evidence_binding,
        job_root=tmp_path / "jobs", challenge_binding=child_binding)
    with StageChallengeHost(first, execute=execute, parent_ready=lambda: None, deadline=time.monotonic()+5) as old:
        old_binding = old.binding()
        child_binding.write_text(json.dumps(old_binding if stage1_active else {
            "protocol": CHALLENGE_PROTOCOL, "job_digest": first.manifest.job_digest, "disabled": True}), encoding="utf-8")
        assert server._call_tool("get_stage_challenge", {})["isError"] is (not stage1_active)
    with StageChallengeHost(second, execute=execute, parent_ready=lambda: None, deadline=time.monotonic()+5) as current:
        bind(second)
        child_binding.write_text(json.dumps(current.binding()), encoding="utf-8")
        with pytest.raises(ValueError, match="not authorized"):
            challenge_rpc(current.endpoint, old_binding["token"], {"action": "collect"})
        started = server._call_tool("start_stage_challenge", {"question": "check", "resources": [
            {"operation": "get_pa_evidence", "arguments": {"fact_ids": ["PA.BAR.K1"]}}]})
        assert not started["isError"]
        assert server._call_tool("get_stage_challenge", {"wait_seconds": 3})["structuredContent"]["status"] == "completed"
