from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace

import pytest

from pa_agent.agent_runtime.codex_worker import CodexCliWorker, CodexWorkerOutputError, CodexWorkerStopped
from pa_agent.agent_runtime.evidence import EvidenceAccessDenied, EvidenceBudgetExceeded, FrozenEvidenceGateway
from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from pa_agent.agent_runtime.publisher import CodexJobPublisher
from tests.integration.conftest import make_frame
from tests.unit.test_codex_job_publisher import _request


def _bounded_job(tmp_path, *, market_context=None, allowed_knowledge_ids=()):
    frame = make_frame()
    if market_context is not None:
        frame = replace(frame, data_metadata={**frame.data_metadata, "market_context": market_context})
    publisher = CodexJobPublisher(frame=frame, prompt_dir=tmp_path,
                                  job_root=tmp_path / "jobs")
    path = publisher(_request("stage1", {"task_id": "bounded-v2",
                     "allowed_knowledge_ids": list(allowed_knowledge_ids)}), 1)
    return FrozenAgentJobStore(path.parent).open(path.name)


def _historical_job(tmp_path, *, semantic_parity=False):
    from pa_agent.agent_runtime.decision_packet import build_stage1_decision_packet
    from pa_agent.agent_runtime.frozen_job import FrozenAgentJobBuilder

    current = _bounded_job(tmp_path)
    context = {key: value for key, value in current.core_context.items()
               if key not in {"stage1_investigation_policy", "stage1_decision_packet"}}
    historical_skill = "Frozen historical Stage 1: read baseline, at most two optional calls."
    context["role_skill"] = historical_skill
    if semantic_parity:
        context["context_policy"] = "semantic_parity_v1"
    else:
        context["stage1_decision_packet"] = build_stage1_decision_packet(
            snapshot_hash=current.manifest.snapshot_hash,
            cutoff_ts_open=current.manifest.cutoff_ts_open,
            bars=current.bars, facts=current.fact_projection["facts"], core_context=context,
            knowledge_documents=current.knowledge_documents,
            knowledge_manifest=current.knowledge_manifest, role_skill=historical_skill,
        )
    job = FrozenAgentJobBuilder(
        runtime_version="historical-bounded-context-test",
        agents_text=current.runtime["agents_text"], skill_documents={"stage1": historical_skill},
    ).build(
        task_id=current.manifest.task_id, request_id=current.manifest.request_id,
        role="stage1", attempt=1, snapshot_hash=current.manifest.snapshot_hash,
        cutoff_ts_open=current.manifest.cutoff_ts_open, messages=current.request["messages"],
        output_schema=current.output_schema, fact_projection=current.fact_projection,
        bars=current.bars, knowledge_documents=current.knowledge_documents,
        allowed_knowledge_ids=tuple(current.knowledge_documents),
        knowledge_manifest=current.knowledge_manifest, core_context=context,
        backend_runtime_identity=current.runtime["backend_runtime_identity"],
        budget=(replace(current.budget, max_evidence_calls=32, max_evidence_bytes=1_048_576)
                if semantic_parity else current.budget),
    )
    store = FrozenAgentJobStore(tmp_path / "historical-jobs")
    path = store.publish(job)
    reopened = store.open(path.name)
    assert reopened.manifest.resource_digests == job.manifest.resource_digests
    assert reopened.runtime["skills"]["stage1"] == historical_skill
    return reopened


def test_market_context_reaches_actual_stage1_packet_not_only_saved_core(tmp_path):
    context = {"instrument_label": "Binance 黄金永续合约", "reference_session": "weekend",
               "holiday_calendar_status": "unavailable"}
    job = _bounded_job(tmp_path, market_context=context)
    result = EvidenceMcpServer(FrozenEvidenceGateway(job), job_digest=job.manifest.job_digest)._call_tool(
        "get_stage1_decision_packet", {})
    packet = result["structuredContent"]["decision_packet"]
    assert packet["market_context"] == context
    assert "market_context" not in _bounded_job(tmp_path / "legacy").core_context["stage1_decision_packet"]


def test_generation_reference_choices_are_frozen_pa_ids_without_changing_host_schema(tmp_path):
    from dataclasses import replace
    from pa_agent.ai.pa_provider_contracts import schema_for_provider_draft

    original = schema_for_provider_draft("stage1")
    job = replace(_bounded_job(tmp_path), output_schema=original)
    transport = CodexCliWorker._transport_schema(job)
    projected = transport["properties"]["result"]["anyOf"][0]
    fields = list(projected["properties"])
    assert fields.index("market_hypotheses") < fields.index("bar_analysis")
    assert fields.index("gate_trace") < fields.index("diagnosis_confidence_reasoning")
    assert set(fields) == set(original["properties"])
    from pa_agent.ai.provider_capabilities import project_strict_output_schema
    expected = project_strict_output_schema(original)
    for name in set(fields) - {"market_hypotheses", "gate_trace"}:
        assert projected["properties"][name] == expected["properties"][name]
    assert projected["required"] == expected["required"]
    refs = projected["properties"]["market_hypotheses"]["properties"]["primary"]["properties"]["supporting_fact_refs"]["items"]
    assert refs == {"$ref": "#/$defs/frozen_pa_reference"}
    choices = transport["$defs"]["frozen_pa_reference"]["enum"]
    assert set(choices) == {item["fact_id"] for item in job.fact_projection["facts"]}
    assert "PA.HTF.H1" not in choices
    assert "HTF.H1" not in choices
    assert "enum" not in original["properties"]["market_hypotheses"]["properties"]["primary"]["properties"]["supporting_fact_refs"]["items"]


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


