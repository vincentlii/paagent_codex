from __future__ import annotations

import pytest

from pa_agent.agent_runtime.codex_worker import CodexCliWorker, CodexWorkerOutputError
from pa_agent.agent_runtime.evidence import EvidenceAccessDenied, FrozenEvidenceGateway
from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from pa_agent.agent_runtime.publisher import CodexJobPublisher
from tests.integration.conftest import make_frame
from tests.unit.test_codex_job_publisher import _request


def _bounded_job(tmp_path):
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path,
                                  job_root=tmp_path / "jobs")
    path = publisher(_request("stage1", {"task_id": "bounded-v2"}), 1)
    return FrozenAgentJobStore(path.parent).open(path.name)


def test_bounded_stage1_exposes_one_replayable_packet_not_legacy_baseline(tmp_path):
    job = _bounded_job(tmp_path)
    gateway = FrozenEvidenceGateway(job)
    assert "get_stage1_decision_packet" in gateway.available_tool_names()
    assert "get_role_baseline" not in gateway.available_tool_names()
    result = EvidenceMcpServer(gateway, job_digest=job.manifest.job_digest)._call_tool(
        "get_stage1_decision_packet", {}
    )
    packet = result["structuredContent"]["decision_packet"]
    assert packet == job.core_context["stage1_decision_packet"]
    assert packet["role_skill"] == job.runtime["skills"]["stage1"]
    events = tuple({"operation": event.operation, "status": event.status,
                    "request": event.request} for event in gateway.audit_events)
    CodexCliWorker._validate_required_evidence_reads(job, events)


def test_bounded_stage1_rejects_missing_duplicate_or_excess_optional_reads(tmp_path):
    job = _bounded_job(tmp_path)
    packet = {"operation": "get_stage1_decision_packet", "status": "ok", "request": {}}
    with pytest.raises(CodexWorkerOutputError, match="exactly one"):
        CodexCliWorker._validate_required_evidence_reads(job, ())
    with pytest.raises(CodexWorkerOutputError, match="exactly one"):
        CodexCliWorker._validate_required_evidence_reads(job, (packet, packet))
    optional = {"operation": "get_pa_evidence", "status": "ok",
                "request": {"fact_ids": ["PA.WINDOW.K5"]}}
    with pytest.raises(CodexWorkerOutputError, match="optional"):
        CodexCliWorker._validate_required_evidence_reads(
            job, (packet, optional, optional, optional)
        )

    gateway = FrozenEvidenceGateway(job)
    gateway.get_stage1_decision_packet()
    gateway.get_pa_evidence(["PA.WINDOW.K5"])
    gateway.get_pa_evidence(["PA.WINDOW.K8"])
    with pytest.raises(EvidenceAccessDenied, match="at most two"):
        gateway.get_pa_evidence(["PA.WINDOW.K10"])
