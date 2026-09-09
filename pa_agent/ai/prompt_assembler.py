"""Prompt assembler for Stage 1 (diagnosis) and Stage 2 (decision)."""
from __future__ import annotations

import copy
import functools
import json
import logging
import math
import re
from pathlib import Path
from typing import Any

from pa_agent.ai.decision_stance import build_decision_stance_guidance, normalize_stance
from pa_agent.ai.fact_catalog import (
    AnalysisFactCatalog,
    analysis_snapshot_hash,
    build_analysis_fact_catalog,
    render_pa_fact_catalog,
)
from pa_agent.ai.higher_timeframe import render_higher_timeframe_context
from pa_agent.ai.kline_features import bar_candle_direction_label, compute_kline_geometry_features
from pa_agent.ai.market_features import (
    inject_market_features_section,
)
from pa_agent.ai.pa_theory import render_pa_theory_rule_catalog
from pa_agent.ai.pattern_routing import (
    STAGE1_DETECTED_PATTERNS_GUIDE,
    STAGE1_PATTERN_BRIEFS_BLOCK,
)
from pa_agent.ai.vpa_context_profile import build_vpa_knowledge_context
from pa_agent.ai.vpa_measurements import (
    build_vpa_evidence_catalog,
    net_close_progress_direction_relation,
    pa_window_fact_refs,
    render_vpa_measurements,
)
from pa_agent.ai.vpa_sanitizer import (
    strip_vpa_fields,
    strip_vpa_prompt_sections,
    wrap_vpa_prompt_block,
)
from pa_agent.config.vpa import vpa_is_enabled
from pa_agent.data.base import KlineFrame
from pa_agent.data.datetime_ts import format_epoch_for_display
from pa_agent.records.schema import AnalysisRecord

logger = logging.getLogger(__name__)


class VpaPromptBuildError(RuntimeError):
    """Typed local failure raised before any VPA provider call is attempted."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

VPA_KNOWLEDGE_MODULES = {
    "core": "wyckoff_vpa/core.txt",
    "trend_pullback": "wyckoff_vpa/trend_pullback.txt",
    "breakout_retest": "wyckoff_vpa/breakout_retest.txt",
    "trading_range_events": "wyckoff_vpa/trading_range_events.txt",
    "climax_absorption_exhaustion": "wyckoff_vpa/climax_absorption_exhaustion.txt",
}

_VPA_EVIDENCE_ANALYST_SYSTEM = """
你是 PA Agent 的独立 Wyckoff VPA 证据分析员。

职责边界：
- PA 已经定义市场框架、方向、Setup 与候选；你不得重做或修改这些结论。
- 你只解释程序提供的量价事实，并分别评估它们对每个 PA 假设、整体背景和当前入场时机的支持、挑战或信息不足。
- Evidence first, label last。先讨论 Background、Location、Effort versus Result、Leg/Wave Comparison、Acceptance/Follow-through，再谨慎使用 Wyckoff 事件标签。
- 单根 K 线只能形成候选解释；没有可比背景、位置和后续已收盘 K 线时，不得把 Spring、UTAD、Absorption、Exhaustion、No Demand 或 No Supply 写成已确认事件。
- OHLCV 不能识别参与者身份、主动买卖方向或机构意图。tick/proxy/unknown volume 只能支持同源相对活动比较。
- 同一 evidence 的 causal rolling baseline 与 seasonality comparator 都可用但方向相反时，必须同时披露两者，再由你解释背景权重；不得只选择有利于某一结论的 comparator。
- 只引用 VPA_PROGRAM_EVIDENCE_CATALOG 中存在的 evidence ID；不得复写或心算 OHLC、Volume、RVOL、ATR、百分比或价格。
- 下一步确认只能等待下一根已收盘 K 线，不得引用 K0、形成中或未收盘 K 线。
- 只输出一个 JSON object，不输出 Markdown、PA 字段、交易方向、订单、胜率或置信度。
""".strip()

_JOINT_ARBITER_SYSTEM = """
你是 PA Agent 的联合决策裁决员。

PA defines. VPA informs. You arbitrate the current Entry Attempt.

职责边界：
- PA 假设、方向、Gate、Brooks Setup 和候选集合已经冻结；只能选择、延后或拒绝现有 PA candidate，不得创造新 Setup、逆转方向或绕过 Gate。
- VPA 是独立证据维度，不是机械 Gate。你要判断其信息增量，而不是复述 PA 或 VPA。
- `estimated_win_rate` 与 `diagnosis_confidence` 已冻结，禁止重估。VPA 只可影响当前时机、`trade_confidence`、`watch_points`、`risk_assessment`、触发条件或拒绝当前尝试。
- `neutral` / `insufficient_data` 必须保持 PA baseline。PA baseline 为不下单时，VPA 不得创造订单。
- 只输出允许的 `decision_patch`，不得复制价格、订单方向、止损、目标、PA trace 或预测。程序会把补丁合并到已校验 baseline，并再次执行 Stage 2 Schema、交易者方程与安全校验。
- 只输出一个 JSON object，不输出 Markdown 或额外说明。
""".strip()

_PA_FACTUAL_CLAIM_RULE = """
## PA 事实表述约束

- “突破前高/前低”必须指向 K2 及更早的可识别结构位，并引用对应 K 线或程序 prior-window 比较字段；K1 自己的 high/low 不能被称为“此前高/前低”。程序只提供数值比较，不替你确认突破、收回或回测。
- 单根 K 线内部位置只引用 `PA.BAR.K#`，不复制 `canonical_value` 的 key=value：程序事实为 `upper_quartile` 时可解释为“收盘靠近本棒高点”，`lower_quartile` 时可解释为“收盘靠近本棒低点”，`middle_half` 时解释为“收盘居于本棒中部”；这不表示该棒创出窗口新高/新低。
- 最近逐棒相邻比较优先引用 `PA.PAIR.K1.K2` 至 `PA.PAIR.K5.K6`；字段名始终表达 newer bar 相对 older bar，例如 `newer_low_vs_older_low` 只比较两棒低点。JSON reason 只引用 fact ID，不复制 canonical_value 的 key=value。K1 相对前窗的高低比较引用 `PA.COMPARE.K1.PRIOR5/10/20`。不要手写价位。跨窗口的`新高/新低/窗口最高/窗口最低`必须同时有具体 `K#-K#` 比较，或一个真实且已列入 `pa_evidence.fact_refs` 的 `PA.PAIR` / `PA.WINDOW` / `PA.COMPARE` 引用。
- 阳线/阴线根数与连续段由 `PA.WINDOW` 保存。`gate_trace` 与摘要只引用对应 fact ID 后做“空头占优/延续性较强”等定性解释，不复写阳线/阴线根数，也不自行重算。
- 任何“反弹/回撤超过 N 根”等数量结论必须引用明确 `K#-K#`；无法给出范围时改写为定性描述。
- `close-EMA20` 是价格距 EMA 的距离，不是 EMA 斜率。EMA 上行/下行幅度只能引用程序给出的“EMA20 自身变化”。
- 高周期数据可用时，`htf_context` 至少引用一个 `HTF.H#`；禁止用 Brooks `H1/H2` 或当前周期 `K#` 冒充 HTF 证据。只解释高周期背景关系，不复写 HTF K 线 OHLC、EMA、ATR 或价位数字。
- 若程序未提供已验证 tick size，“极点外 1 跳”只能作为理论原则，并须明确写“真实 tick size 未确认，执行前校验”；不得把快照小数精度冒充真实 tick。
- 价格事实与解释分开：先引用 K 线编号或程序 fact ID，再写 breakout、follow-through、failed attempt 等解释。逐棒摘要和 gate_trace reason 不得手工复写 OHLC、Volume、RVOL、ATR、比率、阳阴棒数量或 canonical_value 的 key=value；精确事实只保留在程序事实表或专用结构化价格字段中。没有后续已收盘 K 线时，写“待确认”，不要把候选写成已确认。
""".strip()

_PA_EVIDENCE_OUTPUT_RULE = """
## PA_EVIDENCE 结构化理论契约

- `pa_evidence.fact_refs` 只能引用 ANALYSIS_FACT_CATALOG 中存在的 `PA.*` ID；确定性计数、连续段、极值与比较不得自行重算。
- `theory_rule_ids` 只能引用 PA_THEORY_RULE_CATALOG 中存在且本轮实际使用的 Brooks 规则，不得自造或为了凑字段罗列无关规则。
- 非 `spike` 周期时 `spike_analysis=null` 且 `spike_stage=null`，不得残留上一轮 Spike 状态。
- `spike` 周期必须严格填写 `spike_analysis={"anchor_bar":"K#","post_anchor_state":"no_closed_bar_after_anchor|continuation_only|pause_or_pullback_present","first_pause_or_pullback_bar":"K#|null"}`。字段名不得缩写或改名；`anchor_bar` 是本轮识别的 Spike 锚点。
- 一旦识别 `pause_or_pullback_present`，必须给出第一根对应的已收盘 K 线，`spike_stage` 不得仍为 `active`，`market_phase` 必须为 `transitioning`；`pause_or_pullback_present` 时，`transition_risk` 必须从 `high|medium|low` 中选择，并由你根据已收盘价格行为判断，程序不会代填。这个状态组表达 Brooks 的 Spike 向通道/区间过渡语义，不是程序替代市场判断。
- `market_phase=stable` 时 `transition_risk=null`；风险等级只描述模型已经识别出的转换期，不得反向作为隐藏 Gate。
""".strip()

_KLINE_INDICATOR_NOTE = (
    "说明：下表仅含最近 N 根已收盘 K 线；几何特征亦基于此 N 根。"
    "EMA20/ATR14 由程序在更老缓冲 K 线上预热后重算，与外盘图表「全历史延续」"
    "指标可能略有差异，勿逐点对比。"
)

# ── Language (both stages, thinking + final output) ───────────────────────────

_LANGUAGE_ZH_RULE = """
## 语言要求（阶段一、阶段二均必须遵守）

- **思考过程**：扩展思考、内部推理、以及写入 JSON 的 `reason`、`diagnosis_confidence_reasoning`、`trade_confidence_reasoning`、`estimated_win_rate_reasoning` 等说明，**全程使用简体中文**。禁止用英文写推理段落或中英混杂的长句（常见缩写如 HH、HL、Spike、TR 可保留）。
- **最终输出**：阶段一诊断 JSON、阶段二决策 JSON 中所有面向用户的字符串（含 `reasoning`、`key_factors`、`risk_assessment`、`watch_points`、`gate_trace`/`decision_trace` 的 `question` 与 `reason` 等）**一律使用简体中文**。
- **仅允许英文或固定英文枚举**：JSON 字段名（schema 键名）、规定的枚举取值（如 `proceed`、`wait`、`bullish`、`bearish`）、策略文件名、K 线序号格式（如 `K1`、`K42-K1`）。
- **价格行为术语**：思考与 JSON 说明中优先使用下列简体中文 PA 术语（见下节），避免自造词或仅用英文描述。
""".strip()

_PA_TERMINOLOGY_ZH = """
## 价格行为常用术语（简体中文，思考与 JSON 说明中优先使用）

| 术语 | 含义 / 用法提示 |
|------|----------------|
| 信号棒 | 触发入场计划的 K 线；订单须严格位于极点外，真实 tick size 由执行接口确认 |
| 入场棒 | 实际触发入场的 K 线；须在信号棒之后 |
| 确认棒 / 跟随 | 信号或入场后 1–2 根同向延续；无跟随则信号易失败 |
| 突破 | 价格越过结构位、通道线、区间边界或信号棒极点 |
| 假突破 | 突破后快速回到原结构内；区间中常见 |
| 突破回踩 / 回测 | 突破后回撤测试被突破位再延续（勿与「历史回测」混淆） |
| 外包棒 | 高低点完全包含前一根；方向未定时勿追两端 |
| 内包棒 | 完全在前一根范围内；ii/iii 为连续内包 |
| 流星线 | 长上影、小实体，常作顶部拒绝 |
| 锤子线 | 长下影、小实体，常作底部拒绝 |
| 十字星 | 开收接近、多空犹豫 |
| 趋势棒 | 实体大、收盘近极点、影线短 |
| 铁丝网 | 极窄重叠区间，默认少交易 |
| 被套 | 突破方向上的交易者被迫止损离场 |
| 磁力位 | 失败信号棒/入场棒极点吸引价格回测 |