def test_stage2_baseline_locates_exact_contract_and_upstream_without_duplication(tmp_path):
    from dataclasses import replace

    schema = {"type": "object"}
    digest = hashlib.sha256(json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    request = replace(_request("stage2", {
        "task_id": "stage2-handoff",
        "experience_entries": [{"note": "frozen prior analysis"}],
        "role_protocol": {
            "contract_version": "pa_stage2_agent_protocol_v1",
            "provider_contract_digest": digest,
        },
    }), schema=schema)
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path,
                                  job_root=tmp_path / "jobs")
    path = publisher(request, 1)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    gateway = FrozenEvidenceGateway(job)
    result = EvidenceMcpServer(gateway,
                               job_digest=job.manifest.job_digest)._call_tool(
        "get_role_baseline", {},
    )
    assert result["isError"] is False
    baseline = result["structuredContent"]
    receipt = baseline["stage2_handoff_receipt"]
    assert receipt["required_handoff_fields_present"] is True
    assert receipt["provider_contract_digest"] == digest
    assert receipt["read_plan_complete"] is True
    assert receipt["resource_count"] == len(baseline["resources"])
    assert receipt["terminal_resource"] == {
        "operation": baseline["resources"][-1]["operation"],
        "knowledge_id": baseline["resources"][-1]["arguments"].get("knowledge_id"),
    }
    assert list(baseline).index("resources") < list(baseline).index("stage2_handoff_receipt")
    assert receipt["market_context_sources"]["price_frame"]["source"] == "current_baseline"
    assert receipt["market_context_sources"]["price_frame"]["locations"]
    assert receipt["experience_entries"]["count"] == 1
    assert receipt["experience_entries"]["locations"]
    assert baseline["mandatory_modules"] == []
    assert "list_allowed_knowledge" not in [item["operation"] for item in baseline["resources"]]
    assert baseline["resources"][1]["payload"]["core_context"]["provider_output_schema"] == schema
    assert baseline["resources"][2]["payload"]["upstream_artifact"]["stage1_diagnosis"] == {}
    CodexCliWorker._validate_required_evidence_reads(
        job, tuple(asdict(item) for item in gateway.audit_events),
    )

    wrong = replace(request, schema={"type": "array"})
    with pytest.raises(ValueError, match="does not match frozen output schema"):
        publisher(wrong, 1)

    no_digest_path = publisher(_request("stage2", {"task_id": "no-digest-stage2-handoff"}), 1)
    no_digest = FrozenAgentJobStore(no_digest_path.parent).open(no_digest_path.name)
    assert FrozenEvidenceGateway(no_digest).get_market_core()["provider_output_schema"] == no_digest.output_schema
    no_digest_result = EvidenceMcpServer(FrozenEvidenceGateway(no_digest),
                                         job_digest=no_digest.manifest.job_digest)._call_tool(
        "get_role_baseline", {},
    )
    assert no_digest_result["structuredContent"]["stage2_handoff_receipt"]["read_plan_complete"] is True

    historical = replace(no_digest, core_context={
        key: value for key, value in no_digest.core_context.items()
        if key != "analysis_execution_policy"
    })
    historical_result = EvidenceMcpServer(FrozenEvidenceGateway(historical),
                                          job_digest=historical.manifest.job_digest)._call_tool(
        "get_role_baseline", {},
    )
    assert "stage2_handoff_receipt" not in historical_result["structuredContent"]


@pytest.mark.parametrize("role", ["stage2", "vpa_evidence"])
def test_role_draft_rejects_nested_reads_when_final_baseline_assembly_fails(tmp_path, monkeypatch, role):
    path = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path,
                             job_root=tmp_path / "jobs")(
        _request(role, {"task_id": f"incomplete-{role}-baseline"}), 1,
    )
    job = FrozenAgentJobStore(path.parent).open(path.name)
    gateway = FrozenEvidenceGateway(job)

    def fail_assembly(resources):
        raise EvidenceAccessDenied("frozen role baseline is incomplete")

    monkeypatch.setattr(gateway, "stage2_handoff_receipt" if role == "stage2"
                        else "baseline_delivery_index", fail_assembly)
    result = EvidenceMcpServer(gateway, job_digest=job.manifest.job_digest)._call_tool(
        "get_role_baseline", {},
    )
    assert result["isError"] is True
    events = tuple(asdict(event) for event in gateway.audit_events)
    with pytest.raises(CodexWorkerOutputError, match="baseline"):
        CodexCliWorker._validate_required_evidence_reads(job, events)

    historical = replace(job, core_context={
        key: value for key, value in job.core_context.items()
        if key != "baseline_receipt_policy"
    })
    CodexCliWorker._validate_required_evidence_reads(historical, events)


