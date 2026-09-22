"""Analysis-only transport must preserve evidence and avoid coding affordances."""
import json
import tomllib
from dataclasses import replace

import pytest

from pa_agent.agent_runtime.codex_worker import CodexCliConfig, CodexCliWorker
from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from pa_agent.agent_runtime.publisher import CodexJobPublisher
from tests.integration.conftest import make_frame
from tests.unit.test_codex_job_publisher import _request


def _published(tmp_path, role="stage1"):
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path, job_root=tmp_path / "jobs")
    path = publisher(_request(role, {"task_id": "analysis-execution"}), 1)
    return FrozenAgentJobStore(path.parent).open(path.name)


@pytest.mark.parametrize("model", ["gpt-5.6-luna", "deepseek-flash"])
def test_new_jobs_bind_matching_transport_and_readonly_tools(tmp_path, model):
    job = _published(tmp_path)
    worker = CodexCliWorker(CodexCliConfig(
        codex_home=tmp_path / "home", runtime_root=tmp_path / "runtime",
        outbox_root=tmp_path / "outbox", model_catalog=tmp_path / "models.json",
    ))
    schema = worker._materialize_workspace(tmp_path / "workspace", job)
    command = worker._command(
        job=job, job_root=tmp_path / "jobs", workspace=schema.parent,
        schema_path=schema, output_path=tmp_path / "out", audit_path=tmp_path / "audit",
        model=model, reasoning_effort="high",
    )
    settings = dict(command[index + 1].split("=", 1)
                    for index, value in enumerate(command) if value == "--config")
    instructions = json.loads(settings["model_instructions_file"])
    assert settings["project_doc_max_bytes"] == "0"
    assert schema.parent.joinpath("AGENTS.md").read_text("utf-8") == job.runtime["agents_text"]
    assert instructions == str(schema.parent / "AGENTS.md")
    assert settings["features.plugins"] == "false"
    assert settings["agents.enabled"] == "false"
    assert settings["features.shell_tool"] == "false"
    if model == "gpt-5.6-luna":
        assert settings["features.code_mode_host"] == "true"
        mode = tomllib.loads("mode=" + settings["features.code_mode"])["mode"]
        assert mode == {"enabled": True, "excluded_tool_namespaces": ["functions", "clock"]}
        assert "// @exec:" in command[2]
        assert "code-mode exec is unavailable" not in command[2]
    else:
        assert settings["features.code_mode_host"] == "false"
        assert "// @exec:" not in command[2]
    names = json.loads(settings["mcp_servers.pa_evidence.enabled_tools"])
    assert {"get_stage1_decision_packet", "get_pa_evidence", "measure_price_window"} <= set(names)
    assert "get_role_baseline" not in names
    assert "get_vpa_evidence" not in names
    assert "get_repair_findings" not in names
    assert FrozenEvidenceGateway(job).get_market_core()["role_skill"] == job.runtime["skills"]["stage1"]

    old_job = replace(job, core_context={key: value for key, value in job.core_context.items()
                                         if key != "instruction_delivery_policy"})
    old_command = worker._command(
        job=old_job, job_root=tmp_path / "jobs", workspace=schema.parent,
        schema_path=schema, output_path=tmp_path / "out", audit_path=tmp_path / "audit",
        model=model, reasoning_effort="high",
    )
    assert "project_doc_max_bytes=0" not in old_command


def test_worker_does_not_inherit_parent_agent_session(tmp_path, monkeypatch):
    for key in ("CODEX_APP_TOOLS_PIPE_PATH", "CODEX_SESSION_ID", "CODEX_THREAD_ID",
                "CODEX_INTERNAL_ORIGINATOR_OVERRIDE"):
        monkeypatch.setenv(key, "parent-only")
    worker = CodexCliWorker(CodexCliConfig(codex_home=tmp_path / "home",
        runtime_root=tmp_path / "runtime", outbox_root=tmp_path / "outbox"))
    environment = worker._environment()
    assert "parent-only" not in environment.values()
    assert environment["CODEX_HOME"] == str(tmp_path / "home")


