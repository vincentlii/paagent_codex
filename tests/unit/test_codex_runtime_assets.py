from __future__ import annotations

from pa_agent.agent_runtime.runtime_assets import load_codex_runtime_assets


def test_runtime_assets_are_complete_and_deterministically_versioned() -> None:
    first = load_codex_runtime_assets()
    second = load_codex_runtime_assets()

    assert first.runtime_version == second.runtime_version
    assert first.runtime_version.startswith("codex-runtime-v1-")
    assert set(first.skill_documents) == {
        "stage1",
        "stage2",
        "vpa_evidence",
        "joint_arbiter",
    }
    assert "禁止 Web" in first.agents_text
    assert "Brooks Price Action 是主框架" in first.agents_text
    assert all(text.startswith("---\n") for text in first.skill_documents.values())