def test_joint_stage2_handoff_receipt_locates_the_accepted_vpa_report(tmp_path):
    from dataclasses import replace
    from pa_agent.ai.pa_handoff import compile_pa_handoff

    schema = {"type": "object", "properties": {
        "protocol_version": {"const": "pa_vpa_stage2_draft_v1"},
    }}
    digest = hashlib.sha256(json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    report = {
        "catalog_id": "frozen-vpa", "data_status": "available",
        "background_relation": "neutral", "entry_timing_relation": "insufficient_data",
        "observations": [{
            "evidence_ids": ["vpa-e001"], "finding_kind": "background",
            "sequence_claims": [], "interpretation": "Frozen background comparison.",
            "epistemic_status": "unresolved", "interpretation_label": None,
            "auto_bound_evidence_ids": [], "bar_range": "K1",
        }],
        "hypothesis_assessments": [],
        "next_confirmation": {
            "state": "not_applicable", "trigger": None, "requires_closed_bar": False,
        },
        "summary": "validated upstream report",
    }
    hypotheses = compile_pa_handoff({}, None, make_frame()).model_dump(mode="json")
    request = replace(_request("stage2", {
        "task_id": "joint-stage2-handoff", "pa_hypotheses": hypotheses,
        "vpa_catalog_id": "frozen-vpa",
        "vpa_evidence": report,
        "role_protocol": {
            "contract_version": "joint_stage2_agent_protocol_v1",
            "provider_contract_digest": digest,
        },
    }), schema=schema)
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path,
                                  job_root=tmp_path / "jobs")
    path = publisher(request, 1)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    result = EvidenceMcpServer(FrozenEvidenceGateway(job),
                               job_digest=job.manifest.job_digest)._call_tool(
        "get_role_baseline", {},
    )
    assert result["isError"] is False
    baseline = result["structuredContent"]
    path = baseline["stage2_handoff_receipt"]["locations"]["validated_vpa_report"]
    assert path == "resources[2].payload.upstream_artifact.vpa_evidence"
    assert baseline["resources"][2]["payload"]["upstream_artifact"]["vpa_evidence"] == report
    assert "vpa_evidence_ids" not in baseline["resources"][1]["payload"]["core_context"]["evidence_index"]
    assert baseline["stage2_handoff_receipt"]["accepted_vpa_report"] == {
        "catalog_id": "frozen-vpa", "citable_evidence_ids": ["vpa-e001"],
        "citation_scope": "validated_report_only", "raw_catalog_access": False,
    }
    assert baseline["stage2_handoff_receipt"]["pa_hypotheses_lineage"] == {
        "verified_stage1_projection": True,
        "source_digest_scope": "canonical_pa_hypotheses_payload",
    }
    inconsistent = replace(request, agent_context={
        **request.agent_context, "pa_hypotheses": {**hypotheses, "source_digest": "wrong"},
    })
    with pytest.raises(ValueError, match="do not match validated Stage 1 projection"):
        publisher(inconsistent, 1)
    mismatched_catalog = replace(request, agent_context={
        **request.agent_context, "vpa_catalog_id": "other-catalog",
    })
    with pytest.raises(ValueError, match="VPA report catalog identity mismatch"):
        publisher(mismatched_catalog, 1)


def test_stage2_receipt_distinguishes_inherited_from_delivered_knowledge(tmp_path):
    from dataclasses import replace

    (tmp_path / "shared.txt").write_text("Shared Brooks knowledge.", encoding="utf-8")
    (tmp_path / "new.txt").write_text("Stage 2 Brooks knowledge.", encoding="utf-8")
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path,
                                  job_root=tmp_path / "jobs")
    stage1_path = publisher(_request("stage1", {
        "task_id": "knowledge-handoff", "allowed_knowledge_ids": ["shared.txt"],
    }), 1)
    schema = {"type": "object"}
    schema_digest = hashlib.sha256(json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    request = replace(_request("stage2", {
        "task_id": "knowledge-handoff",
        "allowed_knowledge_ids": ["shared.txt", "new.txt"],
        "pa_session_continuation": {
            "parent_job_digest": stage1_path.name, "thread_id": "thread-pa-0001",
        },
        "role_protocol": {
            "contract_version": "pa_stage2_agent_protocol_v1",
            "provider_contract_digest": schema_digest,
        },
    }), schema=schema)
    stage2_path = publisher(request, 1)
    job = FrozenAgentJobStore(stage2_path.parent).open(stage2_path.name)
    baseline = EvidenceMcpServer(FrozenEvidenceGateway(job),
                                 job_digest=job.manifest.job_digest)._call_tool(
        "get_role_baseline", {},
    )["structuredContent"]
    receipt = baseline["stage2_handoff_receipt"]
    assert "delivery_index" not in baseline
    assert receipt["locations"]["role_skill"] == "resources[1].payload.core_context.role_skill"
    assert receipt["mandatory_knowledge_sources"] == [
        {"knowledge_module_id": "shared.txt", "source": "verified_stage1_continuation"},
        {"knowledge_module_id": "new.txt", "source": "current_baseline",
         "location": "resources[3].payload.sections"},
    ]
    assert receipt["market_context_sources"]["price_frame"] == {
        "source": "verified_stage1_continuation", "locations": [],
    }
    assert receipt["market_context_sources"]["higher_timeframe"] == {
        "source": "verified_stage1_continuation", "locations": [],
    }
    assert receipt["experience_entries"] == {"count": 0, "locations": []}
    assert baseline["resources"][3]["payload"]["sections"][0]["text"] == (
        "Stage 2 Brooks knowledge."
    )


def test_new_vpa_baseline_keeps_full_selected_knowledge_without_duplicate_directory(tmp_path):
    source = tmp_path / "wyckoff_vpa" / "core.txt"
    source.parent.mkdir()
    source.write_text("# Core\n完整量价知识。\n", encoding="utf-8")
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path,
                                  job_root=tmp_path / "jobs")
    path = publisher(_request("vpa_evidence", {
        "task_id": "vpa-inline-knowledge",
        "allowed_knowledge_ids": ["wyckoff_vpa/core.txt"],
    }), 1)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    gateway = FrozenEvidenceGateway(job)
    baseline_result = EvidenceMcpServer(gateway,
                                        job_digest=job.manifest.job_digest)._call_tool(
        "get_role_baseline", {},
    )
    assert baseline_result["isError"] is False
    baseline = baseline_result["structuredContent"]
    assert [item["knowledge_module_id"] for item in baseline["mandatory_modules"]] == [
        "wyckoff_vpa/core.txt",
    ]
    operations = [item["operation"] for item in baseline["resources"]]
    assert "list_allowed_knowledge" not in operations
    assert operations.count("read_knowledge_module") == 1
    module = next(item["payload"] for item in baseline["resources"]
                  if item["operation"] == "read_knowledge_module")
    assert "".join(section["text"] for section in module["sections"]) == source.read_text(encoding="utf-8")
    CodexCliWorker._validate_required_evidence_reads(
        job, tuple(asdict(item) for item in gateway.audit_events),
    )


