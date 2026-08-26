---
lifecycle: active
capability: not-applicable
verification_scope: not-verified
最后核验: 2026-08-26
核验提交: f8fffc0
权威来源:
  - CONTEXT.md
  - pa_agent/orchestrator/two_stage.py
  - docs/architecture/ADR-0017-deterministic-facts-and-execution-semantics.md
  - docs/architecture/ADR-0018-evidence-context-and-provider-drafts.md
---

# 分析链路

本页描述当前链路的输入、操作、判断权、输出和失败边界。字段级行为以源码、Provider Contract 和 [CONTEXT.md](../../CONTEXT.md) 为准。

## 1. AnalysisRequest 与身份解析

| 项目 | 内容 |
| --- | --- |
| 输入 | source、venue、symbol、timeframe、模式和调用策略 |
| 操作者 | [`pa_agent/data/factory.py`](../../pa_agent/data/factory.py) 与 instrument adapter |
| 程序判断 | provider、实际 symbol、venue、market、timezone、instrument type/contract units 是否一致 |
| LLM 判断 | 无 |
| 输出 | `ResolvedInstrumentIdentity` 与稳定任务身份 |
| 失败 | 身份无法精确解析或 Paper contract spec 不匹配时 `unavailable_input` |

一次分析内身份不可漂移。多资产任务身份同时绑定 source、venue、symbol、timeframe、Snapshot 和 cohort。

## 2. 冻结 Snapshot Bundle

| 项目 | 内容 |
| --- | --- |
| 输入 | 已解析身份、主周期配置、可选 HTF、volume provenance |
| 操作者 | [`snapshot_bundle.py`](../../pa_agent/data/snapshot_bundle.py)、[`higher_timeframe.py`](../../pa_agent/data/higher_timeframe.py)、[`volume_context.py`](../../pa_agent/data/volume_context.py) |
| 程序判断 | 已收盘 K 线、时序、新鲜度、同源身份、HTF 可用性、成交量类型和 instrument spec |
| LLM 判断 | 无 |
| 输出 | `AnalysisSnapshotBundle`、`DataQualityReport`、`market_snapshot_hash` |
| 失败 | 缺少关键 K 线、同 timestamp OHLC 冲突、主周期/HTF 来源漂移时 `unavailable_input` |

Snapshot 一旦排队即冻结；分析期间 GUI 暂停网络刷新，Headless 不重新抓取替换。

## 3. 确定性 Evidence Context

| 项目 | 内容 |
| --- | --- |
| 输入 | 冻结主周期/HTF OHLCV 与 volume provenance |
| 操作者 | [`fact_catalog.py`](../../pa_agent/ai/fact_catalog.py)、[`market_features.py`](../../pa_agent/ai/market_features.py)、[`higher_timeframe.py`](../../pa_agent/ai/higher_timeframe.py)、[`vpa_measurements.py`](../../pa_agent/ai/vpa_measurements.py) |
| 程序判断 | 精确 OHLC、ATR/EMA 等测量、窗口极值、推进方向、相对量和 seasonality 可用性 |
| LLM 判断 | 这些事实在当前 Background/Location 下意味着什么 |
| 输出 | 可引用 Evidence Catalog/Graph 与 Prompt facts |
| 失败 | 事实不完整时标记 unavailable/insufficient，不机械补标签；关键数据契约失败则 `unavailable_input` |

程序生成多种可复算证据和候选上下文，不预先决定趋势、推动/回调、Spring、UTAD、Absorption 或 Setup。

## 4. PA Stage 1 Draft、物化与 Gate

| 项目 | 内容 |
| --- | --- |
| 输入 | price-only Snapshot facts、Brooks 核心知识、Context 模块和 Provider Contract |
| 判断者 | LLM 判断市场背景、周期位置、Always-In、主/替代假设、方向和 Gate 所需专业语义 |
| 程序操作 | Draft v2 Schema、证据身份、明确 OHLC 事实、权限校验；单一 materializer 生成静态 trace 元数据和 Gate |
| 输出 | 已验证 Stage 1、pattern keys、`gate_result` |
| 失败 | 可修复契约问题最多一次定向修复；仍不可信为 `invalid_model_output` |

Gate 不通过时短路 Stage 2。Brooks Router 只读取已验证枚举和 pattern keys，不从自由文本或程序指标反推策略。

## 5. PA Stage 2 Draft

| 项目 | 内容 |
| --- | --- |
| 输入 | 冻结 Stage 1、PA price-only facts、唯一 Brooks Router 选择的知识和历史经验 |
| 判断者 | LLM 判断 Brooks Setup、是否尝试入场、订单类型/方向、entry、结构 stop、TP1、可选 TP2、风险理由和未校准胜率先验 |
| 程序操作 | 验证 Draft Contract、Evidence 引用、明确价格事实、方向/价格几何和权限 |
| 输出 | 不可变的 Stage 2 model candidate |
| 失败 | 一次定向修复仍失败为 `invalid_model_output`；不得用 normalizer 猜测订单或改价 |

Stage 2 不读取 VPA。`entry_setup`、Setup 质量、结构止损和入场时机仍是 LLM 专业判断，不由固定 ATR/RR 阈值代替。

## 6. Materialization、Trader Equation 与执行语义

