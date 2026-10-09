"""Focused offline checks for the phase-4 execution boundary."""
from dataclasses import asdict, replace

import pytest

from pa_agent.agent_runtime.codex_worker import (
    CodexCliWorker,
    CodexWorkerAvailabilityError,
    CodexWorkerOutputError,
    CodexWorkerStopped,
)
from pa_agent.agent_runtime.evidence import (
    EvidenceAccessDenied,
    EvidenceBudgetExceeded,
    FrozenEvidenceGateway,
)
from pa_agent.agent_runtime.execution_contract import (
    ANALYSIS_EXECUTION_POLICY, RUNTIME_CONTRACT,
    SEMANTIC_PARITY_CONTEXT_POLICY, STAGE2_HOST_GATED_DRAFT_POLICY,
)
from pa_agent.agent_runtime.frozen_job import AgentJobBudget
from tests.unit.test_frozen_agent_job import _job


def measurement_job():
    return replace(_job(), budget=AgentJobBudget(), bars=(
        {"seq": 1, "ts_open": 300, "open": 109, "high": 112, "low": 108, "close": 110},
        {"seq": 2, "ts_open": 200, "open": 102, "high": 110, "low": 100, "close": 109},
        {"seq": 3, "ts_open": 100, "open": 101, "high": 103, "low": 99, "close": 102},
    ))


def test_budget_denial_cannot_pass_as_a_market_decision():
    job = _job()
    gateway = FrozenEvidenceGateway(job)
    for _ in range(3):
        gateway.list_fact_ids()
    with pytest.raises(EvidenceBudgetExceeded, match="budget"):
        gateway.list_fact_ids()
    from pa_agent.agent_runtime.codex_worker import CodexWorkerStopped

    with pytest.raises(CodexWorkerStopped, match="budget") as caught:
        CodexCliWorker._validate_required_evidence_reads(
            job, tuple(asdict(e) for e in gateway.audit_events),
        )
    assert caught.value.stop.status == "incomplete"


def test_projection_is_signed_replayable_and_not_a_setup_classifier():
    job = measurement_job()
    args = dict(start_seq=3, start_field="low", end_seq=2, end_field="high",
                origin_seq=1, origin_field="low")
    gateway = FrozenEvidenceGateway(job)
    result = gateway.project_price_leg(**args)
    assert result["values"] == {"signed_leg": 11.0, "projected_price": 119.0}
    assert result == FrozenEvidenceGateway(job).project_price_leg(**args)
    assert len(result["measurement_digest"]) == 64
    assert result["source_bar_refs"] == ["K3", "K2", "K1"]
    assert "fact_id" not in result  # Never invent a catalog ID the Validator cannot resolve.
    down = gateway.project_price_leg(**{**args, "start_field": "high", "start_seq": 2,
                                        "end_field": "low", "end_seq": 1})
    assert down["values"]["signed_leg"] == -2.0
    assert down["values"]["projected_price"] == 106.0


@pytest.mark.parametrize("change", [{"start_seq": 0}, {"start_seq": 999},
                                    {"start_field": "volume"}, {"start_seq": True}])
def test_measurement_rejects_non_frozen_or_non_price_anchors(change):
    gateway = FrozenEvidenceGateway(measurement_job())
    args = dict(start_seq=3, start_field="low", end_seq=2, end_field="high",
                origin_seq=1, origin_field="low")
    with pytest.raises(EvidenceAccessDenied):
        gateway.project_price_leg(**{**args, **change})
    assert gateway.audit_events[-1].status == "denied"


def test_window_measurements_preserve_chronology_and_missing_is_not_zero():
    gateway = FrozenEvidenceGateway(measurement_job())
    result = gateway.measure_price_window(older_seq=3, newer_seq=1)
    assert result["values"]["net_change"] == 9.0
    assert result["values"]["bar_count"] == 3
    assert result["values"]["high"] == 112.0
    with pytest.raises(EvidenceAccessDenied):
        FrozenEvidenceGateway(_job()).measure_price_window(older_seq=2, newer_seq=1)