def test_historical_bounded_stage1_keeps_original_optional_read_semantics(tmp_path):
    job = _historical_job(tmp_path)
    assert "stage1_investigation_policy" not in job.core_context
    assert job.core_context["stage1_decision_packet"]["investigation_envelope"] == {
        "max_optional_calls": 2, "max_fact_batches": 1, "max_knowledge_batches": 1,
    }
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
    CodexCliWorker._validate_required_evidence_reads(
        job, tuple(asdict(event) for event in gateway.audit_events),
    )
    with pytest.raises(EvidenceBudgetExceeded, match="call budget"):
        gateway.get_pa_evidence(["PA.WINDOW.K10"])


def test_historical_semantic_parity_keeps_complete_baseline_and_optional_read_semantics(tmp_path):
    job = _historical_job(tmp_path, semantic_parity=True)
    assert "stage1_investigation_policy" not in job.core_context
    gateway = FrozenEvidenceGateway(job)
    server = EvidenceMcpServer(gateway, job_digest=job.manifest.job_digest)
    assert not server._call_tool("get_role_baseline", {})["isError"]
    gateway.get_pa_evidence(["PA.WINDOW.K5"])
    gateway.get_pa_evidence(["PA.WINDOW.K8"])
    CodexCliWorker._validate_required_evidence_reads(
        job, tuple(asdict(event) for event in gateway.audit_events),
    )


def test_new_stage1_fact_attempt_quota_is_enforced_before_read_and_at_acceptance(tmp_path):
    job = _bounded_job(tmp_path)
    gateway = FrozenEvidenceGateway(job)
    gateway.get_stage1_decision_packet()
    gateway.get_pa_evidence(["PA.WINDOW.K5"])
    with pytest.raises(EvidenceBudgetExceeded, match="fact"):
        gateway.get_pa_evidence(["PA.WINDOW.K8"])
    assert gateway.audit_events[-1].status == "denied"
    assert gateway.audit_events[-1].response_bytes == 0
    with pytest.raises(CodexWorkerStopped) as stopped:
        CodexCliWorker._validate_required_evidence_reads(
            job, tuple(asdict(event) for event in gateway.audit_events),
        )
    assert stopped.value.stop.status == "incomplete"
    forged = tuple({"operation": operation, "status": "ok", "request": {}}
                   for operation in ("get_stage1_decision_packet", "get_pa_evidence", "get_pa_evidence"))
    with pytest.raises(CodexWorkerStopped):
        CodexCliWorker._validate_required_evidence_reads(job, forged)


def test_new_stage1_freezes_flow_policy_and_delivers_matching_role_skill(tmp_path):
    job = _bounded_job(tmp_path)
    packet = FrozenEvidenceGateway(job).get_stage1_decision_packet()
    assert job.core_context["stage1_investigation_policy"] == "stage1_investigation_v1"
    assert packet["investigation_envelope"] == {
        "max_optional_calls": 2, "max_fact_batches": 1, "max_knowledge_batches": 1,
        "policy_version": "stage1_investigation_v1", "quota_basis": "attempts",
        "baseline_requirement": "successful_packet_first",
    }
    assert packet["role_skill"] == job.runtime["skills"]["stage1"]
    assert "按调用尝试计数，失败也占配额" in packet["role_skill"]
    assert "三者共用一次 fact 配额" in packet["role_skill"]


@pytest.mark.parametrize("fact_first", [True, False])
def test_new_stage1_accepts_one_fact_and_one_knowledge_attempt_in_either_order(tmp_path, fact_first):
    (tmp_path / "brooks.txt").write_text("Exact frozen Brooks section.", encoding="utf-8")
    job = _bounded_job(tmp_path, allowed_knowledge_ids=("brooks.txt",))
    gateway = FrozenEvidenceGateway(job)
    gateway.get_stage1_decision_packet()
    section = job.knowledge_documents["brooks.txt"]["sections"][0]["section_id"]
    read_fact = lambda: gateway.get_pa_evidence(["PA.WINDOW.K5"])
    read_knowledge = lambda: gateway.read_allowed_knowledge(knowledge_id="brooks.txt", section_ids=[section])
    for read in ((read_fact, read_knowledge) if fact_first else (read_knowledge, read_fact)):
        read()
    assert len(gateway.audit_events) == 3
    CodexCliWorker._validate_required_evidence_reads(
        job, tuple(asdict(event) for event in gateway.audit_events),
    )


