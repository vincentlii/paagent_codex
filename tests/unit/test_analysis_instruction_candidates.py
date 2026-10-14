from __future__ import annotations

import copy
import json
from dataclasses import replace
from types import MappingProxyType

import pytest

from pa_agent.agent_runtime.codex_worker import CodexCliWorker, CodexWorkerOutputError
from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from pa_agent.agent_runtime.publisher import CodexJobPublisher
from pa_agent.agent_runtime.runtime_assets import load_codex_runtime_assets
from pa_agent.ai.coherence_checks import validate_stage2_coherence
from pa_agent.ai.json_validator import ValidationError
from pa_agent.ai.pa_handoff import compile_pa_handoff
from pa_agent.ai.prompt_assembler import PromptAssembler, VPA_KNOWLEDGE_MODULES
from pa_agent.ai.retry_feedback import build_retry_feedback
from pa_agent.ai.retry_policy import extract_feedback_targets
from pa_agent.ai.role_validator import validate_vpa_evidence
from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog
from pa_agent.config.paths import PROMPT_DIR
from pa_agent.config.settings import PromptSettings
from pa_agent.orchestrator.two_stage import _VPA_ROLE_PROTOCOL
from tests.integration.conftest import VALID_STAGE1, make_frame
from tests.unit.test_codex_job_publisher import _request
from tests.unit.test_role_contracts import _vpa_payload


@pytest.mark.parametrize("direction,order_direction", [("bullish", "做空"), ("bearish", "做多")])
def test_direction_feedback_preserves_repair_scope_without_restarting_upstream(direction, order_direction):
    stage1 = {"direction": direction}
    stage2 = {"decision": {"order_type": "限价单", "order_direction": order_direction}}
    before = copy.deepcopy((stage1, stage2))
    issues = validate_stage2_coherence(stage2, stage1)
    assert len(issues) == 1
    assert "违反冻结方向权限" in issues[0]
    assert "不得重跑或改写 Stage 1" in issues[0]
    assert (stage1, stage2) == before
    err = ValidationError(category="c", stage="stage2", raw_text=json.dumps(stage2),
                          invalid_fields=["s2:" + issues[0]])
    feedback = build_retry_feedback(err, stage="stage2", attempt=1, max_attempts=1)
    assert "回到阶段一重做" not in feedback
    assert "阶段一 cycle_position / direction" in feedback
    # A diagnostic message must not demand the Codex repair envelope from Direct.
    assert "unrepairable" not in feedback
    old = ["s2:order_direction 与阶段一 direction 相反；同一快照必须回到阶段一重做方向诊断"]
    assert extract_feedback_targets(err.invalid_fields, []) == extract_feedback_targets(old, [])


def _vpa_request(protocol):
    frame = make_frame()
    catalog = build_vpa_evidence_catalog(frame)
    hypotheses = compile_pa_handoff(copy.deepcopy(VALID_STAGE1), None, frame)
    return _request("vpa_evidence", {
        "task_id": "neutral-guidance", "stage1_diagnosis": copy.deepcopy(VALID_STAGE1),
        "pa_hypotheses": hypotheses.model_dump(mode="json"), "role_evidence": catalog,
        "role_protocol": copy.deepcopy(protocol),
        "allowed_knowledge_ids": list(VPA_KNOWLEDGE_MODULES.values()),
    })


def test_direct_and_frozen_codex_allow_no_diagnostic_increment_after_complete_reads(tmp_path):
    frame = make_frame()
    hypotheses = compile_pa_handoff(copy.deepcopy(VALID_STAGE1), None, frame)
    assembler = PromptAssembler(PROMPT_DIR, prompt_settings=PromptSettings(vpa_enabled=True))
    direct = "\n".join(m["content"] for m in assembler.build_vpa_evidence_report(
        frame, VALID_STAGE1, hypotheses.model_dump(mode="json")))
    assert "数据可用不等于存在诊断性量价增量" in direct
    assert "未发现诊断性增量" in direct and "observations=[]" in direct
    assert "未读证据或工具失败" in direct
    assert "至少给出一项真正的" not in direct
    assert "When volume data is available, add at least one" not in direct

    publisher = CodexJobPublisher(frame=frame, prompt_dir=PROMPT_DIR, job_root=tmp_path / "jobs")
    path = publisher(_vpa_request(_VPA_ROLE_PROTOCOL), 1)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    protocol = "\n".join(job.core_context["role_protocol"]["interpretation_contract"])
    skill = job.runtime["skills"]["vpa_evidence"]
    assert "does not imply a diagnostic increment" in protocol
    assert "no diagnostic increment" in protocol and "empty observations" in protocol
    assert "unread evidence or tool failure" in protocol
    assert "数据可用不等于存在诊断性量价增量" in skill
    assert "未发现诊断性增量" in skill and "observations=[]" in skill
    assert "不能伪造完整 VPA 报告" in skill
    core = FrozenEvidenceGateway(job).read_knowledge_module(knowledge_id="wyckoff_vpa/core.txt")
    knowledge = "\n".join(section["text"] for section in core["sections"])
    assert "When volume data is available, add at least one" not in knowledge
    assert "no diagnostic increment" in knowledge
    with pytest.raises(CodexWorkerOutputError):
        CodexCliWorker._validate_required_evidence_reads(job, ())


