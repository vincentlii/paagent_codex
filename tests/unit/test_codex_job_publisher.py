from __future__ import annotations

import json
from pathlib import Path

import pytest

from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from pa_agent.agent_runtime.knowledge_bundle import build_knowledge_bundle
from pa_agent.agent_runtime.publisher import (
    CodexJobPublisher,
    analysis_role_request_digest,
)
from pa_agent.ai.analysis_role_backend import AnalysisRoleRequest
from pa_agent.ai.fact_catalog import build_analysis_fact_catalog
from tests.integration.conftest import make_frame


def _request(role: str, context: dict) -> AnalysisRoleRequest:
    frame = make_frame()
    protocols = {
        "stage1": "pa_stage1_agent_protocol_v1",
        "stage2": "pa_stage2_agent_protocol_v1",
        "vpa_evidence": "vpa_evidence_agent_protocol_v1",
        "joint_arbiter": "joint_arbiter_agent_protocol_v1",
    }
    defaults = {
        "role_protocol": {"contract_version": protocols[role]},
        "higher_timeframe": {},
    }
    if role == "stage2":
        defaults.update(stage1_diagnosis={}, router_manifest=[])
    elif role == "vpa_evidence":
        defaults.update(stage1_diagnosis={}, pa_hypotheses={})
    elif role == "joint_arbiter":
        defaults.update(
            stage1_diagnosis={},
            pa_baseline_stage2={},
            pa_hypotheses={},
            vpa_evidence={},
            router_manifest=[],
        )
    return AnalysisRoleRequest(
        role=role,
        messages=[{"role": "system", "content": "x" * 200_000}],
        schema={"type": "object"},
        thinking=True,
        reasoning_effort="high",
        snapshot_hash=build_analysis_fact_catalog(frame).snapshot_hash,
        cutoff_ts_open=frame.bars[0].ts_open,
        agent_context={**defaults, **context},
    )


def test_retry_without_authorized_pointers_cannot_restart_full_analysis(tmp_path):
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path, job_root=tmp_path/"jobs")
    with pytest.raises(ValueError, match="repair requires Validator-authorized pointers"):
        publisher(_request("vpa_evidence", {"task_id": "unscoped-repair"}), 2)
    assert not (tmp_path/"jobs").exists()


@pytest.mark.parametrize("provider", ["openai_codex", "deepseek_codex_responses"])
def test_repair_keeps_full_validator_feedback_in_frozen_resource_not_prompt(tmp_path, provider):
    from dataclasses import replace
    from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
    from pa_agent.agent_runtime.repair_contract import append_repair_context

    feedback = "Validator finding and original contract: " + "完整规则" * 9000
    messages = [{"role": "user", "content": append_repair_context(
        feedback, original_draft='{"field":"before"}',
        findings=["field"], editable_pointers=["/field"],
    )}]
    request = replace(_request("stage1", {"task_id": "repair-test"}), messages=messages)
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path, job_root=tmp_path/"jobs",
                                  runtime_identity={"provider": provider})
    published = publisher(request, 2)
    job = FrozenAgentJobStore(published.parent).open(published.name)
    initial = json.dumps(job.request["messages"], ensure_ascii=False)
    assert len(initial.encode("utf-8")) < 8192
    assert "完整规则" not in initial
    repair = FrozenEvidenceGateway(job).get_repair_findings()
    assert repair["validator_feedback"] == feedback
    assert repair["original_draft"] == '{"field":"before"}'
    assert repair["editable_pointers"] == ["/field"]
    assert repair["role_skill"] == job.runtime["skills"]["draft_pointer_repair"]
    if provider == "deepseek_codex_responses":
        assert repair["provider_output_schema"] == dict(job.output_schema)


