"""Prompt assembler for Stage 1 (diagnosis) and Stage 2 (decision)."""
from __future__ import annotations

import functools
import json
import logging
import math
from pathlib import Path
from typing import Any

from pa_agent.ai.fact_catalog import build_analysis_fact_catalog, render_pa_fact_catalog
from pa_agent.ai.decision_stance import build_decision_stance_guidance, normalize_stance
from pa_agent.ai.higher_timeframe import render_higher_timeframe_context
from pa_agent.ai.pattern_routing import (
    STAGE1_DETECTED_PATTERNS_GUIDE,
    STAGE1_PATTERN_BRIEFS_BLOCK,
)
from pa_agent.ai.kline_features import bar_candle_direction_label, compute_kline_geometry_features
from pa_agent.ai.market_features import (
    inject_market_features_section,
)
from pa_agent.ai.pa_theory import render_pa_theory_rule_catalog
from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog, render_vpa_measurements
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

VPA_KNOWLEDGE_MODULES = {
    "core": "wyckoff_vpa/core.txt",
    "trend_pullback": "wyckoff_vpa/trend_pullback.txt",
    "breakout_retest": "wyckoff_vpa/breakout_retest.txt",
    "trading_range_events": "wyckoff_vpa/trading_range_events.txt",
    "climax_absorption_exhaustion": "wyckoff_vpa/climax_absorption_exhaustion.txt",
}

_VPA_STAGE2_OUTPUT_REMINDER_V4 = """
## VPA_STAGE2_DELTA_CONTRACT_V4

阶段一已经给出经校验的 `vpa_context`。本阶段只输出 `vpa_impact`，裁决 VPA 对当前 Entry Attempt 的信息增量，不重做 PA：
- 必须输出 `background_relation`、`entry_timing_relation`、`evidence_used`、`decision_effect`、`reason`。
- 背景得到支持不等于当前时机得到支持；两者必须分别判断。
- `entry_timing_relation=neutral|insufficient_data|supports` 时保持 Brooks baseline；只有 `challenges` 可要求确认、偏好回测、降低现有置信度或拒绝当前尝试。
- 不得修改 `estimated_win_rate`、PA direction、Stage 1 gate 或创建原 baseline 不存在的订单。
- 只能引用 Stage 1 已引用的 evidence ID；解释不得复写行情数字，不得推断参与者身份、主动买卖方或机构意图。
- PA gate 为 `proceed` 且 VPA 数据可用时，即使保持 baseline，`evidence_used` 也必须列出本次裁决实际使用的至少一个 Stage 1 evidence ID。
- Volume/RVOL 的解释只写在 `vpa_impact.reason`；`decision`、`decision_trace`、`bar_analysis` 与预测字段保持 PA price-only，只写 VPA 造成的时机/风险后果和 evidence ID，避免重复计权。
""".strip()

_PAIRED_VPA_CONTEXT_SYSTEM_V4 = """
你是 PA Agent 的 Wyckoff VPA 证据分析器。PA baseline 已由独立调用完成并冻结。

你的职责是解释程序提供的量价证据，并分别判断：
1. VPA 对 PA 背景假设的关系；
2. VPA 对当前 Entry Attempt 时机的关系。

硬约束：
- PA defines. VPA informs. LLM arbitrates. VPA 不是 Brooks Setup 的审批器或第二套路由。
- Evidence first, label last；先写背景、位置、Effort vs Result、序列比较与已收盘 K 线的 acceptance/rejection，再谨慎使用事件标签。
- 只输出一个 JSON object，不复述或修改 PA baseline。
- 只使用目录中的 evidence ID。解释字段不得复写 OHLC、Volume、RVOL、ATR、百分比或价位数字。
- provenance capability 是推断上限。OHLCV 不支持参与者身份、主动买卖方或机构意图归因；unknown/tick/proxy 只支持同源相对活跃度比较。
- 下一步确认只能等待未来已收盘 K 线，不得引用 K0、形成中或未收盘 K 线。
- “收盘靠近本棒高点/低点”只描述单根 K 线内部位置，不等于跨窗口创新高/新低；使用极值事件语言时必须写清比较窗口并引用证据。
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
- 非 `spike` 周期时 `spike_analysis=null`。
- `spike` 周期必须填写 `spike_analysis`：`anchor_bar` 是本轮识别的 Spike 锚点；`post_anchor_state` 只能为 `no_closed_bar_after_anchor`、`continuation_only`、`pause_or_pullback_present`。
- 一旦识别 `pause_or_pullback_present`，必须给出第一根对应的已收盘 K 线，`spike_stage` 不得仍为 `active`，`market_phase` 必须为 `transitioning`。这表达 Brooks 的 Spike 向通道/区间过渡语义，不是程序替代市场判断。
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
- 风险点数、盈亏比、交易者方程、胜率估算等**一律在思考过程或 JSON 字段内心算**；禁止为 `risk=stop-entry` 之类简单算术启动解释器。
- **唯一交付物**：assistant 正文 `content` 中的裸 JSON（阶段一或阶段二 schema）。不得在磁盘上留档后再回复。

违反会导致分析极慢、工具刷屏，且程序无法解析你的输出。
""".strip()

_THINKING_CONTENT_OUTPUT_RULE = """
## 思考与正式输出分离（硬约束，违反则程序判定失败）

启用扩展思考时，**思考区仅用于推演草稿**；**程序只读取 assistant 消息的 `content`（正文）** 做 JSON 校验，**不会**把 `reasoning_content` / 思考流当作阶段结果。

**你必须做到：**
1. 思考可以较长，但思考结束后**必须在 `content` 正文里输出完整、可 `json.loads` 的裸 JSON 对象**（阶段一诊断 JSON 或阶段二决策 JSON）。界面会把思考流与 `content` 正文（撰写回答）都显示在「思考过程」窗口；**正文 JSON 仍必须写在 `content`，不能只在思考里写完。**
2. **禁止**把完整 JSON **只**写在思考里而让 `content` 为空、空白或纯叙述文字。
3. **禁止**在 `content` 里输出 markdown 说明、英文长文分析、或「详见上文思考」——`content` 里**只能**是裸 JSON。
4. 若思考预算较大，请**预留足够 token** 给最终 JSON；宁可压缩思考篇幅，也**不得**省略正文 JSON。

阶段一：`content` = 阶段一诊断 JSON（含 `gate_trace`、`gate_result` 等必填字段）。
阶段二：`content` = 阶段二决策 JSON（含 `decision`、`decision_trace`、`terminal` 等必填字段）。
""".strip()

_STAGE1_TAIL_REMINDER = (
    "【最后一步·必做】思考结束后，立即在 assistant 正文 `content` 输出完整阶段一裸 JSON。"
    "思考请用简体中文并尽量简洁；`content` 不得为空。"
    "禁止调用 exec/Python/写文件等工具。\n"
    "逐棒 reason 不得手写 OHLC、EMA、ATR、比率、价位、百分比、阳阴棒数量；"
    "gate_trace reason 同样不得复写这些事实；两者都不得复制 canonical_value 的 key=value，"
    "只引用 fact ID 后写定性解释；"
    "htf_context 只引用 HTF.H# 并解释背景，不复写高周期数值；"
    "单棒内部位置请明确写“收盘靠近本棒高点/低点”；"
    "逐棒相邻高低只引用 PA.PAIR.K1.K2 至 PA.PAIR.K5.K6；K1 前窗高低只引用 PA.COMPARE.K1.PRIOR5/10/20。\n"
    "若 token 紧张：可缩短思考、将 bar_by_bar_summary 保持 5 根，"
    "但 gate_trace 与 gate_result 必须写在 JSON 末尾且不可省略。"
).strip()

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

_VPA_STAGE2_SCHEMA_MARKER = "__VPA_STAGE2_SCHEMA_EXAMPLE__"
_VPA_STAGE2_SCHEMA_EXAMPLE = """\
  "vpa_impact": {
    "background_relation": "supports|neutral|challenges|insufficient_data",
    "entry_timing_relation": "supports|neutral|challenges|insufficient_data",
    "evidence_used": ["vpa-e001"],
    "decision_effect": "keep_brooks_baseline|require_confirmation|prefer_retest|lower_confidence|reject_current_attempt",
    "reason": "仅说明 VPA 对当前 Entry Attempt 的信息增量，并引用已使用的 evidence ID"
  },
"""
_VPA_STAGE2_TAIL_REMINDER = (
    "VPA 已开启：顶层 `vpa_impact` 必填，且必须含 background_relation、"
    "entry_timing_relation、evidence_used、decision_effect、reason。"
    "VPA 不得修改 estimated_win_rate、阶段一方向或 Gate，也不得创建 Brooks baseline 不存在的交易。"
)