英文缩写（可保留）：SB/EB、OB/IB、H1/H2、L1/L2、MTR、AIL/AIS、20GB。
""".strip()

_STAGE2_API_TASK_RULE = """
## 阶段二 API 任务模式（硬约束，非聊天）

本次调用是 PA Agent **阶段二的一次独立 API 请求**。提示中虽含阶段一诊断 JSON，**不代表**阶段二已完成或可以收尾对话。

**禁止**输出：
- 「阶段一和阶段二都已输出完毕」「分析已完成」等会话总结
- 「告诉我你想怎么处理」「请选择 1/2/3/4」等菜单式追问
- Markdown 摘要、复盘建议、保存文件提示（除非写在 JSON 字段内）

**必须**：在 assistant 正文 `content` 输出**完整阶段二裸 JSON**（仅此一种交付物）。
""".strip()

_OPENCLAW_AGENT_NO_TOOLS_RULE = """
## PA Agent × QClaw 任务模式（硬约束）

你正在接收 **PA Agent 程序化 K 线分析**请求，不是通用编程/运维助手会话。

**禁止调用任何工具**，包括但不限于：`exec`、运行 Python/shell、读/写/编辑文件、浏览器、联网搜索、在 `~/.qclaw/workspace` 写中间 `.md`/`.json` 等。

- K 线表、EMA/ATR、几何特征、阶段一诊断（若有）**已全部在用户消息中给出**；禁止再拉数据或读盘。
- 你负责提出结构入场、失效位、目标与 PA 胜率先验，不调用工具。风险点数、盈亏比与交易者方程由程序在输出后复算；不得为了让方程通过而改写结构价格或胜率。
- **唯一交付物**：assistant 正文 `content` 中的裸 JSON（阶段一或阶段二 schema）。不得在磁盘上留档后再回复。

违反会导致分析极慢、工具刷屏，且程序无法解析你的输出。
""".strip()



_INCREMENTAL_OUTPUT_HARD_RULES = """
## 增量输出格式（硬约束，违反则程序自动重试）

本次是**程序自动分析**，不是人机聊天。assistant 正文 `content` **只能**是完整阶段一裸 JSON（以 `{` 开头、以 `}` 结尾）。

