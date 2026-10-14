from __future__ import annotations

import copy
from dataclasses import asdict, replace

import pytest

from pa_agent.agent_runtime.codex_worker import CodexCliWorker, CodexWorkerOutputError
from pa_agent.agent_runtime.evidence import EvidenceAccessDenied
from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from pa_agent.agent_runtime.publisher import CodexJobPublisher
from pa_agent.agent_runtime.runtime_assets import load_codex_runtime_assets
from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer
from pa_agent.agent_runtime.repair_contract import append_repair_context
from pa_agent.ai.pa_handoff import compile_pa_handoff
from pa_agent.ai.pa_provider_contracts import schema_for_joint_stage2_draft
from tests.integration.conftest import make_frame
from tests.unit.test_codex_job_publisher import _request


def test_current_role_receives_its_contract_without_inactive_research_instructions(tmp_path):
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path, job_root=tmp_path / "jobs")
    path = publisher(_request("stage1", {"task_id": "guidance-test"}), 1)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    packet = FrozenEvidenceGateway(job).get_stage1_decision_packet()
    guidance = packet["role_contract"].get("runtime_guidance")
    assert isinstance(guidance, dict), "The frozen packet must deliver the current role contract."
    assert guidance["role"] == "stage1"
    assert guidance["output_mode"] == "result_draft_or_stop"
    assert "stage_challenge" not in guidance
    assert "layered_knowledge" not in guidance
    assert "start_stage_challenge(question, resources)" not in job.runtime["agents_text"]
    assert "layered_stage2_research_v1" not in job.runtime["agents_text"]


def test_unknown_frozen_guidance_policy_is_rejected_by_gateway_and_worker(tmp_path):
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path, job_root=tmp_path / "jobs")
    path = publisher(_request("stage1", {"task_id": "unknown-guidance"}), 1)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    bad = replace(job, core_context={**job.core_context, "runtime_guidance_policy": "unknown"})
    with pytest.raises(EvidenceAccessDenied, match="guidance"):
        FrozenEvidenceGateway(bad)
    with pytest.raises(CodexWorkerOutputError, match="guidance"):
        CodexCliWorker._validate_required_evidence_reads(bad, ())


def test_output_and_failure_wording_are_part_of_the_versioned_runtime_assets():
    assets = load_codex_runtime_assets()
    assert "role_contracts" in assets.guidance_documents
    assert "result_draft_only" in assets.guidance_documents["role_contracts"]
    assert "unrepairable" in assets.guidance_documents["role_contracts"]


def _publish_role(tmp_path, role, *, identity=None, repair=False):
    context = {"task_id": "role-guidance"}
    manifest_role = "stage2" if role == "joint_stage2" else role
    if role == "joint_stage2":
        context.update(
            role_protocol={"contract_version": "joint_stage2_agent_protocol_v1"},
            pa_hypotheses=compile_pa_handoff({}, None, make_frame()).model_dump(mode="json"),
            vpa_catalog_id="frozen-vpa",
            vpa_evidence={"catalog_id": "frozen-vpa", "data_status": "unavailable",
                "summary": "No usable volume evidence.", "observations": [],
                "hypothesis_assessments": [], "background_relation": "insufficient_data",
                "entry_timing_relation": "insufficient_data",
                "next_confirmation": {"state": "not_applicable", "trigger": None,
                                      "requires_closed_bar": False}},
        )
    request = _request(manifest_role, context)
    if role == "joint_stage2":
        request = replace(request, schema=schema_for_joint_stage2_draft())
    if repair:
        field = "protocol_version" if role == "joint_stage2" else "field"
        request = replace(request, messages=[{"role": "user", "content": append_repair_context(
            "Repair field representation only.", original_draft='{"' + field + '":"before"}',
            findings=[field], editable_pointers=["/" + field],
        )}])
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path,
                                   job_root=tmp_path / "jobs", runtime_identity=identity)
    path = publisher(request, 2 if repair else 1)
    return FrozenAgentJobStore(path.parent).open(path.name)