def test_neutral_candidate_keeps_valid_evidence_and_unknown_reference_checks():
    frame = make_frame()
    hypotheses = compile_pa_handoff(copy.deepcopy(VALID_STAGE1), None, frame)
    catalog = build_vpa_evidence_catalog(frame)
    payload = _vpa_payload(catalog, hypotheses, relation="neutral")
    report = payload["vpa_report"]
    report["observations"] = []
    report["entry_timing_relation"] = "insufficient_data"
    report["next_confirmation"] = {"state": "not_applicable", "trigger": None}
    for item in report["hypothesis_assessments"]:
        item["evidence_ids"] = []
        item["reason"] = "No diagnostic increment after examining the available evidence."
    neutral = validate_vpa_evidence(json.dumps(payload), catalog=catalog, pa_hypotheses=hypotheses)
    assert neutral.ok and not neutral.hard_errors
    assert neutral.obj["vpa_report"]["observations"] == []
    assert "usable VPA data produced no contextual observations" in neutral.warnings
    supported = _vpa_payload(catalog, hypotheses)
    assert validate_vpa_evidence(json.dumps(supported), catalog=catalog, pa_hypotheses=hypotheses).ok
    challenged = _vpa_payload(catalog, hypotheses, relation="challenges")
    result = validate_vpa_evidence(json.dumps(challenged), catalog=catalog, pa_hypotheses=hypotheses)
    assert result.ok and result.obj["vpa_report"]["background_relation"] == "challenges"
    supported["vpa_report"]["observations"][0]["evidence_ids"] = ["vpa-unknown"]
    invalid = validate_vpa_evidence(json.dumps(supported), catalog=catalog, pa_hypotheses=hypotheses)
    assert not invalid.ok and invalid.hard_errors


def test_existing_vpa_job_keeps_its_frozen_skill_and_protocol(tmp_path, monkeypatch):
    from pa_agent.agent_runtime import publisher as publisher_module

    current_assets = load_codex_runtime_assets()
    old_skill = "Historical frozen VPA skill: available data must add information."
    old_assets = replace(current_assets, runtime_version="historical-vpa",
                         skill_documents=MappingProxyType({**current_assets.skill_documents,
                                                           "vpa_evidence": old_skill}))
    protocol = copy.deepcopy(_VPA_ROLE_PROTOCOL)
    protocol["interpretation_contract"] = ["Historical available-data rule."]
    monkeypatch.setattr(publisher_module, "load_codex_runtime_assets", lambda: old_assets)
    old_prompt = tmp_path / "historical-prompts"
    (old_prompt / "wyckoff_vpa").mkdir(parents=True)
    old_core = "<!-- knowledge: chapter-009 -->\nHistorical frozen core: available data must add a finding.\n"
    (old_prompt / "wyckoff_vpa/core.txt").write_text(old_core, encoding="utf-8")
    request = _vpa_request(protocol)
    request = replace(request, agent_context={**request.agent_context,
                                             "allowed_knowledge_ids": ["wyckoff_vpa/core.txt"]})
    old_publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=old_prompt,
                                    job_root=tmp_path / "old")
    path = old_publisher(request, 1)
    # Build a genuinely pre-guidance artifact, rather than using today's Publisher
    # and merely calling its newly added role protocol historical.
    from pa_agent.agent_runtime.frozen_job import FrozenAgentJobBuilder

    current_old = FrozenAgentJobStore(path.parent).open(path.name)
    old_context = {key: value for key, value in current_old.core_context.items()
                   if key != "runtime_guidance_policy"}
    old_context["role_protocol"] = protocol
    historical = FrozenAgentJobBuilder(runtime_version=old_assets.runtime_version,
        agents_text=old_assets.agents_text, skill_documents=old_assets.skill_documents).build(
        task_id=current_old.manifest.task_id, request_id=current_old.manifest.request_id,
        role="vpa_evidence", attempt=1, snapshot_hash=current_old.manifest.snapshot_hash,
        cutoff_ts_open=current_old.manifest.cutoff_ts_open,
        messages=current_old.request["messages"], output_schema=current_old.output_schema,
        fact_projection=current_old.fact_projection, bars=current_old.bars,
        knowledge_documents=current_old.knowledge_documents,
        allowed_knowledge_ids=list(current_old.knowledge_documents),
        budget=current_old.budget, knowledge_manifest=current_old.knowledge_manifest,
        core_context=old_context,
    )
    path = FrozenAgentJobStore(tmp_path / "historical").publish(historical)
    original_bytes = {p.relative_to(path): p.read_bytes() for p in path.rglob("*") if p.is_file()}
    monkeypatch.setattr(publisher_module, "load_codex_runtime_assets", lambda: current_assets)
    current = CodexJobPublisher(frame=make_frame(), prompt_dir=PROMPT_DIR, job_root=tmp_path / "new")
    current(_vpa_request(_VPA_ROLE_PROTOCOL), 1)
    restored = FrozenAgentJobStore(path.parent).open(path.name)
    assert {p.relative_to(path): p.read_bytes() for p in path.rglob("*") if p.is_file()} == original_bytes
    assert restored.runtime["skills"]["vpa_evidence"] == old_skill
    assert restored.core_context["role_protocol"] == protocol
    assert "\n".join(s["text"] for s in restored.knowledge_documents["wyckoff_vpa/core.txt"]["sections"]) == old_core
