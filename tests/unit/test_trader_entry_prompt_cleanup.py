"""Trading guidance removes contradictions without expanding execution authority."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from pa_agent.agent_runtime.knowledge_bundle import build_knowledge_bundle
from pa_agent.ai.decision_stance import build_decision_stance_guidance
from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.config.paths import PROMPT_DIR
from tests.integration.conftest import make_frame


def test_structure_stop_rule_reaches_direct_and_frozen_stage_knowledge():
    module_id = "二元决策.txt"
    direct = PromptAssembler(PROMPT_DIR).build_stage1(make_frame())
    modules, _ = build_knowledge_bundle(
        prompt_dir=PROMPT_DIR, knowledge_ids=[module_id], role="stage1",
    )
    delivered = ["\n".join(message["content"] for message in direct),
                 "".join(section["text"] for section in modules[module_id]["sections"])]
    for text in delivered:
        assert not re.search(r"止损：信号棒极点外\s*1\s*到\s*2\s*跳动", text)  # noqa: RUF001
        assert "当前 Setup 的结构失效位置" in text


def test_plan_pricing_resolves_structure_before_return_and_keeps_future_trigger():
    text = (PROMPT_DIR / "二元决策.txt").read_text(encoding="utf-8")
    pricing = text.split("**§10 定价顺序", 1)[1].split("高质量信号棒", 1)[0]
    assert pricing.index("stop_loss_price") < pricing.index("take_profit_price")
    # Readiness is not future follow-through; this remains the existing passive order path.
    assert "未来**回撤/反弹" in text
    assert "不要求独立信号棒" in text
    assert "不取消或修改已存在的挂单/持仓" in text


@pytest.mark.parametrize("stance", ["conservative", "balanced", "aggressive", "extreme_aggressive"])
def test_selectivity_never_manages_existing_orders_or_demands_another_bar(stance):
    text = build_decision_stance_guidance(stance)
    assert "取消或重定价" not in text
    assert "不取消或修改已有挂单/持仓" in text
    assert "优先等待下一根已收盘" not in text
    assert "未来新增闭盘证据" in text
    assert "不得用档位预设固定胜率" in text


def test_bear_channel_teaches_entry_tradeoffs_and_preserves_microchannel_boundary():
    modules, _ = build_knowledge_bundle(prompt_dir=PROMPT_DIR,
        knowledge_ids=["下跌通道交易策略.txt"], role="stage2")
    text = "".join(s["text"] for s in modules["下跌通道交易策略.txt"]["sections"])
    assert "限价单效果优于止损单" not in text
    assert "优先限价单" not in text
    assert "强反向运动" in text
    assert "微型通道形成过程中不入场" in text
    assert "信号棒止损法是最低标准" not in text


def test_joint_skill_reuses_fields_without_forcing_vpa_confirmation_or_new_order_types():
    path = Path(__file__).resolve().parents[2] / "pa_agent/agent_runtime/assets/skills/joint_stage2/SKILL.md"
    text = path.read_text(encoding="utf-8")
    for field in ("entry_reachability", "target_barriers", "invalidation", "selection_vs_wait"):
        assert field in text
    assert "正常回测" in text and "强反向运动" in text
    assert "期限内触达" in text and "成交后TP1先于SL" in text
    assert "无相关证据不强求影响" in text
    assert "未来新增闭盘证据" in text
    assert "不授予新订单类型或旧单管理权限" in text


@pytest.mark.parametrize("joint_stage2", [False, True])
def test_complete_delivered_pa_library_has_no_structure_stop_conflicts(joint_stage2):
    from pa_agent.ai.prompt_assembler import stage1_prompt_txt_files, stage2_prompt_txt_files

    knowledge_ids = list(dict.fromkeys([
        *stage1_prompt_txt_files(), *stage2_prompt_txt_files(load_full_strategy_library=True),
    ]))
    modules, _ = build_knowledge_bundle(
        prompt_dir=PROMPT_DIR, knowledge_ids=knowledge_ids, role="stage2", joint_stage2=joint_stage2,
    )
    prohibited = (
        "信号棒过长时的紧缩止损", "标准图表止损过远时，在信号棒极点外设较紧止损",  # noqa: RUF001
        "可能设在非关键结构位", "先定结构 TP1/TP2，再定 stop",  # noqa: RUF001
        "信号棒止损法是最低标准", "止损：OB的另一侧1跳动",  # noqa: RUF001
        "§10.2 应判「是」→ 等待更小信号棒", "盘中触价即可执行的保护性止损",
        "入场后设置止损和目标", "入场并设置止损",
        "至少留出1-2个跳动点的缓冲空间", "**禁止**边界外仅 1–2 跳的止损",  # noqa: RUF001
    )
    conflicts = {
        name: [rule for rule in prohibited if rule in "".join(s["text"] for s in module["sections"])]
        for name, module in modules.items()
    }
    assert not {name: rules for name, rules in conflicts.items() if rules}


def test_delivered_stop_buffer_examples_keep_structure_and_verified_tick_scope():
    modules, _ = build_knowledge_bundle(prompt_dir=PROMPT_DIR,
        knowledge_ids=["极速上涨交易策略.txt", "极速下跌交易策略.txt", "文件21-铁丝网与无交易环境.txt"],
        role="stage2")
    for module in modules.values():
        text = "".join(section["text"] for section in module["sections"])
        assert "1–2 tick" in text  # noqa: RUF001
        assert "Host" in text
        assert "不是通用下限" in text or "不把跳数设为通用止损下限" in text
    for name in ("极速上涨交易策略.txt", "极速下跌交易策略.txt"):
        text = "".join(section["text"] for section in modules[name]["sections"])
        assert "已确定结构失效锚点且 Host 提供真实 tick" in text
        assert "另一个独立成立的 Setup" in text