@pytest.mark.parametrize("role", ["stage1", "stage2", "joint_stage2", "vpa_evidence"])
def test_actual_role_payload_delivers_guidance_and_preserves_mandatory_audit(tmp_path, role):
    job = _publish_role(tmp_path, role)
    gateway = FrozenEvidenceGateway(job)
    server = EvidenceMcpServer(gateway, job_digest=job.manifest.job_digest)
    if role == "stage1":
        payload = server._call_tool("get_stage1_decision_packet", {})["structuredContent"]["decision_packet"]
        delivered = payload["role_contract"]["runtime_guidance"]
    else:
        payload = server._call_tool("get_role_baseline", {})["structuredContent"]
        core = next(item["payload"]["core_context"] for item in payload["resources"]
                    if item["operation"] == "get_market_core")
        delivered = core["role_protocol"]["runtime_guidance"]
    assert delivered == job.core_context["role_protocol"]["runtime_guidance"]
    assert delivered["output_mode"] == ("result_draft_only" if job.manifest.role == "stage2"
                                         else "result_draft_or_stop")
    CodexCliWorker._validate_required_evidence_reads(job, tuple(asdict(e) for e in gateway.audit_events))
    with pytest.raises(CodexWorkerOutputError):
        CodexCliWorker._validate_required_evidence_reads(job, ())


@pytest.mark.parametrize("role", ["stage1", "stage2", "joint_stage2", "vpa_evidence"])
def test_repair_delivers_only_its_current_contract_even_with_research_policy(tmp_path, role):
    job = _publish_role(tmp_path, role, repair=True,
                        identity={"investigation_policy": "agentic", "stage_subagents": "single_challenge_v1"})
    gateway = FrozenEvidenceGateway(job)
    findings = gateway.get_repair_findings()
    guidance = findings["runtime_guidance"]
    assert guidance == job.core_context["role_protocol"]["runtime_guidance"]
    assert guidance["entrypoint"] == "get_repair_findings"
    assert guidance["output_mode"] == "patch_envelope"
    assert "stage_challenge" not in guidance and "layered_knowledge" not in guidance
    CodexCliWorker._validate_required_evidence_reads(job, tuple(asdict(e) for e in gateway.audit_events))
    result = EvidenceMcpServer(gateway, job_digest=job.manifest.job_digest)._call_tool("get_role_baseline", {})
    assert result["isError"]
    assert "structuredContent" not in result


@pytest.mark.parametrize("role,policy,investigation,expected", [
    ("stage1", "stage1_challenge_v1", "agentic", True),
    ("stage1", "vpa_challenge_v1", "agentic", False),
    ("vpa_evidence", "vpa_challenge_v1", "agentic", True),
    ("stage2", "single_challenge_v1", "agentic", True),
    ("joint_stage2", "stage2_challenge_v1", "agentic", True),
    ("stage1", "off", "agentic", False),
    ("stage1", "off", "fixed", False),
])
def test_challenge_details_are_frozen_only_for_authorized_roles(tmp_path, role, policy, investigation, expected):
    job = _publish_role(tmp_path, role, identity={"investigation_policy": investigation, "stage_subagents": policy})
    guidance = job.core_context["role_protocol"]["runtime_guidance"]
    assert ("stage_challenge" in guidance) == expected
    if expected:
        assert guidance["stage_challenge"] == load_codex_runtime_assets().guidance_documents["stage_challenge"]
        bad = copy.deepcopy(dict(job.core_context))
        del bad["role_protocol"]["runtime_guidance"]["stage_challenge"]
        with pytest.raises(EvidenceAccessDenied, match="guidance"):
            FrozenEvidenceGateway(replace(job, core_context=bad))


def test_packet_contract_copy_cannot_diverge_from_current_role_guidance(tmp_path):
    job = _publish_role(tmp_path, "stage1")
    bad = copy.deepcopy(dict(job.core_context))
    bad["stage1_decision_packet"]["role_contract"]["runtime_guidance"]["output_mode"] = "result_draft_only"
    with pytest.raises(EvidenceAccessDenied, match="packet delivery"):
        FrozenEvidenceGateway(replace(job, core_context=bad))


