"""Predecision evidence is not an assessment of an order that does not yet exist."""
import copy
import json

import pytest

from pa_agent.ai.role_contracts import PREDECISION_VPA_SCOPE, PREDECISION_VPA_SCHEMA, VPA_EVIDENCE_SCHEMA
from pa_agent.ai.pa_handoff import compile_pa_handoff
from pa_agent.ai.role_validator import validate_vpa_evidence
from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog
from tests.integration.conftest import VALID_STAGE1, make_frame
from tests.unit.test_role_contracts import _vpa_payload


def predecision_payload(catalog, hypotheses, relation="challenges"):
    raw = _vpa_payload(catalog, hypotheses, relation=relation)
    raw["vpa_report"].pop("entry_timing_relation")
    for item in raw["vpa_report"]["observations"]:
        if item["finding_kind"] == "entry_timing":
            item["finding_kind"] = "acceptance_rejection"
    return raw


def test_new_scope_has_no_order_timing_placeholder_and_keeps_real_challenge():
    frame = make_frame()
    hypotheses = compile_pa_handoff(VALID_STAGE1, None, frame)
    catalog = build_vpa_evidence_catalog(frame)
    raw = predecision_payload(catalog, hypotheses)
    result = validate_vpa_evidence(json.dumps(raw), catalog=catalog, pa_hypotheses=hypotheses,
                                   report_scope=PREDECISION_VPA_SCOPE)
    assert result.ok, result.hard_errors
    report = result.obj["vpa_report"]
    assert "entry_timing_relation" not in report
    assert report["report_scope"] == PREDECISION_VPA_SCOPE
    assert report["entry_timing_applicability"] == "not_applicable"
    assert report["background_relation"] == "challenges"
    assert report["observations"] and report["hypothesis_assessments"]
    assert not any("no PA setup candidate" in w for w in result.warnings)


def test_predecision_schema_and_legacy_are_distinct_without_reinterpreting_old_reports():
    assert "entry_timing_relation" not in json.dumps(PREDECISION_VPA_SCHEMA)
    assert "entry_timing_relation" in json.dumps(VPA_EVIDENCE_SCHEMA)
    frame = make_frame()
    hypotheses = compile_pa_handoff(VALID_STAGE1, None, frame)
    catalog = build_vpa_evidence_catalog(frame)
    result = validate_vpa_evidence(json.dumps(_vpa_payload(catalog, hypotheses)),
                                   catalog=catalog, pa_hypotheses=hypotheses)
    assert result.ok
    assert result.obj["vpa_report"]["entry_timing_relation"] == "insufficient_data"
    assert "report_scope" not in result.obj["vpa_report"]


@pytest.mark.parametrize("relation", ["neutral", "insufficient_data"])
def test_complete_no_increment_is_allowed_without_becoming_support(relation):
    frame = make_frame()
    h = compile_pa_handoff(VALID_STAGE1, None, frame)
    catalog = build_vpa_evidence_catalog(frame)
    raw = predecision_payload(catalog, h, relation)
    raw["vpa_report"]["observations"] = []
    raw["vpa_report"]["next_confirmation"] = {"state": "not_applicable", "trigger": None}
    result = validate_vpa_evidence(json.dumps(raw), catalog=catalog, pa_hypotheses=h,
                                   report_scope=PREDECISION_VPA_SCOPE)
    assert result.ok and result.obj["vpa_report"]["background_relation"] == relation


def test_predecision_cannot_assess_order_timing_or_reference_unknown_evidence():
    frame = make_frame()
    h = compile_pa_handoff(VALID_STAGE1, None, frame)
    catalog = build_vpa_evidence_catalog(frame)
    raw = predecision_payload(catalog, h)
    raw["vpa_report"]["observations"][0]["finding_kind"] = "entry_timing"
    result = validate_vpa_evidence(json.dumps(raw), catalog=catalog, pa_hypotheses=h,
                                   report_scope=PREDECISION_VPA_SCOPE)
    assert not result.ok
    raw = predecision_payload(catalog, h)
    raw["vpa_report"]["observations"][0]["evidence_ids"] = ["vpa-unknown"]
    assert not validate_vpa_evidence(json.dumps(raw), catalog=catalog, pa_hypotheses=h,
                                     report_scope=PREDECISION_VPA_SCOPE).ok


