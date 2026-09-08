"""Knowledge delivery invariants, not a test of trading profitability."""
from dataclasses import replace
from pathlib import Path

import pytest

from pa_agent.agent_runtime.evidence import EvidenceAccessDenied, FrozenEvidenceGateway
from pa_agent.agent_runtime.knowledge_bundle import _split_sections, build_knowledge_bundle
from pa_agent.ai.prompt_assembler import select_agent_knowledge_text
from pa_agent.ai.vpa_context_profile import build_vpa_knowledge_context
from tests.unit.test_frozen_agent_job import _job

ROOT = Path(__file__).resolve().parents[2] / "prompt_engineering"


def test_sections_preserve_headings_whitespace_and_fenced_examples():
    source = "preface\n# Empty\n# Real\n<!-- knowledge: real -->\n```md\n# Example\n<!-- knowledge: fake -->\n```\n  case  \n"
    sections = _split_sections("pa", source)
    assert "".join(item["text"] for item in sections) == source
    assert len(sections) == 3
    assert sections[-1]["section_id"] == "pa#real"


def test_explicit_ids_survive_reorder_rename_and_content_revision():
    first = "# Old\n<!-- knowledge: one -->\ncase\n# Other\n<!-- knowledge: two -->\nother\n"
    next_text = "# Other\n<!-- knowledge: two -->\nother\n# Renamed\n<!-- knowledge: one -->\nrevised\n"
    before = {s["section_id"]: s for s in _split_sections("pa", first)}
    after = {s["section_id"]: s for s in _split_sections("pa", next_text)}
    assert set(before) == set(after) == {"pa#one", "pa#two"}
    assert before["pa#one"]["sha256"] != after["pa#one"]["sha256"]
    assert before["pa#two"]["sha256"] == after["pa#two"]["sha256"]


def test_duplicate_explicit_ids_are_rejected():
    with pytest.raises(ValueError, match="duplicate knowledge section"):
        _split_sections("pa", "# A\n<!-- knowledge: same -->\nx\n# B\n<!-- knowledge: same -->\ny\n")


def test_legacy_custom_pack_repeated_headings_remain_readable():
    source = "# Example\nsame\n# Example\nsame\n# Example\nsame\n"
    sections = _split_sections("custom", source)
    assert len({s["section_id"] for s in sections}) == 3
    assert "".join(s["text"] for s in sections) == source


@pytest.mark.parametrize("role", ["stage1", "stage2", "vpa"])
def test_actual_knowledge_is_delivered_without_section_loss(role):
    paths = sorted(ROOT.glob("wyckoff_vpa/*.txt") if role == "vpa" else ROOT.glob("*.txt"))
    ids = [p.relative_to(ROOT).as_posix() for p in paths]
    modules, manifest = build_knowledge_bundle(prompt_dir=ROOT, knowledge_ids=ids, role=role)
    assert len(modules) == len(paths)
    for path, module_id, entry in zip(paths, ids, manifest["modules"], strict=True):
        selected = select_agent_knowledge_text(role=role, module_id=module_id, text=path.read_text(encoding="utf-8"))
        sections = modules[module_id]["sections"]
        assert "".join(s["text"] for s in sections) == selected, module_id
        assert len({s["section_id"] for s in sections}) == len(sections)
        assert entry["loading_policy"] == "mandatory"
        assert entry["content_bytes"] == len(selected.encode())


def test_alias_search_is_frozen_and_does_not_cross_authorized_modules():
    sections = _split_sections("brooks/context", "<!-- knowledge: intro; aliases: 测量移动 | Measured Move -->\nH10 counterexample\n")
    job = replace(_job(), knowledge_documents={"brooks/context": {"sections": sections}})
    gateway = FrozenEvidenceGateway(job)
    found = gateway.search_allowed_knowledge(query="测量移动")
    assert found[0]["section_id"] == "brooks/context#intro"
    assert gateway.search_allowed_knowledge(query="H1") == []
    assert gateway.search_allowed_knowledge(query="测量移动", knowledge_id="vpa/private") == []
    with pytest.raises(EvidenceAccessDenied):
        FrozenEvidenceGateway(job).read_knowledge_module(knowledge_id="vpa/private")


@pytest.mark.parametrize("context,expected", [
    ({"cycle_position": "spike"}, ("core", "trend_pullback", "breakout_retest")),
    ({"cycle_position": "trading_range"}, ("core", "breakout_retest", "trading_range_events")),
    ({"detected_patterns": ["wedge"]}, ("core", "climax_absorption_exhaustion")),
    ({"reason": "spike wedge breakout", "volume": 100000}, ("core",)),
])
def test_vpa_references_follow_explicit_pa_context_not_volume_or_prose(context, expected):
    assert build_vpa_knowledge_context(context).module_names == expected