def test_published_knowledge_manifest_is_visible_and_mandatory(tmp_path):
    from dataclasses import replace
    from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
    from pa_agent.agent_runtime.codex_worker import CodexCliWorker, CodexWorkerOutputError

    (tmp_path/"market.txt").write_text("Complete mandatory knowledge", encoding="utf-8")
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path, job_root=tmp_path/"jobs")
    path = publisher(_request("stage1", {"task_id": "manifest-test",
                                       "allowed_knowledge_ids": ["market.txt"]}), 1)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    legacy_core = {key: value for key, value in job.core_context.items()
                   if key != "stage1_decision_packet"}
    job = replace(job, core_context={**legacy_core, "context_policy": "semantic_parity_v1"},
                  budget=replace(job.budget, max_evidence_calls=32,
                                 max_evidence_bytes=1_048_576))
    assert FrozenEvidenceGateway(job).list_allowed_knowledge() == job.knowledge_manifest["modules"]
    events = tuple({"status": "ok", "operation": operation, "request": {}}
                   for operation in ("get_job_manifest", "get_market_core", "list_allowed_knowledge"))
    events += ({"status": "ok", "operation": "get_price_frame",
                "request": {"older_seq": len(job.bars), "newer_seq": 1}},)
    with pytest.raises(CodexWorkerOutputError, match="mandatory"):
        CodexCliWorker._validate_required_evidence_reads(job, events)


def test_publisher_replaces_long_direct_prompt_with_bounded_core_context(tmp_path: Path) -> None:
    frame = make_frame()
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "market.txt").write_text("Brooks market cycle knowledge", encoding="utf-8")
    runtime_identity = {"model": "gpt-test", "codex_binary_digest": "a" * 64}
    publisher = CodexJobPublisher(
        frame=frame,
        prompt_dir=prompt_dir,
        job_root=tmp_path / "jobs",
        runtime_identity=runtime_identity,
    )

    published = publisher(
        _request(
            "stage1",
            {
                "task_id": "task-1",
                "symbol": frame.symbol,
                "timeframe": frame.timeframe,
                "higher_timeframe": {
                    "status": "available",
                    "evidence": [{"evidence_id": "HTF.H1", "ts_open": 900}],
                },
                "previous_analysis": {"stage1_diagnosis": {"cycle": "trend"}},
                "allowed_knowledge_ids": ["market.txt"],
            },
        ),
        1,
    )
    job = FrozenAgentJobStore(tmp_path / "jobs").open(published.name)
    encoded = json.dumps(job.request["messages"], ensure_ascii=False).encode("utf-8")

    assert len(encoded) < 32_768
    assert "x" * 100 not in encoded.decode("utf-8")
    initial_context = json.loads(job.request["messages"][1]["content"])["core_context"]
    assert "previous_analysis" not in initial_context
    assert initial_context["evidence_index"]["upstream_artifact_keys"] == ["previous_analysis"]
    assert '"cycle"' not in encoded.decode("utf-8")
    assert job.core_context["previous_analysis"]["stage1_diagnosis"]["cycle"] == "trend"
    assert job.knowledge_documents["market.txt"]["sections"][0]["text"] == (
        "Brooks market cycle knowledge"
    )
    module_manifest = job.knowledge_manifest["modules"][0]
    assert module_manifest["loading_policy"] == "mandatory"
    assert module_manifest["load_order"] == 1
    assert module_manifest["content_bytes"] == len(
        "Brooks market cycle knowledge".encode("utf-8")
    )
    initial = json.loads(job.request["messages"][1]["content"])["core_context"]
    assert initial["context_policy"] == "bounded_context_v2"
    assert job.core_context["stage1_decision_packet"]["packet_version"] == "bounded_stage1_packet_v2"
    assert job.core_context["stage1_decision_packet"]["identity"]["snapshot_hash"] == job.manifest.snapshot_hash
    assert initial["evidence_index"]["htf_evidence_ids"] == ["HTF.H1"]
    assert initial["evidence_index"]["vpa_evidence_ids"] == []
    assert job.fact_projection["snapshot_hash"] == job.manifest.snapshot_hash
    assert max(int(bar["ts_open"]) for bar in job.bars) <= job.manifest.cutoff_ts_open
    assert job.runtime["backend_runtime_identity"] == runtime_identity
    assert len(job.request["source_request_digest"]) == 64
    assert job.budget.max_evidence_calls == 3
    assert job.budget.max_evidence_bytes >= job.core_context["stage1_decision_packet"]["packet_bytes"]


