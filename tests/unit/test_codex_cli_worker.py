from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from pa_agent.agent_runtime.codex_worker import (
    CodexCliConfig,
    CodexCliWorker,
    CodexWorkerOutputError,
    CodexWorkerUnavailable,
)
from pa_agent.agent_runtime.runtime_audit import prepare_live_canary, run_live_canary
from pa_agent.operations.analysis_profile import AnalysisProfile, profile_fingerprint
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from tests.unit.test_frozen_agent_job import _job


def test_codex_schema_projects_uniqueness_without_weakening_host_validation(tmp_path):
    from jsonschema import Draft7Validator

    source = {"type": "object", "properties": {
        "evidence_refs": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "uniqueItems": {"type": "boolean"},
    }, "required": ["evidence_refs", "uniqueItems"], "additionalProperties": False}
    job = replace(_job(), output_schema=source)
    generated = json.loads(CodexCliWorker._materialize_workspace(tmp_path / "workspace", job).read_text("utf-8"))
    assert "uniqueItems" not in generated["properties"]["evidence_refs"]
    assert generated["properties"]["uniqueItems"] == {"type": "boolean"}
    assert source["properties"]["evidence_refs"]["uniqueItems"] is True
    assert not Draft7Validator(source).is_valid({"evidence_refs": ["K1", "K1"], "uniqueItems": True})


def test_materialized_schema_removes_only_codex_unsupported_composition(
    tmp_path: Path,
) -> None:
    source_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "state": {"type": "string", "enum": ["a", "b"]},
            "fixed_bar": {"const": "K1"},
            "protocol": {"type": "string", "const": "pa_draft_v2"},
            "nested": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "value": {"type": "string", "pattern": "^x$"},
                },
                "required": ["value"],
                "allOf": [
                    {
                        "if": {"properties": {"value": {"const": "x"}}},
                        "then": {"required": ["value"]},
                    }
                ],
            },
        },
        "required": ["state", "nested"],
        "allOf": [{"not": {"required": ["unknown"]}}],
        "dependentSchemas": {"state": {"required": ["nested"]}},
    }
    job = replace(_job(), output_schema=source_schema)

    schema_path = CodexCliWorker._materialize_workspace(tmp_path / "workspace", job)
    generated = json.loads(schema_path.read_text("utf-8"))

    unsupported = {
        "allOf",
        "not",
        "dependentRequired",
        "dependentSchemas",
        "if",
        "then",
        "else",
    }

    def schema_keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from schema_keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from schema_keys(child)

    assert unsupported.isdisjoint(schema_keys(generated))
    assert generated["required"] == ["state", "fixed_bar", "protocol", "nested"]
    assert generated["properties"]["state"]["enum"] == ["a", "b"]
    assert generated["properties"]["fixed_bar"] == {
        "anyOf": [
            {"type": "string", "enum": ["K1"]},
            {"type": "null"},
        ]
    }
    assert generated["properties"]["protocol"] == {
        "anyOf": [
            {"type": "string", "enum": ["pa_draft_v2"]},
            {"type": "null"},
        ]
    }
    assert generated["properties"]["nested"]["properties"]["value"]["pattern"] == "^x$"
    assert job.output_schema == source_schema