def test_runtime_has_distinct_joint_skill_without_enabling_pipeline():
    from pa_agent.agent_runtime.runtime_assets import load_codex_runtime_assets
    assets = load_codex_runtime_assets()
    assert "joint_stage2" in assets.skill_documents
    assert "pa_vpa_stage2_draft_v1" in assets.skill_documents["joint_stage2"]
    assert "analysis_role_stop_v1" in assets.agents_text


def test_stop_is_non_decision_and_never_availability_fallback():
    job = replace(_job(), core_context={"runtime_contract_version": RUNTIME_CONTRACT})
    stopped = {"protocol_version": "analysis_role_stop_v1", "status": "review_required",
               "role": "stage1", "reason": "snapshot identity conflict", "evidence_refs": []}
    with pytest.raises(CodexWorkerStopped) as exc:
        CodexCliWorker._unwrap_result(job, {"result": stopped})
    assert not isinstance(exc.value, CodexWorkerAvailabilityError)
    assert exc.value.stop.status == "review_required"
    with pytest.raises(CodexWorkerOutputError, match="identity"):
        CodexCliWorker._unwrap_result(job, {"result": {**stopped, "role": "stage2"}})
    with pytest.raises(CodexWorkerOutputError, match="malformed"):
        CodexCliWorker._unwrap_result(job, {"result": {**stopped, "decision": {}}})
    assert CodexCliWorker._unwrap_result(job, {"result": {"cycle": "range"}}) == {"cycle": "range"}


def test_transport_schema_preserves_nested_vpa_references():
    import jsonschema

    from pa_agent.ai.role_contracts import VPA_EVIDENCE_SCHEMA

    job = replace(_job(), output_schema=VPA_EVIDENCE_SCHEMA,
                  core_context={"runtime_contract_version": RUNTIME_CONTRACT})
    schema = CodexCliWorker._transport_schema(job)
    jsonschema.Draft7Validator.check_schema(schema)
    assert schema["$defs"]
    assert "$defs" not in schema["properties"]["result"]["anyOf"][0]
    stop = {"protocol_version": "analysis_role_stop_v1", "status": "incomplete",
            "role": "stage1", "reason": "missing mandatory input", "evidence_refs": []}
    jsonschema.validate({"result": stop}, schema)


def test_new_stage2_transport_is_draft_only_without_changing_legacy_stop():
    import jsonschema

    base = _job(core_context={"runtime_contract_version": RUNTIME_CONTRACT})
    legacy = replace(base, manifest=replace(base.manifest, role="stage2"))
    stopped = {"protocol_version": "analysis_role_stop_v1", "status": "review_required",
               "role": "stage2", "reason": "claimed upstream conflict", "evidence_refs": []}
    jsonschema.validate({"result": stopped}, CodexCliWorker._transport_schema(legacy))
    with pytest.raises(CodexWorkerStopped):
        CodexCliWorker._unwrap_result(legacy, {"result": stopped})

    gated = replace(legacy, core_context={
        **legacy.core_context,
        "analysis_execution_policy": ANALYSIS_EXECUTION_POLICY,
        "context_policy": SEMANTIC_PARITY_CONTEXT_POLICY,
        "result_policy": STAGE2_HOST_GATED_DRAFT_POLICY,
    })
    schema = CodexCliWorker._transport_schema(gated)
    jsonschema.Draft7Validator.check_schema(schema)
    jsonschema.validate({"result": {"cycle": "range"}}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"result": stopped}, schema)
    with pytest.raises(CodexWorkerOutputError, match="cannot return a model role stop"):
        CodexCliWorker._unwrap_result(gated, {"result": stopped})
    assert CodexCliWorker._unwrap_result(gated, {"result": {"cycle": "range"}}) == {"cycle": "range"}
    with pytest.raises(CodexWorkerOutputError, match="skipped required frozen context reads"):
        CodexCliWorker._validate_required_evidence_reads(gated, ())
    with pytest.raises(CodexWorkerOutputError, match="evidence budget exceeded"):
        CodexCliWorker._validate_required_evidence_reads(gated, ({
            "status": "denied", "reason": "budget_exceeded", "operation": "get_market_core",
        },))


