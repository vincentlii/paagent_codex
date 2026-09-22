"""The batched baseline must deliver exactly the existing authorized resources."""
from dataclasses import asdict, replace
import json

import pytest

from pa_agent.agent_runtime.codex_worker import CodexCliWorker
from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from pa_agent.agent_runtime.publisher import CodexJobPublisher
from tests.integration.conftest import make_frame
from tests.unit.test_codex_job_publisher import _request


def test_vpa_delivery_matches_host_selected_window_scope(tmp_path):
    from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog, prompt_vpa_evidence_ids
    from pa_agent.agent_runtime.evidence import EvidenceAccessDenied

    catalog = build_vpa_evidence_catalog(make_frame())
    hypotheses = {"primary": {"fact_refs": ["PA.WINDOW.K8"]}}
    allowed = prompt_vpa_evidence_ids(catalog, hypotheses)
    assert "vpa-w005" not in allowed
    assert "vpa-w008" in allowed
    original = json.dumps(catalog, sort_keys=True)
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path, job_root=tmp_path / "jobs")
    path = publisher(_request("vpa_evidence", {
        "task_id": "vpa-scope", "role_evidence": catalog, "pa_hypotheses": hypotheses,
    }), 1)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    delivered = job.fact_projection["role_evidence"]
    assert {item["evidence_id"] for item in delivered["evidence"]} == allowed
    assert delivered["evidence"] == [item for item in catalog["evidence"] if item["evidence_id"] in allowed]
    assert json.dumps(catalog, sort_keys=True) == original
    assert set(job.core_context["evidence_index"]["vpa_evidence_ids"]) == allowed
    with pytest.raises(EvidenceAccessDenied):
        FrozenEvidenceGateway(job).get_vpa_evidence(["vpa-w005"])


@pytest.mark.parametrize("role", ["stage1", "stage2", "vpa_evidence", "joint_arbiter"])
def test_baseline_preserves_complete_resources_and_individual_audit(tmp_path, role):
    text = "# 原文\n完整案例、反证和边界，不得摘要。\n" * 100
    (tmp_path / "market.txt").write_text(text, encoding="utf-8")
    context = {"task_id": "baseline", "allowed_knowledge_ids": ["market.txt"],
               "higher_timeframe": {"evidence": [{"evidence_id": "HTF.H1", "value": 123}]},
               "experience_entries": [{"summary": "frozen experience"}],
               "role_evidence": {"evidence": [{"evidence_id": "vpa-e001", "facts": {"volume": 7}}]}}
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path, job_root=tmp_path/"jobs")
    path = publisher(_request(role, context), 1)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    if role == "stage1":
        legacy_core = {key: value for key, value in job.core_context.items()
                       if key != "stage1_decision_packet"}
        job = replace(job, core_context={**legacy_core, "context_policy": "semantic_parity_v1"},
                      budget=replace(job.budget, max_evidence_calls=32,
                                     max_evidence_bytes=1_048_576))
    gateway = FrozenEvidenceGateway(job)
    audit = tmp_path / "audit.json"
    server = EvidenceMcpServer(gateway, job_digest=path.name, audit_output=audit)
    result = server._call_tool("get_role_baseline", {})
    assert result["isError"] is False
    bundle = result["structuredContent"]
    assert bundle["baseline_version"] == "mandatory_baseline_v1"

    expected = [("get_job_manifest", {}), ("get_market_core", {})]
    if role != "stage1":
        expected.append(("get_upstream_artifact", {}))
    expected.append(("list_allowed_knowledge", {}))
    expected.append(("read_knowledge_module", {"knowledge_id": "market.txt"}))
    if role in {"stage1", "stage2"}:
        for newer in range(1, len(job.bars) + 1, 50):
            expected.append(("get_price_frame", {"older_seq": min(newer + 49, len(job.bars)), "newer_seq": newer}))
        expected.append(("get_htf_evidence", {"fact_ids": ["HTF.H1"]}))
    if role == "stage2":
        expected.append(("get_experience_entries", {}))
    if role == "vpa_evidence":
        expected.append(("get_vpa_evidence", {"evidence_ids": ["vpa-e001"]}))
    individual = EvidenceMcpServer(FrozenEvidenceGateway(job), job_digest=path.name)
    assert bundle["resources"] == [
        {"operation": operation, "arguments": arguments,
         "payload": individual._call_tool(operation, arguments)["structuredContent"]}
        for operation, arguments in expected
    ]
    events = json.loads(audit.read_text("utf-8"))["events"]
    assert [(event["operation"], event["request"]) for event in events] == expected
    assert {event["mcp_call_index"] for event in events} == {1}
    assert all(event["status"] == "ok" for event in events)
    CodexCliWorker._validate_required_evidence_reads(job, tuple(events))


def test_baseline_does_not_bypass_resource_budget_or_repair_scope(tmp_path):
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path, job_root=tmp_path/"jobs")
    path = publisher(_request("stage1", {"task_id": "bounded"}), 1)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    for limited in (
        replace(job, budget=replace(job.budget, max_evidence_calls=1)),
        replace(job, repair_findings={"editable_pointers": ["/field"]}),
    ):
        gateway = FrozenEvidenceGateway(limited)
        server = EvidenceMcpServer(gateway, job_digest=path.name)
        result = server._call_tool("get_role_baseline", {})
        assert result["isError"] is True
        assert "structuredContent" not in result
        assert any(asdict(event)["status"] == "denied" for event in gateway.audit_events)


@pytest.mark.parametrize("role", ["stage1", "stage2"])
def test_baseline_supplies_exact_fact_index_without_a_discovery_round(tmp_path, role):
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path, job_root=tmp_path / "jobs")
    path = publisher(_request(role, {"task_id": "fact-navigation"}), 1)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    if role == "stage1":
        legacy_core = {key: value for key, value in job.core_context.items()
                       if key != "stage1_decision_packet"}
        job = replace(job, core_context={**legacy_core, "context_policy": "semantic_parity_v1"},
                      budget=replace(job.budget, max_evidence_calls=32,
                                     max_evidence_bytes=1_048_576))
    gateway = FrozenEvidenceGateway(job)
    server = EvidenceMcpServer(gateway, job_digest=path.name)
    baseline = server._call_tool("get_role_baseline", {})["structuredContent"]
    core = next(item["payload"]["core_context"] for item in baseline["resources"]
                if item["operation"] == "get_market_core")
    ids = core["fact_id_index"]
    expected = sorted(item["fact_id"] for item in job.fact_projection["facts"])
    assert ids == expected
    assert "PA.WINDOW.K5" in ids
    assert all(item.startswith("PA.") for item in ids)
    selected = gateway.get_pa_evidence(["PA.WINDOW.K5"])
    assert selected == [item for item in job.fact_projection["facts"] if item["fact_id"] == "PA.WINDOW.K5"]
    assert not any(event.operation == "list_fact_ids" for event in gateway.audit_events)
    assert job.core_context["fact_id_index"] == ids
    assert "fact_id_index" not in json.dumps(job.request["messages"])