def test_new_stage1_second_knowledge_attempt_is_rejected(tmp_path):
    (tmp_path / "brooks.txt").write_text("Exact frozen Brooks section.", encoding="utf-8")
    job = _bounded_job(tmp_path, allowed_knowledge_ids=("brooks.txt",))
    gateway = FrozenEvidenceGateway(job)
    gateway.get_stage1_decision_packet()
    section = job.knowledge_documents["brooks.txt"]["sections"][0]["section_id"]
    gateway.read_allowed_knowledge(knowledge_id="brooks.txt", section_ids=[section])
    with pytest.raises(EvidenceBudgetExceeded, match="knowledge"):
        gateway.read_allowed_knowledge(knowledge_id="brooks.txt", section_ids=[section])
    with pytest.raises(CodexWorkerStopped):
        CodexCliWorker._validate_required_evidence_reads(
            job, tuple(asdict(event) for event in gateway.audit_events),
        )


def test_new_stage1_optional_read_requires_successful_packet_not_a_prior_attempt(tmp_path):
    job = _bounded_job(tmp_path)
    gateway = FrozenEvidenceGateway(job)
    with pytest.raises(EvidenceAccessDenied, match="successfully read"):
        gateway.get_pa_evidence(["PA.WINDOW.K5"])
    with pytest.raises(CodexWorkerOutputError, match="successfully read"):
        CodexCliWorker._validate_required_evidence_reads(
            job, tuple(asdict(event) for event in gateway.audit_events),
        )
    incomplete_packet = {"operation": "get_stage1_decision_packet", "status": "pending"}
    optional = {"operation": "get_pa_evidence", "status": "ok"}
    with pytest.raises(CodexWorkerOutputError, match="successfully read"):
        CodexCliWorker._validate_required_evidence_reads(job, (incomplete_packet, optional))


def test_new_stage1_denied_attempt_consumes_its_category_and_cannot_hide_behind_draft(tmp_path):
    job = _bounded_job(tmp_path)
    gateway = FrozenEvidenceGateway(job)
    gateway.get_stage1_decision_packet()
    with pytest.raises(EvidenceAccessDenied, match="outside role snapshot"):
        gateway.get_pa_evidence(["PA.UNKNOWN"])
    denied = tuple(asdict(event) for event in gateway.audit_events)
    with pytest.raises(CodexWorkerOutputError, match="cannot hide"):
        CodexCliWorker._validate_required_evidence_reads(job, denied)
    with pytest.raises(EvidenceBudgetExceeded, match="fact"):
        gateway.get_pa_evidence(["PA.WINDOW.K5"])


@pytest.mark.parametrize("operation,arguments", [
    ("measure_price_window", {"older_seq": 5, "newer_seq": 1}),
    ("project_price_leg", {"start_seq": 5, "start_field": "low", "end_seq": 3,
                           "end_field": "high", "origin_seq": 1, "origin_field": "low"}),
])
def test_new_stage1_measurement_tools_share_the_fact_attempt_quota(tmp_path, operation, arguments):
    job = _bounded_job(tmp_path)
    gateway = FrozenEvidenceGateway(job)
    server = EvidenceMcpServer(gateway, job_digest=job.manifest.job_digest)
    assert not server._call_tool("get_stage1_decision_packet", {})["isError"]
    assert not server._call_tool(operation, arguments)["isError"]
    assert server._call_tool("get_pa_evidence", {"fact_ids": ["PA.WINDOW.K5"]})["isError"]
    assert gateway.audit_events[-1].reason == "stage1_fact_budget_exceeded"
    with pytest.raises(CodexWorkerStopped):
        CodexCliWorker._validate_required_evidence_reads(job, tuple(asdict(event) for event in gateway.audit_events))


@pytest.mark.parametrize("budget_denial", [False, True])
def test_new_stage1_worker_rejects_a_successful_draft_after_denied_evidence(tmp_path, budget_denial):
    import subprocess
    from pathlib import Path
    from pa_agent.agent_runtime.codex_worker import CodexCliConfig

    job = _bounded_job(tmp_path)
    published = FrozenAgentJobStore(tmp_path / "jobs").publish(job)

    def fake_cli(command, **kwargs):
        args = json.loads(next(item.split("=", 1)[1] for item in command
                               if item.startswith("mcp_servers.pa_evidence.args=")))
        server = EvidenceMcpServer(FrozenEvidenceGateway(job), job_digest=job.manifest.job_digest,
                                   audit_output=Path(args[args.index("--audit-output") + 1]))
        assert not server._call_tool("get_stage1_decision_packet", {})["isError"]
        if budget_denial:
            assert not server._call_tool("get_pa_evidence", {"fact_ids": ["PA.WINDOW.K5"]})["isError"]
        assert server._call_tool("get_pa_evidence", {
            "fact_ids": ["PA.WINDOW.K8" if budget_denial else "PA.UNKNOWN"],
        })["isError"]
        Path(command[command.index("--output-last-message") + 1]).write_text('{"result":{}}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"type": "turn.completed",
            "usage": {"input_tokens": 20, "output_tokens": 5}}), stderr="")

    config = CodexCliConfig(codex_home=tmp_path / "home", runtime_root=tmp_path / "runtime",
                            outbox_root=tmp_path / "outbox", model="gpt-6-luna", reasoning_effort="xhigh")
    expected_error = CodexWorkerStopped if budget_denial else CodexWorkerOutputError
    with pytest.raises(expected_error) as rejected:
        CodexCliWorker(config, process_runner=fake_cli).run(published)
    if budget_denial:
        assert rejected.value.stop.status == "incomplete"
    assert rejected.value.observed_usage.total_tokens == 25
    assert not (config.outbox_root / f"{job.manifest.job_digest}.json").exists()