def test_worker_builds_ephemeral_chatgpt_only_read_only_command(tmp_path: Path) -> None:
    published = FrozenAgentJobStore(tmp_path / "jobs").publish(_job())
    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = kwargs
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"cycle":"trading_range"}', encoding="utf-8")
        stdout = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 120,
                            "cached_input_tokens": 20,
                            "output_tokens": 30,
                            "reasoning_output_tokens": 10,
                        },
                    }
                ),
            ]
        )
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    config = CodexCliConfig(
        codex_binary="codex",
        codex_home=tmp_path / "codex-home",
        runtime_root=tmp_path / "runtime",
        outbox_root=tmp_path / "outbox",
        model="gpt-5.6-terra",
        reasoning_effort="high",
        python_executable=sys.executable,
    )
    previous_openai = os.environ.get("OPENAI_API_KEY")
    previous_codex = os.environ.get("CODEX_API_KEY")
    os.environ["OPENAI_API_KEY"] = "must-not-leak"
    os.environ["CODEX_API_KEY"] = "must-not-leak"
    try:
        result = CodexCliWorker(config, process_runner=fake_run).run(published)
    finally:
        if previous_openai is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = previous_openai
        if previous_codex is None:
            os.environ.pop("CODEX_API_KEY", None)
        else:
            os.environ["CODEX_API_KEY"] = previous_codex

    command = captured["command"]
    assert command[:2] == ["codex", "exec"]
    assert 'approval_policy="never"' in command
    assert "--ephemeral" in command
    assert "--ignore-user-config" not in command
    assert "--ignore-rules" not in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--output-schema" in command
    assert "--json" in command
    assert 'forced_login_method="chatgpt"' in command
    assert "features.shell_tool=false" in command
    for feature in ("apps", "multi_agent", "memories", "hooks", "goals", "remote_plugin"):
        assert f"features.{feature}=false" in command
    assert 'web_search="disabled"' in command
    assert "tools.view_image=false" not in command
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
    assert any("mcp_servers.pa_evidence.required=true" == item for item in command)
    # MCP limits alone leave both outer exec and history truncation active.
    history_limit = next((int(item.split("=", 1)[1]) for item in command
                          if item.startswith("tool_output_token_limit=")), None)
    assert history_limit is not None and history_limit >= 2 * _job().budget.max_evidence_bytes
    prompt = command[command.index("exec") + 1]
    pragma_line = next(line for line in prompt.splitlines() if line.startswith("// @exec: "))
    assert json.loads(pragma_line.removeprefix("// @exec: "))["max_output_tokens"] == history_limit
    from pa_agent.agent_runtime.evidence_mcp import _tools
    for tool in _tools():
        prefix = f"mcp_servers.pa_evidence.tools.{tool['name']}.output_token_limit="
        budget = _job().budget
        bound = (budget.max_evidence_bytes if tool["name"] in {
                     "get_role_baseline", "get_stage1_decision_packet"}
                 else budget.max_evidence_response_bytes)
        assert any(item.startswith(prefix) and int(item.split("=")[1]) >= 2 * bound for item in command)
    assert "OPENAI_API_KEY" not in captured["kwargs"]["env"]
    assert "CODEX_API_KEY" not in captured["kwargs"]["env"]
    assert result.content == '{"cycle":"trading_range"}'
    assert result.thread_id == "thread-1"
    assert result.usage.prompt_tokens == 120
    assert result.usage.cached_prompt_tokens == 20
    assert result.usage.completion_tokens == 30


def test_worker_rejects_output_when_mandatory_semantic_context_was_skipped() -> None:
    job = replace(
        _job(),
        core_context={"context_policy": "semantic_parity_v1"},
        knowledge_manifest={
            "modules": [
                {
                    "knowledge_module_id": "brooks/context",
                    "loading_policy": "mandatory",
                    "load_order": 1,
                }
            ]
        },
    )
    base_events = (
        {"operation": "get_job_manifest", "status": "ok", "request": {}},
        {"operation": "get_market_core", "status": "ok", "request": {}},
        {
            "operation": "get_price_frame",
            "status": "ok",
            "request": {"older_seq": 2, "newer_seq": 1},
        },
    )

    with pytest.raises(CodexWorkerOutputError, match="mandatory"):
        CodexCliWorker._validate_required_evidence_reads(job, base_events)

    CodexCliWorker._validate_required_evidence_reads(
        job,
        (
            *base_events,
            {
                "operation": "read_knowledge_module",
                "status": "ok",
                "request": {"knowledge_id": "brooks/context"},
            },
        ),
    )


