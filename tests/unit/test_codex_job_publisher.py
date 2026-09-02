from __future__ import annotations

import json
from pathlib import Path

import pytest

from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from pa_agent.agent_runtime.publisher import CodexJobPublisher
from pa_agent.ai.analysis_role_backend import AnalysisRoleRequest
from pa_agent.ai.fact_catalog import build_analysis_fact_catalog
from tests.integration.conftest import make_frame


def _request(role: str, context: dict) -> AnalysisRoleRequest:
    frame = make_frame()
    return AnalysisRoleRequest(
        role=role,
        messages=[{"role": "system", "content": "x" * 200_000}],
        schema={"type": "object"},
        thinking=True,
        reasoning_effort="high",
        snapshot_hash=build_analysis_fact_catalog(frame).snapshot_hash,
        cutoff_ts_open=frame.bars[0].ts_open,
        agent_context=context,
    )


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
                "allowed_knowledge_ids": ["market.txt"],
            },
        ),
        1,
    )
    job = FrozenAgentJobStore(tmp_path / "jobs").open(published.name)
    encoded = json.dumps(job.request["messages"], ensure_ascii=False).encode("utf-8")

    assert len(encoded) < 32_768
    assert "x" * 100 not in encoded.decode("utf-8")
    assert job.knowledge_documents == {"market.txt": "Brooks market cycle knowledge"}
    assert job.fact_projection["snapshot_hash"] == job.manifest.snapshot_hash
    assert max(int(bar["ts_open"]) for bar in job.bars) <= job.manifest.cutoff_ts_open
    assert job.runtime["backend_runtime_identity"] == runtime_identity


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