def test_repair_cannot_reopen_investigation_and_only_installs_repair_skill(tmp_path):
    job = replace(measurement_job(), repair_findings={"allowed_pointers": ["/reason"]},
                  runtime={"agents_text": "runtime", "skills": {"stage1": "old skill",
                           "draft_pointer_repair": "repair skill"}})
    gateway = FrozenEvidenceGateway(job)
    with pytest.raises(EvidenceAccessDenied, match="repair"):
        gateway.measure_price_window(older_seq=3, newer_seq=1)
    CodexCliWorker._materialize_workspace(tmp_path / "worker", job)
    assert [p.name for p in (tmp_path / "worker/.agents/skills").iterdir()] == ["draft-pointer-repair"]


def test_joint_skill_selected_only_by_matching_frozen_protocol_and_schema(tmp_path):
    from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
    from pa_agent.agent_runtime.publisher import CodexJobPublisher
    from pa_agent.ai.pa_handoff import compile_pa_handoff
    from pa_agent.ai.pa_provider_contracts import schema_for_joint_stage2_draft
    from tests.integration.conftest import make_frame
    from tests.unit.test_codex_job_publisher import _request

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    hypotheses = compile_pa_handoff({}, None, make_frame()).model_dump(mode="json")
    request = _request("stage2", {"task_id": "task-1",
        "role_protocol": {"contract_version": "joint_stage2_agent_protocol_v1"},
        "pa_hypotheses": hypotheses,
        "vpa_catalog_id": "frozen-vpa",
        "vpa_evidence": {
            "catalog_id": "frozen-vpa", "data_status": "unavailable",
            "summary": "No usable volume evidence.", "observations": [],
            "hypothesis_assessments": [], "background_relation": "insufficient_data",
            "entry_timing_relation": "insufficient_data",
            "next_confirmation": {
                "state": "not_applicable", "trigger": None, "requires_closed_bar": False,
            },
        }})
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=prompts, job_root=tmp_path / "jobs")
    with pytest.raises(ValueError, match="must agree"):
        publisher(request, 1)
    published = publisher(replace(request, schema=schema_for_joint_stage2_draft()), 1)
    job = FrozenAgentJobStore(published.parent).open(published.name)
    assert job.manifest.role == "stage2"
    assert job.core_context["result_policy"] == STAGE2_HOST_GATED_DRAFT_POLICY
    assert set(job.runtime["skills"]) == {"joint_stage2"}
    import jsonschema

    transport = CodexCliWorker._transport_schema(job)
    jsonschema.Draft7Validator.check_schema(transport)
    assert "anyOf" not in transport["properties"]["result"]
    assert "pa-vpa-stage2-decision" in CodexCliWorker._initial_prompt(job)
    assert "only the Stage 2 Draft" in CodexCliWorker._initial_prompt(job)
    assert FrozenEvidenceGateway(job).get_market_core()["role_skill"] == job.runtime["skills"]["joint_stage2"]
    assert all("volume" not in bar for bar in job.bars)
    assert job.fact_projection["role_evidence"] is None


def test_mcp_measurement_dispatch_is_read_only_and_records_replay_receipt(tmp_path):
    import json

    from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer, _tools

    job = measurement_job()
    server = EvidenceMcpServer(FrozenEvidenceGateway(job), job_digest=job.manifest.job_digest,
                               audit_output=tmp_path / "audit.json")
    result = server._call_tool("measure_price_window", {"older_seq": 3, "newer_seq": 1})
    assert not result["isError"]
    audit = json.loads((tmp_path / "audit.json").read_text("utf-8"))
    receipt = result["structuredContent"]["measurement"]
    assert audit["events"][0]["measurement"] == receipt
    assert receipt == FrozenEvidenceGateway(job).measure_price_window(**receipt["request"])
    assert all(t["annotations"]["readOnlyHint"] for t in _tools() if t["name"] != "start_stage_challenge")


