---
lifecycle: active
capability: not-applicable
verification_scope: not-verified
最后核验: 2026-08-26
核验提交: f8fffc0
权威来源:
  - CONTEXT.md
---

# 术语索引

本页只提供别名、缩写、使用位置和权威入口。领域定义以 [CONTEXT.md](../../CONTEXT.md) 为准。

| 术语 | 别名/缩写 | 项目内使用位置 | 权威入口 |
| --- | --- | --- | --- |
| Price Action | PA、Brooks PA | 市场框架、Setup、Stage 1/2 正式分析 | [核心目标与权限边界](../../CONTEXT.md#核心目标) |
| Wyckoff Volume Price Analysis | VPA、Wyckoff VPA | Shadow Evidence Analyst、Joint 研究 | [领域对象](../../CONTEXT.md#领域对象)、[VPA 集成规范](../wyckoff_vpa_integration.md) |
| Joint Decision Proposal | Joint、联合裁决 | A/A/B/B 研究 B 臂的有界 decision patch | [领域对象](../../CONTEXT.md#领域对象)、[实验边界](../../CONTEXT.md#实验边界) |
| Shadow | VPA Shadow | PA 正式结果后追加独立量价报告 | [实验边界](../../CONTEXT.md#实验边界) |
| Soft | soft 请求 | 正式运行收敛为 Shadow；仅研究 executor 可授权 Joint | [实验边界](../../CONTEXT.md#实验边界) |
| Gate | Stage 1 Gate | Stage 1 框架是否进入 Stage 2 的结果 | [分析顺序](../../CONTEXT.md#分析顺序) |
| Entry Attempt | 当前入场尝试 | Stage 2 已存在的真实可执行/计划候选 | [不变量](../../CONTEXT.md#不变量) |
| Candidate | Stage 2 model candidate | LLM 原始交易意图，程序不得改写 | [不变量](../../CONTEXT.md#不变量) |
| Execution Disposition | 执行处置、effective semantics | candidate 外的订单数学、置信度政策和执行资格 | [领域对象](../../CONTEXT.md#领域对象) |
| Analysis Snapshot Bundle | Snapshot、冻结快照 | 主周期、HTF、volume、身份和质量报告 | [领域对象](../../CONTEXT.md#领域对象) |
| Market Snapshot Hash | snapshot hash | A/A/B/B 独立样本和去重身份 | [实验边界](../../CONTEXT.md#实验边界) |
| Cohort | cohort_id、实验队列 | 隔离 Provider/settings/源码/Prompt 版本 | [实验边界](../../CONTEXT.md#实验边界) |
| Run Outcome | 运行终态 | completed、降级、输入/Provider/模型/内部失败 | [失败契约](../../CONTEXT.md#失败契约) |
| Pa Hypothesis Set | PA handoff | PA 到 VPA/Joint 的唯一交接对象 | [领域对象](../../CONTEXT.md#领域对象) |
| Evidence Catalog/Graph | 事实目录、证据图 | 程序可复算事实和引用身份 | [权限边界](../../CONTEXT.md#权限边界) |
| Structural Stop | 结构止损 | 当前 Setup 被否定时的保护性 stop | [不变量](../../CONTEXT.md#不变量) |
| Invalidation Condition | 假设失效条件 | 触发重新分析的语义条件，可能需要收盘确认 | [不变量](../../CONTEXT.md#不变量) |
| Trader Equation | 交易者方程 | PA 主观先验与程序订单数学形成的期望值框架 | [分析顺序](../../CONTEXT.md#分析顺序) |
| Paper OHLC | Paper 回放 | 对计划订单的反事实 OHLC 路径评价 | [实验边界](../../CONTEXT.md#实验边界) |
| Analysis Outcome | 真实结果 | 人工核验的真实执行结果 | [不变量](../../CONTEXT.md#不变量) |
| Counterfactual Outcome | 反事实结果 | 闭盘 OHLC 对计划的回放结果 | [不变量](../../CONTEXT.md#不变量) |
| ITT | intention-to-treat | 未下单/未触发按 `0R` 纳入的研究主估计 | [实验边界](../../CONTEXT.md#实验边界) |
| Per-protocol | PP、交付样本 | 只评价成功交付处理的诊断指标 | [实验边界](../../CONTEXT.md#实验边界) |
| Censored | 截尾/未决 | pending 或路径无法确定，不猜测结果 | [实验边界](../../CONTEXT.md#实验边界) |
| Desired State | 期望状态 | Dashboard 请求的 running/paused | [多资产运行时](../../CONTEXT.md#多资产运行时) |
| Applied Generation | 已应用控制版本 | Headless 对控制请求的实际确认 | [多资产运行时](../../CONTEXT.md#多资产运行时) |

术语定义变化应先更新 `CONTEXT.md` 或相应 ADR，再同步本索引。
