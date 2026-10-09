from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace

import pytest

from pa_agent.agent_runtime.codex_worker import CodexCliWorker, CodexWorkerOutputError
from pa_agent.agent_runtime.evidence import EvidenceAccessDenied, EvidenceBudgetExceeded, FrozenEvidenceGateway
from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from pa_agent.agent_runtime.publisher import CodexJobPublisher
from tests.integration.conftest import make_frame
from tests.unit.test_codex_job_publisher import _request


def _bounded_job(tmp_path, *, market_context=None):
    frame = make_frame()
    if market_context is not None:
        frame = replace(frame, data_metadata={**frame.data_metadata, "market_context": market_context})
    publisher = CodexJobPublisher(frame=frame, prompt_dir=tmp_path,
                                  job_root=tmp_path / "jobs")
    path = publisher(_request("stage1", {"task_id": "bounded-v2"}), 1)
    return FrozenAgentJobStore(path.parent).open(path.name)


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
    with pytest.raises(EvidenceBudgetExceeded, match="call budget"):
        gateway.get_pa_evidence(["PA.WINDOW.K10"])
