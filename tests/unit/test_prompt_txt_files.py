"""Tests for stage prompt .txt file list helpers."""
from __future__ import annotations

from pathlib import Path

from pa_agent.ai.prompt_assembler import (
    COMMON_SYSTEM_STAGE1_TXT_FILES,
    COMMON_SYSTEM_STAGE2_TXT_FILES,
    STAGE1_TASK_PROMPT_TXT_FILES,
    STAGE2_BASE_PROMPT_TXT_FILES,
    STAGE2_FULL_STRATEGY_PROMPT_TXT_FILES,
    stage1_prompt_txt_files,
    stage2_prompt_txt_files,
    stage2_user_task_txt_files,
)
from pa_agent.ai.pa_theory import PA_THEORY_RULES
from pa_agent.config.paths import PROMPT_DIR


def test_stage1_txt_files() -> None:
    files = stage1_prompt_txt_files()
    assert files == [
        *COMMON_SYSTEM_STAGE1_TXT_FILES,
        "二元决策.txt",
        *STAGE1_TASK_PROMPT_TXT_FILES,
    ]
    # The UI exposes the source file; assembly slices it to Stage 1 sections.
    assert "二元决策.txt" in files
    assert "二元决策_闸门.txt" not in files
    assert "文件13-窄通道与宽通道策略.txt" not in files


def test_stage2_routed_only_bullish() -> None:
    routed = ["震荡区间交易策略.txt", "上涨通道分析识别.txt"]
    files = stage2_user_task_txt_files(routed, direction="bullish")
    assert "上涨通道分析识别.txt" in files
    assert "下跌通道分析识别.txt" not in files
    assert "下跌通道交易策略.txt" not in files
    assert "文件17-止损和止盈与仓位管理.txt" in files
    for name in STAGE2_FULL_STRATEGY_PROMPT_TXT_FILES:
        if name.startswith("下跌") or name.startswith("极速下跌"):
            assert name not in files


def test_stage2_full_library_flag() -> None:
    routed = ["震荡区间交易策略.txt"]
    files = stage2_user_task_txt_files(
        routed,
        direction="bullish",
        load_full_strategy_library=True,
    )
    for name in STAGE2_FULL_STRATEGY_PROMPT_TXT_FILES:
        assert name in files


def test_stage2_txt_files_order() -> None:
    routed = ["震荡区间交易策略.txt", "震荡区间分析识别.txt"]
    files = stage2_prompt_txt_files(routed, direction="neutral")
    expected_user = stage2_user_task_txt_files(routed, direction="neutral")
    assert files == [
        *COMMON_SYSTEM_STAGE2_TXT_FILES,
        "二元决策.txt",
        *expected_user,
    ]
    assert files[: len(COMMON_SYSTEM_STAGE2_TXT_FILES)] == list(
        COMMON_SYSTEM_STAGE2_TXT_FILES
    )
    assert files[-4:] == list(STAGE2_BASE_PROMPT_TXT_FILES)
    assert routed[0] in files


def test_vpa_knowledge_modules_do_not_encode_joint_actions() -> None:
    forbidden = {
        "joint",
        "decision_effect",
        "decision_patch",
        "keep_brooks_baseline",
        "require_confirmation",
        "prefer_retest",
        "lower_confidence",
        "reject_current_attempt",
    }
    knowledge_dir = Path(PROMPT_DIR) / "wyckoff_vpa"

    for path in knowledge_dir.glob("*.txt"):
        content = path.read_text(encoding="utf-8").casefold()
        assert not (forbidden & set(content.replace("`", "").split())), path.name
        assert all(token not in content for token in forbidden), path.name


def test_pa_theory_separates_structural_risk_and_uncalibrated_probability() -> None:
    assert "BROOKS.RISK.STRUCTURAL_INVALIDATION" in PA_THEORY_RULES
    assert "BROOKS.PROBABILITY.CONTEXTUAL_PRIOR" in PA_THEORY_RULES


def test_stage2_knowledge_does_not_restore_false_precision_or_mandatory_tp2() -> None:
    content = "\n".join(
        (Path(PROMPT_DIR) / name).read_text(encoding="utf-8")
        for name in stage2_user_task_txt_files(
            [],
            direction="neutral",
            load_full_strategy_library=True,
        )
    )

    assert "有下单时 TP1 与 TP2 均必填" not in content
    assert "estimated_win_rate` 自述以此为准" not in content
    assert "止损 = 信号棒极点外1跳" not in content
    assert "TP2 仅在存在独立远端结构依据时填写" in content
