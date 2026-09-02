from __future__ import annotations

import json
from pathlib import Path

import pytest

from pa_agent.agent_runtime.frozen_job import (
    AgentJobBudget,
    FrozenAgentJobBuilder,
    FrozenAgentJobStore,
    JobIntegrityError,
)
from pa_agent.operations.analysis_profile import AnalysisProfile, profile_fingerprint


def _builder() -> FrozenAgentJobBuilder:
    return FrozenAgentJobBuilder(
        runtime_version="codex-runtime-test",
        agents_text="PA authority remains outside Codex.",
        skill_documents={"stage1": "Diagnose Brooks context from evidence."},
    )


def _job(
    *,
    role: str = "stage1",
    attempt: int = 1,
    runtime_identity: dict | None = None,
):
    return _builder().build(
        task_id="task-1",
        request_id="request-1",
        role=role,
        attempt=attempt,
        snapshot_hash="a" * 64,
        cutoff_ts_open=1_725_000_000_000,
        messages=[{"role": "user", "content": "diagnose"}],
        output_schema={"type": "object", "required": ["cycle"]},
        fact_projection={
            "catalog_id": "catalog-1",
            "snapshot_hash": "a" * 64,
            "facts": [
                {
                    "fact_id": "PA.BAR.K1",
                    "domain": "price",
                    "bar_range": "K1",
                    "fact_type": "bar_ohlc_geometry",
                    "value": {"seq": 1, "close": 101.0},
                },
                {
                    "fact_id": "VPA.BAR.K1",
                    "domain": "volume",
                    "bar_range": "K1",
                    "fact_type": "volume_observation",
                    "value": {"seq": 1, "volume": 12.0},
                },
            ],
            "relations": [],
        },
        bars=[
            {"seq": 1, "ts_open": 1_725_000_000_000, "close": 101.0},
            {"seq": 2, "ts_open": 1_724_996_400_000, "close": 100.0},
        ],
        knowledge_documents={
            "brooks/context": "Brooks market cycle knowledge.",
            "vpa/effort-result": "VPA effort versus result knowledge.",
        },
        allowed_knowledge_ids=("brooks/context",),
        budget=AgentJobBudget(max_evidence_calls=3, max_evidence_bytes=512),
        backend_runtime_identity=runtime_identity,
    )


def test_builder_is_deterministic_and_binds_all_runtime_inputs() -> None:
    first = _job()
    second = _job()

    assert first.manifest.job_digest == second.manifest.job_digest
    assert first.manifest.resource_digests == second.manifest.resource_digests
    assert first.manifest.role == "stage1"
    assert first.manifest.snapshot_hash == "a" * 64
    assert first.manifest.cutoff_ts_open == 1_725_000_000_000
    assert "vpa/effort-result" not in first.knowledge_documents
    assert set(first.runtime["skills"]) == {"stage1"}


def test_digest_changes_when_schema_knowledge_or_runtime_changes() -> None:
    baseline = _job()
    changed_runtime = FrozenAgentJobBuilder(
        runtime_version="codex-runtime-next",
        agents_text="PA authority remains outside Codex.",
        skill_documents={"stage1": "Diagnose Brooks context from evidence."},
    ).build(
        task_id="task-1",
        request_id="request-1",
        role="stage1",
        attempt=1,
        snapshot_hash="a" * 64,
        cutoff_ts_open=1_725_000_000_000,
        messages=[{"role": "user", "content": "diagnose"}],
        output_schema={"type": "object", "required": ["cycle"]},
        fact_projection=baseline.fact_projection,
        bars=[dict(item) for item in baseline.bars],
        knowledge_documents={"brooks/context": "Brooks market cycle knowledge."},
        allowed_knowledge_ids=("brooks/context",),
        budget=baseline.budget,
    )

    assert changed_runtime.manifest.job_digest != baseline.manifest.job_digest


def test_builder_rejects_bars_after_the_frozen_cutoff() -> None:
    baseline = _job()
    bars = [dict(item) for item in baseline.bars]
    bars[0]["ts_open"] = baseline.manifest.cutoff_ts_open + 1

    with pytest.raises(ValueError, match="cutoff"):
        _builder().build(
            task_id="task-1",
            request_id="request-1",
            role="stage1",
            attempt=1,
            snapshot_hash="a" * 64,
            cutoff_ts_open=baseline.manifest.cutoff_ts_open,
            messages=[{"role": "user", "content": "diagnose"}],
            output_schema=baseline.output_schema,
            fact_projection=baseline.fact_projection,
            bars=bars,
            knowledge_documents={"brooks/context": "Brooks market cycle knowledge."},
            allowed_knowledge_ids=("brooks/context",),
            budget=baseline.budget,
        )


def test_store_publishes_complete_job_and_detects_resource_tampering(
    tmp_path: Path,
) -> None:
    store = FrozenAgentJobStore(tmp_path)
    published = store.publish(_job())

    assert published.name == _job().manifest.job_digest
    loaded = store.open(published.name)
    assert loaded.manifest.job_digest == _job().manifest.job_digest

    schema_path = published / "output_schema.json"
    schema_path.chmod(0o600)
    schema_path.write_text('{"type":"array"}', encoding="utf-8")
    with pytest.raises(JobIntegrityError, match="output_schema.json"):
        store.open(published.name)


def test_store_rejects_unpublished_or_manifest_only_directories(tmp_path: Path) -> None:
    store = FrozenAgentJobStore(tmp_path)
    partial = tmp_path / ("b" * 64)
    partial.mkdir()
    (partial / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(JobIntegrityError):
        store.open(partial.name)


def test_manifest_is_canonical_json_without_absolute_paths(tmp_path: Path) -> None:
    published = FrozenAgentJobStore(tmp_path).publish(_job())
    raw = json.loads((published / "manifest.json").read_text(encoding="utf-8"))

    assert raw["schema_version"] == 1
    assert raw["resource_digests"]["facts.json"]
    assert str(tmp_path) not in json.dumps(raw)


def test_job_manifest_and_runtime_freeze_profile_model_and_effort() -> None:
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
    job = _builder().build(
        task_id="task-1",
        request_id="request-1",
        role="stage1",
        attempt=1,
        snapshot_hash="a" * 64,
        cutoff_ts_open=1_725_000_000_000,
        messages=[{"role": "user", "content": "diagnose"}],
        output_schema={"type": "object"},
        fact_projection={"snapshot_hash": "a" * 64, "facts": [], "relations": []},
        bars=[{"seq": 1, "ts_open": 1_725_000_000_000}],
        knowledge_documents={},
        allowed_knowledge_ids=(),
        budget=AgentJobBudget(),
        backend_runtime_identity=identity,
    )

    assert job.manifest.model == "gpt-5.6-terra"
    assert job.manifest.reasoning_effort == "xhigh"
    assert job.manifest.profile_fingerprint == profile_fingerprint(profile)
    assert job.runtime["model"] == "gpt-5.6-terra"
    assert job.runtime["reasoning_effort"] == "xhigh"
    assert job.runtime["profile_fingerprint"] == profile_fingerprint(profile)