def test_baseline_navigation_does_not_mistake_index_or_upstream_refs_for_delivery(tmp_path):
    job = _published(tmp_path)
    job = replace(job, core_context={**job.core_context,
                  "previous_analysis": {"fact_id": "PA.WINDOW.K5"}})
    gateway = FrozenEvidenceGateway(job)
    packet = EvidenceMcpServer(gateway, job_digest=job.manifest.job_digest)._call_tool(
        "get_stage1_decision_packet", {})["structuredContent"]["decision_packet"]
    assert "PA.WINDOW.K5" in packet["available_fact_ids"]
    assert "PA.WINDOW.K5" in {item["fact_id"] for item in packet["pa_fact_spine"]}
    assert len(packet["price_frame"]["rows"]) == len(job.bars)
    assert not any(event.operation == "list_fact_ids" for event in gateway.audit_events)


def test_repair_tools_remain_bounded_and_fixed_jobs_do_not_offer_optional_investigation(tmp_path):
    job = _published(tmp_path)
    repaired = replace(job, repair_findings={"editable_pointers": ["/field"]})
    names = FrozenEvidenceGateway(repaired).available_tool_names()
    assert set(names) == {"get_repair_findings", "get_facts", "get_pa_evidence",
                         "get_htf_evidence", "read_allowed_knowledge"}
    fixed = replace(job, runtime={**job.runtime, "backend_runtime_identity": {"investigation_policy": "fixed"}})
    names = FrozenEvidenceGateway(fixed).available_tool_names()
    assert "get_stage1_decision_packet" in names
    assert "get_pa_evidence" not in names
    assert "measure_price_window" not in names


def test_vpa_delivery_keeps_its_own_values_and_legacy_jobs_keep_old_transport(tmp_path):
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=tmp_path, job_root=tmp_path / "jobs")
    path = publisher(_request("vpa_evidence", {"task_id": "vpa-delivery",
                     "role_evidence": {"evidence": [{"evidence_id": "vpa-e001", "facts": {"volume": 7},
                         "source_reference": {"fact_id": "PA.WINDOW.K5"}}]}}), 1)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    gateway = FrozenEvidenceGateway(job)
    baseline = EvidenceMcpServer(gateway, job_digest=path.name)._call_tool("get_role_baseline", {})["structuredContent"]
    assert baseline["delivery_index"]["delivered_value_ids"] == ["vpa-e001"]
    assert "get_vpa_evidence" in gateway.available_tool_names()
    assert "get_price_frame" not in gateway.available_tool_names()
    assert gateway.get_market_core()["role_skill"] == job.runtime["skills"]["vpa_evidence"]

    legacy = replace(job, core_context={key: value for key, value in job.core_context.items()
                                       if key != "analysis_execution_policy"})
    legacy_gateway = FrozenEvidenceGateway(legacy)
    assert "role_skill" not in legacy_gateway.get_market_core()
    old = EvidenceMcpServer(legacy_gateway, job_digest=path.name)._call_tool("get_role_baseline", {})["structuredContent"]
    assert "delivery_index" not in old
    assert "// @exec:" in CodexCliWorker._initial_prompt(legacy)


def test_real_mcp_sdk_delivers_complete_skill_and_navigation_without_model(tmp_path, monkeypatch):
    import asyncio
    from mcp.server import MCPServer
    from pa_agent.agent_runtime.evidence_mcp import _run_official_sdk

    job = _published(tmp_path)
    instances = []
    # Replace only the blocking stdio loop; registration and serialization are real SDK code.
    monkeypatch.setattr(MCPServer, "run", lambda self: instances.append(self))
    _run_official_sdk(EvidenceMcpServer(FrozenEvidenceGateway(job), job_digest=job.manifest.job_digest))

    async def read_baseline():
        return await instances[0].call_tool("get_stage1_decision_packet", {})

    result = asyncio.run(read_baseline()).model_dump(by_alias=True)
    assert not result["isError"]
    packet = result["structuredContent"]["decision_packet"]
    assert "PA.WINDOW.K5" in packet["available_fact_ids"]
    assert packet["role_skill"] == job.runtime["skills"]["stage1"]