def test_direct_predecision_prompt_delivers_scope_without_timing_output_field():
    from pa_agent.ai.prompt_assembler import PromptAssembler
    from pa_agent.config.paths import PROMPT_DIR
    from pa_agent.config.settings import PromptSettings

    f = make_frame()
    h = compile_pa_handoff(VALID_STAGE1, None, f).model_dump(mode="json")
    assembler = PromptAssembler(PROMPT_DIR, prompt_settings=PromptSettings(vpa_enabled=True))
    prompt = "\n".join(m["content"] for m in assembler.build_vpa_evidence_report(
        f, copy.deepcopy(VALID_STAGE1), h, report_scope=PREDECISION_VPA_SCOPE))
    assert PREDECISION_VPA_SCOPE in prompt
    assert "entry_timing_relation" not in prompt
    assert "not_applicable" in prompt
    assert "不强凑" in prompt


@pytest.mark.parametrize("scope,schema", [(None, VPA_EVIDENCE_SCHEMA),
                                          (PREDECISION_VPA_SCOPE, PREDECISION_VPA_SCHEMA)])
def test_direct_vpa_output_example_matches_provider_fields_without_repeating_schema(scope, schema):
    from pa_agent.ai.prompt_assembler import PromptAssembler
    from pa_agent.config.paths import PROMPT_DIR
    from pa_agent.config.settings import PromptSettings

    frame = make_frame()
    h = compile_pa_handoff(VALID_STAGE1, None, frame).model_dump(mode="json")
    assembler = PromptAssembler(PROMPT_DIR, prompt_settings=PromptSettings(vpa_enabled=True))
    content = assembler.build_vpa_evidence_report(
        frame, copy.deepcopy(VALID_STAGE1), h, report_scope=scope)[1]["content"]
    # The transport/Host keep the full Schema; text supplies one readable shape.
    example_text = content.split("输出且只输出：\n", 1)[1].lstrip()
    example, _ = json.JSONDecoder().raw_decode(example_text)
    def resolve(node):
        return schema["$defs"][node["$ref"].rsplit("/", 1)[1]] if "$ref" in node else node

    report_schema = resolve(schema["properties"]["vpa_report"])
    assert set(example["vpa_report"]) == set(report_schema["properties"])
    assert set(example["vpa_report"]["observations"][0]) == set(
        resolve(report_schema["properties"]["observations"]["items"])["properties"])
    assert '"additionalProperties"' not in content
    # Shared mandatory knowledge reaches both scopes; direction alone is not magnitude.
    assert "read the signed deviation_pct before judging magnitude" in content


def test_unknown_canonical_scope_is_rejected_and_old_shape_is_not_upgraded():
    from pa_agent.ai.role_contracts import parse_vpa_report
    f = make_frame()
    h = compile_pa_handoff(VALID_STAGE1, None, f)
    c = build_vpa_evidence_catalog(f)
    accepted = validate_vpa_evidence(json.dumps(predecision_payload(c, h)), catalog=c,
                                     pa_hypotheses=h, report_scope=PREDECISION_VPA_SCOPE)
    canonical = accepted.obj["vpa_report"]
    assert parse_vpa_report(canonical).report_scope == PREDECISION_VPA_SCOPE
    with pytest.raises(ValueError):
        parse_vpa_report({**canonical, "report_scope": "unknown_v9"})
    with pytest.raises(ValueError):
        parse_vpa_report({k: v for k, v in canonical.items() if k != "report_scope"})


def test_unavailable_catalog_remains_insufficient_without_creating_order_timing():
    f = make_frame()
    h = compile_pa_handoff(VALID_STAGE1, None, f)
    c = build_vpa_evidence_catalog(f)
    c["data_status"] = "unavailable"
    raw = predecision_payload(c, h)
    raw["vpa_report"]["observations"] = []
    result = validate_vpa_evidence(json.dumps(raw), catalog=c, pa_hypotheses=h,
                                   report_scope=PREDECISION_VPA_SCOPE)
    assert result.ok, result.hard_errors
    report = result.obj["vpa_report"]
    assert report["data_status"] == "unavailable"
    assert report["background_relation"] == "insufficient_data"
    assert all(a["relation"] == "insufficient_data" for a in report["hypothesis_assessments"])
    assert report["entry_timing_applicability"] == "not_applicable"
    assert "entry_timing_relation" not in report