**禁止**在 `content` 里输出：
- Markdown 标题（`##`）、表格、项目符号摘要、emoji
- 「诊断已更新完毕」「如需进入阶段二」「随时告诉我」等对话用语
- 「诊断更新摘要」「主要更新字段」类 executive summary（变化说明应写在 JSON 的 `incremental_delta.summary`、`risk_warning`、`gate_trace` 等字段内）
- ` ```json ` 代码围栏或任何 markdown 围栏

**必须**：输出与全量阶段一相同 schema 的**完整** JSON（含 `incremental_delta`），不是差异补丁或文字版变更说明。
""".strip()

_MARKET_FEATURES_AUTHORITY_NOTE = (
    "⚠️ **程序数值事实以本消息为准**：下方重算块覆盖上方阶段一消息中的旧数值。"
    "这些事实不包含趋势、形态、Setup 或交易结论。\n\n"
)


# ── Hardcoded output format reminders ─────────────────────────────────────────



# ── Analysis-mode–aware Stage 1 output rule ───────────────────────────────────


def _stage1_output_reminder_for_mode(
    analysis_mode: str = "original",
    *,
    decision_tree_path: Path | None = None,
) -> str:
    """Return Stage 1 output rules (same for original and optimized).

    Data preflight handles §1.1; the LLM owns all seven semantic gate nodes.
    """
    _ = analysis_mode  # reserved for future mode-specific tweaks
    from pa_agent.ai.pa_provider_contracts import render_provider_draft_contract

    return (
        f"{_STAGE1_OUTPUT_REMINDER}\n\n"
        "## Exact Stage 1 provider contract\n"
        f"{render_provider_draft_contract('stage1', decision_tree_path=decision_tree_path)}"
    )


def _stage1_tail_reminder() -> str:
    return _STAGE1_TAIL_REMINDER


def _stage2_output_contract(
    *,
    decision_tree_path: Path | None = None,
    joint_stage2: bool = False,
) -> str:
    from pa_agent.ai.pa_provider_contracts import render_provider_draft_contract

    return (
        f"{_STAGE2_OUTPUT_CONTRACT}\n\n"
        "## Exact Stage 2 provider contract\n"
        f"{render_provider_draft_contract('stage2', decision_tree_path=decision_tree_path, joint_stage2=joint_stage2)}"
    )


def _stage2_tail_reminder() -> str:
    return _STAGE2_TAIL_REMINDER


_NEXT_BAR_PREDICTION_INSTRUCTION = """\
## 下一根K线预测任务（阶段二附加输出，不影响下单决策）

完成 decision / decision_trace / terminal 后，必须在阶段二 JSON 顶层追加键 `next_bar_prediction`，
表达对下一根（尚未开始或正在形成）K线收盘后的方向预测：

```json
"next_bar_prediction": {
  "direction": "bullish|bearish|neutral",
  "probabilities": {"bullish": 45, "bearish": 35, "neutral": 20},
  "reasoning": "简体中文理由，30–1500 字。须明确引用阶段一诊断、最近 K 线几何特征、以及（若提供）上一轮预测摘要。",
  "unpredictable": false,
  "features_used": ["stage1_diagnosis", "kline_features"]
}
```

硬约束（违反则整体阶段二 JSON 校验失败）：

1. probabilities 三个值均为 0–100 整数，三者之和必须落在 [99, 101]（容差 ±1，源于取整）。
2. direction 必须等于 probabilities 中数值最大的键；并列最大时取 JSON 出现顺序中靠前的键
   （即按 bullish → bearish → neutral 的字面顺序）。
3. reasoning 长度 30–1500 字，简体中文，不写下单价格、不写止损止盈，仅讨论方向与概率依据。
4. features_used 合法取值封闭列表（只能从下方选对应值，禁止自造字符串）：
   "stage1_diagnosis"、"kline_features"、"analysis_history"、"experience_library"、"stage2_decision"、"previous_prediction_summary"。
   至少包含 "stage1_diagnosis"；若提示词中提供了对应来源，应同步包含
   "kline_features" / "analysis_history" / "experience_library" / "previous_prediction_summary"。
5. 数据不足（K 线数 < 8）、或阶段一诊断为 extreme_tr / unknown、或市场极端混乱时：
   设 unpredictable=true，direction=null，probabilities=null，reasoning 写明原因。
6. 此预测**不**进入交易者方程、**不**改变 decision 中任意字段，仅作辅助参考。
""".strip()

_NEXT_BAR_DISABLED_NOTE = """\
## 下根K线预测（用户已关闭）

用户已关闭「下根K线预期」功能以节省 token：**你无需在 JSON 中输出 `next_bar_prediction`**。
校验器不会要求该字段；请把篇幅集中在 decision / decision_trace / terminal /
`next_cycle_prediction` 上，勿因缺少 `next_bar_prediction` 反复重试。
""".strip()

def _build_next_cycle_prediction_instruction(*, enable_next_bar: bool) -> str:
    """Return next-cycle instruction; avoid referencing next_bar when that feature is off."""
    if enable_next_bar:
        return _NEXT_CYCLE_PREDICTION_INSTRUCTION
    return _NEXT_CYCLE_PREDICTION_INSTRUCTION.replace(
        "完成 next_bar_prediction 后，必须在阶段二 JSON 顶层追加键 `next_cycle_prediction`，",
        "必须在阶段二 JSON 顶层追加键 `next_cycle_prediction`，",
    )


_NEXT_CYCLE_PREDICTION_INSTRUCTION = """\
## 下一个市场周期预测任务（阶段二附加输出，不影响下单决策）

完成 next_bar_prediction 后，必须在阶段二 JSON 顶层追加键 `next_cycle_prediction`，
表达对当前市场周期结束后、下一个市场周期的预测：

```json
"next_cycle_prediction": {
  "cycle": "broad_channel",
  "direction": "bullish",
  "probabilities": {
    "spike": 3,
    "micro_channel": 5,
    "tight_channel": 8,
    "normal_channel": 20,
    "broad_channel": 35,
    "trending_tr": 15,
    "trading_range": 10,
    "extreme_tr": 4
  },
  "reasoning": "简体中文理由，1–1500 字。须引用阶段一周期诊断、K 线结构演变特征，说明各周期概率依据。",
  "unpredictable": false,
  "features_used": ["stage1_diagnosis", "kline_features"]
}
```

市场周期枚举（cycle 字段的合法取值，共 8 个，不含 unknown）：
spike | micro_channel | tight_channel | normal_channel | broad_channel | trending_tr | trading_range | extreme_tr

硬约束（违反则整体阶段二 JSON 校验失败）：

1. probabilities 八个值均为 0–100 整数，八者之和必须落在 [99, 101]（容差 ±1，源于取整）。
2. cycle 必须等于 probabilities 中数值最大的键；并列最大时按上方枚举的字面顺序取靠前者
   （即 spike → micro_channel → tight_channel → normal_channel → broad_channel → trending_tr → trading_range → extreme_tr）。
3. direction 为独立的方向预测（bullish / bearish / neutral），不由 cycle argmax 强制推导；
   表达的是预测下一个周期时市场整体偏向的方向。
4. reasoning 长度 1–1500 字，简体中文，仅讨论周期演变依据，不写下单价格、不写止损止盈。
5. features_used 合法取值封闭列表（只能从下方选对应值，禁止自造字符串）：
   "stage1_diagnosis"、"kline_features"、"analysis_history"、"experience_library"、"stage2_decision"、"previous_prediction_summary"。
   至少包含 "stage1_diagnosis"；若提示词中提供了对应来源，应同步包含
   "kline_features" / "analysis_history" / "experience_library" / "previous_prediction_summary"。
6. 数据不足（K 线数 < 8）、或阶段一诊断为 extreme_tr / unknown、或市场极端混乱时：
   设 unpredictable=true，cycle=null，direction=null，probabilities=null，reasoning 写明原因。
7. 此预测**不**进入交易者方程、**不**改变 decision 中任意字段，仅作辅助参考。
""".strip()

# Prompt files exposed to UI/debug helpers.  The decision tree is sliced by
# semantic section at assembly time instead of being injected in full twice.
COMMON_SYSTEM_STAGE1_TXT_FILES: tuple[str, ...] = (
    "提示词大纲_人设与思维方式.txt",
)
COMMON_SYSTEM_STAGE2_TXT_FILES: tuple[str, ...] = (
    "提示词大纲_人设与思维方式.txt",
)
# Back-compat alias for UI helpers that list common static files.
COMMON_SYSTEM_PROMPT_TXT_FILES: tuple[str, ...] = COMMON_SYSTEM_STAGE2_TXT_FILES

_DECISION_TREE_STAGE2_HEADING = "## 9. 入场信号二元检查"
_DECISION_TREE_STAGE1_HEADING = "## 1. 是否可以决策"
_DIAGNOSIS_SIGNAL_LAYER_HEADING = "## 第三层：逐K信号确认"
_DETAILED_GEOMETRY_BAR_LIMIT = 10

# Process-wide system prompt cache: DeepSeek KV hits need byte-identical prefixes
# across PromptAssembler instances that share the same prompt directory.
_SYSTEM_PROMPT_CACHE: dict[str, str] = {}

STAGE1_TASK_PROMPT_TXT_FILES: tuple[str, ...] = (
    "市场诊断框架.txt",
    "文件16-K线信号识别.txt",
)

_CHANNEL_FILE_GROUPS: dict[str, tuple[str, ...]] = {
    "bullish": (
        "上涨通道分析识别.txt",
        "上涨通道交易策略.txt",
    ),
    "bearish": (
        "下跌通道分析识别.txt",
        "下跌通道交易策略.txt",
    ),
}
_SPIKE_FILE_GROUPS: dict[str, tuple[str, ...]] = {
    "bullish": (
        "极速上涨分析识别.txt",
        "极速上涨交易策略.txt",
    ),
    "bearish": (
        "极速下跌分析识别.txt",
        "极速下跌交易策略.txt",
    ),
}

STAGE2_BASE_PROMPT_TXT_FILES: tuple[str, ...] = (
    "逐棒分析检查单.txt",
    "文件16-K线信号识别.txt",
    "文件17-止损和止盈与仓位管理.txt",
    "文件23-MeasuredMove与结构目标.txt",
)

STAGE2_FULL_STRATEGY_PROMPT_TXT_FILES: tuple[str, ...] = (
    "上涨通道分析识别.txt",
    "上涨通道交易策略.txt",
    "下跌通道分析识别.txt",
    "下跌通道交易策略.txt",
    "极速上涨分析识别.txt",
    "极速上涨交易策略.txt",
    "极速下跌分析识别.txt",
    "极速下跌交易策略.txt",
    "震荡区间分析识别.txt",
    "震荡区间交易策略.txt",
    "文件13-窄通道与宽通道策略.txt",
    "文件14-楔形形态分析交易.txt",
    "文件15-二次入场机会.txt",
    "文件18-突破失败与突破测试.txt",
    "文件19-H1H2-L1L2计数.txt",
    "文件20-AlwaysIn与20GB.txt",
    "文件21-铁丝网与无交易环境.txt",
    "文件22-信号失败后的磁力位.txt",
    "文件24-最终旗形与趋势末端.txt",
    "文件25-主要趋势反转MTR.txt",
    "文件27-三角形与收敛形态.txt",
    "文件28-双重顶底与微型结构.txt",
)


def _fmt_feature(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def stage1_prompt_txt_files() -> list[str]:
    """Return ordered .txt filenames injected in the Stage 1 prompt."""
    return [
        *COMMON_SYSTEM_STAGE1_TXT_FILES,
        "二元决策.txt",
        *STAGE1_TASK_PROMPT_TXT_FILES,
    ]


def _directional_channel_files(direction: str) -> list[str]:
    key = str(direction or "").strip().lower()
    if key in _CHANNEL_FILE_GROUPS:
        return list(_CHANNEL_FILE_GROUPS[key])
    return []


def stage2_user_task_txt_files(
    strategy_files: list[str] | None = None,
    *,
    direction: str = "",
    load_full_strategy_library: bool = False,
) -> list[str]:
    """Return .txt filenames loaded into the Stage 2 user turn only."""
    routed = [f for f in (strategy_files or []) if f]
    if load_full_strategy_library:
        core = [*STAGE2_FULL_STRATEGY_PROMPT_TXT_FILES, *STAGE2_BASE_PROMPT_TXT_FILES]
    else:
        dir_key = str(direction or "").strip().lower()
        opposite = (
            _CHANNEL_FILE_GROUPS.get("bearish", ())
            if dir_key == "bullish"
            else _CHANNEL_FILE_GROUPS.get("bullish", ())
            if dir_key == "bearish"
            else ()
        )
        opposite_spike = (
            _SPIKE_FILE_GROUPS.get("bearish", ())
            if dir_key == "bullish"
            else _SPIKE_FILE_GROUPS.get("bullish", ())
            if dir_key == "bearish"
            else ()
        )
        skip = frozenset((*opposite, *opposite_spike))
        core = [
            f
            for f in routed
            if f not in skip
        ]
        core.extend(STAGE2_BASE_PROMPT_TXT_FILES)
    return list(dict.fromkeys([*core]))


def stage2_prompt_txt_files(
    strategy_files: list[str] | None = None,
    *,
    direction: str = "",
    load_full_strategy_library: bool = False,
) -> list[str]:
    """Return all .txt files relevant to Stage 2 (system common + user task), for UI/debug."""
    return [
        *COMMON_SYSTEM_STAGE2_TXT_FILES,
        "二元决策.txt",
        *stage2_user_task_txt_files(
            strategy_files,
            direction=direction,
            load_full_strategy_library=load_full_strategy_library,
        ),
    ]


def select_agent_knowledge_text(*, role: str, module_id: str, text: str) -> str:
    """Apply the same role boundaries used by the mature Direct prompt.

    Codex knowledge retrieval must not broaden Stage 1 into the execution half
    of the decision tree, nor omit the global principles needed by an isolated
    Stage 2 task.  Custom prompt packs without the stable headings retain their
    complete document, matching PromptAssembler's compatibility behavior.
    """
    if role == "stage1" and module_id == "二元决策.txt":
        marker = f"\n{_DECISION_TREE_STAGE2_HEADING}"
        index = text.find(marker)
        selected = text if index < 0 else text[:index].rstrip()
        return re.sub(
            r"(?ms)^###\s+1\.1\b.*?(?=^###\s+1\.2\b)",
            "§1.1 数据充足性已由程序预检；模型从 §1.2 开始判断。\n\n",
            selected,
        )
    if role == "stage1" and module_id == "市场诊断框架.txt":
        marker = f"\n{_DIAGNOSIS_SIGNAL_LAYER_HEADING}"
        index = text.find(marker)
        return text if index < 0 else text[:index].rstrip()
    if role == "stage2" and module_id == "二元决策.txt":
        execution_marker = f"\n{_DECISION_TREE_STAGE2_HEADING}"
        execution_index = text.find(execution_marker)
        if execution_index < 0:
            return text
        stage1_marker = f"\n{_DECISION_TREE_STAGE1_HEADING}"
        stage1_index = text.find(stage1_marker)
        global_principles = (
            text[:stage1_index].rstrip()
            if stage1_index >= 0
            else text[:execution_index].rstrip()
        )
        execution = text[execution_index + 1 :].lstrip()
        return f"{global_principles}\n\n{execution}"
    return text


# Provider Draft v2 deliberately omits deterministic protocol fields. Canonical
# records remain unchanged after JsonValidator materializes the draft.
_THINKING_CONTENT_OUTPUT_RULE = """
## 思考与正式输出分离
思考区用于推演；正文只输出可被 json.loads 解析的完整裸 JSON。不得使用 Markdown
围栏、工具或外部数据。控制推演篇幅，为最终 JSON 预留充足输出预算。
""".strip()

_STAGE1_OUTPUT_REMINDER = """
## Stage 1 Provider Draft v2
只输出裸 JSON。PA 负责判断 Background、cycle、direction、Always In、Setup 线索与
竞争假设；程序只提供可复算事实并物化决策树静态字段。Evidence first，hypothesis
plural，label last。不得根据单一阈值机械分类，也不得为了满足格式制造替代假设。

字段形状、必填项、类型和枚举只以紧随其后的自动生成 Provider Contract 为准，
不要沿用旧版 Canonical JSON 的顶层镜像或静态 trace 字段。证据 ID 必须来自本次
ANALYSIS_FACT_CATALOG；alternatives 可为空，禁止复制 primary 伪造不同观点。
`gate_trace` 不要输出 `question/section/bar_range`，这些字段由程序物化。
只评估 §1-§2，禁止输出 10.3、14 或 `gate_result`。完整可决策路径应走到 §2.5；
§1.2 无法识别时在该节点停止，§1.3 极端混乱时在该节点停止。
""".strip()

_STAGE2_OUTPUT_CONTRACT = """
## Stage 2 Provider Draft v2
只输出裸 JSON。Stage 2 负责 Brooks Setup、信号与入场时机、entry、结构 stop、TP1、
可选的结构延伸 TP2、PA 主观胜率先验及最终 `trade|wait|reject` 意图；程序复算价格几何、RR、Breakeven、Expectancy
和 Trader Equation，并执行订单安全检查。
Stage 2 是具体 PA 交易方案的唯一所有者；程序不替它选择 Setup 或入场时机。

字段形状、必填项、类型和枚举只以紧随其后的自动生成 Provider Contract 为准。
不要输出 Stage 1 冻结字段、`gate_shortcircuited`、静态 trace 字段、§10.3、
`terminal` 或 `next_cycle_prediction`；程序根据 Evidence ID、模型意图和确定性交易数学物化。
`decision_trace` 不要输出 `question/section/bar_range`、§10.3 或 `terminal`。
Trace protocol: `node_id` must be an exact question ID; section heading `11` is invalid,
so choose the applicable `11.1`-`11.4`. Every executable order must include node `14`.
若 `order_type=不下单`，方向、三价、交易置信度和胜率必须按 contract 置 null。
`next_bar_prediction` 是启用时由 LLM 提供的辅助判断，不参与订单授权。
模型仍需判断 §10.1/§10.2、Setup 是否成立、信号质量和是否执行；
不得为了让方程通过而调整结构价格或胜率。`estimated_win_rate` 仍是未经统计校准的 PA 主观胜率先验。
先基于本轮 Background、Location、Setup、信号与反证形成定性概率排序，再给数值；
教学材料中的通用频率不得复制为本轮胜率，Breakeven 也不得反推胜率。
TP1 是执行目标和 Trader Equation 唯一回报基础；仅在存在可核对的独立远端结构目标时填写 TP2，
否则 `take_profit_price_2=null`，不得为满足字段或美化收益凭空投射。
""".strip()

_STAGE1_TAIL_REMINDER = (
    "【输出】正文只写 Stage 1 Draft v2 裸 JSON。确认含真实 primary、可为空的 alternatives、"
    "证据引用和完整语义 gate_trace；不要输出程序拥有的静态字段。"
)

_STAGE2_TAIL_REMINDER = (
    "【输出】正文只写 Stage 2 Draft v2 裸 JSON。确认保留最终 decision_intent，"
    "不要输出 Stage1 冻结字段、§10.3、terminal 或静态 trace 字段。"
)


# ── PromptAssembler ────────────────────────────────────────────────────────────

class PromptAssembler:
    """Builds message lists for Stage 1 and Stage 2 API calls."""

    def __init__(
        self,
        prompt_dir: Path,
        experience_reader: Any = None,
        *,
        prompt_settings: Any = None,
        vpa_evidence_catalog: dict[str, Any] | None = None,
        analysis_fact_catalog: AnalysisFactCatalog | None = None,
    ) -> None:
        self._prompt_dir = prompt_dir
        self._experience_reader = experience_reader
        self._prompt_settings = prompt_settings
        self._vpa_evidence_catalog = vpa_evidence_catalog
        self._analysis_fact_catalog = analysis_fact_catalog
        self._txt_cache: dict[str, str] = {}

    @property
    def prompt_dir(self) -> Path:
        """Directory containing the immutable prompt pack for this run."""
        return Path(self._prompt_dir)

    @property
    def decision_tree_path(self) -> Path:
        return self.prompt_dir / "\u4e8c\u5143\u51b3\u7b56.txt"

    def _load_full_strategy_library(self) -> bool:
        cfg = self._prompt_settings
        if cfg is None:
            return False
        return bool(getattr(cfg, "stage2_load_full_strategy_library", False))

    def _vpa_enabled(self) -> bool:
        return vpa_is_enabled(self._prompt_settings)

    def clone_with_prompt_settings(
        self,
        prompt_settings: Any,
        *,
        vpa_evidence_catalog: dict[str, Any] | None = None,
    ) -> "PromptAssembler":
        """Create an isolated assembler for one PA, VPA, or Joint role."""
        return PromptAssembler(
            self._prompt_dir,
            self._experience_reader,
            prompt_settings=prompt_settings,
            vpa_evidence_catalog=vpa_evidence_catalog,
            analysis_fact_catalog=self._analysis_fact_catalog,
        )

    def clone_with_analysis_fact_catalog(
        self,
        catalog: AnalysisFactCatalog,
    ) -> "PromptAssembler":
        """Bind one immutable fact catalog to a frozen analysis run."""
        return PromptAssembler(
            self._prompt_dir,
            self._experience_reader,
            prompt_settings=self._prompt_settings,
            vpa_evidence_catalog=self._vpa_evidence_catalog,
            analysis_fact_catalog=catalog,
        )

    def _vpa_modules_for_stage2(
        self,
        stage1_json: dict[str, Any] | None,
        *,
        include_core: bool = True,
    ) -> list[str]:
        """Select reference knowledge from already-validated Brooks context.

        This is a prompt-budget selector, not a strategy router or a Wyckoff
        event classifier.  The original Brooks Router remains authoritative.
        """
        return list(
            build_vpa_knowledge_context(
                stage1_json,
                include_core=include_core,
            ).module_names
        )

    def _load_vpa_rule_prompt(
        self,
        *,
        stage1_json: dict[str, Any] | None = None,
        include_core: bool = True,
    ) -> str:
        if not self._vpa_enabled():
            return ""
        module_names = (
            self._vpa_modules_for_stage2(stage1_json, include_core=include_core)
            if stage1_json
            else (["core"] if include_core else [])
        )
        if not module_names:
            return ""
        content = ["# Wyckoff VPA knowledge modules", "Loaded modules: " + ", ".join(module_names)]
        content.extend(self._load(VPA_KNOWLEDGE_MODULES[name]) for name in module_names)
        return wrap_vpa_prompt_block("\n\n---\n\n".join(part for part in content if part))

    def _render_vpa_measurements_block(
        self,
        frame: KlineFrame,
        pa_hypotheses: dict[str, Any] | None = None,
    ) -> str:
        if not self._vpa_enabled():
            return ""
        try:
            catalog = self._vpa_evidence_catalog
            if catalog is None:
                catalog = build_vpa_evidence_catalog(frame)
            return wrap_vpa_prompt_block(
                render_vpa_measurements(
                    catalog=catalog,
                    selected_fact_refs=pa_window_fact_refs(pa_hypotheses),
                )
            )
        except Exception as exc:
            logger.warning("render_vpa_measurements failed: %s", exc)
            raise VpaPromptBuildError(
                "vpa_measurement_render_failed",
                f"VPA program evidence could not be rendered: {exc}",
            ) from exc

    @staticmethod
    def _entry_attempt_context(pa_hypotheses: dict[str, Any]) -> dict[str, Any]:
        candidates = pa_hypotheses.get("setup_candidates", [])
        candidates = candidates if isinstance(candidates, list) else []
        candidate_ids = [
            str(item.get("candidate_id"))
            for item in candidates
            if isinstance(item, dict) and item.get("candidate_id")
        ]
        return {
            "applicable": bool(candidate_ids),
            "candidate_ids": candidate_ids,
            "reason": "candidate_available" if candidate_ids else "no_pa_setup_candidate",
        }

    def _referenced_program_comparators(
        self,
        vpa_report: dict[str, Any],
    ) -> list[dict[str, Any]]:
        catalog = self._vpa_evidence_catalog
        if not isinstance(catalog, dict):
            return []
        cited_ids = {
            str(evidence_id)
            for section in (
                vpa_report.get("observations", []),
                vpa_report.get("hypothesis_assessments", []),
            )
            if isinstance(section, list)
            for item in section
            if isinstance(item, dict)
            for evidence_id in item.get("evidence_ids", [])
        }
        projected: list[dict[str, Any]] = []
        for item in catalog.get("evidence", []):
            if not isinstance(item, dict):
                continue
            evidence_id = str(item.get("evidence_id") or "")
            if evidence_id not in cited_ids or not evidence_id.startswith(
                ("vpa-w", "vpa-c")
            ):
                continue
            facts = item.get("facts") if isinstance(item.get("facts"), dict) else {}
            projected.append(
                {
                    "evidence_id": evidence_id,
                    "current_window": facts.get("current_window"),
                    "prior_window": facts.get("prior_window"),
                    "current_progress_direction": facts.get("current_progress_direction"),
                    "prior_progress_direction": facts.get("prior_progress_direction"),
                    "net_progress_direction_relation": (
                        net_close_progress_direction_relation(facts)
                    ),
                    "volume_relation": facts.get("current_vs_prior_volume_relation"),
                    "abs_progress_relation": facts.get(
                        "current_vs_prior_abs_progress_relation"
                    ),
                    "spread_relation": facts.get("current_vs_prior_spread_relation"),
                }
            )
        return projected

    def _record_context(self, value: Any) -> Any:
        """Return legacy-record context without obsolete embedded VPA fields."""
        from pa_agent.ai.vpa_sanitizer import price_only_history
        return price_only_history(value)

    # ── Process-level system-prompt cache ────────────────────────────────────
    # DeepSeek KV Cache hits require the *prefix* of consecutive requests to
    # be byte-identical.  System prompts are fully static (persona + txt files)
    # and never change during a session, so we cache them at the process level.
    # Stage 1 and Stage 2 share one static system blob so S1→S2 prefix matches.

    @functools.cached_property
    def _shared_system_prompt(self) -> str:
        """Shared Stage 1/2 system prompt (cached for this instance)."""
        return self._get_shared_system_prompt()

    def _get_shared_system_prompt(self) -> str:
        key = str(self._prompt_dir.resolve())
        cached = _SYSTEM_PROMPT_CACHE.get(key)
        if cached is not None:
            return cached
        built = self._build_shared_system_prompt_inner()
        _SYSTEM_PROMPT_CACHE[key] = built
        return built

    def _build_stage1_system_prompt(self) -> str:
        """Return cached shared system prompt."""
        return self._shared_system_prompt

    def _build_stage2_system_prompt(self) -> str:
        """Return cached shared system prompt (byte-identical to Stage 1)."""
        return self._shared_system_prompt

    def _build_shared_system_prompt_inner(self) -> str:
        """Build the static persona and transport contract shared by both stages."""
        system_parts = [
            _LANGUAGE_ZH_RULE,
            _PA_TERMINOLOGY_ZH,
            _OPENCLAW_AGENT_NO_TOOLS_RULE,
            _THINKING_CONTENT_OUTPUT_RULE,
        ]
        system_parts.extend(self._load(name) for name in COMMON_SYSTEM_PROMPT_TXT_FILES)
        return "\n\n---\n\n".join(p for p in system_parts if p)

    # ── File loading ──────────────────────────────────────────────────────────

    def _load(self, filename: str) -> str:
        """Load a prompt file by name. Returns empty string on error."""
        if filename in self._txt_cache:
            return self._txt_cache[filename]
        path = self._prompt_dir / filename
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to load prompt file %s: %s", filename, exc)
            content = f"[ERROR: could not load {filename}]"
        self._txt_cache[filename] = content
        return content

    def _load_before_heading(self, filename: str, heading: str) -> str:
        """Load the part before a stable Markdown heading.

        Custom prompt packs without the heading keep their complete content;
        this preserves compatibility while the bundled pack gets deterministic
        context slicing.
        """
        content = self._load(filename)
        marker = f"\n{heading}"
        index = content.find(marker)
        return content if index < 0 else content[:index].rstrip()

    def _decision_tree_stage1(self) -> str:
        """Return global principles and the PA framework/setup sections 0-8."""
        content = self._load_before_heading(
            "二元决策.txt",
            _DECISION_TREE_STAGE2_HEADING,
        )
        content = re.sub(
            r"(?ms)^###\s+1\.1\b.*?(?=^###\s+1\.2\b)",
            "§1.1 数据充足性已由程序预检；模型从 §1.2 开始判断。\n\n",
            content,
        )
        return content

    def _decision_tree_stage2(self, *, include_global: bool) -> str:
        """Return execution sections 9-15, plus global principles if standalone."""
        content = self._load("二元决策.txt")
        marker = f"\n{_DECISION_TREE_STAGE2_HEADING}"
        index = content.find(marker)
        if index < 0:
            return content
        execution = content[index + 1 :].lstrip()
        if not include_global:
            return execution
        stage1_marker = f"\n{_DECISION_TREE_STAGE1_HEADING}"
        stage1_index = content.find(stage1_marker)
        global_principles = (
            content[:stage1_index].rstrip()
            if stage1_index >= 0
            else content[:index].rstrip()
        )
        return f"{global_principles}\n\n{execution}"

    def _market_diagnosis_framework(self) -> str:
        """Return the cycle/background layers; signal theory is loaded separately."""
        return self._load_before_heading(
            "市场诊断框架.txt",
            _DIAGNOSIS_SIGNAL_LAYER_HEADING,
        )

    # ── K-line table rendering ────────────────────────────────────────────────

    @staticmethod
    def _render_kline_table(frame: KlineFrame, limit: int | None = None) -> str:
        """Render the price-only K-line table (newest closed bar first)."""
        lines = [
            "序号 | 时间                | 开盘价    | 最高价    | 最低价    | 收盘价    | 阳阴 | EMA20     | ATR14",
            "-----+--------------------+----------+----------+----------+----------+------+-----------+----------",
        ]
        bars = frame.bars[:limit] if limit is not None else frame.bars
        for i, bar in enumerate(bars):
            ema = frame.indicators.ema20[i]
            atr = frame.indicators.atr14[i]
            ema_str = f"{ema:.4f}" if not math.isnan(ema) else "N/A"
            atr_str = f"{atr:.4f}" if not math.isnan(atr) else "N/A"
            yang_yin = bar_candle_direction_label(bar)
            dt = format_epoch_for_display(bar.ts_open, short=True)
            lines.append(
                f"{bar.seq:<4} | {dt:<19} | {bar.open:<9.4f} | {bar.high:<9.4f} | "
                f"{bar.low:<9.4f} | {bar.close:<9.4f} | {yang_yin:<4} | "
                f"{ema_str:<10} | {atr_str}"
            )
        lines.append(_KLINE_INDICATOR_NOTE)
        return "\n".join(lines)

    @staticmethod
    def _render_kline_feature_table(frame: KlineFrame, limit: int | None = None) -> str:
        """Render reproducible per-bar facts without market interpretation."""
        shown = limit if limit is not None else len(frame.bars)
        lines = [
            f"（最近 {shown} 根已收盘 K 线；只给公式唯一的几何与指标差值，不给 bar_type、信号或形态标签）",
            "序号 | 阳阴事实 | Range | Body | 实体比 | 上影比 | 下影比 | 收盘位置 | ATR14 | Range/ATR | EMA20 | Close-EMA | (Close-EMA)/ATR | 与前棒关系 | 重叠比 | ΔHigh | ΔLow | ΔClose",
            "-----+----------+-------+------+--------+--------+--------+----------+-------+-----------+-------+-----------+-----------------+------------+--------+-------+------+-------",
        ]
        for feat in compute_kline_geometry_features(frame, limit=limit):
            lines.append(
                f"{feat.seq:<4} | {feat.candle_direction:<8} | "
                f"{_fmt_feature(feat.full_range):<5} | "
                f"{_fmt_feature(feat.body_size):<4} | "
                f"{_fmt_feature(feat.body_ratio):<6} | "
                f"{_fmt_feature(feat.upper_wick_ratio):<6} | "
                f"{_fmt_feature(feat.lower_wick_ratio):<6} | "
                f"{_fmt_feature(feat.close_position):<8} | "
                f"{_fmt_feature(feat.atr14):<5} | "
                f"{_fmt_feature(feat.range_atr_ratio):<9} | "
                f"{_fmt_feature(feat.ema20):<5} | "
                f"{_fmt_feature(feat.close_minus_ema):<9} | "
                f"{_fmt_feature(feat.close_minus_ema_atr):<15} | "
                f"{feat.relation_to_previous:<10} | "
                f"{_fmt_feature(feat.overlap_prev_ratio):<6} | "
                f"{_fmt_feature(feat.high_delta_prev):<5} | "
                f"{_fmt_feature(feat.low_delta_prev):<4} | "
                f"{_fmt_feature(feat.close_delta_prev)}"
            )
        lines.append(_KLINE_INDICATOR_NOTE)
        return "\n".join(lines)

    def _render_simple_market_features_block(self, frame: KlineFrame) -> str:
        """Render the canonical price-only fact catalog."""
        try:
            catalog = self._analysis_fact_catalog
            if catalog is None:
                catalog = build_analysis_fact_catalog(frame)
            elif catalog.snapshot_hash != analysis_snapshot_hash(frame):
                raise ValueError("analysis fact catalog does not match the frozen frame")
            rendered = render_pa_fact_catalog(catalog).replace(
                "## ANALYSIS_FACT_CATALOG",
                "ANALYSIS_FACT_CATALOG",
                1,
            )
            from pa_agent.ai.market_features import MARKET_FEATURES_SECTION_PREFIX

            return f"{MARKET_FEATURES_SECTION_PREFIX}\n\n{rendered}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("_render_simple_market_features_block failed: %s", exc)
            return ""

    def _inject_market_features_block(self, prompt: str, frame: KlineFrame) -> str:
        """Refresh or insert program market-features into a Stage 1 user prompt."""
        block = self._render_simple_market_features_block(frame)
        if not block:
            return prompt
        return inject_market_features_section(prompt, block)

    # ── Stage 1 ───────────────────────────────────────────────────────────────

    def build_stage1_agent_protocol(
        self,
        frame: KlineFrame,
        *,
        analysis_mode: str,
        incremental_new_bar_count: int | None = None,
    ) -> dict[str, Any]:
        """Publish the code-generated Stage 1 semantics omitted from knowledge files.

        The complete Brooks documents remain PA-selected mandatory knowledge.  This
        contract carries the mature Windows prompt's executable rules so the Codex
        backend cannot silently replace them with a shorter generic Skill.
        """
        n_bars = len(frame.bars)
        incremental = incremental_new_bar_count is not None
        return {
            "contract_version": "pa_stage1_agent_protocol_v1",
            "role": "stage1",
            "semantic_parity_policy": (
                "Apply this contract and every mandatory knowledge module before "
                "using selective evidence investigation. Token reduction is not "
                "permission to omit a rule."
            ),
            "task_boundary": (
                "Only diagnose market context and Gate. Do not evaluate a concrete "
                "order, entry, stop, target, position size, or final Trade/No Trade."
            ),
            "bar_numbering": {
                "newest_closed_bar": "K1",
                "oldest_available_bar": f"K{n_bars}",
                "unclosed_bar_allowed": False,
                "bar_range_rule": (
                    "Every decision-node bar_range must cover every K reference in "
                    "its reason and remain inside the frozen window."
                ),
            },
            "analysis_windows": {
                "background": f"K{n_bars}-K41 when available",
                "recent_structure": f"K{min(40, n_bars)}-K1",
                "immediate_momentum": f"K{min(8, n_bars)}-K1",
                "immediate_signal": f"K{min(10, n_bars)}-K1",
                "bar_by_bar_summary": "K5-K1; exactly five items when at least five bars exist",
                "precedence": (
                    "Recent structure determines direction; long background is risk "
                    "context and must not veto recent direction by itself."
                ),
            },
            "incremental": {
                "enabled": incremental,
                "new_closed_bar_count": (
                    int(incremental_new_bar_count or 0) if incremental else 0
                ),
                "anti_anchoring": (
                    "Re-diagnose the complete frozen chart independently. Previous "
                    "analysis is a hypothesis to retest, not a constraint; replace it "
                    "when breakout, reversal, acceleration, exhaustion, or invalidated "
                    "support/resistance changes structure."
                ),
                "delta_required": incremental,
            },
            "embedded_rules": [
                _LANGUAGE_ZH_RULE,
                _PA_TERMINOLOGY_ZH,
                _PA_FACTUAL_CLAIM_RULE,
                _PA_EVIDENCE_OUTPUT_RULE,
                render_pa_theory_rule_catalog(),
                self._stage1_pattern_supplement(),
                _stage1_output_reminder_for_mode(
                    analysis_mode,
                    decision_tree_path=self.decision_tree_path,
                ),
                _stage1_tail_reminder(),
            ],
        }

    def build_stage1(
        self,
        frame: KlineFrame,
        *,
        analysis_mode: str = "original",
        htf_context: dict[str, Any] | None = None,
    ) -> list[dict]:
        """Build the message list for Stage 1 (market diagnosis)."""
        system_content = self._build_stage1_system_prompt()
        user_content = self._build_stage1_user_prompt(
            frame,
            analysis_mode=analysis_mode,
            htf_context=htf_context,
        )

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

    def build_vpa_evidence_report(
        self,
        frame: KlineFrame,
        stage1_json: dict[str, Any],
        pa_hypotheses: dict[str, Any],
        *,
        htf_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Build an independent VPA evidence call over frozen PA hypotheses."""
        if not self._vpa_enabled():
            raise ValueError("VPA evidence analysis requires VPA to be enabled")
        frozen_keys = (
            "spike_stage",
            "climax_risk",
            "market_phase",
            "transition_risk",
            "detected_patterns",
            "htf_context",
            "support_levels",
            "resistance_levels",
            "risk_warning",
            "bar_analysis",
            "pa_evidence",
            "gate_result",
        )
        frozen_pa = {
            key: stage1_json[key]
            for key in frozen_keys
            if key in stage1_json
        }
        htf_identity = (
            {
                key: htf_context.get(key)
                for key in ("timeframe", "available", "reason")
                if key in htf_context
            }
            if isinstance(htf_context, dict)
            else {}
        )
        knowledge = self._load_vpa_rule_prompt(stage1_json=stage1_json)
        measurements = self._render_vpa_measurements_block(frame, pa_hypotheses)
        entry_attempt_context = self._entry_attempt_context(pa_hypotheses)
        analysis_envelope = {
            "pa_hypotheses": pa_hypotheses,
            "pa_context": frozen_pa,
            "entry_attempt": entry_attempt_context,
            "htf_identity": htf_identity or None,
        }
        user_content = (
            "## VPA_EVIDENCE_ANALYSIS_TASK\n\n"
            "先读量价 Evidence Catalog，再对照以下单一冻结分析信封。你只能评估量价证据与这些"
            "假设及候选的关系，不能重写 PA。候选中的订单价格、trade_math 与"
            "window_context 均由程序冻结；有候选时，entry_timing_relation 必须"
            "针对该真实 Entry Attempt，而不是笼统复述 PA 方向。\n\n"
            "### VPA_ANALYSIS_ENVELOPE\n\n"
            f"```json\n{json.dumps(analysis_envelope, ensure_ascii=False, indent=2)}\n```\n\n"
            + (f"{knowledge}\n\n" if knowledge else "")
            + (f"{measurements}\n\n" if measurements else "")
            + (
                "Comparator relations such as above/below record direction only; "
                "read the signed deviation_pct before judging magnitude, and a ratio "
                "sign alone does not justify strong activity wording.\n\n"
            )
            + """输出且只输出：
{
  "vpa_report": {
    "summary": "核心量价解释、竞争解释与不确定性，不复写数字",
    "observations": [{
      "evidence_ids": ["仅使用目录中的 ID"],
      "finding_kind": "background | effort_result | sequence_comparison | acceptance_rejection | entry_timing",
      "sequence_claims": [{
        "earlier_evidence_id": "较早证据 ID",
        "later_evidence_id": "较晚证据 ID",
        "relation": "response | follow_through | acceptance | rejection | test"
      }],
      "interpretation": "先写事实综合，再比较合理解释并说明不确定性，不复写数字",
      "epistemic_status": "candidate | confirmed | unresolved | insufficient_data",
      "interpretation_label": "开放式专业标签或 null；必须最后填写"
    }],
    "hypothesis_assessments": [{
      "hypothesis_id": "逐字复制每个 PA hypothesis_id；每个假设恰好一项",
      "relation": "supports | neutral | challenges | insufficient_data",
      "evidence_ids": ["实际使用的 evidence ID；无证据时为空数组"],
      "reason": "该量价证据为何支持、挑战或不足"
    }],
    "background_relation": "supports | neutral | challenges | insufficient_data",
    "entry_timing_relation": "supports | neutral | challenges | insufficient_data",
    "next_confirmation": {
      "state": "pending_closed_bar | not_applicable",
      "trigger": "pending_closed_bar 时填写下一根已收盘 K 线需要验证的 acceptance/rejection；not_applicable 时为 null"
    }
  }
}

不要输出 `bar_range`，程序会根据 evidence IDs 唯一生成。K 序号越大越早，K1
是最新已收盘 K 线；凡声称 response/follow-through/acceptance/rejection/test，优先用
`sequence_claims` 已经明确声明较早与较晚 evidence ID，无需仅为格式在父级 `evidence_ids` 重复列出；
程序会把目录内已知 ID 记入 `auto_bound_evidence_ids`，但不会据此替你升级 supports/challenges。两个证据
各自 `bar_range` 不得重叠。证据不够时保持 `unresolved`，不要为了填标签把候选事件写成确认事件。
`interpretation_label` 是开放式速记，不是第二份证明。每条 `interpretation` 明确提到的 K# 应优先由
该条自己的 `evidence_ids` 覆盖；唯一可绑定的单棒引用可由程序审计补全，未知、歧义或越界引用仍会拒绝。
只有 VPA 本身存在需要下一根已收盘 K 线验证的竞争解释时，
`next_confirmation` 才写 `pending_closed_bar`；VPA 为 neutral/insufficient，或没有独立的后续
验证条件时可写 `not_applicable`。该字段是证据观察项，不是 Brooks Entry Attempt 的审批 Gate。
当 `VPA_ANALYSIS_ENVELOPE.entry_attempt.applicable=false` 时，不存在可被 VPA 评价的当前入场尝试，
必须写 `entry_timing_relation=insufficient_data`；仍可输出独立的背景观察和后续证据条件。
数据可用时，
至少给出一项真正的 Effort/Result、序列比较、Acceptance/Rejection 或
Timing 信息增量，不能只复述 PA。无证据也必须完整输出，并明确写
`insufficient_data`。"""
        )
        return [
            {"role": "system", "content": _VPA_EVIDENCE_ANALYST_SYSTEM},
            {"role": "user", "content": user_content},
        ]

    def build_joint_decision(
        self,
        baseline_stage2: dict[str, Any],
        pa_hypotheses: dict[str, Any],
        vpa_report: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build a focused arbitration call over validated role outputs.

        The joint role does not receive the original K-line tables or full PA
        theory again.  Its task is comparison, not a third market analysis.
        """
        baseline_context = {
            key: copy.deepcopy(baseline_stage2.get(key))
            for key in (
                "decision",
                "bar_analysis",
                "decision_trace",
                "terminal",
            )
            if key in baseline_stage2
        }
        entry_attempt_context = self._entry_attempt_context(pa_hypotheses)
        referenced_comparators = self._referenced_program_comparators(vpa_report)
        user_content = (
            "## JOINT_DECISION_ARBITRATION_TASK\n\n"
            "以下 PA 候选、PA-only 决策和 VPA 报告均已通过程序校验并冻结。"
            "只裁决 VPA 是否给当前 Entry Attempt 带来真实信息增量；不要重做"
            " PA、重算价格或复制完整 Stage 2。\n\n"
            "### PA_HYPOTHESES_AND_CANDIDATES\n\n"
            f"```json\n{json.dumps(pa_hypotheses, ensure_ascii=False, indent=2)}\n```\n\n"
            "### ENTRY_ATTEMPT_CONTEXT\n\n"
            f"```json\n{json.dumps(entry_attempt_context, ensure_ascii=False, indent=2)}\n```\n\n"
            "### VALIDATED_VPA_REPORT\n\n"
            f"```json\n{json.dumps(vpa_report, ensure_ascii=False, indent=2)}\n```\n\n"
            + (
                "### REFERENCED_PROGRAM_COMPARATORS\n\n"
                f"```json\n{json.dumps(referenced_comparators, ensure_ascii=False, indent=2)}\n```\n\n"
                if referenced_comparators
                else ""
            )
            + "### FROZEN_PA_ONLY_STAGE2_BASELINE\n\n"
            f"```json\n{json.dumps(baseline_context, ensure_ascii=False, indent=2)}\n```\n\n"
            """输出且只输出：
{
  "joint_arbitration": {
    "selected_candidate_id": "当前被裁决的 PA candidate_id；PA 本来不下单时可为 null",
    "decision_effect": "keep_brooks_baseline | require_confirmation | prefer_retest | lower_confidence | reject_current_attempt",
    "evidence_used": ["仅引用 validated VPA report 已使用的 evidence ID"],
    "reason": "VPA 对当前 Entry Attempt 的信息增量、竞争解释及裁决理由，不复写数字"
  },
  "decision_patch": {
    "trade_confidence": null,
    "trade_confidence_reasoning": null,
    "watch_points": null,
    "risk_assessment": null,
    "invalidation_condition": null
  }
}

规则：
- `keep_brooks_baseline` 时 `decision_patch` 输出空对象 `{}`；中性或证据不足必须保持 baseline。
- `lower_confidence` 必须给出严格低于 baseline 的 `trade_confidence`；不得为了形式随意降分。
- `require_confirmation`、`prefer_retest`、`reject_current_attempt` 至少给出一个可执行的
  `watch_points` 或 `invalidation_condition`。程序会确定性地把当前尝试转为不下单；
  不要复制或改写订单价格、方向、止损、目标、胜率、PA trace 或预测。
- VPA 可以支持背景但不支持立即入场；必须分别判断背景与 timing。
- 非 baseline effect 必须引用 VPA report 中真实使用且含相对量比较的 evidence ID。
- 禁止提高 `trade_confidence`，禁止修改 `estimated_win_rate`、`diagnosis_confidence`、
  PA direction/Gate/Setup，禁止从 PA 不下单 baseline 创造交易。
"""
        )
        return [
            {"role": "system", "content": _JOINT_ARBITER_SYSTEM},
            {"role": "user", "content": user_content},
        ]

    def _normalize_prev_stage1_assistant_for_incremental(
        self,
        previous_record: AnalysisRecord,
        raw_content: str,
    ) -> str:
        """Use validated diagnosis JSON in incremental context, not prose/markdown replies."""
        from pa_agent.ai.json_validator import format_model_json_for_context

        diag = getattr(previous_record, "stage1_diagnosis", None) or {}
        if isinstance(diag, dict) and diag:
            diag = strip_vpa_fields(diag)
            return json.dumps(diag, ensure_ascii=False, indent=2)

        formatted = format_model_json_for_context(raw_content)
        if formatted:
            return formatted

        logger.warning(
            "incremental stage1: could not normalize previous assistant to JSON; "
            "using raw stage1_response content (%d chars)",
            len(raw_content or ""),
        )
        return raw_content

    def build_incremental_stage1(
        self,
        frame: KlineFrame,
        previous_record: AnalysisRecord,
        new_bar_count: int,
        *,
        analysis_mode: str = "original",
        provider_settings: Any | None = None,
        htf_context: dict[str, Any] | None = None,
    ) -> list[dict]:
        """Build Stage 1 as a continuation-based incremental update.

        Structure:
          [0] system    — Stage 1 system prompt (same as full Stage 1)
          [1] user      — Previous full Stage 1 user prompt (with K-line table)
          [2] assistant — Previous Stage 1 reply
          [3] user      — Incremental task (new K-lines only, no full table)

        Benefits vs old 2-message incremental:
        - [system, user(S1)] prefix is IDENTICAL to full Stage 1 → prefix cache hit
        - Full K-line table is in [1], not re-sent in [3] → saves ~14.5K tokens
        - Stage 2 continuation can also cache-hit this prefix chain
        """
        prev_s1_messages = getattr(previous_record, "stage1_messages", None) or []
        prev_s1_response = getattr(previous_record, "stage1_response", None) or {}

        # Extract previous Stage 1 user message
        prev_user_content = ""
        for msg in prev_s1_messages:
            if msg.get("role") == "user":
                prev_user_content = msg["content"]
                break

        # Extract previous Stage 1 assistant reply content
        prev_assistant_content = ""
        if isinstance(prev_s1_response, dict):
            prev_assistant_content = prev_s1_response.get("content", "") or ""

        if not prev_user_content:
            raise ValueError(
                f"build_incremental_stage1: previous_record.stage1_messages "
                f"contains no user message. "
                f"stage1_messages has {len(prev_s1_messages)} items, "
                f"roles={[m.get('role') for m in prev_s1_messages]}. "
                f"record.meta: {getattr(previous_record, 'meta', '<missing>')!r}"
            )
        prev_diag = getattr(previous_record, "stage1_diagnosis", None) or {}
        if not prev_assistant_content and not (
            isinstance(prev_diag, dict) and prev_diag
        ):
            raise ValueError(
                f"build_incremental_stage1: previous_record.stage1_response "
                f"has no 'content' field. "
                f"stage1_response type={type(prev_s1_response).__name__}, "
                f"keys={list(prev_s1_response.keys()) if isinstance(prev_s1_response, dict) else 'N/A'}. "
                f"record.meta: {getattr(previous_record, 'meta', '<missing>')!r}"
            )

        prev_assistant_content = self._normalize_prev_stage1_assistant_for_incremental(
            previous_record,
            prev_assistant_content,
        )

        prev_user_content = self._inject_market_features_block(prev_user_content, frame)
        prev_user_content = strip_vpa_prompt_sections(prev_user_content)

        prev_reasoning = ""
        if isinstance(prev_s1_response, dict):
            prev_reasoning = str(prev_s1_response.get("reasoning_content") or "")
        preserve_mimo = False
        if provider_settings is not None:
            from pa_agent.ai.mimo_compat import (
                build_assistant_api_message,
                is_mimo_provider,
            )

            preserve_mimo = is_mimo_provider(
                getattr(provider_settings, "base_url", ""),
                getattr(provider_settings, "model", ""),
            )
        if preserve_mimo:
            assistant_turn = build_assistant_api_message(
                prev_assistant_content,
                reasoning_content=prev_reasoning,
            )
        else:
            assistant_turn = {"role": "assistant", "content": prev_assistant_content}

        system_content = self._build_stage1_system_prompt()
        incremental_user_content = self._build_incremental_stage1_continuation_user_prompt(
            frame,
            previous_record,
            new_bar_count,
            analysis_mode=analysis_mode,
            htf_context=htf_context,
        )

        return [
            {"role": "system",    "content": system_content},
            {"role": "user",      "content": prev_user_content},
            assistant_turn,
            {"role": "user",      "content": incremental_user_content},
        ]

    def _stage1_pattern_supplement(self) -> str:
        """Pattern tag table + briefs for Stage 1 (optional via settings)."""
        if self._prompt_settings is not None and not getattr(
            self._prompt_settings, "stage1_inject_pattern_briefs", True
        ):
            return ""
        return f"{STAGE1_DETECTED_PATTERNS_GUIDE}\n\n---\n\n{STAGE1_PATTERN_BRIEFS_BLOCK}"

    def _build_stage1_user_prompt(
        self,
        frame: KlineFrame,
        *,
        analysis_mode: str = "original",
        htf_context: dict[str, Any] | None = None,
    ) -> str:
        """Build the Stage 1 task turn; stage-specific rules stay out of system."""
        pattern_block = self._stage1_pattern_supplement()
        stage1_parts = [
            self._decision_tree_stage1(),
            self._market_diagnosis_framework(),
            self._load("文件16-K线信号识别.txt"),
            *([pattern_block] if pattern_block else []),
            _PA_FACTUAL_CLAIM_RULE,
            _PA_EVIDENCE_OUTPUT_RULE,
            render_pa_theory_rule_catalog(),
            _stage1_output_reminder_for_mode(
                analysis_mode,
                decision_tree_path=self.decision_tree_path,
            ),
        ]
        stage1_context = "\n\n---\n\n".join(p for p in stage1_parts if p)
        kline_table = self._render_kline_table(frame)
        feature_table = self._render_kline_feature_table(
            frame,
            limit=min(_DETAILED_GEOMETRY_BAR_LIMIT, len(frame.bars)),
        )
        simple_features_block = self._render_simple_market_features_block(frame)
        higher_timeframe_block = render_higher_timeframe_context(htf_context)
        n_bars = len(frame.bars)
        if n_bars > 40:
            bg_window = f"**长程背景 K{n_bars}–K41**（较老部分）：\n"
        else:
            bg_window = (
                f"**长程背景**（当前仅 {n_bars} 根，不足 41 根，与近期窗口重叠；"
                "请明确降低背景判断置信度）：\n"
            )
        return (
            "## 阶段一任务\n\n"
            "你现在只执行阶段一：市场诊断与闸门判断。不要评估具体下单、止损、止盈或仓位。\n\n"
            f"{stage1_context}\n\n"
            "---\n\n"
            f"## 当前分析目标\n\n"
            f"品种:{frame.symbol} 周期:{frame.timeframe} K线数量:{n_bars}\n"
            f"（K线序号：1=最新已收盘，最大 K{n_bars}；"
            "每个决策节点的 bar_range 是审计覆盖范围，必须覆盖 reason 内所有 K 引用；"
            f"跨窗口时写完整包络范围，勿超出 K{n_bars}-K1）\n\n"
            f"## ⚠️ 分析窗口分层规则（必须遵守）\n\n"
            f"你收到全部 {n_bars} 根 K 线数据；下列分层与 `市场诊断框架.txt` 使用同一语义：\n\n"
            f"{bg_window}"
            f"- swing 高低点、磁力位参考 → 写入 `htf_context`；§2.2 背景方向\n"
            f"- **禁止**用背景方向否决近期 `direction`；冲突时近期为主、背景作风险参考\n\n"
            f"**近期结构 K{min(40, n_bars)}–K1：**\n"
            f"- `cycle_position`、`direction`、通道/区间/波段主结构\n"
            f"- §2.3 方向与多数结构节点优先参考此窗口；由你结合背景解释\n\n"
            f"**即时惯性 K{min(8, n_bars)}–K1：**\n"
            f"- §2.4 Always In、§2.5 惯性强度、近端 spike_stage / 尖峰识别\n\n"
            f"**即时信号 K{min(10, n_bars)}–K1：**\n"
            f"- 信号棒/入场棒/二次入场/突破失败（阶段二 §9 裁定窗口）\n\n"
            f"**逐棒摘要 K5–K1：**\n"
            f"- `bar_by_bar_summary` **必须**恰好 5 条（窗口≥5 根时），每条 1 句 reason\n\n"
            + (f"{higher_timeframe_block}\n" if higher_timeframe_block else "")
            + f"## K线数据(序号1=最新已收盘K线,序号越大越早;不含当前未收盘K线;"
            f"阳阴列由程序按收盘价与开盘价计算:收盘>开盘=阳线,收盘<开盘=阴线,相等=平)\n\n"
            f"{kline_table}\n\n"
            "## 程序可复算 K线事实(只含数值、方向和精确几何关系；不输出 bar_type、趋势或 Setup；"
            "基于当前 N 根已收盘 K 线，指标非全历史延续)\n\n"
            f"{feature_table}\n\n"
            + (f"{simple_features_block}\n\n" if simple_features_block else "")
            + f"请根据以上数据，严格输出阶段一 JSON 诊断结果。\n\n"
            f"{_stage1_tail_reminder()}"
        )

    def _build_incremental_stage1_user_prompt(
        self,
        frame: KlineFrame,
        previous_record: AnalysisRecord,
        new_bar_count: int,
        *,
        analysis_mode: str = "original",
        htf_context: dict[str, Any] | None = None,
    ) -> str:
        """Build a Stage 1 update turn using the last completed analysis."""
        pattern_block = self._stage1_pattern_supplement()
        stage1_parts = [
            self._decision_tree_stage1(),
            self._market_diagnosis_framework(),
            self._load("文件16-K线信号识别.txt"),
            *([pattern_block] if pattern_block else []),
            _PA_FACTUAL_CLAIM_RULE,
            _PA_EVIDENCE_OUTPUT_RULE,
            render_pa_theory_rule_catalog(),
            _stage1_output_reminder_for_mode(
                analysis_mode,
                decision_tree_path=self.decision_tree_path,
            ),
        ]
        stage1_context = "\n\n---\n\n".join(p for p in stage1_parts if p)
        n_bars = len(frame.bars)
        new_count = max(0, min(new_bar_count, n_bars))
        new_kline_table = self._render_kline_table(frame, limit=new_count)
        new_feature_table = self._render_kline_feature_table(frame, limit=new_count)
        full_kline_table = self._render_kline_table(frame)
        full_feature_table = self._render_kline_feature_table(
            frame,
            limit=min(_DETAILED_GEOMETRY_BAR_LIMIT, n_bars),
        )
        simple_features_block = self._render_simple_market_features_block(frame)
        higher_timeframe_block = render_higher_timeframe_context(htf_context)
        previous_summary = {
            "meta": previous_record.meta.model_dump(),
            "stage1_diagnosis": self._record_context(previous_record.stage1_diagnosis),
            "stage2_decision": self._record_context(previous_record.stage2_decision),
            "strategy_files_used": previous_record.strategy_files_used or [],
        }
        return (
            "## 阶段一增量任务\n\n"
            "你现在只执行阶段一：基于上一轮已完成分析和新增 K 线，更新市场诊断与闸门判断。\n"
            "不要评估具体下单、止损、止盈或仓位；这些留到阶段二。\n\n"
            "增量分析规则：\n"
            "- 先检查上一轮诊断在新增 K 线后是否仍成立。\n"
            "- 如果市场结构未被破坏，可以延续上一轮 cycle_position/direction，但必须用新增 K 线重新说明依据。\n"
            "- 如果新增 K 线出现突破、反转、极端波动或让原结论失效，必须更新诊断。\n"
            "- 必须输出顶层字段 **incremental_delta**（不可省略），结构示例：\n"
            '  "incremental_delta": {"new_closed_bars":["K1"],'
            '"changed_fields":["direction","cycle_position"],'
            '"summary":"相对上一轮：新增K1突破区间上沿，方向由中性转偏多"}\n'
            "- new_closed_bars 长度必须等于「新增已收盘K线」数量（1根则只写 [\"K1\"]）。\n"
            "- 并在 summary / risk_warning / gate_trace 中说明相对上一轮变化。\n"
            "- gate_result=proceed 时 gate_trace 须覆盖 §1.2、§1.3、§2.1、§2.2、§2.3、§2.4、§2.5；程序不替你填写市场语义。\n"
            "- 输出仍必须是完整阶段一 JSON，而不是差异补丁。\n\n"
            f"{_INCREMENTAL_OUTPUT_HARD_RULES}\n\n"
            f"{stage1_context}\n\n"
            "---\n\n"
            f"## 当前分析目标\n\n"
            f"品种:{frame.symbol} 周期:{frame.timeframe} K线数量:{n_bars} 新增已收盘K线:{new_count}\n"
            f"（K线序号：1=最新已收盘，最大 K{n_bars}；"
            "每个决策节点的 bar_range 是审计覆盖范围，必须覆盖 reason 内所有 K 引用；"
            f"跨窗口时写完整包络范围，勿超出 K{n_bars}-K1）\n\n"
            "## 上一轮已完成分析（仅作为延续上下文）\n\n"
            f"```json\n{json.dumps(previous_summary, ensure_ascii=False, indent=2)}\n```\n\n"
            f"## 新增 K线数据(共{new_count}根，序号1=最新已收盘；含阳阴列)\n\n"
            f"{new_kline_table}\n\n"
            f"## 新增程序事实(共{new_count}根；数值与前棒精确关系按完整{n_bars}根窗口计算)\n\n"
            f"{new_feature_table}\n\n"
            f"## 当前完整 K线数据(共{n_bars}根，用于必要时复核整体结构；含阳阴列)\n\n"
            f"{full_kline_table}\n\n"
            f"## 当前完整程序事实(只含数值和精确几何关系，不替代周期判断；"
            f"基于当前 N 根已收盘 K 线，指标非全历史延续)\n\n"
            f"{full_feature_table}\n\n"
            + (f"{higher_timeframe_block}\n" if higher_timeframe_block else "")
            + (f"{simple_features_block}\n\n" if simple_features_block else "")
            + "请基于上一轮结论、新增K线和当前完整K线，严格输出更新后的阶段一 JSON 诊断结果。\n\n"
            f"{_stage1_tail_reminder()}"
        )

    def _build_incremental_stage1_continuation_user_prompt(
        self,
        frame: KlineFrame,
        previous_record: AnalysisRecord,
        new_bar_count: int,
        *,
        analysis_mode: str = "original",
        htf_context: dict[str, Any] | None = None,
    ) -> str:
        """Build the incremental continuation user turn (message [3] in 4-message mode).

        Only sends NEW K-line data; the model can reference the full K-line table
        from the previous Stage 1 user message ([1]) above.
        """
        simple_features_block = self._render_simple_market_features_block(frame)
        if simple_features_block:
            simple_features_block = _MARKET_FEATURES_AUTHORITY_NOTE + simple_features_block
        higher_timeframe_block = render_higher_timeframe_context(htf_context)
        n_bars = len(frame.bars)
        new_count = max(0, min(new_bar_count, n_bars))
        new_kline_table = self._render_kline_table(frame, limit=new_count)
        new_feature_table = self._render_kline_feature_table(frame, limit=new_count)
        previous_summary = {
            "meta": previous_record.meta.model_dump(),
            "stage1_diagnosis": self._record_context(previous_record.stage1_diagnosis),
            "stage2_decision": self._record_context(previous_record.stage2_decision),
            "strategy_files_used": previous_record.strategy_files_used or [],
        }
        return (
            "## 阶段一增量更新任务\n\n"
            "上方是你上一轮完成的阶段一诊断。现在基于新增 K 线，更新诊断与闸门判断。\n"
            "完整 K 线数据已包含在上方阶段一用户消息中（K线序号已重新编号，"
            "K1=当前最新已收盘K线），你可以回溯查看任何历史 K 线。\n\n"
            "⚠ 反锚定要求——这是增量分析最重要的原则：\n"
            "- 不要因为上一轮已得出结论就倾向于延续它；上一轮结论只是参考起点，不是约束。\n"
            "- 如果新增 K 线改变了市场结构（突破、反转、趋势加速/衰竭），必须果断推翻上一轮结论，而非在旧结论上微调。\n"
            "- 判断标准：如果你是第一次看到这组完整 K 线（包括上方历史K线+新增K线），你会得出什么结论？那才是正确结论。\n"
            "- 每次增量更新都应视为一次重新诊断——只是你不必重复描述未变的部分。\n\n"
            "增量分析规则：\n"
            "- 先独立审视完整 K 线数据，形成自己的判断，再与上一轮结论对照。\n"
            "- 如果市场结构确实未被破坏，可以延续上一轮 cycle_position/direction，但必须用新增 K 线重新说明依据。\n"
            "- 如果新增 K 线出现突破、反转、极端波动或让原结论失效，必须更新诊断——宁可过度更新，不可锚定延续。\n"
            "- 若 K1 收盘已突破上一轮 resistance_levels 或跌破 support_levels，必须重算支撑/阻力，"
            "不得原样延续已失效价位；程序只校验价位格式与方向，不替你重算结构位。\n"
            "- 必须输出顶层字段 **incremental_delta**（不可省略），结构示例：\n"
            '  "incremental_delta": {"new_closed_bars":["K1"],'
            '"changed_fields":["direction","cycle_position"],'
            '"summary":"相对上一轮：新增K1突破区间上沿，方向由中性转偏多"}\n'
            "- new_closed_bars 长度必须等于「新增已收盘K线」数量（1根则只写 [\"K1\"]）。\n"
            "- 并在 summary / risk_warning / gate_trace 中说明相对上一轮变化。\n"
            "- gate_result=proceed 时 gate_trace 须覆盖 §1.2、§1.3、§2.1、§2.2、§2.3、§2.4、§2.5；程序不替你填写市场语义。\n"
            "- 输出仍必须是完整阶段一 JSON，而不是差异补丁。\n\n"
            f"{_INCREMENTAL_OUTPUT_HARD_RULES}\n\n"
            f"{_PA_FACTUAL_CLAIM_RULE}\n\n"
            f"{_PA_EVIDENCE_OUTPUT_RULE}\n\n"
            f"{render_pa_theory_rule_catalog()}\n\n"
            f"## 当前分析目标更新\n\n"
            f"品种:{frame.symbol} 周期:{frame.timeframe} K线数量:{n_bars} 新增已收盘K线:{new_count}\n"
            f"（K线序号已重新编号：1=最新已收盘，最大 K{n_bars}；"
            "每个决策节点的 bar_range 是审计覆盖范围，必须覆盖 reason 内所有 K 引用；"
            f"跨窗口时写完整包络范围，勿超出 K{n_bars}-K1）\n\n"
            "## 上一轮已完成分析（仅作为延续上下文）\n\n"
            f"```json\n{json.dumps(previous_summary, ensure_ascii=False, indent=2)}\n```\n\n"
            f"## 新增 K线数据(共{new_count}根，序号1=最新已收盘；含阳阴列)\n\n"
            f"{new_kline_table}\n\n"
            f"## 新增程序事实(共{new_count}根；数值与前棒精确关系按完整{n_bars}根窗口计算)\n\n"
            f"{new_feature_table}\n\n"
            + (f"{higher_timeframe_block}\n" if higher_timeframe_block else "")
            + (f"{simple_features_block}\n\n" if simple_features_block else "")
            + "请基于上方完整K线数据、上一轮结论和新增K线，严格输出更新后的阶段一 JSON 诊断结果。\n\n"
            f"{_stage1_tail_reminder()}"
        )

    # ── Stage 2 ───────────────────────────────────────────────────────────────

    def build_stage2_agent_protocol(
        self,
        *,
        frame: KlineFrame,
        stage1_json: dict[str, Any],
        decision_stance: str,
        previous_record: Any | None,
        enable_next_bar_prediction: bool,
        joint_stage2: bool = False,
    ) -> dict[str, Any]:
        """Publish mature code-generated Stage 2 rules beside routed knowledge."""
        from pa_agent.ai.decision_continuity import (
            build_continuity_context,
            render_continuity_prompt_block,
        )

        continuity = render_continuity_prompt_block(
            build_continuity_context(
                frame=frame,
                stage1_json=stage1_json,
                previous_record=previous_record,
            )
        )
        from pa_agent.util.price_tick import format_breakout_price_hint

        breakout_tick_hint = format_breakout_price_hint(frame)
        previous_prediction = self._render_previous_prediction(previous_record)
        return {
            "contract_version": "joint_stage2_agent_protocol_v1" if joint_stage2 else "pa_stage2_agent_protocol_v1",
            "role": "stage2",
            "semantic_parity_policy": (
                "Apply this contract and every Brooks Router-selected mandatory "
                "knowledge module before selective evidence investigation."
            ),
            "task_boundary": (
                "Evaluate Setup, entry, structural invalidation and targets only "
                "inside validated Stage 1 and the Router manifest. Risk, position "
                "size, Trader Equation and execution remain host authority."
            ),
            "bar_numbering": {
                "newest_closed_bar": "K1",
                "oldest_available_bar": f"K{len(frame.bars)}",
                "unclosed_bar_allowed": False,
            },
            "embedded_rules": [
                _LANGUAGE_ZH_RULE,
                _PA_TERMINOLOGY_ZH,
                _STAGE2_API_TASK_RULE,
                build_decision_stance_guidance(normalize_stance(decision_stance)),
                continuity,
                self._render_transition_guidance(stage1_json),
                breakout_tick_hint or "",
                previous_prediction,
                _stage2_output_contract(decision_tree_path=self.decision_tree_path, joint_stage2=joint_stage2),
                (
                    _NEXT_BAR_PREDICTION_INSTRUCTION
                    if enable_next_bar_prediction
                    else _NEXT_BAR_DISABLED_NOTE
                ),
                _stage2_tail_reminder(),
            ],
        }

    def build_stage2(
        self,
        frame: KlineFrame,
        stage1_json: dict,
        strategy_files: list[str],
        experience_entries: list[Any],
        *,
        decision_stance: str = "conservative",
        htf_context: dict[str, Any] | None = None,
    ) -> list[dict]:
        """Build a standalone Stage 2 request (kept for tests/tools)."""
        system_content = self._build_stage2_system_prompt()
        user_content = self._build_stage2_user_prompt(
            frame=frame,
            stage1_json=stage1_json,
            strategy_files=strategy_files,
            experience_entries=experience_entries,
            decision_stance=decision_stance,
            enable_next_bar_prediction=False,
            htf_context=htf_context,
        )
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

    def _render_previous_prediction(self, previous_record: Any) -> str:
        """Render previous-bar prediction summary for incremental context (R5.2)."""
        if previous_record is None:
            return ""
        # previous_record may be AnalysisRecord or dict-like
        s2 = getattr(previous_record, "stage2_decision", None)
        if s2 is None and isinstance(previous_record, dict):
            s2 = previous_record.get("stage2_decision")
        if not isinstance(s2, dict):
            return ""
        s2 = strip_vpa_fields(s2)
        pred = s2.get("next_bar_prediction")
        if not isinstance(pred, dict):
            return ""

        unpredictable = bool(pred.get("unpredictable", False))
        if unpredictable:
            return (
                "## 上一轮下一根K线预测\n\n"
                "上一轮标记为不可预测；本轮请独立判断。\n"
            )

        direction = pred.get("direction") or "—"
        probs = pred.get("probabilities") or {}
        bull = probs.get("bullish", "?")
        bear = probs.get("bearish", "?")
        neut = probs.get("neutral", "?")
        dir_zh = {"bullish": "阳线", "bearish": "阴线", "neutral": "中性"}.get(direction, direction)
        return (
            "## 上一轮下一根K线预测\n\n"
            f"方向：{dir_zh}（阳 {bull}% / 阴 {bear}% / 中性 {neut}%）。"
            "本轮请基于最新数据独立重新预测，不必延续上轮结论。\n"
        )

    def _normalize_stage1_assistant_for_chain(
        self,
        stage1_json: dict,
        stage1_reply_content: str,
    ) -> str:
        """Compact validated Stage 1 JSON for assistant turn in prefix-chain mode."""
        from pa_agent.ai.json_validator import format_model_json_for_context

        if isinstance(stage1_json, dict) and stage1_json:
            stage1_json = strip_vpa_fields(stage1_json)
            return json.dumps(stage1_json, ensure_ascii=False, indent=2)
        formatted = format_model_json_for_context(stage1_reply_content)
        if formatted:
            return formatted
        return stage1_reply_content or ""

    def build_stage2_continuation(
        self,
        *,
        frame: KlineFrame,
        stage1_messages: list[dict],
        stage1_reply_content: str,
        stage1_json: dict,
        strategy_files: list[str],
        experience_entries: list[Any],
        decision_stance: str = "conservative",
        previous_record: Any | None = None,
        enable_next_bar_prediction: bool = False,
        provider_settings: Any | None = None,
        use_prefix_chain: bool | None = None,
        htf_context: dict[str, Any] | None = None,
        joint_stage2: bool = False,
    ) -> list[dict]:
        """Build Stage 2 messages, optionally chaining after Stage 1 for KV cache.

        Prefix-chain mode (DeepSeek native, default when safe):
          [system, user(S1…), assistant(S1 JSON), user(S2 task only)]

        Standalone mode (OpenClaw Agent and similar):
          [system, user(S2 task + full K-line tables)]
        """
        from pa_agent.ai.deepseek_client import supports_kv_prefix_chain

        if use_prefix_chain is None:
            use_prefix_chain = supports_kv_prefix_chain(provider_settings)

        chain_after_s1 = bool(use_prefix_chain and stage1_messages)
        stage2_user_content = self._build_stage2_user_prompt(
            frame=frame,
            stage1_json=stage1_json,
            strategy_files=strategy_files,
            experience_entries=experience_entries,
            decision_stance=decision_stance,
            previous_record=previous_record,
            enable_next_bar_prediction=enable_next_bar_prediction,
            omit_kline_block=chain_after_s1,
            htf_context=htf_context,
            omit_htf_context=chain_after_s1,
            joint_stage2=joint_stage2,
        )

        if chain_after_s1:
            assistant_content = self._normalize_stage1_assistant_for_chain(
                stage1_json,
                stage1_reply_content,
            )
            chain = [dict(m) for m in stage1_messages]
            while chain and chain[-1].get("role") == "assistant":
                chain.pop()
            chain.append({"role": "assistant", "content": assistant_content})
            chain.append({"role": "user", "content": stage2_user_content})
            return chain

        system_content = self._build_stage2_system_prompt()
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": stage2_user_content},
        ]

    def _build_stage2_user_prompt(
        self,
        *,
        frame: KlineFrame,
        stage1_json: dict,
        strategy_files: list[str],
        experience_entries: list[Any],
        decision_stance: str = "conservative",
        previous_record: Any | None = None,
        enable_next_bar_prediction: bool = False,
        omit_kline_block: bool = False,
        htf_context: dict[str, Any] | None = None,
        omit_htf_context: bool = False,
        joint_stage2: bool = False,
    ) -> str:
        """Build the Stage 2 task turn for standalone or prefix-chain mode."""
        from pa_agent.ai.decision_continuity import (
            build_continuity_context,
            render_continuity_prompt_block,
        )

        stance_block = build_decision_stance_guidance(normalize_stance(decision_stance))
        continuity_ctx = build_continuity_context(
            frame=frame,
            stage1_json=stage1_json,
            previous_record=previous_record,
        )
        continuity_block = render_continuity_prompt_block(continuity_ctx)
        transition_block = self._render_transition_guidance(stage1_json)
        stage2_parts = [
            self._decision_tree_stage2(include_global=not omit_kline_block),
            stance_block,
            continuity_block,
            transition_block,
            *(
                self._load(name)
                for name in stage2_user_task_txt_files(
                    strategy_files,
                    direction=str(stage1_json.get("direction", "") or ""),
                    load_full_strategy_library=self._load_full_strategy_library(),
                )
            ),
        ]
        if experience_entries:
            max_chars = 400
            if self._prompt_settings is not None:
                max_chars = int(
                    getattr(
                        self._prompt_settings,
                        "experience_max_chars_per_entry",
                        400,
                    )
                )
            stage2_parts.append(
                self._render_experience(
                    experience_entries,
                    max_chars_per_entry=max_chars,
                )
            )
        stage2_parts.append(
            _stage2_output_contract(
                decision_tree_path=self.decision_tree_path,
                joint_stage2=joint_stage2,
            )
        )
        if enable_next_bar_prediction:
            stage2_parts.append(_NEXT_BAR_PREDICTION_INSTRUCTION)
        else:
            stage2_parts.append(_NEXT_BAR_DISABLED_NOTE)
        # Static strategy / contract blocks first → better KV prefix reuse across runs.
        stage2_context = "\n\n---\n\n".join(p for p in stage2_parts if p)

        from pa_agent.util.price_tick import format_breakout_price_hint

        n_bars = len(frame.bars)
        breakout_tick_hint = format_breakout_price_hint(frame)
        # Prefix-chain Stage 2 already retains the exact catalog from Stage 1.
        # Repeating it would spend tokens without adding a new factual observation.
        higher_timeframe_block = "" if omit_htf_context else render_higher_timeframe_context(htf_context)
        prev_pred_block = self._render_previous_prediction(previous_record)
        compact_s1 = json.dumps(
            self._compact_stage1_for_stage2(stage1_json), ensure_ascii=False, indent=2
        )

        if omit_kline_block:
            kline_block = (
                "## K线数据\n\n"
                "完整 K 线表与几何特征已包含在上方阶段一用户消息中"
                "（K1=最新已收盘）；程序将复算交易数学。\n\n"
            )
            if breakout_tick_hint:
                kline_block += f"{breakout_tick_hint}\n\n"
        else:
            kline_table = self._render_kline_table(frame)
            feature_table = self._render_kline_feature_table(
                frame,
                limit=min(_DETAILED_GEOMETRY_BAR_LIMIT, n_bars),
            )
            simple_features_block = self._render_simple_market_features_block(frame)
            kline_block = (
                f"## K线数据(共{n_bars}根，含阳阴列；各节点 bar_range 由你据实填写)\n\n"
                f"{kline_table}\n\n"
                "## 程序可复算 K线事实(仅含数值和精确几何关系；不得替代背景解释或交易者方程；"
                "基于当前 N 根已收盘 K 线，指标非全历史延续)\n\n"
                f"{feature_table}\n\n"
            )
            if simple_features_block:
                kline_block += f"{simple_features_block}\n\n"
            if breakout_tick_hint:
                kline_block += f"{breakout_tick_hint}\n\n"

        kline_intro = (
            "完整 K 线表见上方阶段一用户消息。\n\n"
            if omit_kline_block
            else "本消息下方附有完整 K 线表与几何特征。\n\n"
        )
        stage1_block = "" if omit_kline_block else (
            f"## 阶段一诊断结果\n\n```json\n{compact_s1}\n```\n\n"
        )
        return (
            f"{_STAGE2_API_TASK_RULE}\n\n"
            "## 阶段二任务\n\n"
            "你现在独立执行阶段二：交易决策、风险收益和下单方式评估（基于阶段一诊断结果）。\n"
            "以下 JSON 是程序校验通过后的阶段一诊断结果，请以此为权威依据；"
            f"{kline_intro}"
            f"{stage2_context}\n\n"
            "---\n\n"
            f"{stage1_block}"
            + (f"{higher_timeframe_block}\n" if higher_timeframe_block else "")
            + f"{kline_block}"
            f"{prev_pred_block + chr(10) if prev_pred_block else ''}"
            f"请根据以上诊断和K线数据按路由后的 Brooks 分支与 §9–§11、§14 输出 Stage 2 Draft。\n"
            f"注意:如果判断不下单,entry_price、take_profit_price、take_profit_price_2、stop_loss_price、order_direction 必须全部为 null。\n\n"
            f"{_stage2_tail_reminder()}"
        )

    def stage2_system_prompt_only(
        self,
        strategy_files: list[str],
        experience_entries: list[Any],
    ) -> str:
        """Return the shared system prompt used by Stage 2 requests."""
        return self._build_stage2_system_prompt()

    def _compact_stage1_for_stage2(
        self,
        stage1_json: dict,
    ) -> dict:
        """Subset of Stage 1 fields needed for Stage 2 (reduces prompt noise)."""
        keys = (
            "cycle_position",
            "alternative_cycle_position",
            "direction",
            "diagnosis_confidence",
            "spike_stage",
            "climax_risk",
            "market_phase",
            "transition_risk",
            "detected_patterns",
            "key_signals",
            "htf_context",
            "entry_setup",
            "support_levels",
            "resistance_levels",
            "risk_warning",
            "bar_analysis",
            "bar_by_bar_summary",
            "pa_evidence",
            "gate_trace",
            "gate_result",
        )
        return {k: stage1_json[k] for k in keys if k in stage1_json}

    @staticmethod
    def _render_transition_guidance(stage1_json: dict) -> str:
        """Render dynamic risk guidance from Stage 1 market_phase fields."""
        if stage1_json.get("market_phase") != "transitioning":
            return ""
        risk = stage1_json.get("transition_risk") or "medium"
        if risk == "high":
            size = "显著降低 trade_confidence，只接受证据最完整的结构"
            selectivity = "只接受最清晰的二次入场、突破回踩或边界信号"
        elif risk == "medium":
            size = "按歧义程度降低 trade_confidence，放弃弱信号与中部位置"
            selectivity = "选择性入场，放弃弱信号和中间位置"
        else:
            size = "依据实际不确定性小幅降低 trade_confidence"
            selectivity = "保持正常流程，但在 reason 中说明状态转换风险"
        return (
            "## 状态转换期风险指导\n\n"
            f"阶段一判断 market_phase=transitioning，transition_risk={risk}。\n"
            f"- 信号把握：{size}（**禁止**在 JSON 写仓位比例/手数）。\n"
            f"- 入场选择：{selectivity}。\n"
            "- 不因为状态转换而跳过 §9、§10、§14；只是提高信号质量门槛并降低交易频率。"
        )

    @staticmethod
    def _render_experience(
        entries: list[Any],
        *,
        max_chars_per_entry: int = 400,
    ) -> str:
        """Render experience library entries as a text block."""
        lines = [
            "## 经验库(最近案例,供参考)",
            "以下案例仅作对照，**不得**因相似就改变对本图结构/方向的独立判断。",
        ]
        for i, entry in enumerate(entries, 1):
            if isinstance(entry, dict):
                blob = json.dumps(entry, ensure_ascii=False, indent=2)
            elif hasattr(entry, "content"):
                blob = json.dumps(
                    getattr(entry, "content", entry),
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                blob = str(entry)
            if len(blob) > max_chars_per_entry:
                blob = blob[: max_chars_per_entry - 3] + "..."
            lines.append(f"\n### 案例 {i}\n```json\n{blob}\n```")
        return "\n".join(lines)
