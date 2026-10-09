from __future__ import annotations

import json
from pathlib import Path

from pa_agent.agent_runtime.decision_packet import (
    STAGE1_PACKET_VERSION,
    build_stage1_decision_packet,
    verify_stage1_decision_packet,
)
from pa_agent.ai.fact_catalog import build_analysis_fact_catalog
from pa_agent.data.snapshot_bundle import serialize_frame_bars
from tests.integration.conftest import make_frame
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from pa_agent.agent_runtime.publisher import CodexJobPublisher
from pa_agent.ai.prompt_assembler import stage1_prompt_txt_files
from tests.unit.test_codex_job_publisher import _request


def test_stage1_packet_is_complete_price_only_and_deterministic() -> None:
    frame = make_frame()
    catalog = build_analysis_fact_catalog(frame).view("pa")
    bars = serialize_frame_bars(frame)
    knowledge = {"市场诊断框架.txt": {"sections": [{
        "section_id": "市场诊断框架.txt#core", "title": "core",
        "sha256": "a" * 64, "aliases": [], "text": "exact core",
    }]}}
    manifest = {"modules": [{"knowledge_module_id": "市场诊断框架.txt"}]}
    kwargs = dict(snapshot_hash=catalog["snapshot_hash"], cutoff_ts_open=frame.bars[0].ts_open,
                  bars=bars, facts=catalog["facts"], core_context={
                      "role_protocol": {"contract_version": "pa_stage1_agent_protocol_v1",
                                        "embedded_rules": ["large duplicate"]},
                      "higher_timeframe": {"evidence": [{"evidence_id": "HTF.H1"}]},
                  }, knowledge_documents=knowledge, knowledge_manifest=manifest)

    first = build_stage1_decision_packet(**kwargs)
    second = build_stage1_decision_packet(**kwargs)

    assert first == second
    assert first["packet_version"] == STAGE1_PACKET_VERSION
    assert len(first["price_frame"]["rows"]) == len(bars)
    assert first["price_frame"]["columns"] == ["seq", "ts_open", "open", "high", "low", "close", "closed"]
    assert first["role_contract"]["embedded_rules"] == ["large duplicate"]
    assert first["higher_timeframe"]["evidence"] == [{"evidence_id": "HTF.H1"}]
    assert first["knowledge"]["foundation_exact_sections"][0]["text"] == "exact core"
    assert first["knowledge"]["foundation_exact_sections"][0]["aliases"] == []
    assert first["knowledge"]["section_index"] == []
    assert "PA.WINDOW.K5" in {item["fact_id"] for item in first["pa_fact_spine"]}
    measurements = {}
    for table in first["bar_measurements"]:
        for row in table["rows"]:
            values = dict(zip(table["columns"], row, strict=True))
            fact_id = values.pop("fact_id")
            measurements[fact_id] = {
                "fact_id": fact_id, "bar_range": values.pop("bar_range"),
                "domain": table["domain"], "fact_type": table["fact_type"], "value": values,
            }
    for fact in catalog["facts"]:
        if fact["fact_id"].startswith(("PA.BAR.", "PA.INDICATOR.")):
            assert measurements[fact["fact_id"]] == fact
    assert len(json.dumps(first["bar_measurements"])) < len(json.dumps(list(measurements.values())))
    verify_stage1_decision_packet(first)


def test_real_stage1_knowledge_packet_fits_frozen_single_response_budget(tmp_path) -> None:
    prompt_dir = Path(__file__).resolve().parents[2] / "prompt_engineering"
    publisher = CodexJobPublisher(frame=make_frame(), prompt_dir=prompt_dir,
                                  job_root=tmp_path / "jobs")
    path = publisher(_request("stage1", {
        "task_id": "real-packet-budget",
        "allowed_knowledge_ids": stage1_prompt_txt_files(),
    }), 1)
    job = FrozenAgentJobStore(path.parent).open(path.name)
    packet = job.core_context["stage1_decision_packet"]
    encoded = json.dumps({"decision_packet": packet}, ensure_ascii=False,
                         separators=(",", ":"), sort_keys=True).encode("utf-8")
    assert packet["knowledge"]["foundation_exact_sections"]
    delivered = {
        (item["knowledge_module_id"], item["section_id"]): item["text"]
        for item in packet["knowledge"]["foundation_exact_sections"]
    }
    expected = {
        (module["knowledge_module_id"], section["section_id"]): section["text"]
        for module in job.knowledge_manifest["modules"]
        if module["loading_policy"] == "mandatory"
        for section in job.knowledge_documents[module["knowledge_module_id"]]["sections"]
    }
    assert delivered == expected
    assert len(encoded) <= job.budget.max_evidence_response_bytes
    assert job.budget.max_evidence_calls == 3