_STAGE2_TAIL_REMINDER = (
    "【最后一步·必做】思考结束后，立即在 assistant 正文 `content` 输出完整阶段二裸 JSON"
    "（含 decision、decision_trace、terminal）。思考用简体中文并尽量简洁；`content` 不得为空。"
    "禁止调用 exec/Python/写文件等工具；算术在 JSON 推理字段内完成。\n"
    "若 token 紧张，优先保证 `content` 有 JSON，可缩短思考。\n"
    "⚠️ 禁止在 content 中只写思考过程或分隔符（如 ---输出JSON---）而不附 JSON——"
    "这会导致校验直接失败。哪怕只输出最小骨架 {\"decision\":{\"order_type\":\"不下单\",...}} 也比没有强。\n\n"
    "【⚠️ 输出前自检 — terminal.outcome 语义规则（在输出 JSON 前逐项确认）：】\n"
    "1. §9.0=否 时，**必须先写 §9.0P** 评估背景限价；仅当 §9.0P 也=否 且无三价方案时，"
    "terminal.outcome=wait（node_id=9.0P 或 9.0）。\n"
    "   **禁止** §9.0=否 后直接 wait 而跳过 §9.0P/§10。\n"
    "   §9.0=是（有合格信号棒）或 §9.0P=是（计划型限价）且有三价 → 继续 §10，不得因缺信号棒直接 wait。\n"
    "   禁止写 reject — 你没有东西可以拒绝（除非 §10.3 已有三价方案）。\n"
    "2. 你有入场方案（entry/stop/target 三价齐全），但 10.3 交易者方程不通过？\n"
    "   → 这才可以写 terminal.outcome=reject，node_id=\"10.3\"。\n"
    "3. 你有入场方案且 10.3 通过？→ terminal.outcome=trade，node_id 为最终节点。\n"
    "   **禁止**写 action/execute/entry 等自创词，只能是 wait|reject|trade|proceed。\n"
    "4. 限价/突破尚未触发？→ entry_bar.freshness=pending（禁止 limit_order_pending 等自创词）。\n"
    "5. `terminal` 与 `decision` **同级**（顶层字段），禁止把 terminal 嵌套在 decision 内。\n"
    "6. `next_cycle_prediction` 在 `unpredictable=false` 时 **必须** 含 `probabilities` 对象（各 cycle 概率，和≈100）。\n"
    "禁止在 JSON 前写「好的」「修改完成」等对话前缀；禁止 ```json 围栏。\n"
    "常见错误速查：§9.0=否 + §10.1=否 → outcome=wait（不是 reject！）"
).strip()

# ── Hardcoded output format reminders ─────────────────────────────────────────

_VPA_STAGE1_SCHEMA_MARKER = "__VPA_STAGE1_SCHEMA_EXAMPLE__"
_VPA_STAGE1_SCHEMA_EXAMPLE = """\
  "vpa_context": {
    "catalog_id": "逐字复制程序 catalog_id",
    "data_status": "available|limited|unavailable",
    "summary": "数据质量、量价上下文与不确定性；不复写数值",
    "observations": [
      {
        "evidence_ids": ["vpa-e001"],
        "finding_kind": "background|effort_result|sequence_comparison|acceptance_rejection|entry_timing",
        "interpretation": "基于 evidence ID 的上下文解释；不复写数值"
      }
    ],
    "background_relation": "supports|neutral|challenges|insufficient_data",
    "entry_timing_relation": "supports|neutral|challenges|insufficient_data",
    "next_confirmation": {
      "state": "pending_closed_bar|not_applicable",
      "trigger": "下一根已收盘 K 线对明确结构锚点的 acceptance/rejection",
      "requires_closed_bar": true
    }
  },
"""
_VPA_STAGE1_TAIL_REMINDER = (
    "VPA 已开启：顶层 `vpa_context` 必填；逐字使用程序 catalog_id/data_status，"
    "observation 只输出 evidence_ids/finding_kind/interpretation，禁止输出 bar_range；"
    "next_confirmation.state 只能是 pending_closed_bar/not_applicable。"
)