def test_semantic_parity_bundle_preserves_stage_boundaries(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    decision_tree = (
        "global principles\n\n"
        "## 1. 是否可以决策\n"
        "### 1.1 data preflight\nlegacy model preflight\n"
        "### 1.2 context gate\nstage one context\n"
        "## 9. 入场信号二元检查\nstage two execution\n"
        "## 15. 最终执行口诀\nfinish\n"
    )
    diagnosis = (
        "## 第一层：背景\ncontext\n"
        "## 第三层：逐K信号确认\nsignal details\n"
    )
    (prompt_dir / "二元决策.txt").write_text(decision_tree, encoding="utf-8")
    (prompt_dir / "市场诊断框架.txt").write_text(diagnosis, encoding="utf-8")

    stage1, _ = build_knowledge_bundle(
        prompt_dir=prompt_dir,
        knowledge_ids=("二元决策.txt", "市场诊断框架.txt"),
        role="stage1",
    )
    stage2, _ = build_knowledge_bundle(
        prompt_dir=prompt_dir,
        knowledge_ids=("二元决策.txt",),
        role="stage2",
    )
    stage1_text = json.dumps(stage1, ensure_ascii=False)
    stage2_text = json.dumps(stage2, ensure_ascii=False)

    assert "§1.1 数据充足性已由程序预检" in stage1_text
    assert "legacy model preflight" not in stage1_text
    assert "stage one context" in stage1_text
    assert "stage two execution" not in stage1_text
    assert "signal details" not in stage1_text
    assert "global principles" in stage2_text
    assert "stage one context" not in stage2_text
    assert "stage two execution" in stage2_text


def test_publisher_exposes_only_vpa_evidence_id_index_before_retrieval(
    tmp_path: Path,
) -> None:
    frame = make_frame()
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    publisher = CodexJobPublisher(
        frame=frame,
        prompt_dir=prompt_dir,
        job_root=tmp_path / "jobs",
    )
    published = publisher(
        _request(
            "vpa_evidence",
            {
                "task_id": "task-vpa",
                "allowed_knowledge_ids": [],
                "role_evidence": {
                    "evidence": [
                        {"evidence_id": "vpa-e001", "facts": {"volume": 1}},
                        {"evidence_id": "vpa-e002", "facts": {"volume": 2}},
                    ]
                },
            },
        ),
        1,
    )
    job = FrozenAgentJobStore(tmp_path / "jobs").open(published.name)
    initial = json.loads(job.request["messages"][1]["content"])["core_context"]

    assert initial["evidence_index"]["vpa_evidence_ids"] == ["vpa-e001", "vpa-e002"]
    assert "role_evidence" not in job.request["messages"][1]["content"]
    assert job.fact_projection["role_evidence"]["evidence"][0]["facts"]["volume"] == 1


def test_publisher_rejects_knowledge_outside_prompt_allowlist(tmp_path: Path) -> None:
    frame = make_frame()
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    publisher = CodexJobPublisher(
        frame=frame,
        prompt_dir=prompt_dir,
        job_root=tmp_path / "jobs",
    )

    with pytest.raises(ValueError, match="knowledge document"):
        publisher(
            _request("stage2", {"task_id": "task-1", "allowed_knowledge_ids": ["../secret"]}),
            1,
        )


def test_publisher_rejects_missing_mature_role_protocol(tmp_path: Path) -> None:
    frame = make_frame()
    publisher = CodexJobPublisher(
        frame=frame,
        prompt_dir=tmp_path,
        job_root=tmp_path / "jobs",
    )
    request = _request("stage1", {"task_id": "task-1", "allowed_knowledge_ids": []})
    request = AnalysisRoleRequest(
        **{
            **request.__dict__,
            "agent_context": {
                "task_id": "task-1",
                "higher_timeframe": {},
                "allowed_knowledge_ids": [],
            },
        }
    )

    with pytest.raises(ValueError, match="mature role protocol"):
        publisher(request, 1)


def test_source_request_digest_is_canonical_for_key_order_and_unicode() -> None:
    first = AnalysisRoleRequest(
        role="stage1",
        messages=[{"role": "user", "content": "诊断市场"}],
        schema={"type": "object", "properties": {"方向": {"type": "string"}}},
        thinking=True,
        reasoning_effort="high",
        snapshot_hash="a" * 64,
        cutoff_ts_open=1_725_000_000_000,
        agent_context={"b": 2, "a": 1},
    )
    reordered = AnalysisRoleRequest(
        role="stage1",
        messages=[{"content": "诊断市场", "role": "user"}],
        schema={"properties": {"方向": {"type": "string"}}, "type": "object"},
        thinking=True,
        reasoning_effort="high",
        snapshot_hash="A" * 64,
        cutoff_ts_open=1_725_000_000_000,
        agent_context={"a": 1, "b": 2},
    )

    digest = analysis_role_request_digest(first, 1)

    assert digest == analysis_role_request_digest(reordered, 1)
    assert digest == "aab770c2a9770c0e305339b6e91d9ff7ab8915daf463ad8c5d5155b79f474e92"


def test_stage2_continuation_freezes_verified_inheritance_and_delta_baseline(
    tmp_path: Path,
) -> None:
    from dataclasses import replace
    from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway

    frame = make_frame()
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "shared.txt").write_text(
        "Shared Brooks knowledge without role-specific slicing.", encoding="utf-8"
    )
    runtime_identity = {
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "runtime_version": "runtime-v1",
    }
    publisher = CodexJobPublisher(
        frame=frame,
        prompt_dir=prompt_dir,
        job_root=tmp_path / "jobs",
        runtime_identity=runtime_identity,
    )
    common = {
        "task_id": "task-session",
        "allowed_knowledge_ids": ["shared.txt"],
        "higher_timeframe": {
            "status": "available",
            "evidence": [{"evidence_id": "HTF.H1", "ts_open": frame.bars[-1].ts_open}],
        },
    }
    stage1_request = _request("stage1", {
        **common,
        "role_protocol": {"contract_version": "pa_stage1_agent_protocol_v1"},
    })
    stage1_path = publisher(stage1_request, 1)
    stage2_request = _request("stage2", {
        **common,
        "role_protocol": {"contract_version": "pa_stage2_agent_protocol_v1"},
        "stage1_diagnosis": {},
        "router_manifest": [],
        "pa_session_continuation": {
            "parent_job_digest": stage1_path.name,
            "thread_id": "thread-pa-0001",
        },
    })
    stage2_path = publisher(stage2_request, 1)
    stage2_job = FrozenAgentJobStore(stage2_path.parent).open(stage2_path.name)

    continuation = stage2_job.core_context["pa_session_continuation"]
    assert continuation["protocol_version"] == "pa_session_continuation_v1"
    assert continuation["parent_job_digest"] == stage1_path.name
    assert continuation["thread_id"] == "thread-pa-0001"
    assert continuation["inherited_price_frame"] is True
    assert continuation["inherited_higher_timeframe"] is True
    assert continuation["inherited_knowledge_ids"] == ["shared.txt"]
    operations = [item[0] for item in FrozenEvidenceGateway(stage2_job).mandatory_read_plan()]
    assert "get_upstream_artifact" in operations
    assert "get_price_frame" not in operations
    assert "get_htf_evidence" not in operations
    assert "read_knowledge_module" not in operations

    mismatched = replace(
        stage2_request,
        agent_context={**dict(stage2_request.agent_context or {}), "task_id": "other-task"},
    )
    with pytest.raises(ValueError, match="parent job"):
        publisher(mismatched, 1)