def test_predecision_freezes_matching_scope_schema_budget_and_joint_receipt(tmp_path):
    from dataclasses import replace
    from pa_agent.agent_runtime.publisher import CodexJobPublisher
    from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
    from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
    from pa_agent.config.paths import PROMPT_DIR
    from tests.unit.test_joint_pipeline_runtime import run_joint
    requests = []
    f = make_frame()
    record, calls, _ = run_joint(codex=True, frame=f, captured_requests=requests)
    assert record.exception is None
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    vpa_request = next(r for r in requests if r.role == "vpa_evidence")
    assert vpa_request.schema == PREDECISION_VPA_SCHEMA
    assert vpa_request.agent_context["report_scope"] == PREDECISION_VPA_SCOPE
    assert "entry_timing_relation" not in json.dumps(vpa_request.agent_context["role_protocol"])
    publisher = CodexJobPublisher(frame=f, prompt_dir=PROMPT_DIR, job_root=tmp_path / "jobs")
    path = publisher(vpa_request, 1)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    assert job.output_schema == PREDECISION_VPA_SCHEMA
    assert job.core_context["report_scope"] == PREDECISION_VPA_SCOPE
    assert job.budget.max_wall_seconds == 240
    assert publisher._budget_for("joint_arbiter", 1, mandatory_module_count=0, mandatory_knowledge_bytes=0, bar_count=100, role_evidence_count=0, role_evidence_bytes=0).max_wall_seconds == 180
    assert "predecision_vpa_v2" in job.runtime["skills"]["vpa_evidence"]
    with pytest.raises(ValueError, match="Provider Contract"):
        publisher(replace(vpa_request, schema=VPA_EVIDENCE_SCHEMA), 1)
    bad_context = {**vpa_request.agent_context, "report_scope": "other"}
    with pytest.raises(ValueError, match="scope"):
        publisher(replace(vpa_request, agent_context=bad_context), 1)
    stage2_request = next(r for r in requests if r.role == "stage2")
    path = publisher(stage2_request, 1)
    joint_job = FrozenAgentJobStore(path.parent).open(path.name)
    from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer
    result = EvidenceMcpServer(FrozenEvidenceGateway(joint_job), job_digest=joint_job.manifest.job_digest)._call_tool("get_role_baseline", {})
    assert result["isError"] is False
    receipt = result["structuredContent"]["stage2_handoff_receipt"]
    assert receipt["accepted_vpa_report"]["report_scope"] == PREDECISION_VPA_SCOPE
    assert receipt["accepted_vpa_report"]["entry_timing_applicability"] == "not_applicable"
    # An opened job keeps its serialized scope/schema even when a later contract changes.
    assert FrozenAgentJobStore(path.parent).open(path.name).output_schema == joint_job.output_schema


def test_review_displays_host_not_applicable_without_faking_insufficient_relation():
    from pa_agent.operations.trading import _review
    from pa_agent.trading_page import TRADING_HTML
    review = _review(json.dumps({"vpa_evidence": {
        "report_scope": PREDECISION_VPA_SCOPE,
        "entry_timing_applicability": "not_applicable",
        "background_relation": "challenges", "data_status": "limited"}}))
    assert review["vpa_timing"] == "not_applicable"
    assert review["vpa_background"] == "challenges"
    assert "决策前位置与背景取证" in TRADING_HTML


def test_pa_knowledge_distinguishes_entry_location_and_pending_trigger():
    from pa_agent.config.paths import PROMPT_DIR
    text = (PROMPT_DIR / "二元决策.txt").read_text(encoding="utf-8")
    assert '拟议 **entry** 锚定当前已存在的' in text
    assert '没有其他当前有效信号或合格 §9.0P 方案' in text
    assert '“信号棒已收盘”“未来价格触发入场”“入场后已收盘跟随”' in text
    assert '市价入场确实缺少必需确认，仍须等待' in text
    assert '<!-- knowledge: chapter-054 -->' in text
    assert '<!-- knowledge: chapter-061 -->' in text


def test_new_scope_keeps_legitimate_joint_wait_and_does_not_force_trade():
    from tests.fixtures.ai_payloads import VALID_STAGE2_NO_ORDER
    from tests.unit.test_joint_pipeline_runtime import run_joint, joint_draft
    from tests.unit.test_pa_provider_contracts import _trace_draft

    draft = joint_draft()
    no_order = copy.deepcopy(VALID_STAGE2_NO_ORDER)
    draft["decision_intent"] = "wait"
    draft["decision"] = no_order["decision"]
    for field in ("diagnosis_confidence", "diagnosis_confidence_reasoning"):
        draft["decision"].pop(field)
    draft["decision_trace"] = _trace_draft(no_order["decision_trace"])
    record, calls, writer = run_joint(stage2_drafts=[draft])
    assert record.exception is None, record.exception
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    assert record.stage2_decision["terminal"]["outcome"] == "wait"
    assert record.vpa_evidence["entry_timing_applicability"] == "not_applicable"
    writer.save_full.assert_called_once()