_STAGE1_OUTPUT_REMINDER = """
请严格按照以下 JSON 格式输出诊断结果,不要输出任何其他内容。
**硬约束：思考结束后，必须在 assistant 正文 `content` 输出下方完整阶段一 JSON；不得仅在思考区分析而让 `content` 为空。**
**思考过程与 JSON 内所有说明性文字必须使用简体中文**（仅 JSON 键名与规定枚举除外）。
禁止用 markdown 代码围栏（不要写 ```json 或结尾的 ```），只输出裸 JSON 对象。
JSON 字符串内不要用英文双引号强调，改用「」或不用引号。

```json
{
  "cycle_position": "spike|micro_channel|tight_channel|normal_channel|broad_channel|trending_tr|trading_range|extreme_tr|unknown",
  "alternative_cycle_position": null,
  "direction": "bullish|bearish|neutral",
  "diagnosis_confidence": 75,
  "spike_stage": null,
  "climax_risk": "none|warning|triggered",
  "market_phase": "stable|transitioning",
  "transition_risk": null,
  "detected_patterns": [],
  "key_signals": [],
  "htf_context": "",
  "entry_setup": "",
  "support_levels": ["5402", "5119"],
  "resistance_levels": ["6147", "6300"],
  "risk_warning": "",
  "bar_analysis": {
    "always_in": "long|short|neutral",
    "last_closed_bar": "K1",
    "bar_type": "trend_bull|trend_bear|doji|inside|outside_bull|outside_bear|flat|other",
    "signal_bar": {
      "bar": "K2 或 null（无独立信号棒时填 null，禁止整个 signal_bar 为 null）",
      "quality": "strong|medium|weak|invalid（bar=null 时必须填 invalid，禁止填 null）",
      "reason": "信号棒质量判断"
    },
    "entry_setup_type": "H1|H2|L1|L2|MTR|wedge|tr_boundary|breakout_pullback|none",
    "follow_through": "yes|no|pending|failed"
  },
  "bar_by_bar_summary": [
    {
      "bar": "K1",
      "role": "structure|signal|entry|confirmation|noise|trap|climax|test",
      "bar_type": "trend_bull|trend_bear|doji|inside|outside_bull|outside_bear|flat|other",
      "context_effect": "strengthens_bull|weakens_bull|strengthens_bear|weakens_bear|neutral|transition",
      "follow_through": "yes|no|pending|failed",
      "trapped_side": "bulls|bears|both|none|unknown",
      "reason": "一句话说明该K线对当前市场状态的增量影响"
    }
  ],
  "pa_evidence": {
    "fact_refs": ["PA.WINDOW.K5", "PA.BAR.K1"],
    "theory_rule_ids": ["BROOKS.CYCLE.SPIKE_TO_CHANNEL"],
    "spike_analysis": null
  },
__VPA_STAGE1_SCHEMA_EXAMPLE__
  "gate_trace": [
    {
      "node_id": "1.2",
      "question": "是否能识别出当前市场周期？",
      "answer": "是",
      "reason": "K线结构特征清晰，可识别为正常通道",
      "branch": "normal_channel",
      "section": "K线识别",
      "bar_range": "K12-K1"
    },
    {
      "node_id": "2.1",
      "question": "近期结构是否呈现明确惯性方向？",
      "answer": "是",
      "reason": "LH+LL 结构清晰",
      "branch": "bearish",
      "section": "方向判断",
      "bar_range": "K8-K1"
    }
  ],
  "gate_result": "proceed"
}
```

## 阶段一闸门（二元决策树 §1–§2，必须执行）

在输出诊断 JSON 前，按《二元决策.txt》与内置提示文本**依次**评估以下节点，并写入 gate_trace：
**当 gate_result=proceed 时，必须包含节点 1.2、1.3、2.1、2.2、2.3、2.4、2.5 共 7 条**（每条须填 bar_range；中间节点 reason 可留空，见下）。
§1：程序的数据前置检查已经通过，不输出 §1.1；由你评估 1.2 识别周期 → 1.3 极端混乱。
- **节点 1.2**：answer 用 是/否；识别出的周期类型写在 **branch**（如 `broad_channel`、`trading_range`），**禁止** branch 写 `yes`/`no`。
§2：由你依次评估 2.1 惯性方向 → 2.2 大时间框架 → 2.3 当前方向 → 2.4 Always In → 2.5 惯性强度（**answer 只能用 是/否/中性**；方向或 AIL/AIS 写在 branch，勿写「多头」「空头」作 answer）。

**§2.5 重要说明：§2.5 answer=否/中性 ≠ gate_result=wait。**
- §2.5 answer=否 或 answer=中性：均只代表惯性不足、不做激进趋势跟踪，**阶段二分析必须继续进入**，gate_result 必须为 **proceed**。
- **禁止**因 §2.5 判断惯性不足（否/中性）而将 gate_result 设为 wait——这是最常见的错误。
- gate_result=wait 只在以下情况下才成立：§1.2 无法识别周期（unknown）、§1.3 **answer=是**且判断为极端混乱（extreme_tr）。
- **校验硬规则**：gate_result=wait/unknown 时，最后节点只能是 §1.2 的「否/等待」，或 §1.3 的「是」（确认极端混乱）。
- §2.5 答"否"或"中性"时，gate_result 仍为 proceed，阶段二切换为"等待反弹/回撤到位后的顺势信号"策略，并在 watch_points 中明确触发条件。

**禁止在阶段一评估：**
- **0.3**（交易者方程仅为原则；数值检验在阶段二 **10.3**）
- **§9–§11**（入场、风险、下单均属阶段二）

**逐K摘要硬规则：**
- 必须输出 `bar_by_bar_summary`，**恰好 5 条**（分析窗口≥5根时），覆盖最近 **K5–K1** 每一根已收盘 K 线各 1 条。数据不足 5 根则覆盖全部已有 K 线（每条 1 根）。
- 每条只写该 K 线对当前结构的增量作用，不写下单价格、不写止损止盈。
- `role` 只能使用示例中的英文枚举（structure/signal/entry/confirmation/noise/trap/climax/test 等）；延续/跟随棒统一写 `confirmation`，不要写 `continuation`。
- **`reversal_attempt`、`mtr`、`h2`、`l2` 等形态标签只能写在顶层 `detected_patterns` 数组中；禁止用作 `bar_by_bar_summary[].role`。**
- K线序号方向：K1 是最新已收盘，K2 是它前一根；判断 K2 的后续跟随时看 K1，判断 K3 的后续跟随时看 K2/K1；K1 的跟随通常为 pending。
- 程序几何表只提供实体、影线、收盘位置、ATR 比值和与前棒的精确包含/重叠关系，**不提供 `bar_type` 结论**。
- `bar_type` 由你结合几何事实与市场背景解释；`inside` / `outside_*` 必须符合程序给出的精确包含关系，`doji` / `trend_*` 等语义标签不可只靠单一固定阈值机械决定。
- 先引用 K 线与程序事实 ID，再给出标签；`reason` 只写定性解释，不复写程序表中的数值、计数或 canonical key=value，不得自行抄算、扩展窗口或伪造程序未提供的事实。若多种解释均合理，使用 `other` 或在 `reason` 中说明歧义。
- `context_effect` 必须使用 **strengthens_bull / strengthens_bear**（带 s），禁止写 strengthen_bull、strengthen_bear。

规则：
- answer 只能是：是 / 否 / 中性 / 等待 / 不适用（**禁止**写「部分」「待确认」「待定」等——部分一致用 **中性**，尚需下一根K线确认用 **等待**）
- **gate_result=wait/unknown 的合法触发条件只有两个：§1.2 answer≠是（无法识别周期）或 §1.3 answer=是（极端混乱 extreme_tr）。** §2.1/§2.5 答「否」或「中性」、§2.3 中性、方向不明等**均不得**设 gate_result=wait——须 proceed 并在阶段二提高门槛或 wait 下单。
- §6/§9/§10 等阶段二节点的"否"答案不代表阶段一闸门阻断。
- gate_result=proceed 表示可通过闸门进入阶段二；wait/unknown 表示不应进入策略与下单评估（仅 §1.2/§1.3 可触发）
- gate_trace 与 cycle_position、direction 不得矛盾
- **每个未跳过节点 reason 必填**：用一句短句写该节点的独立依据，禁止留空或填 `"—"`；勿重复整段总论。
  - 阶段一 **gate_trace 最后一条**还须说明「闸门通过/进入阶段二」或具体等待原因。
  - 阶段二 **§10.3** 交易者方程须写方程用到的入场/止损/目标数字或胜率假设。
- bar_range、question、answer 仍必填；七个阶段一语义节点均由你输出，程序只校验格式、引用范围与内部一致性
- **禁止在 gate_trace 中输出 node_id 为 "14.1" 的节点**：§14 属于阶段二，阶段一输出会导致校验失败
- **禁止在 gate_trace 中输出 `gate_end` / `gate_summary` / `summary` 等汇总节点**：`gate_result` 字段已表达 proceed/wait/unknown；闸门通过说明写在 **最后一条真实节点**（通常是 §2.5）的 reason 中，answer 只能用 是/否/中性/等待/不适用
- 节点 2.4 / 2.5 的 question 须与决策树原文一致（含 Always In 空格、用「支持」而非仅改措辞）

**每条 gate_trace / decision_trace 必须包含 bar_range（K线依据，由你自行判断）：**
- **程序不会替你填写**；你必须根据「本节点实际引用了哪些 K 线」写出序号范围
- 格式：`K{较老序号}-K{较新序号}` 或单根 `K1`（**序号1=最新已收盘**，序号越大越早）
- **⚠️ bar_range 禁止出现 K0**：K0 是当前未收盘棒，不在 frame 中。如需讨论"下一根K线"请写在 reason 中，bar_range 只能引用 K1~K{max}
- `bar_range` 必须忠实覆盖该节点实际引用的证据；不同节点若确实使用同一窗口可以相同，禁止为了形式差异而虚构范围
- 区间格式必须为 **K{较老}-K{较新}**（如 K4-K1），**禁止** K1-K4；单根写 K1；全图分析可写「全局」（程序会展开）
- **reason 里写到的每一根 K 线**（如「K4 之后」「对比 K2」）都必须落在该条 **bar_range** 内；勿在 bar_range=K2 的 reason 里单独提 K4——应写 **K4-K2** 或 **K4-K1**，或 reason 只谈 K2
- 方向/分类类节点（如 4.2 上涨还是下跌）：**answer 只用 是/否/中性**，方向写在 **branch**（bullish/bearish），勿写「上涨」「下跌」作 answer
- **6.2**（区间类型）：answer=是/否，branch=trending_tr 或 trading_range；勿把「趋势型交易区间」写在 answer
- **6.3**（是否在边界）：answer=是/否，branch=lower/upper/middle；勿写「是，在下边界」——应写 answer=是、branch=lower
- **阶段二 §14（禁止行为扫描，仅 decision_trace）**：`answer=是` = **触犯**禁止项；`answer=否` = **未触犯**。禁止用 `是` 表示「扫描完成/通过」。阶段一 gate_trace **不得**输出 §14/14.1。
- **禁止照抄**本提示 JSON 示例里的占位文字或说明中的举例数字；必须对应当前 K 线表与你在 reason 中的分析
- 跳过节点（skipped:true）：answer=不适用，bar_range 填字符串 `不适用`（**禁止填 null**）
- question 只写问题本身，不要把 bar_range 写进 question

diagnosis_confidence 必须为 0-100 的整数(满分100),表示对 cycle_position 等诊断结论的综合置信评分。
禁止使用 high、medium、low 等字符串;分数越高表示对当前市场状态判断越有把握。

打分必须相对当前证据校准：证据越完整、相互一致且替代解释越少，分数越高；
证据冲突、关键位置不清或多种周期解释并存时降低。不要把固定分数段机械映射为交易动作，
也不要从 `diagnosis_confidence` 自动推导 `trade_confidence`。

**support_levels / resistance_levels 填写规则：**
- `support_levels`：从近期 K 线结构中识别出的**当前价格下方**支撑价位，按由近到远排列，最多 3 个。每项填价格字符串（如 `"5402"` 或 `"5380-5400"` 表示区间），不识别时填空数组 `[]`。
- `resistance_levels`：从近期 K 线结构中识别出的**当前价格上方**阻力价位，按由近到远排列，最多 3 个。格式同上，不识别时填空数组 `[]`。
- **突破后必须更新**：若 K1 收盘已跌破原支撑（支撑价位 ≥ 收盘价），该支撑不得继续保留，应改填**当前收盘价下方**最近的有效摆动低点/结构位；若 K1 收盘已突破原阻力（阻力价位 ≤ 收盘价），该阻力不得继续保留，应改填**当前收盘价上方**最近的有效摆动高点/结构位。增量分析时禁止照抄上一轮 support/resistance。
- 填写依据：近期摆动高低点、通道边界、EMA、前期整数关口、突破/失败突破位。**禁止**填写远离当前价格超过长程结构窗口波动幅度的历史高低点。
- 若市场处于 `extreme_tr` 或无法识别周期，允许填 `[]`。""".strip()