def test_live_canary_keeps_workspace_agents_and_role_skill_discoverable(
    tmp_path: Path,
) -> None:
    canary = prepare_live_canary(
        workspace=tmp_path / "canary",
        output_path=tmp_path / "result.json",
        codex_binary="codex",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )

    command = list(canary.command)
    assert command[:2] == ["codex", "exec"]
    assert 'approval_policy="never"' in command
    assert command[command.index("--cd") + 1] == str(canary.workspace)
    assert "--ignore-user-config" not in command
    assert "--ignore-rules" not in command
    assert "--output-schema" in command
    assert "--output-last-message" in command
    assert "tools.view_image=false" not in command
    assert canary.sentinel not in " ".join(command)
    assert "$pa-runtime-audit-canary" in (canary.workspace / "AGENTS.md").read_text("utf-8")
    assert "$pa-runtime-audit-canary" in command[2]
    assert canary.sentinel in (
        canary.workspace
        / ".agents"
        / "skills"
        / "pa-runtime-audit-canary"
        / "SKILL.md"
    ).read_text("utf-8")
    schema = json.loads((canary.workspace / "output_schema.json").read_text("utf-8"))
    assert "const" not in json.dumps(schema)
    assert "enum" not in json.dumps(schema)


def test_live_canary_closes_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    def fake_run(command, **kwargs):
        captured["kwargs"] = kwargs
        output_path = Path(command[command.index("--output-last-message") + 1])
        skill_path = (
            Path(command[command.index("--cd") + 1])
            / ".agents"
            / "skills"
            / "pa-runtime-audit-canary"
            / "SKILL.md"
        )
        sentinel = skill_path.read_text("utf-8").split("exactly `", 1)[1].split("`", 1)[0]
        output_path.write_text(
            json.dumps({"agents_sentinel": sentinel, "skill_observed": True}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    ok, _message = run_live_canary(
        codex_binary="codex",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        process_runner=fake_run,
    )

    assert ok is True
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL


def test_worker_maps_timeout_without_returning_partial_output(tmp_path: Path) -> None:
    published = FrozenAgentJobStore(tmp_path / "jobs").publish(_job())

    def timeout(_command, **_kwargs):
        raise subprocess.TimeoutExpired("codex", timeout=1, output="partial")

    config = CodexCliConfig(
        codex_home=tmp_path / "codex-home",
        runtime_root=tmp_path / "runtime",
        outbox_root=tmp_path / "outbox",
        model="gpt-5.6-terra",
        reasoning_effort="high",
        python_executable=sys.executable,
    )

    worker = CodexCliWorker(config, process_runner=timeout)
    try:
        worker.run(published)
    except Exception as exc:
        assert type(exc).__name__ == "CodexWorkerTimeout"
    else:
        raise AssertionError("timeout must fail closed")

    assert not list((tmp_path / "outbox").glob("*.json"))


def test_worker_maps_process_start_oserror_to_availability(tmp_path: Path) -> None:
    published = FrozenAgentJobStore(tmp_path / "jobs").publish(_job())

    def unavailable(_command, **_kwargs):
        raise PermissionError("not executable")

    worker = CodexCliWorker(
        CodexCliConfig(
            codex_home=tmp_path / "home",
            runtime_root=tmp_path / "runtime",
            outbox_root=tmp_path / "outbox",
            model="gpt-5.6-terra",
            reasoning_effort="high",
        ),
        process_runner=unavailable,
    )

    with pytest.raises(CodexWorkerUnavailable):
        worker.run(published)


def test_worker_leaves_schema_judgment_to_pa_validator(tmp_path: Path) -> None:
    published = FrozenAgentJobStore(tmp_path / "jobs").publish(_job())

    def schema_invalid(command, **_kwargs):
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"wrong":true}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    worker = CodexCliWorker(
        CodexCliConfig(
            codex_home=tmp_path / "home",
            runtime_root=tmp_path / "runtime",
            outbox_root=tmp_path / "outbox",
            model="gpt-5.6-terra",
            reasoning_effort="high",
        ),
        process_runner=schema_invalid,
    )

    result = worker.run(published)

    assert result.content == '{"wrong":true}'


def test_worker_uses_frozen_job_model_and_effort_and_rejects_invalid_pair(
    tmp_path: Path,
) -> None:
    profile = AnalysisProfile(
        codex_model="gpt-5.6-terra",
        codex_reasoning_effort="xhigh",
    )
    identity = {
        "model": profile.codex_model,
        "reasoning_effort": profile.codex_reasoning_effort,
        "profile_fingerprint": profile_fingerprint(profile),
        "runtime_version": "runtime-v1",
    }
    valid_published = FrozenAgentJobStore(tmp_path / "jobs").publish(
        _job(runtime_identity=identity)
    )
    captured: dict[str, list[str]] = {}

    def fake_run(command, **_kwargs):
        captured["command"] = list(command)
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"cycle":"trading_range"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    worker = CodexCliWorker(
        CodexCliConfig(
            codex_home=tmp_path / "home",
            runtime_root=tmp_path / "runtime",
            outbox_root=tmp_path / "outbox",
            model="gpt-5.6-sol",
            reasoning_effort="low",
        ),
        process_runner=fake_run,
    )
    worker.run(valid_published)
    command = captured["command"]
    assert command[command.index("--model") + 1] == "gpt-5.6-terra"
    assert 'model_reasoning_effort="xhigh"' in command

    invalid_profile = AnalysisProfile(
        codex_model="gpt-5.4-mini",
        codex_reasoning_effort="high",
    )
    invalid_identity = {
        **identity,
        "model": invalid_profile.codex_model,
        "reasoning_effort": "max",
        "profile_fingerprint": profile_fingerprint(invalid_profile),
    }
    invalid_published = FrozenAgentJobStore(tmp_path / "jobs").publish(
        _job(runtime_identity=invalid_identity)
    )
    launched = False

    def should_not_run(*_args, **_kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("invalid profile must fail before launch")

    invalid_worker = CodexCliWorker(
        CodexCliConfig(
            codex_home=tmp_path / "home-2",
            runtime_root=tmp_path / "runtime-2",
            outbox_root=tmp_path / "outbox-2",
            model="gpt-5.6-sol",
            reasoning_effort="low",
        ),
        process_runner=should_not_run,
    )
    with pytest.raises(CodexWorkerOutputError, match="model/effort"):
        invalid_worker.run(invalid_published)
    assert launched is False


def test_worker_rejects_unknown_legacy_default_model(tmp_path: Path) -> None:
    worker = CodexCliWorker(
        CodexCliConfig(
            codex_home=tmp_path / "home",
            runtime_root=tmp_path / "runtime",
            outbox_root=tmp_path / "outbox",
            model="gpt-unknown",
            reasoning_effort="high",
        ),
        process_runner=lambda *_args, **_kwargs: None,
    )
    published = FrozenAgentJobStore(tmp_path / "jobs").publish(_job())

    with pytest.raises(CodexWorkerOutputError, match="legacy worker model/effort"):
        worker.run(published)


def test_worker_persists_stage1_workspace_then_uses_codex_resume_for_stage2(
    tmp_path: Path,
) -> None:
    from pa_agent.agent_runtime.frozen_job import AgentJobBudget, FrozenAgentJobBuilder

    builder = FrozenAgentJobBuilder(
        runtime_version="runtime-session-test",
        agents_text="Frozen PA runtime rules.",
        skill_documents={"stage1": "Stage 1 skill.", "stage2": "Stage 2 skill."},
    )
    common = dict(
        task_id="task-session",
        attempt=1,
        snapshot_hash="a" * 64,
        cutoff_ts_open=1_725_000_000_000,
        messages=[{"role": "user", "content": "analyze"}],
        output_schema={"type": "object", "required": ["cycle"]},
        fact_projection={"snapshot_hash": "a" * 64, "facts": [], "relations": []},
        bars=[{"seq": 1, "ts_open": 1_725_000_000_000, "close": 100.0}],
        knowledge_documents={},
        allowed_knowledge_ids=(),
        budget=AgentJobBudget(max_evidence_calls=3, max_evidence_bytes=4096),
    )
    stage1 = builder.build(
        request_id="task-session:stage1:1",
        role="stage1",
        core_context={
            "task_id": "task-session",
            "frozen_snapshot": {"snapshot_hash": "a" * 64, "cutoff_ts_open": 1_725_000_000_000},
        },
        **common,
    )
    jobs = FrozenAgentJobStore(tmp_path / "jobs")
    stage1_path = jobs.publish(stage1)
    stage2 = builder.build(
        request_id="task-session:stage2:1",
        role="stage2",
        core_context={
            "task_id": "task-session",
            "frozen_snapshot": {"snapshot_hash": "a" * 64, "cutoff_ts_open": 1_725_000_000_000},
            "pa_session_continuation": {
                "protocol_version": "pa_session_continuation_v1",
                "parent_job_digest": stage1_path.name,
                "thread_id": "thread-pa-0001",
                "inherited_price_frame": True,
                "inherited_higher_timeframe": True,
                "inherited_knowledge_ids": [],
            },
        },
        **common,
    )
    stage2_path = jobs.publish(stage2)
    calls: list[tuple[list[str], dict]] = []
    bindings: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append((list(command), kwargs))
        mcp_args = json.loads(next(
            item.split("=", 1)[1]
            for item in command
            if item.startswith("mcp_servers.pa_evidence.args=")
        ))
        binding_path = Path(mcp_args[mcp_args.index("--binding-file") + 1])
        bindings.append(json.loads(binding_path.read_text(encoding="utf-8")))
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"cycle":"trading_range"}', encoding="utf-8")
        stdout = json.dumps({"type": "thread.started", "thread_id": "thread-pa-0001"})
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    config = CodexCliConfig(
        codex_home=tmp_path / "home",
        runtime_root=tmp_path / "runtime",
        outbox_root=tmp_path / "outbox",
        model="gpt-5.6-terra",
        reasoning_effort="high",
    )
    worker = CodexCliWorker(config, process_runner=fake_run)
    worker.run(stage1_path, persist_session=True)
    stable_workspace = Path(calls[0][1]["cwd"])
    assert stable_workspace.is_dir()
    worker.run(
        stage2_path,
        resume_thread_id="thread-pa-0001",
        parent_job_digest=stage1_path.name,
    )

    initial_command, resume_command = calls[0][0], calls[1][0]
    assert initial_command[:2] == ["codex", "exec"]
    assert "--ephemeral" not in initial_command
    assert resume_command[:3] == ["codex", "exec", "resume"]
    assert resume_command[3] == "thread-pa-0001"
    assert "--ephemeral" not in resume_command
    assert "--cd" not in resume_command
    assert Path(calls[1][1]["cwd"]) == stable_workspace
    assert bindings[0]["job_digest"] == stage1_path.name
    assert bindings[1]["job_digest"] == stage2_path.name
    assert bindings[0]["job_root"] == bindings[1]["job_root"]
    assert bindings[0]["audit_output"] != bindings[1]["audit_output"]
    assert not stable_workspace.exists()


def test_worker_rejects_resume_thread_not_bound_to_frozen_stage2(
    tmp_path: Path,
) -> None:
    job = replace(
        _job(),
        core_context={
            "pa_session_continuation": {
                "protocol_version": "pa_session_continuation_v1",
                "parent_job_digest": "b" * 64,
                "thread_id": "thread-pa-0001",
            }
        },
    )
    worker = CodexCliWorker(
        CodexCliConfig(
            codex_home=tmp_path / "home",
            runtime_root=tmp_path / "runtime",
            outbox_root=tmp_path / "outbox",
            model="gpt-5.6-terra",
            reasoning_effort="high",
        )
    )

    with pytest.raises(CodexWorkerOutputError, match="continuation"):
        worker._validate_session_options(
            job,
            persist_session=False,
            resume_thread_id="thread-pa-other",
            parent_job_digest="b" * 64,
        )