def test_stop_survives_isolated_spool_without_becoming_availability():
    from pa_agent.agent_runtime.spool_worker import _decode_result

    raw = {"schema_version": 1, "request_id": "r", "job_digest": "j", "status": "error",
           "acceptance_key": "a", "runtime_identity": {"version": "v"},
           "error_type": "CodexWorkerStopped", "role_stop": {
               "protocol_version": "analysis_role_stop_v1", "status": "incomplete",
               "role": "stage2", "reason": "evidence budget exceeded", "evidence_refs": []}}
    args = {"request_id": "r", "job_digest": "j", "acceptance_key": "a", "runtime_identity": {"version": "v"}}
    with pytest.raises(CodexWorkerStopped) as exc:
        _decode_result(raw, **args)
    assert exc.value.stop.role == "stage2"
    with pytest.raises(CodexWorkerOutputError, match="identity"):
        _decode_result({**raw, "acceptance_key": "other"}, **args)


def test_incomplete_transport_never_creates_a_success_outbox(tmp_path):
    import json
    import subprocess

    from pa_agent.agent_runtime.codex_worker import CodexCliConfig
    from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore

    job = _job(core_context={"runtime_contract_version": RUNTIME_CONTRACT})
    published = FrozenAgentJobStore(tmp_path / "jobs").publish(job)
    config = CodexCliConfig(codex_home=tmp_path / "home", runtime_root=tmp_path / "runtime",
                            outbox_root=tmp_path / "outbox", model="gpt-5.6-terra", reasoning_effort="high")

    def run(command, **kwargs):
        from pathlib import Path
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps({"result": {"protocol_version": "analysis_role_stop_v1",
            "status": "incomplete", "role": "stage1", "reason": "mandatory resource missing", "evidence_refs": []}}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(CodexWorkerStopped):
        CodexCliWorker(config, process_runner=run).run(published)
    assert not list((tmp_path / "outbox").iterdir())
    assert not list((tmp_path / "runtime").iterdir())


def test_mcp_sdk_registration_uses_the_same_gateway(monkeypatch):
    import mcp.server

    from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer, _run_official_sdk, _tools

    registered = {}

    class RegistrationOnly:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self, **kwargs):
            def register(func):
                registered[func.__name__] = func
                return func
            return register

        def run(self):
            pass

    monkeypatch.setattr(mcp.server, "MCPServer", RegistrationOnly)
    job = measurement_job()
    _run_official_sdk(EvidenceMcpServer(FrozenEvidenceGateway(job), job_digest=job.manifest.job_digest))
    assert set(registered) == {t["name"] for t in _tools()}
    result = registered["measure_price_window"](older_seq=3, newer_seq=1)
    assert result["measurement"]["values"]["bar_count"] == 3


@pytest.mark.parametrize("invalid", [None, float("nan"), float("inf"), True])
def test_invalid_numeric_anchor_is_not_filled_or_coerced(invalid):
    job = measurement_job()
    bars = tuple({**bar, "open": invalid} if bar["seq"] == 3 else bar for bar in job.bars)
    with pytest.raises(EvidenceAccessDenied, match="missing or non-finite"):
        FrozenEvidenceGateway(replace(job, bars=bars)).measure_price_window(older_seq=3, newer_seq=1)


def test_skill_metadata_and_tool_references_match_executable_surface():
    import re
    from pa_agent.agent_runtime.runtime_assets import load_codex_runtime_assets
    from pa_agent.agent_runtime.execution_contract import SKILL_NAMES
    from pa_agent.agent_runtime.evidence_mcp import _tools

    assets = load_codex_runtime_assets()
    tools = {item["name"] for item in _tools()}
    for role, text in assets.skill_documents.items():
        metadata = dict(line.split(": ", 1) for line in text.split("---", 2)[1].strip().splitlines())
        assert metadata["name"] == SKILL_NAMES[role]
        assert 0 < len(metadata["description"]) <= 1024
        mentioned_tools = set(re.findall(r"`((?:get|list|read|search|measure|project)_[a-z_]+)`", text))
        assert mentioned_tools <= tools