| 项目 | 内容 |
| --- | --- |
| 输入 | 已验证 Stage 2 candidate、真实 tick/contract/cost facts |
| 操作者 | [`pa_provider_contracts.py`](../../pa_agent/ai/pa_provider_contracts.py)、[`execution_policy.py`](../../pa_agent/ai/execution_policy.py) |
| 程序判断 | 风险、TP1 回报、RR、盈亏平衡胜率、期望值、价格几何、执行资格和安全 |
| LLM 判断 | 不再新增；使用 Stage 2 已给出的未校准胜率先验和结构判断 |
| 输出 | terminal、Trader Equation、独立 `execution_disposition` |
| 失败 | 非法订单保持不可执行并记录原因；程序不改写原 candidate |

Paper 或未来执行器只读取 effective execution semantics。TP2 是可选研究扩展，不影响 TP1 方程和合法 candidate 存在性。

## 7. 唯一 PA Handoff

| 项目 | 内容 |
| --- | --- |
| 输入 | 已验证 Stage 1/Stage 2、Snapshot identity、PA 已引用窗口 |
| 操作者 | [`compile_pa_handoff(...)`](../../pa_agent/ai/pa_handoff.py) |
| 程序操作 | 复制 PA 语义并补充订单数学、稳定身份和 PA 已引用窗口的数值位置 |
| 输出 | `PaHypothesisSet` |
| 禁止 | 新增 Setup、位置标签、方向、VPA 含义或交易结论 |

这是 PA 到 VPA/Joint 的唯一角色交接对象，防止两条平行 handoff 漂移。

## 8. VPA Shadow Evidence Analyst

| 项目 | 内容 |
| --- | --- |
| 输入 | `PaHypothesisSet`、独立 volume facts、按 PA Context 注入的 VPA 知识 |
| 判断者 | LLM 比较 Background、Location、Effort/Result、Leg/Wave、acceptance/rejection 和 competing hypotheses |
| 程序操作 | Evidence identity、bar range、明确量价事实、权限和 sequence chronology 校验 |
| 输出 | `VpaEvidenceReport`：support/challenge/neutral/insufficient 与后续确认条件 |
| 失败 | 保留有效 PA，记录 warning、`completed_with_warnings` 或 `degraded_pa_only` |

VPA 不是审批 Gate。它不能创建 Setup、翻转方向、修改 `cycle_position`/`gate_result`、订单价格或 `estimated_win_rate`。正式结果仍是 PA Stage 2。

## 9. 隔离 A/A/B/B 与 Joint

| 项目 | 内容 |
| --- | --- |
| 输入 | 已落盘、可重放的生产 Shadow Snapshot 与同一冻结输入 |
| 操作者 | [`pa_agent/experiments/`](../../pa_agent/experiments/) 独立进程 |
| 模型调用 | A1/A2 为 PA-only；B1/B2 为相同 PA baseline + VPA + 有界 Joint Arbiter |
| 程序操作 | 调用前登记、四独立客户端、ABBA/BAAB、租约、cohort、去重和统计 |
| 输出 | 四臂 artifact、A/A、B/B、配对 A-B、ITT/per-protocol 和 Paper episode |
| 失败 | 研究失败不影响正式 record；已有四臂而 Paper 暂时失败时只重放 artifact |

Joint 只能在已有 PA candidate 内给出有界 timing/confidence patch，不复制完整 Stage 2，不创建订单价格、Setup 或胜率。生产请求 `soft` 收敛为 `shadow`。

## 10. 持久化、Paper 与运行投影

| 项目 | 内容 |
| --- | --- |
| 输入 | 正式 PA、可选 VPA、调用/校验/资源 digest、执行语义和任务终态 |
| 操作者 | [`pa_agent/records/`](../../pa_agent/records/)、[`pa_agent/execution/`](../../pa_agent/execution/)、[`pa_agent/operations/`](../../pa_agent/operations/) |
| 输出 | JSON record、audit、SQLite projection、Paper ledger/report、脱敏 runtime status |
| 程序原则 | 只有完整落盘成功进入去重历史；真实 outcome、反事实 outcome、Paper v1/v2 分离 |
| 控制 | Dashboard 写 desired state；Headless 应用并发布 applied generation；systemd 管进程 |

同柱成交路径无法确定时保持 ambiguous/censored，不猜测先后；反事实 Paper 不能称为真实 PnL。

## RunOutcome 语义

| 状态 | 含义 | 是否可有正式 PA 结果 |
| --- | --- | --- |
| `completed` | 全部要求角色和持久化成功 | 是 |
| `completed_with_warnings` | 正式 PA 有效，附带非致命 warning | 是 |
| `degraded_pa_only` | VPA/附加角色失败，PA-only 保留 | 是 |
| `unavailable_input` | 数据、身份或 Snapshot 不可用 | 否 |
| `unavailable_provider` | Provider/API/网络不可用 | 否 |
| `invalid_model_output` | 模型输出在有界修复后仍不可信 | 否 |
| `cancelled` | 用户或进程协作取消 | 否 |
| `internal_error` | 未分类内部异常 | 否 |

界面中的“分析未通过”只是用户提示，不能作为根因。排障必须继续读取 RunOutcome、失败角色、validation issues、provider failure、record/audit 和日志，见 [运行与排障](operations-and-debugging.md)。

## 权限摘要

> 程序测量事实并保护契约与订单安全；PA LLM 定义市场框架与 Setup；VPA LLM 补充或挑战量价证据；隔离 Joint LLM 在已有候选内裁决；正式结果始终由 PA Stage 2 提供。

继续阅读：[架构图谱](architecture-map.md) · [问题历史](problem-history.md) · [研究与评价](research-and-evaluation.md)