def test_new_stage1_repeated_packet_is_denied_without_redelivery(tmp_path):
    job = _bounded_job(tmp_path)
    gateway = FrozenEvidenceGateway(job)
    gateway.get_stage1_decision_packet()
    with pytest.raises(EvidenceAccessDenied, match="exactly one"):
        gateway.get_stage1_decision_packet()
    assert gateway.audit_events[-1].response_bytes == 0
    with pytest.raises(CodexWorkerOutputError, match="exactly one"):
        CodexCliWorker._validate_required_evidence_reads(
            job, tuple(asdict(event) for event in gateway.audit_events),
        )


@pytest.mark.parametrize("policy", ["future-policy", "", None])
def test_unknown_explicit_stage1_policy_fails_closed(tmp_path, policy):
    current = _bounded_job(tmp_path)
    job = replace(current, core_context={**current.core_context, "stage1_investigation_policy": policy})
    with pytest.raises(EvidenceAccessDenied, match="unknown"):
        FrozenEvidenceGateway(job)
    with pytest.raises(CodexWorkerOutputError, match="unknown"):
        CodexCliWorker._validate_required_evidence_reads(job, ())


def test_unknown_stage1_policy_is_rejected_before_starting_cli(tmp_path):
    from pa_agent.agent_runtime.codex_worker import CodexCliConfig
    from tests.unit.test_frozen_agent_job import _job

    job = _job(core_context={"stage1_investigation_policy": "future-policy"})
    published = FrozenAgentJobStore(tmp_path / "unknown-jobs").publish(job)
    calls = []
    worker = CodexCliWorker(CodexCliConfig(
        codex_home=tmp_path / "home", runtime_root=tmp_path / "runtime",
        outbox_root=tmp_path / "outbox", model="gpt-6-luna", reasoning_effort="xhigh",
    ), process_runner=lambda *args, **kwargs: calls.append(args))
    with pytest.raises(CodexWorkerOutputError, match="unknown"):
        worker.run(published)
    assert calls == []


def test_new_stage1_fixed_denial_cannot_be_hidden_by_a_later_draft(tmp_path):
    current = _bounded_job(tmp_path)
    job = replace(current, runtime={**current.runtime,
                  "backend_runtime_identity": {"investigation_policy": "fixed"}})
    gateway = FrozenEvidenceGateway(job)
    gateway.get_stage1_decision_packet()
    with pytest.raises(EvidenceAccessDenied, match="fixed"):
        gateway.get_pa_evidence(["PA.WINDOW.K5"])
    with pytest.raises(CodexWorkerOutputError, match="cannot hide"):
        CodexCliWorker._validate_required_evidence_reads(
            job, tuple(asdict(event) for event in gateway.audit_events),
        )


def test_new_stage1_child_and_pointer_repair_keep_their_independent_scope_and_budget(tmp_path):
    from pa_agent.agent_runtime.frozen_job import AgentJobBudget

    job = _bounded_job(tmp_path)
    child = FrozenEvidenceGateway(job, child_budget=AgentJobBudget(max_evidence_calls=2))
    child.get_pa_evidence(["PA.WINDOW.K5"])
    child.get_pa_evidence(["PA.WINDOW.K8"])
    assert all(event.actor == "child" for event in child.audit_events)
    with pytest.raises(EvidenceBudgetExceeded):
        child.get_pa_evidence(["PA.WINDOW.K10"])
    with pytest.raises(CodexWorkerOutputError, match="non-parent"):
        CodexCliWorker._validate_required_evidence_reads(
            job, tuple(asdict(event) for event in child.audit_events),
        )
    repaired = replace(job, repair_findings={"editable_pointers": ["/field"]},
                       runtime={**job.runtime, "skills": {"draft_pointer_repair": "frozen repair skill"}})
    repair_gateway = FrozenEvidenceGateway(repaired)
    repair_gateway.get_repair_findings()
    repair_gateway.get_pa_evidence(["PA.WINDOW.K5"])
    repair_gateway.get_pa_evidence(["PA.WINDOW.K8"])
    CodexCliWorker._validate_required_evidence_reads(
        repaired, tuple(asdict(event) for event in repair_gateway.audit_events),
    )
    with pytest.raises(EvidenceBudgetExceeded):
        repair_gateway.get_pa_evidence(["PA.WINDOW.K10"])


def test_optional_operation_categories_cover_all_tools_and_unknown_audit_operation_is_rejected(tmp_path):
    from pa_agent.agent_runtime.investigation_policy import OPTIONAL_INVESTIGATION_CATEGORIES
    from pa_agent.agent_runtime.evidence import OPTIONAL_INVESTIGATION_OPERATIONS

    assert OPTIONAL_INVESTIGATION_CATEGORIES == {
        "list_fact_ids": "fact", "get_measurements": "fact", "get_pa_evidence": "fact",
        "get_facts": "fact", "get_bars": "fact", "measure_price_window": "fact",
        "project_price_leg": "fact", "search_allowed_knowledge": "knowledge",
        "read_allowed_knowledge": "knowledge",
    }
    assert set(OPTIONAL_INVESTIGATION_OPERATIONS) == set(OPTIONAL_INVESTIGATION_CATEGORIES)
    with pytest.raises(CodexWorkerOutputError, match="outside"):
        CodexCliWorker._validate_required_evidence_reads(_bounded_job(tmp_path), (
            {"operation": "get_stage1_decision_packet", "status": "ok"},
            {"operation": "future_unclassified_investigation", "status": "ok"},
        ))


