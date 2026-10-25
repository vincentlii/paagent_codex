"""Stage 2 selectivity profiles; no profile can weaken order safety."""
from __future__ import annotations

from typing import Literal

DecisionStance = Literal["conservative", "balanced", "aggressive", "extreme_aggressive"]

STANCE_LABELS_ZH: dict[str, str] = {
    "conservative": "保守",
    "balanced": "均衡",
    "aggressive": "激进",
    "extreme_aggressive": "极度激进",
}

_STANCE_ALIASES: dict[str, DecisionStance] = {
    "conservative": "conservative",
    "保守": "conservative",
    "balanced": "balanced",
    "均衡": "balanced",
    "aggressive": "aggressive",
    "激进": "aggressive",
    "extreme_aggressive": "extreme_aggressive",
    "extreme": "extreme_aggressive",
    "极度激进": "extreme_aggressive",
}


def normalize_stance(value: str | None) -> DecisionStance:
    if not value:
        return "conservative"
    text = str(value).strip()
    return _STANCE_ALIASES.get(text.lower(), _STANCE_ALIASES.get(text, "conservative"))


def stance_label_zh(stance: str | None) -> str:
    return STANCE_LABELS_ZH[normalize_stance(stance)]


def build_decision_stance_guidance(stance: str | None) -> str:
    """Render selectivity guidance while keeping one invariant safety contract."""
    normalized = normalize_stance(stance)
    label = stance_label_zh(normalized)
    profiles = {
        "conservative": (
            "只执行背景、位置、信号质量和跟随证据均较清晰的 Setup；"
            "歧义较大时说明能改变判断的具体条件，避免机械滚动等待下一根。"
        ),
        "balanced": (
            "允许执行证据完整但不完美的 Setup；无独立信号棒时仍须评估有明确结构锚点的"
            "计划型限价，不因单一弱点机械放弃。"
        ),
        "aggressive": (
            "允许在背景和位置有利时采用更早触发，但必须明确失效结构、当前尚缺的确认以及"
            "为何提前进入仍优于等待；不得把候选形态写成已确认。"
        ),
        "extreme_aggressive": (
            "选择最早仍有证据支撑的可执行触发；若方向、结构失效位或目标任一不清晰，仍须"
            "不下单。此档不等于强制交易，也不允许在多空间无证据二选一。"
        ),
    }
    common = (
        "所有档位共享以下硬约束：\n"
        "- PA 背景、位置、Setup 与时机由你综合判断；程序只复算价格和交易者方程。\n"
        "- 先按结构确定 entry 和 stop，再按本轮退出协议评估目标、路径障碍及交易价值；"
        "不得为通过方程移动结构位或上调主观胜率。\n"
        "- 交易者方程不通过、订单字段不合法或触犯 §14 时，必须不下单；RR 本身不另设门槛。\n"
        "- 不得用档位预设固定胜率、固定置信度或固定交易频率；评分必须来自当前证据。\n"
        "- 无信号棒不等于自动等待：通道/区间边界或顺势回撤若有明确锚点，可走 §9.0P；"
        "中部位置、失效位不清或目标不清时等待。资格确实依赖未来新增闭盘证据时仍等待；"
        "现有证据已支持合格挂单时，不要求入场后确认先出现。\n"
        "- 上一方案是历史意见；根据冻结的后续 OHLC、当前结构和 Setup 复核条件，"
        "只决定本轮是否沿用、放弃或另拟价格，不取消或修改已有挂单/持仓；触价不等于成交。\n"
        "- decision、decision_trace 与 terminal 必须一致；程序发现矛盾会拒绝，不会静默改单。"
    )
    return (
        f"## 交易倾向（当前：{label} / {normalized}）\n\n"
        f"{common}\n\n"
        f"本档选择性：{profiles[normalized]}\n"
        "在 decision.reasoning 中说明本档只影响了入场选择性或确认要求，不得声称它改变数学底线。"
    )
