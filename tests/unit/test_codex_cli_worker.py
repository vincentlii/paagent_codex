from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from pa_agent.agent_runtime.codex_worker import CodexCliConfig, CodexCliWorker
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from tests.unit.test_frozen_agent_job import _job


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
    assert command[:4] == ["codex", "--ask-for-approval", "never", "exec"]
    assert "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--output-schema" in command
    assert "--json" in command
    assert 'forced_login_method="chatgpt"' in command
    assert "features.shell_tool=false" in command
    assert 'web_search="disabled"' in command
    assert any("mcp_servers.pa_evidence.required=true" == item for item in command)
    assert "OPENAI_API_KEY" not in captured["kwargs"]["env"]
    assert "CODEX_API_KEY" not in captured["kwargs"]["env"]
    assert result.content == '{"cycle":"trading_range"}'
    assert result.thread_id == "thread-1"
    assert result.usage.prompt_tokens == 120
    assert result.usage.cached_prompt_tokens == 20
    assert result.usage.completion_tokens == 30


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