def test_new_stage1_actual_mcp_challenge_keeps_parent_and_child_investigations_separate(tmp_path):
    import time
    from pa_agent.agent_runtime.stage_challenge import ChildExecution, StageChallengeHost, challenge_rpc
    from pa_agent.ai.deepseek_client import AIUsage

    (tmp_path / "brooks.txt").write_text("Exact frozen Brooks section.", encoding="utf-8")
    job = _bounded_job(tmp_path, allowed_knowledge_ids=("brooks.txt",))
    gateway = FrozenEvidenceGateway(job)
    audit_path, binding_path = tmp_path / "audit.json", tmp_path / "challenge.json"
    server = EvidenceMcpServer(gateway, job_digest=job.manifest.job_digest,
                               audit_output=audit_path, challenge_binding=binding_path)
    assert not server._call_tool("get_stage1_decision_packet", {})["isError"]

    def execute(context, endpoint, token, *args):
        refs = []
        for index in range(2):
            refs.extend(challenge_rpc(endpoint, token, {"action": "read", "resource_index": index})["citable_ids"])
        return ChildExecution({"source_digest": context["job_digest"], "evidence_refs": refs,
                               "support": "Scoped comparison", "challenge": "", "unknown": ""}, AIUsage())

    def ready():
        CodexCliWorker._validate_required_evidence_reads(
            job, CodexCliWorker._read_evidence_audit(audit_path, job),
        )

    with StageChallengeHost(job, execute=execute, parent_ready=ready, deadline=time.monotonic() + 10) as host:
        binding_path.write_text(json.dumps(host.binding()), encoding="utf-8")
        started = server._call_tool("start_stage_challenge", {"question": "Compare two frozen windows", "resources": [
            {"operation": "get_pa_evidence", "arguments": {"fact_ids": [fact_id]}}
            for fact_id in ("PA.WINDOW.K5", "PA.WINDOW.K8")
        ]})
        assert not started["isError"]
        collected = server._call_tool("get_stage_challenge", {"wait_seconds": 3})
        assert collected["structuredContent"]["status"] == "completed"
        child_events = host.audit()["evidence_events"]
        assert len(child_events) == 2
        assert {event["actor"] for event in child_events} == {"child"}
    assert not server._call_tool("get_pa_evidence", {"fact_ids": ["PA.WINDOW.K5"]})["isError"]
    section = job.knowledge_documents["brooks.txt"]["sections"][0]["section_id"]
    assert not server._call_tool("read_allowed_knowledge", {
        "knowledge_id": "brooks.txt", "section_ids": [section],
    })["isError"]
    events = CodexCliWorker._read_evidence_audit(audit_path, job)
    assert [event["operation"] for event in events] == [
        "get_stage1_decision_packet", "get_pa_evidence", "read_allowed_knowledge",
    ]
    assert [event["mcp_call_index"] for event in events] == [1, 4, 5]
    CodexCliWorker._validate_required_evidence_reads(job, events)


@pytest.mark.parametrize("operation,arguments", [
    ("get_pa_evidence", {}),
    ("get_pa_evidence", []),
    ("get_pa_evidence", {"fact_ids": "private-invalid-value"}),
    ("measure_price_window", {"older_seq": 5}),
    ("project_price_leg", {}),
    ("read_allowed_knowledge", {"knowledge_id": "private-invalid-value"}),
    ("get_stage1_decision_packet", {"credential": "private-invalid-value"}),
])
def test_new_stage1_handle_invalid_arguments_are_audited_without_peer_values(tmp_path, operation, arguments):
    job = _bounded_job(tmp_path)
    gateway = FrozenEvidenceGateway(job)
    server = EvidenceMcpServer(gateway, job_digest=job.manifest.job_digest,
                               audit_output=tmp_path / "audit.json")
    if operation != "get_stage1_decision_packet":
        assert not server._call_tool("get_stage1_decision_packet", {})["isError"]
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": operation, "arguments": arguments}})
    assert "error" in response or response["result"]["isError"]
    event = gateway.audit_events[-1]
    assert (event.operation, event.status, event.response_bytes, event.request) == (operation, "denied", 0, {})
    assert "private-invalid-value" not in json.dumps(response)
    assert "private-invalid-value" not in (tmp_path / "audit.json").read_text("utf-8")
    with pytest.raises((CodexWorkerOutputError, CodexWorkerStopped)):
        CodexCliWorker._validate_required_evidence_reads(job, tuple(asdict(item) for item in gateway.audit_events))


def test_new_stage1_invalid_arguments_before_packet_are_charged_as_denied(tmp_path):
    job = _bounded_job(tmp_path)
    gateway = FrozenEvidenceGateway(job)
    server = EvidenceMcpServer(gateway, job_digest=job.manifest.job_digest)
    assert server._call_tool("get_pa_evidence", {})["isError"]
    assert len(gateway.audit_events) == 1
    assert gateway.audit_events[0].reason == "stage1_baseline_required"
    assert gateway.audit_events[0].request == {}
    with pytest.raises(CodexWorkerOutputError):
        CodexCliWorker._validate_required_evidence_reads(job, tuple(asdict(item) for item in gateway.audit_events))


