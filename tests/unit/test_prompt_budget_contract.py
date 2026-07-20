from __future__ import annotations

import tomllib
from pathlib import Path

from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.ai.token_counter import estimate_tokens
from pa_agent.config.settings import PromptSettings
from tests.integration.conftest import (
    VALID_STAGE1,
    VALID_STAGE2,
    make_frame,
)

_MIGRATION_STAGE1_PROMPT_TOKENS = 78_608


def _quality_contract() -> dict:
    root = Path(__file__).resolve().parents[2]
    with (root / "config" / "quality_contract.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_prompt_budgets_match_the_quality_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    frame = make_frame()
    pa = PromptAssembler(root / "prompt_engineering")
    stage1 = pa.build_stage1(frame)
    stage2 = pa.build_stage2_continuation(
        frame=frame,
        stage1_messages=stage1,
        stage1_reply_content="{}",
        stage1_json=VALID_STAGE1,
        strategy_files=[],
        experience_entries=[],
        use_prefix_chain=True,
    )

    vpa = PromptAssembler(
        root / "prompt_engineering",
        prompt_settings=PromptSettings(vpa_mode="shadow"),
    )
    hypotheses = {
        "primary": {"hypothesis_id": "pa-primary"},
        "setup_candidates": [],
    }
    vpa_messages = vpa.build_vpa_evidence_report(
        frame,
        VALID_STAGE1,
        hypotheses,
    )
    joint_messages = vpa.build_joint_decision(
        VALID_STAGE2,
        hypotheses,
        {
            "summary": "Validated independent VPA evidence.",
            "entry_timing_relation": "neutral",
        },
    )

    stage1_tokens = estimate_tokens(stage1)
    stage2_increment_tokens = estimate_tokens([stage2[-1]])
    pa_only_tokens = stage1_tokens + stage2_increment_tokens
    soft_tokens = (
        pa_only_tokens
        + estimate_tokens(vpa_messages)
        + estimate_tokens(joint_messages)
    )
    performance = _quality_contract()["performance"]

    assert stage1_tokens <= int(
        _MIGRATION_STAGE1_PROMPT_TOKENS
        * performance["pa_only_prompt_p95_baseline_ratio"]
    )
    assert soft_tokens <= int(
        pa_only_tokens * performance["soft_total_prompt_p95_pa_only_ratio"]
    )


def test_compact_pa_framework_preserves_required_semantic_domains() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / "prompt_engineering" / "市场诊断框架.txt").read_text(
        encoding="utf-8"
    )

    for state in (
        "spike",
        "micro_channel",
        "tight_channel",
        "normal_channel",
        "broad_channel",
        "trending_tr",
        "trading_range",
        "extreme_tr",
    ):
        assert "### 4." in text and state in text
    for concept in (
        "Background",
        "Always In",
        "Acceptance",
        "Setup 候选",
        "竞争假设",
        "Stage 1 Gate",
        "HTF",
    ):
        assert concept in text


def test_modular_vpa_knowledge_replaces_legacy_monolith() -> None:
    root = Path(__file__).resolve().parents[2]
    knowledge_dir = root / "prompt_engineering" / "wyckoff_vpa"
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(knowledge_dir.glob("*.txt"))
    )

    assert not (root / "prompt_engineering" / "wyckoff_vpa_rules.txt").exists()
    for concept in (
        "Supply and Demand",
        "Cause and Effect",
        "Effort versus Result",
        "Composite Operator",
        "Spring",
        "UTAD",
        "SOS/SOW",
        "LPS/LPSY",
        "Absorption",
        "Exhaustion",
        "No Demand",
        "No Supply",
        "acceptance",
        "tick",
        "proxy",
    ):
        assert concept in combined
    assert "vpa_context" not in combined
    assert "vpa_impact" not in combined