def test_stage2_resume_keeps_common_instructions_and_delivers_current_role_contract(tmp_path):
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path, job_root=tmp_path / "jobs")
    parent_path = publisher(_request("stage1", {"task_id": "same-snapshot"}), 1)
    parent = FrozenAgentJobStore(parent_path.parent).open(parent_path.name)
    FrozenEvidenceGateway(parent).get_stage1_decision_packet()
    path = publisher(_request("stage2", {"task_id": "same-snapshot", "pa_session_continuation": {
        "parent_job_digest": parent_path.name, "thread_id": "thread-guidance-0001",
    }}), 1)
    stage2 = FrozenAgentJobStore(path.parent).open(path.name)
    assert stage2.runtime["agents_text"] == parent.runtime["agents_text"]
    assert stage2.runtime["backend_runtime_identity"] == parent.runtime["backend_runtime_identity"]
    gateway = FrozenEvidenceGateway(stage2)
    baseline = EvidenceMcpServer(gateway, job_digest=path.name)._call_tool("get_role_baseline", {})["structuredContent"]
    assert baseline["baseline_version"] == "mandatory_pa_delta_v1"
    assert "get_price_frame" not in {e.operation for e in gateway.audit_events}
    core = next(item["payload"]["core_context"] for item in baseline["resources"]
                if item["operation"] == "get_market_core")
    assert core["role_protocol"]["runtime_guidance"]["output_mode"] == "result_draft_only"
    CodexCliWorker._validate_required_evidence_reads(stage2, tuple(asdict(e) for e in gateway.audit_events))


def test_layered_guidance_is_delivered_only_in_the_explicit_research_job(tmp_path):
    job = _publish_role(tmp_path, "joint_stage2", identity={"investigation_policy": "agentic",
        "stage_subagents": "off", "knowledge_delivery_policy": "layered_stage2_research_v1"})
    gateway = FrozenEvidenceGateway(job)
    result = EvidenceMcpServer(gateway, job_digest=job.manifest.job_digest)._call_tool("get_role_baseline", {})
    assert not result["isError"]
    baseline = result["structuredContent"]
    core = next(item["payload"]["core_context"] for item in baseline["resources"]
                if item["operation"] == "get_market_core")
    guidance = core["role_protocol"]["runtime_guidance"]
    assert guidance["layered_knowledge"] == load_codex_runtime_assets().guidance_documents["layered_knowledge"]
    assert "layered_stage2_research_v1" not in core["role_skill"]
    bad = copy.deepcopy(dict(job.core_context))
    del bad["role_protocol"]["runtime_guidance"]["layered_knowledge"]
    with pytest.raises(EvidenceAccessDenied, match="guidance"):
        FrozenEvidenceGateway(replace(job, core_context=bad))


def test_frozen_role_change_and_repair_delivery_mismatch_are_rejected(tmp_path):
    normal = _publish_role(tmp_path, "stage1")
    with pytest.raises(EvidenceAccessDenied, match="guidance"):
        FrozenEvidenceGateway(replace(normal, manifest=replace(normal.manifest, role="stage2")))
    repair = _publish_role(tmp_path / "repair", "stage1", repair=True)
    bad_findings = copy.deepcopy(dict(repair.repair_findings))
    bad_findings["runtime_guidance"]["output_mode"] = "result_draft_or_stop"
    with pytest.raises(EvidenceAccessDenied, match="repair delivery"):
        FrozenEvidenceGateway(replace(repair, repair_findings=bad_findings))


def test_legacy_job_without_guidance_marker_retains_its_frozen_contract():
    from tests.unit.test_frozen_agent_job import _job

    legacy = _job(core_context={"role_protocol": {"legacy_wording": "Frozen legacy contract."}})
    before = dict(legacy.core_context)
    delivered = FrozenEvidenceGateway(legacy).get_market_core()
    assert delivered["role_protocol"] == before["role_protocol"]
    assert "runtime_guidance_policy" not in delivered
    assert legacy.core_context == before