@pytest.mark.parametrize("scope", ["historical", "child", "repair", "stage2"])
def test_new_mcp_argument_accounting_preserves_other_frozen_scopes(tmp_path, scope):
    from pa_agent.agent_runtime.frozen_job import AgentJobBudget

    job = _historical_job(tmp_path) if scope == "historical" else _bounded_job(tmp_path)
    if scope == "repair":
        job = replace(job, repair_findings={"editable_pointers": ["/field"]})
    elif scope == "stage2":
        job = replace(job, manifest=replace(job.manifest, role="stage2"))
    gateway = FrozenEvidenceGateway(job, child_budget=AgentJobBudget() if scope == "child" else None)
    server = EvidenceMcpServer(gateway, job_digest=job.manifest.job_digest)
    with pytest.raises(KeyError):
        server._call_tool("get_pa_evidence", {})
    assert not gateway.audit_events
    assert server.reject_invalid_tool_arguments("get_pa_evidence", {}) is None
    assert not gateway.audit_events


@pytest.mark.parametrize("invalid_attempt", [False, True])
def test_new_stage1_real_sdk_stdio_accounts_valid_and_invalid_attempts(tmp_path, invalid_attempt):
    import subprocess
    import sys
    from pa_agent.agent_runtime.decision_packet import build_stage1_decision_packet
    from pa_agent.agent_runtime.frozen_job import FrozenAgentJobBuilder
    from tests.unit.test_codex_evidence_mcp import _initialize, _request as mcp_request

    (tmp_path / "brooks.txt").write_text("Exact optional Brooks section.", encoding="utf-8")
    current = _bounded_job(tmp_path, allowed_knowledge_ids=("brooks.txt",))
    knowledge_manifest = {**current.knowledge_manifest, "modules": [
        {**module, "loading_policy": "on_demand"} for module in current.knowledge_manifest["modules"]
    ]}
    context = dict(current.core_context)
    context["stage1_decision_packet"] = build_stage1_decision_packet(
        snapshot_hash=current.manifest.snapshot_hash, cutoff_ts_open=current.manifest.cutoff_ts_open,
        bars=current.bars, facts=current.fact_projection["facts"], core_context=context,
        knowledge_documents=current.knowledge_documents, knowledge_manifest=knowledge_manifest,
        role_skill=current.runtime["skills"]["stage1"],
    )
    job = FrozenAgentJobBuilder(runtime_version=current.manifest.runtime_version,
        agents_text=current.runtime["agents_text"], skill_documents=current.runtime["skills"]).build(
        task_id=current.manifest.task_id, request_id=current.manifest.request_id, role="stage1", attempt=1,
        snapshot_hash=current.manifest.snapshot_hash, cutoff_ts_open=current.manifest.cutoff_ts_open,
        messages=current.request["messages"], output_schema=current.output_schema,
        fact_projection=current.fact_projection, bars=current.bars,
        knowledge_documents=current.knowledge_documents, allowed_knowledge_ids=("brooks.txt",),
        knowledge_manifest=knowledge_manifest, core_context=context, budget=current.budget,
        backend_runtime_identity=current.runtime["backend_runtime_identity"],
    )
    path = FrozenAgentJobStore(tmp_path / "jobs").publish(job)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    audit_path = tmp_path / "sdk-audit.json"
    process = subprocess.Popen([
        sys.executable, "-B", "-m", "pa_agent.agent_runtime.evidence_mcp", "--job-root",
        str(tmp_path / "jobs"), "--job-digest", job.manifest.job_digest, "--audit-output", str(audit_path),
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        _initialize(process)
        def call(request_id, name, arguments):
            return mcp_request(process, {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                                        "params": {"name": name, "arguments": arguments}})
        assert not call(1, "get_stage1_decision_packet", {})["result"]["isError"]
        if invalid_attempt:
            invalid = call(2, "get_pa_evidence", {})
            assert "error" in invalid or invalid["result"]["isError"]
            events = CodexCliWorker._read_evidence_audit(audit_path, job)
            assert [(event["operation"], event["status"]) for event in events] == [
                ("get_stage1_decision_packet", "ok"), ("get_pa_evidence", "denied"),
            ]
            assert events[-1]["request"] == {}
            with pytest.raises(CodexWorkerOutputError, match="cannot hide"):
                CodexCliWorker._validate_required_evidence_reads(job, events)
            repeated = call(3, "get_pa_evidence", {"fact_ids": ["PA.WINDOW.K5"]})
            assert repeated["result"]["isError"]
            events = CodexCliWorker._read_evidence_audit(audit_path, job)
            assert events[-1]["reason"] == "stage1_fact_budget_exceeded"
        else:
            fact = call(2, "get_pa_evidence", {"fact_ids": ["PA.WINDOW.K5"]})["result"]
            assert not fact["isError"]
            assert fact["structuredContent"]["facts"][0]["fact_id"] == "PA.WINDOW.K5"
            section = job.knowledge_documents["brooks.txt"]["sections"][0]["section_id"]
            knowledge = call(3, "read_allowed_knowledge", {
                "knowledge_id": "brooks.txt", "section_ids": [section],
            })["result"]
            assert not knowledge["isError"]
            assert knowledge["structuredContent"]["sections"][0]["text"] == "Exact optional Brooks section."
            events = CodexCliWorker._read_evidence_audit(audit_path, job)
            assert [(event["operation"], event["status"]) for event in events] == [
                ("get_stage1_decision_packet", "ok"), ("get_pa_evidence", "ok"), ("read_allowed_knowledge", "ok"),
            ]
            CodexCliWorker._validate_required_evidence_reads(job, events)
        assert [event["call_index"] for event in events] == [1, 2, 3]
        assert [event["mcp_call_index"] for event in events] == [1, 2, 3]
    finally:
        process.stdin.close()
        process.wait(timeout=5)
