from __future__ import annotations

import copy
import json
from dataclasses import asdict

import pytest

from pa_agent.ai.json_validator import JsonValidator, Ok
from pa_agent.ai.router import route_strategy_files
from pa_agent.ai.router import route_strategy_knowledge
from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.config.paths import PROMPT_DIR
from pa_agent.ai.vpa_context_profile import build_vpa_knowledge_context
from pa_agent.ai.pa_handoff import compile_pa_handoff
from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog, prompt_vpa_evidence_ids
from tests.integration.conftest import make_frame
from tests.unit.test_pa_provider_contracts import _stage1_draft


def competing_stage1():
    draft = _stage1_draft()
    range_hypothesis = copy.deepcopy(draft["market_hypotheses"]["alternatives"][0])
    bearish = copy.deepcopy(draft["market_hypotheses"]["primary"])
    bearish.update(direction="bearish", setup_hint="sell on pullback",
                   thesis="The bearish channel is a competing explanation.")
    draft["market_hypotheses"]["alternatives"] = [bearish, range_hypothesis]
    result = JsonValidator().validate("stage1", json.dumps(draft), kline_frame=make_frame())
    assert isinstance(result, Ok)
    return result.obj


def test_router_covers_every_declared_hypothesis_independently_of_alternative_order():
    stage1 = competing_stage1()
    before = copy.deepcopy(stage1)
    files = route_strategy_files(stage1)
    assert {
        "上涨通道分析识别.txt", "下跌通道分析识别.txt", "下跌通道交易策略.txt",
        "震荡区间分析识别.txt", "震荡区间交易策略.txt", "文件21-铁丝网与无交易环境.txt",
    } <= set(files)
    swapped = copy.deepcopy(stage1)
    swapped["market_hypotheses"]["alternatives"].reverse()
    swapped["alternative_cycle_position"] = swapped["market_hypotheses"]["alternatives"][0]["cycle_position"]
    assert set(route_strategy_files(swapped)) == set(files)
    assert len(files) == len(set(files))
    assert stage1 == before


def test_vpa_citable_windows_include_challenges_without_inventing_missing_windows():
    frame = make_frame()
    hypotheses = compile_pa_handoff(competing_stage1(), None, frame).model_dump(mode="json")
    for hypothesis in [hypotheses["primary"], hypotheses["alternative"], *hypotheses["alternatives"]]:
        hypothesis["fact_refs"] = ["PA.BAR.K1"]
        hypothesis["challenging_fact_refs"] = ["PA.WINDOW.K20", "PA.WINDOW.K999"]
    before = copy.deepcopy(hypotheses)
    catalog = build_vpa_evidence_catalog(frame)
    visible = prompt_vpa_evidence_ids(catalog, hypotheses)
    assert "vpa-w020" in visible
    assert "vpa-w999" not in visible
    assert hypotheses == before


def test_stage2_loads_routed_review_knowledge_without_authorizing_reverse_orders():
    stage1 = competing_stage1()
    messages = PromptAssembler(PROMPT_DIR).build_stage2(
        make_frame(), stage1, route_strategy_files(stage1), [],
    )
    text = "\n".join(item["content"] for item in messages)
    bearish = (PROMPT_DIR / "下跌通道分析识别.txt").read_text("utf-8")
    assert bearish in text
    assert "复核知识不授予反向下单权限" in text
    assert "review 不独立授予 Setup" in text
    assert "market_hypotheses" in text


def test_vpa_knowledge_includes_second_alternative_cycle():
    stage1 = competing_stage1()
    before = copy.deepcopy(stage1)
    context = build_vpa_knowledge_context(stage1)
    assert context.module_names == ("core", "trend_pullback", "breakout_retest", "trading_range_events")
    assert context.cycles == frozenset({"normal_channel", "trading_range"})
    assert stage1 == before


def test_orchestrator_and_frozen_stage2_deliver_complete_review_sources(tmp_path, monkeypatch):
    from pa_agent.agent_runtime.codex_worker import CodexCliWorker, CodexWorkerOutputError
    from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
    from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer
    from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
    from pa_agent.agent_runtime.publisher import CodexJobPublisher
    from tests.unit import test_joint_pipeline_runtime as pipeline_fixture
    from tests.unit.test_pa_vpa_joint_orchestrator import _vpa_report

    stage1 = competing_stage1()
    report = _vpa_report()
    report["vpa_report"]["hypothesis_assessments"] = [
        {**report["vpa_report"]["hypothesis_assessments"][0], "hypothesis_id": hypothesis_id}
        for hypothesis_id in ["pa-primary", "pa-alternative-1", "pa-alternative-2"]
    ]
    from tests.unit import test_pa_vpa_joint_orchestrator as vpa_fixture
    monkeypatch.setattr(vpa_fixture, "_vpa_report", lambda: copy.deepcopy(report))
    captured = []
    record, _, _ = pipeline_fixture.run_joint(codex=True, stage1_override=stage1, captured_requests=captured)
    assert record.exception is None, record.exception
    request = next(item for item in captured if item.role == "stage2")
    selection = request.agent_context["knowledge_selection"]
    assert selection == route_strategy_knowledge(stage1)
    assert selection["下跌通道分析识别.txt"] == {
        "purpose": "review", "hypothesis_ids": ["pa-alternative-1"],
    }
    assert "下跌通道分析识别.txt" in request.agent_context["allowed_knowledge_ids"]
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=PROMPT_DIR, job_root=tmp_path / "jobs")
    path = publisher(request, 1)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    module = next(item for item in job.knowledge_manifest["modules"]
                  if item["knowledge_module_id"] == "下跌通道分析识别.txt")
    assert module["purpose"] == "review"
    assert module["hypothesis_ids"] == ["pa-alternative-1"]
    assert module["loading_policy"] == "mandatory"
    gateway = FrozenEvidenceGateway(job)
    result = EvidenceMcpServer(gateway, job_digest=path.name)._call_tool("get_role_baseline", {})
    assert not result["isError"]
    summary = next(item for item in result["structuredContent"]["mandatory_modules"]
                   if item["knowledge_module_id"] == "下跌通道分析识别.txt")
    assert summary.get("purpose") == "review"
    assert summary.get("hypothesis_ids") == ["pa-alternative-1"]
    resources = result["structuredContent"]["resources"]
    delivered = next(item["payload"] for item in resources if item["operation"] == "read_knowledge_module"
                     and item["arguments"]["knowledge_id"] == "下跌通道分析识别.txt")
    assert "".join(section["text"] for section in delivered["sections"]) == (
        PROMPT_DIR / "下跌通道分析识别.txt").read_text("utf-8")
    events = tuple(asdict(e) for e in gateway.audit_events)
    CodexCliWorker._validate_required_evidence_reads(job, events)
    with pytest.raises(CodexWorkerOutputError):
        CodexCliWorker._validate_required_evidence_reads(job, tuple(e for e in events if e["request"].get(
            "knowledge_id") != "下跌通道分析识别.txt"))
