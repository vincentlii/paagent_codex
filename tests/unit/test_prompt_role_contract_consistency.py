"""Model-visible role instructions must agree with the Provider Draft contract."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from pa_agent.agent_runtime.publisher import CodexJobPublisher
from pa_agent.ai.analysis_role_backend import AnalysisRoleRequest
from pa_agent.ai.fact_catalog import analysis_snapshot_hash
from pa_agent.ai.pa_provider_contracts import schema_for_provider_draft
from pa_agent.ai.prompt_assembler import (
    PromptAssembler,
    select_agent_knowledge_text,
    stage1_prompt_txt_files,
)
from pa_agent.config.paths import PROMPT_DIR
from tests.integration.conftest import VALID_STAGE1, make_frame
from tests.unit.test_joint_pipeline_runtime import run_joint


@pytest.mark.parametrize("backend", ["direct", "codex"])
def test_stage1_delivered_fact_instructions_use_provider_owned_reference_fields(tmp_path, backend):
    frame = make_frame()
    assembler = PromptAssembler(PROMPT_DIR)
    schema = schema_for_provider_draft("stage1", decision_tree_path=assembler.decision_tree_path)
    messages = assembler.build_stage1(frame)
    if backend == "direct":
        delivered = messages[-1]["content"]
    else:
        request = AnalysisRoleRequest(
            role="stage1", messages=messages, schema=schema, thinking=True,
            reasoning_effort="xhigh", snapshot_hash=analysis_snapshot_hash(frame),
            cutoff_ts_open=frame.bars[0].ts_open,
            agent_context={
                "task_id": "reference-contract", "higher_timeframe": {},
                "role_protocol": assembler.build_stage1_agent_protocol(frame, analysis_mode="original"),
                "allowed_knowledge_ids": stage1_prompt_txt_files(),
            },
        )
        path = CodexJobPublisher(frame=frame, prompt_dir=PROMPT_DIR, job_root=tmp_path / "jobs")(request, 1)
        job = FrozenAgentJobStore(path.parent).open(path.name)
        packet = FrozenEvidenceGateway(job).get_stage1_decision_packet()
        delivered = "\n".join(packet["role_contract"]["embedded_rules"])

    assert "fact_refs" not in schema["properties"]["pa_evidence"]["properties"]
    assert "已列入 `pa_evidence.fact_refs`" not in delivered
    assert "`supporting_fact_refs` / `challenging_fact_refs`" in delivered
    assert "`gate_trace.evidence_refs`" in delivered
    assert "不额外输出程序物化的 `pa_evidence.fact_refs`" in delivered


def _assert_joint_output_rules(text):
    assert "pa_vpa_stage2_draft_v1" in text
    assert "未经统计校准的联合主观胜率先验" in text
    assert "机械加减 PA-only 先验" in text
    assert "`joint_analysis`" in text
    assert "Stage 2 Provider Draft v2" not in text
    assert "正文只写 Stage 2 Draft v2" not in text
    assert "PA 主观胜率先验及最终" not in text


@pytest.mark.parametrize("prefix_chain", [False, True])
def test_joint_stage2_projects_only_its_system_role_and_matches_output_schema(prefix_chain):
    assembler = PromptAssembler(PROMPT_DIR)
    frame = make_frame()
    stage1_messages = assembler.build_stage1(frame)
    original_messages = copy.deepcopy(stage1_messages)
    kwargs = dict(
        frame=frame, stage1_messages=stage1_messages,
        stage1_reply_content=json.dumps(VALID_STAGE1), stage1_json=VALID_STAGE1,
        strategy_files=[], experience_entries=[], use_prefix_chain=prefix_chain,
    )
    ordinary = assembler.build_stage2_continuation(**kwargs)
    joint = assembler.build_stage2_continuation(**kwargs, joint_stage2=True)
    assert stage1_messages == original_messages
    assert ordinary[0] == stage1_messages[0]
    if prefix_chain:
        assert ordinary[:len(stage1_messages)] == stage1_messages
        assert joint[1:len(stage1_messages)] == stage1_messages[1:]

    assert joint[0]["role"] == "system"
    assert joint[0]["content"] != ordinary[0]["content"]
    assert "只依赖 K 线与 EMA20（**不依赖成交量**）" not in joint[0]["content"]
    assert "忽略**一切成交量条款" not in joint[0]["content"]
    assert "Host 已验收的 VPA 报告" in joint[0]["content"]
    assert "机械加减 PA-only 先验" in joint[0]["content"]
    assert "目标与 PA 胜率先验" not in joint[0]["content"]
    assert "完整裸 JSON" in joint[0]["content"]
    assert "`result` 传输封套" not in joint[0]["content"]
    _assert_joint_output_rules(joint[-1]["content"])
    assert "PA 主观胜率先验及最终" in ordinary[-1]["content"]
    assert "pa_vpa_stage2_draft_v1" not in ordinary[-1]["content"]
    assert "VPA" not in ordinary[0]["content"]
    assert "VPA" not in ordinary[-1]["content"]
    assert assembler.build_stage1(frame)[0] == original_messages[0]
    assert assembler.build_stage2(frame, VALID_STAGE1, [], [])[0] == original_messages[0]


def test_actual_direct_joint_messages_have_consistent_system_role_and_draft_rules():
    record, calls, _ = run_joint()
    assert record.exception is None, record.exception
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    system = next(m["content"] for m in record.stage2_messages if m["role"] == "system")
    assert "Host 已验收的 VPA 报告" in system
    assert "只依赖 K 线与 EMA20（**不依赖成交量**）" not in system
    task = next(m["content"] for m in record.stage2_messages if "## 阶段二任务" in m["content"])
    _assert_joint_output_rules(task)


def test_actual_direct_pa_off_retains_price_only_role_and_original_prefix():
    from unittest.mock import MagicMock

    from pa_agent.ai.json_validator import JsonValidator
    from pa_agent.ai.router import route_strategy_files
    from pa_agent.config.settings import PromptSettings, Settings, ValidationSettings
    from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
    from pa_agent.util.threading import CancelToken
    from tests.integration.conftest import VALID_STAGE2, make_reply

    settings = Settings(prompt=PromptSettings(vpa_mode="off"),
        validation=ValidationSettings(retry_enabled=False))
    client = MagicMock()
    client.stream_chat.side_effect = [make_reply(VALID_STAGE1), make_reply(VALID_STAGE2)]
    writer = MagicMock()
    writer.save_full.return_value.ok = writer.save_full.return_value.audit_ok = True
    experience = MagicMock()
    experience.read_for_stage2.return_value = []
    orchestrator = TwoStageOrchestrator(client=client, assembler=PromptAssembler(PROMPT_DIR),
        router=route_strategy_files, validator=JsonValidator(), pending_writer=writer,
        exp_reader=experience, settings=settings)
    record = orchestrator.submit(make_frame(), CancelToken(), lambda _event: None)
    assert record.exception is None, record.exception
    assert client.stream_chat.call_count == 2
    assert record.vpa_evidence is None
    assert record.stage1_messages[0] == record.stage2_messages[0]
    assert "只依赖 K 线与 EMA20（**不依赖成交量**）" in record.stage2_messages[0]["content"]
    delivered = "\n".join(m["content"] for m in record.stage2_messages)
    assert "PA 主观胜率先验及最终" in delivered
    for forbidden in ("Host 已验收的 VPA 报告", "VPA_PROGRAM_EVIDENCE_CATALOG",
                      "Wyckoff VPA Core", '"vpa_report":', '"joint_analysis":'):
        assert forbidden not in delivered
    assert "joint_analysis" not in record.stage2_decision
    writer.save_full.assert_called_once()


def test_actual_joint_frozen_baseline_delivers_consistent_role_schema_and_all_knowledge(tmp_path, monkeypatch):
    requests = []
    frame = make_frame()
    record, calls, _ = run_joint(codex=True, frame=frame, captured_requests=requests)
    assert record.exception is None, record.exception
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    request = next(item for item in requests if item.role == "stage2")
    path = CodexJobPublisher(frame=frame, prompt_dir=PROMPT_DIR, job_root=tmp_path / "joint-jobs")(request, 1)
    store = FrozenAgentJobStore(path.parent)
    job = store.open(path.name)
    result = EvidenceMcpServer(FrozenEvidenceGateway(job), job_digest=job.manifest.job_digest)._call_tool(
        "get_role_baseline", {},
    )
    assert result["isError"] is False
    baseline = result["structuredContent"]
    core = next(item["payload"]["core_context"] for item in baseline["resources"]
                if item["operation"] == "get_market_core")
    rules = "\n".join(core["role_protocol"]["embedded_rules"])
    _assert_joint_output_rules(rules)
    assert "裸 JSON" not in rules
    assert core["provider_output_schema"] == request.schema == job.output_schema
    assert core["provider_output_schema"]["properties"]["protocol_version"]["const"] == "pa_vpa_stage2_draft_v1"
    assert "joint_analysis" in core["provider_output_schema"]["required"]
    schema_digest = hashlib.sha256(json.dumps(request.schema, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert core["role_protocol"]["provider_contract_digest"] == schema_digest
    assert core["role_protocol"] == request.agent_context["role_protocol"]

    delivered = {item["arguments"]["knowledge_id"]: item["payload"]
                 for item in baseline["resources"] if item["operation"] == "read_knowledge_module"}
    assert set(delivered) == set(request.agent_context["allowed_knowledge_ids"])
    assert all(item["loading_policy"] == "mandatory" for item in job.knowledge_manifest["modules"])
    for module_id, payload in delivered.items():
        selected = select_agent_knowledge_text(role="stage2", module_id=module_id,
            text=(PROMPT_DIR / module_id).read_text(encoding="utf-8"), joint_stage2=True)
        assert "".join(section["text"] for section in payload["sections"]) == selected
        assert payload["sections"] == job.knowledge_documents[module_id]["sections"]
    persona = "".join(section["text"] for section in delivered["提示词大纲_人设与思维方式.txt"]["sections"])
    assert "Host 已验收的 VPA 报告" in persona
    assert "Codex 联合角色使用 `result` 传输封套" in persona
    assert "只依赖 K 线与 EMA20（**不依赖成交量**）" not in persona
    # Later live rule changes cannot reproject an already frozen role resource.
    monkeypatch.setattr("pa_agent.ai.prompt_assembler._stage2_output_rules",
                        lambda **_kwargs: "later live role instruction")
    reopened = store.open(path.name)
    assert reopened.core_context == job.core_context
    assert reopened.knowledge_documents == job.knowledge_documents
    replayed = EvidenceMcpServer(FrozenEvidenceGateway(reopened), job_digest=reopened.manifest.job_digest)._call_tool(
        "get_role_baseline", {},
    )
    assert replayed["structuredContent"] == baseline