_STAGE2_OUTPUT_CONTRACT = """
请严格按照以下 JSON 格式输出决策结果，不要输出任何其他内容。
**硬约束：思考结束后，必须在 assistant 正文 `content` 输出下方完整阶段二 JSON；不得仅在思考区分析而让 `content` 为空。**
**思考过程与 JSON 内所有说明性文字必须使用简体中文**（仅 JSON 键名与规定枚举除外）。
禁止用 markdown 代码围栏（不要写 ```json 或结尾的 ```），只输出裸 JSON 对象。
JSON 字符串内不要用英文双引号强调，改用「」或不用引号。
重要规则：当 order_type 为“不下单”时，entry_price、take_profit_price、take_profit_price_2、stop_loss_price、order_direction 必须全部为 null。

```json
{
  "decision": {
    "order_direction": "做多|做空|null（禁止写 bearish/bullish/short/long）",
    "order_type": "限价单|突破单|市价单|不下单",
    "entry_price": null,
    "entry_basis_bar": null,
    "entry_basis_extreme": null,
    "entry_rule": null,
    "take_profit_price": null,
    "take_profit_price_2": null,
    "stop_loss_price": null,
    "reasoning": "",
    "diagnosis_confidence": 75,
    "diagnosis_confidence_reasoning": "",
    "trade_confidence": 70,
    "trade_confidence_reasoning": "",
    "estimated_win_rate": 50,
    "estimated_win_rate_reasoning": "",
    "key_factors": [],
    "watch_points": [],
    "risk_assessment": "",
    "invalidation_condition": ""
  },
  "bar_analysis": {
    "signal_bar": {
      "bar": "K2 或 null（计划型挂单尚无已收盘信号棒时为 null）",
      "quality": "strong|medium|weak|invalid",
      "pattern": "H1|H2|L1|L2|MTR|wedge|tr_boundary|breakout_pullback|none",
      "reason": "信号棒质量判断"
    },
    "entry_bar": {
      "bar": "K1 或 null（计划尚未触发时）",
      "strength": "strong|weak|not_triggered",
      "follow_through": true,
      "still_valid": true,
      "freshness": "fresh|pending|stale|invalid"
    },
    "second_entry": {
      "is_second_entry": true,
      "type": "H2|L2|MTR|wedge|tr_boundary|trendline|none（is_second_entry=false 时必须填 none，禁止 null）"
    }
  },
__VPA_STAGE2_SCHEMA_EXAMPLE__
  "decision_trace": [
    {
      "node_id": "4.1",
      "section": "通道",
      "question": "是否出现有序波段结构？",
      "answer": "是",
      "reason": "HH+HL",
      "skipped": false,
      "bar_range": "由你填写"
    }
  ],
  "terminal": {
    "node_id": "11.2",
    "outcome": "trade",
    "label": "..."
  }
}
```

说明：decision_trace 需输出完整决策路径（通常多条）；每条 trace 的 **bar_range 必须由你根据该节点实际使用的 K 线填写**，不得照抄示例。
**⚠️ bar_range 禁止出现 K0**（K0 是未收盘棒，不在 frame 中；如需讨论下一根 K 线写在 reason 里）。
**每条 trace 的 answer 只能是以下五选一**：`是`、`否`、`中性`、`等待`、`不适用`。
禁止写「部分符合」「部分是」「上涨通道」等；模糊或分类细节写在 **reason**（方向类节点可另填 **branch**）。

阶段二沿用同一冻结快照中阶段一的 `cycle_position`、`direction`、`bar_analysis.always_in` 与 K1 `bar_type`，**不再重复输出 `diagnosis_summary`，也不得输出 §2.3 重判方向**。若阶段二发现足以质疑阶段一方向的反证，本轮必须 `order_type=不下单`，把反证写入 `risk_assessment` / `watch_points`，并等待下一份已收盘快照重新执行阶段一；不得在阶段二直接生成相反方向订单。

## 阶段二决策路径（二元决策树 §3–§11、§14）

阶段一 gate_result=proceed 时，decision_trace 必须遵守**执行顺序**（可跳过不适用分支，但不可乱序）：

1. **§3–§8** 按 cycle_position 走对应结构分支（尖峰/通道/区间/反转/楔形等）
2. **§9 执行顺序（硬规则）**：§9.0（信号棒）→ 若否则 **§9.0P（背景限价，必填）** → §9.1–§9.7 中适用于当前路径的节点。
   - **§9.0=否 不是终局**；必须评估 §9.0P 后才能决定 wait。
   - **§9.0P=是** → 继续 §10/§11 尝试限价单；三价写入 decision，**禁止**只在 watch_points 写触发价。
3. **§9 的信号质量、形态、跟随、二次入场和时机均由你结合背景判断**并写入 decision_trace。程序只验证 K 线引用、价格算术和输出自洽，不用单棒阈值替你裁决。
4. **§10** 风险收益（必须按序）：**10.1 止损明确 → 10.2 止损不过大 → 10.3 交易者方程**（勿编造具体手数、合约数或资金规模）
5. **§11 下单方式由你根据 Setup、位置和触发条件选择**；程序只校验订单字段、引用关系、三价方向、数学与执行安全。真实 tick size 由交易接口最终校验。
6. **§14** 禁止行为清单：下单前快速扫描，触犯任一条 → order_type=不下单
   - **⚠️ §14 answer 语义硬规则（违反会校验失败）：**
     - `answer=是` = **触犯了禁止行为**（此时必须输出 order_type=不下单）
     - `answer=否` = **未触犯任何禁止项**（可以继续下单）
   - **未触犯时必须写 `answer=否`**，不能写 `是`。许多 AI 误用 `是` 表示"已完成扫描"，这是错误的。
   - 例：扫描完成、无触犯 → `{"node_id":"14","answer":"否","reason":"扫描§14：未触犯任何禁止项。①...②..."}`
   - 例：触犯了宽通道追突破 → `{"node_id":"14","answer":"是","reason":"触犯：宽通道中追突破，放弃入场，order_type=不下单"}`

**交易者方程（10.3）规则：**
- 必须使用 **decision 中已填写的 entry_price / stop_loss_price / take_profit_price** 做数值计算（**take_profit_price_2 不参与 §10.3**），**禁止**用 K 线收盘、信号棒极点间距或「计划中的 1.8 点/3 点」代替三价
- **突破单须先定 entry 再定 stop/target**：entry 必须在引用 K 线极点之外，再用三价做 10.3。程序会复算三价方向、RR、交易者方程和极点关系，**不会静默改价**；真实 tick size 不在快照中时不得冒充已知。
- `decision_trace[10.3].reason` 中的入场/止损/目标数字必须与 `decision` 三价一致（勿用未写入 decision 的中间价）
- 做多：风险点数 = entry − stop，回报点数 = take_profit_price − entry；做空：风险 = stop − entry，回报 = entry − take_profit_price
- 盈亏比 = 回报 ÷ 风险（程序与界面只认此公式；reasoning 中写的 RR 必须与三价一致，否则校验失败）
- **结构定价优先**：entry、TP1、TP2 与 stop 都必须有各自的结构依据；RR 是校验结果，不是调价目标。**禁止**为凑 RR 而缩小 TP1、扩大止损或把 stop 贴进噪音。
- **定价顺序（推荐）**：
  1. 定 **entry**（结构位/边界/回撤位或突破极值±跳动）
  2. 定 **take_profit_price（TP1）** 于最近有效结构目标（通道对边、区间对侧、前 swing 等）
  3. 定 **take_profit_price_2（TP2）** 于更远结构目标（Measured Move、通道对边远端、区间翻测等）
  4. 定 **stop_loss_price** 于结构失效位之外；若引用“极点外 1 跳”原则，须注明真实 tick size 未确认、执行前校验
  5. 若按结构定价得到 RR **≥ 1.0**：保留有结构依据的三价，再检验 §10.3；不得为了让 RR 靠近某个数值而移动 stop。
  6. 若结构 stop 导致 RR < 1.0：只可改选另一个仍有效的 entry、目标或失效结构；不能为通过方程扩大止损或贴近噪音。仍无法 ≥1.0 → reject。
- **TP1 / TP2 硬规则**：
  - 有下单时 `take_profit_price` 与 `take_profit_price_2` **均必填**；不下单时均为 null
  - 做多：stop < entry < take_profit_price < take_profit_price_2
  - 做空：take_profit_price_2 < take_profit_price < entry < stop
  - §10.3 交易者方程与 RR 校验**仅使用 take_profit_price（TP1）**；TP2 不得用于方程计算
- 有下单时：盈亏比须 **≥ 1.0**（回报÷风险），且须满足 **胜率%×回报 > (100−胜率)%×风险**
- 不满足上述任一条 → **10.3 必须判「否」**，order_type=**不下单**，不得输出限价/突破/市价单
- **10.3 通过之前**不得输出具体下单类型；**10.3 之后**才写 §11
- 因方程不通过而放弃（已有三价方案）：terminal.node_id 应为 **10.3**，outcome=**reject**（有方案可拒，不用 wait）
- 完成 10.3 后，必须把你在方程中使用的**胜率主观估计**写入 decision.estimated_win_rate（0–100 整数），并在 estimated_win_rate_reasoning 简要说明依据；**禁止**留空或仅从 trace 文字里暗示。它是当前 PA Setup 的可复盘主观先验，不是已验证统计胜率；不得用固定数字、单次样本或外部辅助证据的单次确认机械上调。

**结构型止损 / 止盈质量规则（防止噪音内小单）：**
- `stop_loss_price` 必须放在「本笔交易假设真正失效」的结构位之外，而不是为了通过 10.3 方程而贴近 EMA、K1 low/high、整数位或单根 K 线内部噪音。
- 若止损只是在 EMA / 支撑 / 阻力外侧很近的位置，且没有越过明确 swing low/high、信号棒极点、通道边界失效位或区间边界失效位，则视为「噪音内止损」；§10.1 或 §10.2 应判「否」。
- `take_profit_price`（TP1）应放在有结构依据的最近有效目标位，不要为了通过方程而选 K1 内部噪音位
- `take_profit_price_2`（TP2）应为更远但有结构依据的目标（MM 投影、通道对边远端、区间高度翻测等）；必须满足做多 tp2>tp1、做空 tp2<tp1
- 若结构止损合理但 RR < 1.0：只能改选另一组仍有结构依据的 entry、目标或失效位；不得把 stop 收紧进噪音，也禁止向外扩 stop；若仍 < 1.0 或方程不通过 → `order_type=不下单`
- 计划型限价单只有在「结构失效位」和「目标结构位」都清晰时才可执行；宽通道 / 区间边界 setup 只是允许进入评估，不代表必须下单。

**计划型限价优先级（背景与周期 > 独立信号棒）：**
- 阶段一 `gate_result=proceed` 且 `cycle_position` 为 broad_channel / trading_range / normal_channel / trending_tr 时，**默认先评估计划型限价（§9.0P）**。
- **无独立信号棒（signal_bar.bar=null）** → §9.0=否，**必须**继续 §9.0P；若 §9.0P=是 则给出限价三价。
- `direction=neutral`、K1 为 doji/inside/弱棒、或 `transition_risk=medium` **单独出现**时，仍应尝试 §9.0P 边界/回撤限价；仅当 **§10.1–10.3 无法通过** 或 **§14 触犯** 时才 `不下单`。
- 禁止以「等下一根 K 确认信号棒」为由跳过 §9.0P——计划型限价本来就是等价格到位。
- 仅当同时满足：**区间/通道中部（6.3=middle）**、**无结构锚点定 stop**、**K1 已穿过计划 entry/stop**、或 **barbwire 且无边界锚点** 时，才可在 §9.0P 判否。

**低质量计划型限价降级规则（已弱化 — 仅作风控提醒，非默认不下单）：**
- 以下情形**倾向降低 trade_confidence**，但**不自动**改为 `不下单`；若 10.3 通过且结构清晰，仍应输出限价单并在 reasoning 说明接受的瑕疵：neutral 方向、transition_risk 偏高、diagnosis_confidence<50、K1 弱棒无跟随、方程边际通过。
- 只有 **10.3 不通过** 或 **§14 触犯** 时，才必须 `order_type=不下单`。

**突破单不可用时的限价单备选路径（重要）：**
- 当通道/趋势结构默认倾向突破单，但**当前没有合格突破入场**（信号棒失效、无跟随、极点不清晰、无法填写 entry_basis_bar/extreme、突破已错过等）时，**不要直接输出「不下单」**。
- 若结构方向仍清晰，且能在**支撑/阻力/通道边界/EMA/前棒极点**附近设定限价 entry，并能给出清晰的**结构失效止损**与**有效结构目标**，且 **10.3 交易者方程可通过（数学期望为正）** → **应尝试 `order_type=限价单`**。
- 限价备选典型场景：顺势回撤到结构位做多/做空、区间边界反弹/回落、宽通道靠边界挂单、突破测试失败后的反向结构位。
- 限价单 `entry_basis_*` 可填 null；`signal_bar.bar` 可为 null（quality=invalid），须在 **§9.0P** 说明「计划型限价，等待回撤/反弹到位」；`entry_bar` 设 not_triggered/pending。
- 仅当**突破与限价两种路径均无法**给出满足 §10.1–10.3 的三价方案时，才 `order_type=不下单`。

**§9.0P 计划型限价（宽通道/区间/通道边界 — 无合格信号棒时的正式路径）：**
- **§9.0** 只评「是否已有合格收盘信号棒」；无信号棒 → §9.0=否，**必须**写 **§9.0P**。
- 当 cycle_position 为 **broad_channel / trading_range / normal_channel / trending_tr**，且价格靠近 **支撑/阻力/通道边界**（阶段一 support_levels/resistance_levels），或顺势 **回撤/反弹到结构位** 可挂限价时：
  - **§9.0P 应判「是」**，reason 写明「计划型限价，等待回撤/反弹到位」；
  - §9.0 同时写「否」（无合格信号棒）；
  - `signal_bar.bar` 为 **null**，`quality=invalid` 或 **weak**；
  - `entry_bar` 设 `strength=not_triggered`、`freshness=pending`；
  - 继续 §10 定三价 → 10.3 通过 → `order_type=限价单`。
- **禁止**因 K1 为 doji/弱棒/无跟随，就 §9.0P=否 后直接 `不下单`——应先尝试结构位限价。
- **§9.0P=否/等待**：区间/通道 **中部**、`barbwire`/重叠区无边界锚点、无结构锚点定 stop、当前收盘已接受在失效结构之外且没有收回证据、或 §14 触犯。
- **direction=neutral 时**：§9.0P 默认 **wait**（禁止双边边界挂单）；仅 §9.0=是 且有合格顺向信号棒时可下单。

**宽通道 vs Always In（硬规则）：**
- **禁止一切逆势**；宽通道仅顺 direction / Always In 一侧。
- AIS 下宽通道上边界**禁止做多**；AIL 下宽通道下边界**禁止做空**。

**楔形 / 三推（硬规则）：**
- **末端楔形 / 楔形反转 / 三推递减**（与主趋势同向）→ 意味反转*可能*发生：**禁止追顺势**、**禁止逆势三价**；`order_type=不下单`，仅 `watch_points`。
- **楔形回撤**（与主趋势相反的楔形）→ 突破确认后可评估**顺主趋势**；若同时 `climax_risk` 或末端三推特征，**禁止追单**。

**限价单时点解释**：
- 新订单是在当前快照之后提交；K1 的历史 high/low 触达只能作为背景证据，不能机械等同于未来成交或新计划失效。
- 结合收盘接受/拒绝、结构是否仍成立和当前可执行价格，判断该限价是等待未来回撤、已经错过，还是应重新定价。
- 程序没有实时 bid/ask 与 K1 盘中路径，不会替你裁决限价是否仍新鲜；结论须在 §9.0P 与 reasoning 中说明。

**突破单 entry_price 硬规则（区分显示精度与真实 tick size）：**
- order_type="突破单" 时，必须填写 decision.entry_basis_bar、decision.entry_basis_extreme、decision.entry_rule。
- 做多突破单：entry_basis_extreme 必须为 "high"，`entry_price` 必须严格大于引用棒 high；做空必须为 "low"，`entry_price` 必须严格低于引用棒 low。
- 若程序只提供 OHLC 显示小数增量，可用它生成计划价，但必须理解这不是交易场所已确认的 tick size；执行接口仍需按真实品种规格复核。
- **做空突破单 basis 必须是 low**：即使叙事是「反弹至高点做空」，突破单仍挂在依据 K 的 **低点下方** 突破位；禁止写 `entry_basis_extreme="high"`（与「限价在高点附近做空」不同）。
- entry_rule 只写挂单位置规则（如「严格位于 K1 高点上方；真实 tick size 未确认，执行前校验」），**禁止**在 entry_rule 里重复 order_type/方向或写 `entry_price=` 公式串。
- 突破单禁止使用 K 线实体中部、收盘价、EMA 或「约等于高点」作为 entry_price。
- 若无法从 K 线表确定依据 K 的 high/low，不得输出突破单；若只缺真实 tick size，可输出严格位于极点外的计划价并在 `entry_rule` 说明执行前须校验，不得声称 tick 已确认。
- 限价单/市价单不使用 entry_basis_* 字段，可填 null。

**§9 逐K信号链与新鲜度硬规则：**
- §9.0–§9.7 必须引用 `bar_analysis.signal_bar.bar` 与阶段一 `bar_by_bar_summary` 中的对应 K 线；计划型限价时 `signal_bar.bar` 为 null，须在 **§9.0P**（非 §9.0）写明依据；`quality="invalid"`、`pattern="none"`。若限价单/突破单尚未触发，`bar_analysis.entry_bar.bar` 可为 null，但必须设 `strength="not_triggered"`、`freshness="pending"`，并在 9.7 写明“等待触发，尚无入场棒”。
- **⚠️ 市价单 entry_bar 硬规则**：`order_type="市价单"` 代表基于当前已收盘棒立即入场，**不存在「等待触发」状态**。`entry_bar.bar` 必须填写信号棒（通常为 K1），`strength` 设为 `strong` 或 `weak`，`freshness` 设为 `fresh`，`follow_through` 设为 `true`。**禁止**市价单将 `entry_bar.bar` 填为 null 或将 `freshness` 填为 `pending`——这会导致校验失败。
- **K 线序号约定**：K 数字越大表示越早的已收盘棒（K8 早于 K1）。信号棒通常比入场棒更早，故 signal_bar 的 K 序号 **大于** entry_bar 的 K 序号（例：K3 信号 → K1 入场）。
- 如果信号棒之后已经出现 2–3 根无跟随、反向强 K、或 `entry_bar.freshness=stale|invalid`，不得继续把旧信号当作新的突破单依据。
- 如果最新 K1 是 doji、弱入场棒、无跟随或反向确认，应降低 trade_confidence；但若 **计划型限价边界 setup（§9.0P=是）** 且周期/方向/结构位一致，**仍应继续 §10 并尝试限价单**，不要仅因 K1 不完美就 `不下单`。
- 当 `bar_analysis.signal_bar.quality=weak|invalid`，或已触发入场棒但 `entry_bar.follow_through=false` 时，若仍下单，必须在 §9 和 reasoning 中明确说明为何该弱点未使信号失效；否则应等待。挂单未触发时不得把 `follow_through=false` 当作失败跟随，应写 `pending`。
- **计划型限价单**：quality=weak|invalid 且 entry_bar 为 pending 时，**不视为**必须观望；须在 **§9.0P** 判「是」并说明结构位/setup 依据。

**⚠️ watch_points 与 stage1 risk_warning 一致性规则（必须遵守）：**
- 阶段一 `risk_warning` 是风险警示，**watch_points 中的触发条件不得与其直接矛盾**。
- 典型违反：risk_warning 说"在 4435–4440 底轨区域不宜追空"，watch_points 却建议"下破 4438 追空"——这是在 risk_warning 明确警示的区域做 risk_warning 禁止的操作。
- **写 watch_points 前必须回顾 stage1 risk_warning**：如果你的触发条件恰好落在 risk_warning 描述的风险区域，必须在 watch_points 里注明该风险或修改触发条件以避开冲突区域。
- 如果有充分依据认为 risk_warning 的风险在阶段二已经消除，必须在 reasoning 里明确说明原因。

**⚠️ detected_patterns 必须引用规则：**
- 阶段一 `detected_patterns` 中识别出的每一个形态（如 `failed_signal`、`magnet`、`breakout_test`、`breakout_failure`）都与当前交易风险直接相关。
- **阶段二 reasoning 和 watch_points 中必须明确引用 detected_patterns 中的形态**，说明它们对本次交易决策的影响（支持还是否定入场，或设为 watch 条件）。
- 不得从头重新推理而完全忽略 detected_patterns 中已识别的形态。

**跳过规则：**
- 无持仓：跳过 §12、§13（不写 trace）
- 不适用分支：skipped:true，answer=不适用

terminal 必须与 order_type 一致（**decision 与 decision_trace 同步**）：
- 有下单 → outcome=trade，10.3 必须为「是」，decision 含有效三价
- 不下单 → outcome=wait 或 reject，order_type=不下单，三价与 order_direction 均为 null
- **禁止** decision 写突破单/限价单/市价单，同时 decision_trace 里 10.3=否 或 terminal=reject
- **§14 是禁止行为扫描，不是成交终局节点**：若 §14 answer=否（未触犯）且有下单，terminal.node_id 应填最终 §11 下单节点（如 `11.2`/`11.3`）或 `10.3`，**禁止**填 `14`/`14.1`；只有 §14 answer=是（触犯）时才可作为拒绝/等待的终止原因，且必须 `order_type=不下单`。

**⚠️ terminal.node_id 和 outcome 的语义规则（必须区分以下两种情形）：**

情形 A：**有入场计划，但交易者方程不通过**（有具体止损、止盈数字，但盈亏比不达标）
→ `terminal.node_id = "10.3"`，`outcome = "reject"`
→ 典型表现：10.3 trace 里有具体数值计算，方程结果为负

情形 B：**§9.0=否 且 §9.0P=否**（或 §10.1=否 因无止损锚点）
→ `terminal.node_id = "9.0P"`（或 9.0），`outcome = "wait"`
→ **不能** terminal 在 10.3，因为从未有过可评估的交易方案
→ **不能** 写 outcome="reject"——拒绝一个不存在的方案在语义上是无意义的

常见错误：§9.0=否 → **跳过 §9.0P** → §10.1=否 → terminal=10.3/reject
正确做法：§9.0=否 时**必须先写 §9.0P**；仅当 §9.0P 也=否（或 §10.1 因无锚点=否）→ terminal.node_id=**9.0P**（或 9.0），outcome=**wait**；10.3 不应出现在 trace 里（或标 skipped=true）。**禁止**在未评估 §9.0P 时因 §9.0=否 直接 terminal=9.0。

阶段一 gate_result 为 wait/unknown 时：系统会短路，不应调用本阶段。

**decision.reasoning 字数硬限制：≤280 字（中文）**。只写结论 + 1–2 个关键依据（周期/信号/为何不下单或为何入场），细节放在 decision_trace 的 §10.3 或 key_factors，勿重复推演整棵决策树。

置信度分为两部分，各自独立打分（均为 0–100 整数，必须填写）：

一、diagnosis_confidence —— 对市场趋势与市场周期判断的把握
按周期证据完整度、方向一致性、替代解释数量和 HTF 冲突程度相对校准；不使用固定分档触发交易动作。
diagnosis_confidence_reasoning：必须简要说明打分依据（如“trending_tr 与 normal_channel 特征重叠，HTF 方向与小框架不一致”）

二、trade_confidence —— 对交易决策本身的把握
按 Setup 完整度、位置、触发质量、失效位清晰度、结构目标和主要反证相对校准。
该分数描述当前交易判断，不代替 §10.3，也不应为了越过用户配置阈值而上调。
trade_confidence_reasoning：必须简要说明打分依据（如“入场信号明确但止损空间偏大，risk:reward 仅 1.5:1”）

三、estimated_win_rate —— 对**本笔交易方案**成交后获利概率的主观估计（0–100 整数）
- 与 trade_confidence **不是同一概念**：trade_confidence 是对「是否该做这笔决策」的把握；estimated_win_rate 是「若按该 entry/stop/target 成交，你认为获胜的概率」
- **必须在 §10.3 交易者方程评估完成后**由你自行判断并填写；须与 10.3 节点 reason 中的胜率假设一致
- order_type=「不下单」时：estimated_win_rate 填 **null**，estimated_win_rate_reasoning 填 **null**（无交易方案，无胜率可估）
- 有下单时：estimated_win_rate 为 **必填整数**（不要填区间字符串，取你判断的最可能值，如 47）
estimated_win_rate_reasoning：必须简要说明依据（如“宽通道顺势 Low1，结构支持约 45–50%，取 47% 用于方程”）
""".strip()

