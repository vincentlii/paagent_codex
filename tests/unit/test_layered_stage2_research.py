from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from pa_agent.agent_runtime.codex_worker import CodexCliWorker, CodexWorkerOutputError
from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from pa_agent.agent_runtime.knowledge_bundle import build_knowledge_bundle
from pa_agent.agent_runtime.publisher import CodexJobPublisher
from pa_agent.ai.pa_handoff import compile_pa_handoff
from tests.integration.conftest import make_frame
from tests.unit.test_codex_job_publisher import _request


def test_research_layer_keeps_full_text_frozen_and_counterexample_spine(tmp_path):
    strategy = """<!-- knowledge: overview; aliases: channel -->
【核心知识】
<!-- knowledge: core -->
一、核心原则
完整原则。
二、具体入场
具体案例和触发条件。
【例外与陷阱】
<!-- knowledge: traps -->
反证及不交易条件。
"""
    recognition = "【核心知识】\n识别主解释及竞争解释。\n【例外与陷阱】\n识别反证。\n"
    (tmp_path / "上涨通道交易策略.txt").write_text(strategy, encoding="utf-8")
    (tmp_path / "上涨通道分析识别.txt").write_text(recognition, encoding="utf-8")

    full, full_manifest = build_knowledge_bundle(
        prompt_dir=tmp_path,
        knowledge_ids=["上涨通道分析识别.txt", "上涨通道交易策略.txt"],
        role="stage2", joint_stage2=True,
    )
    layered, manifest = build_knowledge_bundle(
        prompt_dir=tmp_path,
        knowledge_ids=["上涨通道分析识别.txt", "上涨通道交易策略.txt"],
        role="stage2", joint_stage2=True, layered_stage2_research=True,
    )

    assert layered == full
    assert [item["loading_policy"] for item in full_manifest["modules"]] == ["mandatory"] * 2
    assert [item["loading_policy"] for item in manifest["modules"]] == ["mandatory", "on_demand"]
    strategy_module = manifest["modules"][1]
    sections = layered["上涨通道交易策略.txt"]["sections"]
    mandatory_ids = strategy_module["baseline_section_ids"]
    assert mandatory_ids
    assert any("反证" in item["text"] for item in sections if item["section_id"] in mandatory_ids)
    assert any("完整原则" in item["text"] for item in sections if item["section_id"] in mandatory_ids)
    assert any("具体案例" in item["text"] for item in sections if item["section_id"] not in mandatory_ids)


def test_research_stage2_baseline_and_audit_keep_optional_text_frozen(tmp_path):
    (tmp_path / "上涨通道分析识别.txt").write_text("# 识别\n完整主替代结构。\n", encoding="utf-8")
    (tmp_path / "上涨通道交易策略.txt").write_text(
        "# 原则\n入场背景。\n# 条件\n真实案例。\n# 细节\n详细比较。\n"
        "# 【例外与陷阱】\n反证和禁令。\n# 其他\n后续案例。\n",
        encoding="utf-8",
    )
    frame = make_frame()
    hypotheses = compile_pa_handoff({}, None, frame).model_dump(mode="json")
    report = {
        "catalog_id": "frozen-vpa", "data_status": "available",
        "background_relation": "neutral", "entry_timing_relation": "insufficient_data",
        "observations": [], "hypothesis_assessments": [],
        "next_confirmation": {"state": "not_applicable", "trigger": None,
                              "requires_closed_bar": False},
        "summary": "No decisive volume evidence.",
    }
    context = {
        "task_id": "layered-research", "pa_hypotheses": hypotheses,
        "vpa_catalog_id": "frozen-vpa", "vpa_evidence": report,
        "router_manifest": ["上涨通道分析识别.txt", "上涨通道交易策略.txt"],
        "allowed_knowledge_ids": ["上涨通道分析识别.txt", "上涨通道交易策略.txt"],
        "role_protocol": {"contract_version": "joint_stage2_agent_protocol_v1"},
    }
    request = replace(_request("stage2", context), schema={
        "type": "object", "properties": {
            "protocol_version": {"const": "pa_vpa_stage2_draft_v1"},
        },
    })
    identity = {"investigation_policy": "agentic", "stage_subagents": "off",
                "knowledge_delivery_policy": "layered_stage2_research_v1"}
    publisher = CodexJobPublisher(frame=frame, prompt_dir=tmp_path,
                                  job_root=tmp_path / "jobs", runtime_identity=identity)
    path = publisher(request, 1)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    modules = job.knowledge_manifest["modules"]
    assert [item["loading_policy"] for item in modules] == ["mandatory", "on_demand"]
    gateway = FrozenEvidenceGateway(job)
    baseline = EvidenceMcpServer(gateway, job_digest=path.name)._call_tool(
        "get_role_baseline", {},
    )["structuredContent"]
    assert baseline["baseline_version"] == "layered_stage2_research_v1"
    assert baseline["stage2_handoff_receipt"]["read_plan_complete"] is True
    assert baseline["on_demand_sections"]
    indexed = baseline["on_demand_sections"][0]
    section_id = indexed["sections"][0]["section_id"]
    selected = gateway.read_allowed_knowledge(
        knowledge_id=indexed["knowledge_module_id"], section_ids=[section_id],
    )
    expected = next(item["text"] for item in job.knowledge_documents[
        indexed["knowledge_module_id"]]["sections"] if item["section_id"] == section_id)
    assert selected["sections"][0]["text"] == expected
    ops = [item.operation for item in gateway.audit_events]
    assert "read_allowed_knowledge" in ops
    assert "list_allowed_knowledge" not in ops
    CodexCliWorker._validate_required_evidence_reads(
        job, tuple(asdict(item) for item in gateway.audit_events),
    )
    missing_spine = tuple(
        asdict(item)
        for item in gateway.audit_events if item.operation != "read_allowed_knowledge"
    )
    with pytest.raises(CodexWorkerOutputError, match="baseline sections"):
        CodexCliWorker._validate_required_evidence_reads(job, missing_spine)

    direct_gateway = FrozenEvidenceGateway(job)
    direct_server = EvidenceMcpServer(direct_gateway, job_digest=path.name)
    for operation, arguments in direct_gateway.mandatory_read_plan():
        assert direct_server._call_tool(operation, arguments)["isError"] is False
    with pytest.raises(CodexWorkerOutputError, match="baseline assembly"):
        CodexCliWorker._validate_required_evidence_reads(
            job, tuple(asdict(item) for item in direct_gateway.audit_events),
        )