# ── Analysis-mode–aware Stage 1 output rule ───────────────────────────────────


def _stage1_output_reminder_for_mode(
    analysis_mode: str = "original",
    *,
    include_vpa: bool = False,
) -> str:
    """Return Stage 1 output rules (same for original and optimized).

    Data preflight handles §1.1; the LLM owns all seven semantic gate nodes.
    """
    _ = analysis_mode  # reserved for future mode-specific tweaks
    vpa_schema = _VPA_STAGE1_SCHEMA_EXAMPLE if include_vpa else ""
    return _STAGE1_OUTPUT_REMINDER.replace(_VPA_STAGE1_SCHEMA_MARKER, vpa_schema)


def _stage1_tail_reminder(*, include_vpa: bool) -> str:
    if not include_vpa:
        return _STAGE1_TAIL_REMINDER
    return f"{_STAGE1_TAIL_REMINDER}\n{_VPA_STAGE1_TAIL_REMINDER}"


def _stage2_output_contract(*, include_vpa: bool) -> str:
    vpa_schema = _VPA_STAGE2_SCHEMA_EXAMPLE if include_vpa else ""
    return _STAGE2_OUTPUT_CONTRACT.replace(_VPA_STAGE2_SCHEMA_MARKER, vpa_schema)


def _stage2_tail_reminder(*, include_vpa: bool) -> str:
    if not include_vpa:
        return _STAGE2_TAIL_REMINDER
    return f"{_STAGE2_TAIL_REMINDER}\n{_VPA_STAGE2_TAIL_REMINDER}"


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

# txt files merged into each stage prompt (order preserved)
COMMON_SYSTEM_STAGE1_TXT_FILES: tuple[str, ...] = (
    "提示词大纲_人设与思维方式.txt",
    "二元决策.txt",           # unified with Stage 2 for prefix caching; §0–§2 gate subset is included
)
COMMON_SYSTEM_STAGE2_TXT_FILES: tuple[str, ...] = (
    "提示词大纲_人设与思维方式.txt",
    "二元决策.txt",
)
# Back-compat alias for UI helpers that list “common” files (Stage 2 full tree).
COMMON_SYSTEM_PROMPT_TXT_FILES: tuple[str, ...] = COMMON_SYSTEM_STAGE2_TXT_FILES

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
    return [*COMMON_SYSTEM_STAGE1_TXT_FILES, *STAGE1_TASK_PROMPT_TXT_FILES]


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
        *stage2_user_task_txt_files(
            strategy_files,
            direction=direction,
            load_full_strategy_library=load_full_strategy_library,
        ),
    ]


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
    ) -> None:
        self._prompt_dir = prompt_dir
        self._experience_reader = experience_reader
        self._prompt_settings = prompt_settings
        self._vpa_evidence_catalog = vpa_evidence_catalog
        self._txt_cache: dict[str, str] = {}

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
        """Create an isolated assembler for a paired A/B arm."""
        return PromptAssembler(
            self._prompt_dir,
            self._experience_reader,
            prompt_settings=prompt_settings,
            vpa_evidence_catalog=vpa_evidence_catalog,
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
        modules = ["core"] if include_core else []
        if not isinstance(stage1_json, dict):
            return modules
        cycles = {
            str(stage1_json.get(key, "") or "").lower()
            for key in ("cycle_position", "alternative_cycle_position")
        }
        patterns = {
            str(item).strip().lower()
            for item in stage1_json.get("detected_patterns", []) or []
        }
        if cycles.intersection(
            {"micro_channel", "tight_channel", "normal_channel", "broad_channel", "trending_tr"}
        ):
            modules.append("trend_pullback")
        if patterns.intersection(
            {
                "breakout_test",
                "breakout_pullback",
                "breakout_failure",
                "failed_breakout",
            }
        ):
            modules.append("breakout_retest")
        if cycles.intersection({"trading_range", "trending_tr", "extreme_tr"}):
            modules.append("trading_range_events")
        if (
            str(stage1_json.get("climax_risk", "") or "").lower() in {"warning", "triggered"}
            or str(stage1_json.get("spike_stage", "") or "").lower() in {"ending", "transitioning"}
        ):
            modules.append("climax_absorption_exhaustion")
        return modules

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

    def _render_vpa_measurements_block(self, frame: KlineFrame) -> str:
        if not self._vpa_enabled():
            return ""
        try:
            catalog = self._vpa_evidence_catalog
            if catalog is None:
                catalog = build_vpa_evidence_catalog(frame)
            return wrap_vpa_prompt_block(render_vpa_measurements(catalog=catalog))
        except Exception as exc:  # noqa: BLE001
            logger.warning("render_vpa_measurements failed: %s", exc)
            return ""

    def _vpa_stage2_output_reminder(self) -> str:
        if not self._vpa_enabled():
            return ""
        return wrap_vpa_prompt_block(_VPA_STAGE2_OUTPUT_REMINDER_V4)

    def _record_context(self, value: Any) -> Any:
        if self._vpa_enabled():
            return value or {}
        return strip_vpa_fields(value or {})

    # ── Process-level system-prompt cache ────────────────────────────────────
    # DeepSeek KV Cache hits require the *prefix* of consecutive requests to
    # be byte-identical.  System prompts are fully static (persona + txt files)
    # and never change during a session, so we cache them at the process level.
    # Stage 1 and Stage 2 share one system blob so S1→S2 prefix matches.

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
        """Persona + full binary decision tree (both stages)."""
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

    @staticmethod
    def _render_simple_market_features_block(frame: KlineFrame) -> str:
        """Render the canonical price-only fact catalog."""
        try:
            catalog = build_analysis_fact_catalog(frame)
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

    @staticmethod
    def _inject_market_features_block(prompt: str, frame: KlineFrame) -> str:
        """Refresh or insert program market-features into a Stage 1 user prompt."""
        block = PromptAssembler._render_simple_market_features_block(frame)
        if not block:
            return prompt
        return inject_market_features_section(prompt, block)

    # ── Stage 1 ───────────────────────────────────────────────────────────────

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

    def build_paired_vpa_context(
        self,
        frame: KlineFrame,
        baseline_stage1: dict[str, Any],
        *,
        htf_context: dict[str, Any] | None = None,
    ) -> list[dict]:
        """Build the focused VPA sidecar call for a frozen paired baseline."""
        if not self._vpa_enabled():
            raise ValueError("paired VPA context requires VPA to be enabled")
        baseline_keys = (
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
            "gate_result",
        )
        frozen_baseline = {
            key: baseline_stage1[key]
            for key in baseline_keys
            if key in baseline_stage1
        }
        knowledge = self._load_vpa_rule_prompt(stage1_json=baseline_stage1)
        measurements = self._render_vpa_measurements_block(frame)
        market_facts = self._render_simple_market_features_block(frame)
        higher_timeframe = render_higher_timeframe_context(htf_context)
        user_content = (
            "## PAIRED_VPA_CONTEXT_TASK\n\n"
            "以下 PA baseline 已校验并冻结。不得重新输出或修改其中任何字段。\n\n"
            f"```json\n{json.dumps(frozen_baseline, ensure_ascii=False, indent=2)}\n```\n\n"
            + (f"{higher_timeframe}\n\n" if higher_timeframe else "")
            + (f"{market_facts}\n\n" if market_facts else "")
            + (f"{knowledge}\n\n" if knowledge else "")
            + (f"{measurements}\n\n" if measurements else "")
            + """输出且只输出：
{
  "vpa_context": {
    "catalog_id": "逐字复制程序 catalog_id",
    "data_status": "逐字复制程序 data_status",
    "summary": "仅写数据质量、核心上下文解释与不确定性，不复写数字",
    "observations": [
      {
        "evidence_ids": ["仅使用目录中的 ID"],
        "finding_kind": "background | effort_result | sequence_comparison | acceptance_rejection | entry_timing",
        "interpretation": "只写上下文推断与竞争解释，不复写数字"
      }
    ],
    "background_relation": "supports | neutral | challenges | insufficient_data",
    "entry_timing_relation": "supports | neutral | challenges | insufficient_data",
    "next_confirmation": {
      "state": "pending_closed_bar|not_applicable",
      "trigger": "未来已收盘 K 线需要验证的 acceptance/rejection",
      "requires_closed_bar": true
    }
  }
}

不要输出 `bar_range`，程序会从 evidence IDs 唯一生成；不要输出任何其他顶层字段。
数据可用时至少给出一项真正的 effort/result、序列比较、acceptance/rejection 或 timing 发现，不能只复述 PA。"""
        )
        return [
            {"role": "system", "content": _PAIRED_VPA_CONTEXT_SYSTEM_V4},
            {"role": "user", "content": user_content},
        ]

    def build_paired_vpa_stage2(
        self,
        baseline_messages: list[dict[str, Any]],
        baseline_stage2: dict[str, Any],
        frame: KlineFrame,
        stage1_json: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Append VPA evidence to the exact baseline Stage-2 request prefix."""
        if not baseline_messages:
            raise ValueError("paired VPA Stage 2 requires baseline messages")
        messages = [dict(message) for message in baseline_messages]
        while messages and messages[-1].get("role") == "assistant":
            messages.pop()
        user_index = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if messages[index].get("role") == "user"
            ),
            None,
        )
        if user_index is None:
            raise ValueError("paired baseline Stage 2 has no user message")
        context = stage1_json.get("vpa_context")
        if not isinstance(context, dict):
            raise ValueError("paired VPA Stage 2 requires validated vpa_context")
        knowledge = self._load_vpa_rule_prompt(stage1_json=stage1_json)
        measurements = self._render_vpa_measurements_block(frame)
        addon = (
            "\n\n---\n\n## PAIRED_VPA_DECISION_ADDENDUM\n\n"
            "上方内容是 A 组原始 Brooks Stage 2 请求，保持完全有效。现在仅增加以下 VPA sidecar；"
            "由你融合裁决当前 Entry Attempt 的时机与可信度，VPA 不是独立 Gate。\n\n"
            + (f"{knowledge}\n\n" if knowledge else "")
            + (f"{measurements}\n\n" if measurements else "")
            + "## 已校验 vpa_context\n\n"
            + f"```json\n{json.dumps(context, ensure_ascii=False, indent=2)}\n```\n\n"
            + "## 冻结的 Brooks Stage 2 baseline\n\n"
            + f"```json\n{json.dumps(baseline_stage2, ensure_ascii=False, indent=2)}\n```\n\n"
            + f"{self._vpa_stage2_output_reminder()}\n\n"
            "以上 baseline 已通过程序校验。你现在输出差量 JSON：必须输出 `vpa_impact`。"
            "若 decision_effect=keep_brooks_baseline，只输出 `vpa_impact`；若确需改变当前尝试，"
            "再额外输出完整的 `decision`、`decision_trace`、`terminal` 三项，程序会把它们合并到 baseline 后"
            "执行原 Stage 2 Schema、交易者方程与安全校验。不得输出 diagnosis_summary、bar_analysis 或预测字段。"
            "`vpa_impact.reason` 只引用 Stage 1 已用 evidence ID，"
            "不得复写行情数字。VPA 不得修改 `estimated_win_rate`；其影响只能落入现有 trade_confidence、"
            "watch_points、risk_assessment、entry timing 与最终 order decision。若 baseline 为不下单，"
            "VPA 不得创造交易；若 decision_effect=keep_brooks_baseline，行为字段必须逐项保持 baseline。"
            "先区分 PA 背景是否得到支持与当前 Entry Attempt 是否已到时机：背景支持并不自动等于立即执行。"
            "require_confirmation / prefer_retest 必须实际改写 entry_rule、entry_price、watch_points、"
            "invalidation_condition 或 order_type 中至少一个时机字段，不能只改置信度。"
            "禁止裸写高量、低量、放量或缩量；若必须描述相对量能，须明确对应 evidence 中的比较器。"
        )
        messages[user_index]["content"] = str(messages[user_index].get("content") or "") + addon
        return messages

    def _normalize_prev_stage1_assistant_for_incremental(
        self,
        previous_record: AnalysisRecord,
        raw_content: str,
    ) -> str:
        """Use validated diagnosis JSON in incremental context, not prose/markdown replies."""
        from pa_agent.ai.json_validator import format_model_json_for_context

        diag = getattr(previous_record, "stage1_diagnosis", None) or {}
        if isinstance(diag, dict) and diag:
            if not self._vpa_enabled():
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
        if not self._vpa_enabled():
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
            *(self._load(name) for name in STAGE1_TASK_PROMPT_TXT_FILES),
            *([pattern_block] if pattern_block else []),
            _PA_FACTUAL_CLAIM_RULE,
            _PA_EVIDENCE_OUTPUT_RULE,
            render_pa_theory_rule_catalog(),
            self._load_vpa_rule_prompt(),
            _stage1_output_reminder_for_mode(
                analysis_mode,
                include_vpa=self._vpa_enabled(),
            ),
        ]
        stage1_context = "\n\n---\n\n".join(p for p in stage1_parts if p)
        kline_table = self._render_kline_table(frame)
        feature_table = self._render_kline_feature_table(frame)
        simple_features_block = self._render_simple_market_features_block(frame)
        vpa_measurements_block = self._render_vpa_measurements_block(frame)
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
            + (f"{vpa_measurements_block}\n\n" if vpa_measurements_block else "")
            + f"请根据以上数据，严格输出阶段一 JSON 诊断结果。\n\n"
            f"{_stage1_tail_reminder(include_vpa=self._vpa_enabled())}"
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
            *(self._load(name) for name in STAGE1_TASK_PROMPT_TXT_FILES),
            *([pattern_block] if pattern_block else []),
            _PA_FACTUAL_CLAIM_RULE,
            _PA_EVIDENCE_OUTPUT_RULE,
            render_pa_theory_rule_catalog(),
            self._load_vpa_rule_prompt(),
            _stage1_output_reminder_for_mode(
                analysis_mode,
                include_vpa=self._vpa_enabled(),
            ),
        ]
        stage1_context = "\n\n---\n\n".join(p for p in stage1_parts if p)
        n_bars = len(frame.bars)
        new_count = max(0, min(new_bar_count, n_bars))
        new_kline_table = self._render_kline_table(frame, limit=new_count)
        new_feature_table = self._render_kline_feature_table(frame, limit=new_count)
        full_kline_table = self._render_kline_table(frame)
        full_feature_table = self._render_kline_feature_table(frame)
        simple_features_block = self._render_simple_market_features_block(frame)
        vpa_measurements_block = self._render_vpa_measurements_block(frame)
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
            + (f"{vpa_measurements_block}\n\n" if vpa_measurements_block else "")
            + "请基于上一轮结论、新增K线和当前完整K线，严格输出更新后的阶段一 JSON 诊断结果。\n\n"
            f"{_stage1_tail_reminder(include_vpa=self._vpa_enabled())}"
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
        vpa_rule_prompt = self._load_vpa_rule_prompt()
        vpa_measurements_block = self._render_vpa_measurements_block(frame)
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
            + (f"{vpa_rule_prompt}\n\n" if vpa_rule_prompt else "")
            + (f"{simple_features_block}\n\n" if simple_features_block else "")
            + (f"{vpa_measurements_block}\n\n" if vpa_measurements_block else "")
            + "请基于上方完整K线数据、上一轮结论和新增K线，严格输出更新后的阶段一 JSON 诊断结果。\n\n"
            f"{_stage1_tail_reminder(include_vpa=self._vpa_enabled())}"
        )

    # ── Stage 2 ───────────────────────────────────────────────────────────────

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
            omit_vpa_core=False,
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
        if not self._vpa_enabled():
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
            if not self._vpa_enabled():
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
            omit_vpa_core=chain_after_s1,
            htf_context=htf_context,
            omit_htf_context=chain_after_s1,
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
        omit_vpa_core: bool = False,
        htf_context: dict[str, Any] | None = None,
        omit_htf_context: bool = False,
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
            stance_block,
            continuity_block,
            transition_block,
            self._load_vpa_rule_prompt(
                stage1_json=stage1_json,
                include_core=not omit_vpa_core,
            ),
            self._vpa_stage2_output_reminder() if omit_vpa_core else "",
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
            _stage2_output_contract(include_vpa=self._vpa_enabled())
        )
        if enable_next_bar_prediction:
            stage2_parts.append(_NEXT_BAR_PREDICTION_INSTRUCTION)
        else:
            stage2_parts.append(_NEXT_BAR_DISABLED_NOTE)
        stage2_parts.append(
            _build_next_cycle_prediction_instruction(enable_next_bar=enable_next_bar_prediction)
        )
        # Static strategy / contract blocks first → better KV prefix reuse across runs.
        stage2_context = "\n\n---\n\n".join(p for p in stage2_parts if p)

        from pa_agent.util.price_tick import format_breakout_price_hint

        n_bars = len(frame.bars)
        breakout_tick_hint = format_breakout_price_hint(frame)
        # Prefix-chain Stage 2 already retains the exact catalog from Stage 1.
        # Repeating it would spend tokens without adding a new factual observation.
        vpa_measurements_block = "" if omit_kline_block else self._render_vpa_measurements_block(frame)
        higher_timeframe_block = "" if omit_htf_context else render_higher_timeframe_context(htf_context)
        prev_pred_block = self._render_previous_prediction(previous_record)
        compact_s1 = json.dumps(
            self._compact_stage1_for_stage2(
                stage1_json,
                include_vpa_context=not omit_vpa_core,
            ),
            ensure_ascii=False,
            indent=2,
        )

        if omit_kline_block:
            kline_block = (
                "## K线数据\n\n"
                "完整 K 线表与几何特征已包含在上方阶段一用户消息中"
                "（序号按当前分析窗口编号，K1=最新已收盘）。"
                "阶段二须结合该表与下方阶段一诊断 JSON 做交易者方程与定价。\n\n"
            )
            simple_features_block = self._render_simple_market_features_block(frame)
            if simple_features_block:
                kline_block += (
                    _MARKET_FEATURES_AUTHORITY_NOTE
                    + simple_features_block
                    + "\n\n"
                )
            if vpa_measurements_block:
                kline_block += f"{vpa_measurements_block}\n\n"
            if breakout_tick_hint:
                kline_block += f"{breakout_tick_hint}\n\n"
        else:
            kline_table = self._render_kline_table(frame)
            feature_table = self._render_kline_feature_table(frame)
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
            if vpa_measurements_block:
                kline_block += f"{vpa_measurements_block}\n\n"
            if breakout_tick_hint:
                kline_block += f"{breakout_tick_hint}\n\n"

        kline_intro = (
            "完整 K 线表见上方阶段一用户消息。\n\n"
            if omit_kline_block
            else "本消息下方附有完整 K 线表与几何特征。\n\n"
        )
        return (
            f"{_STAGE2_API_TASK_RULE}\n\n"
            "## 阶段二任务\n\n"
            "你现在独立执行阶段二：交易决策、风险收益和下单方式评估（基于阶段一诊断结果）。\n"
            "以下 JSON 是程序校验通过后的阶段一诊断结果，请以此为权威依据；"
            f"{kline_intro}"
            f"{stage2_context}\n\n"
            "---\n\n"
            f"## 阶段一诊断结果\n\n```json\n"
            f"{compact_s1}"
            f"\n```\n\n"
            + (f"{higher_timeframe_block}\n" if higher_timeframe_block else "")
            + f"{kline_block}"
            f"{prev_pred_block + chr(10) if prev_pred_block else ''}"
            f"请根据以上诊断和K线数据,按《二元决策.txt》§3–§11、§14 输出 JSON 决策结果"
            f"(含 decision_trace 与 terminal)。\n"
            f"注意:如果判断不下单,entry_price、take_profit_price、take_profit_price_2、stop_loss_price、order_direction 必须全部为 null。\n\n"
            f"{_stage2_tail_reminder(include_vpa=self._vpa_enabled())}"
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
        *,
        include_vpa_context: bool = True,
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
        compact = {k: stage1_json[k] for k in keys if k in stage1_json}
        if include_vpa_context and self._vpa_enabled() and "vpa_context" in stage1_json:
            compact["vpa_context"] = stage1_json["vpa_context"]
        return compact

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
