---
lifecycle: active
capability: no-go
verification_scope: not-verified
最后核验: 2026-08-26
核验提交: f8fffc0
权威来源:
  - CONTEXT.md
  - docs/architecture/ADR-0001-analysis-authority.md
  - docs/architecture/ADR-0009-production-shadow-research-joint.md
  - docs/architecture/ADR-0023-vps-observability-and-control-plane.md
---

# 项目概览

## 一句话定义

PA Agent 对同一份冻结行情快照进行 Brooks Price Action 分析，并可在不改变正式 PA 结果的前提下追加独立 Wyckoff VPA Shadow 证据；程序负责确定事实和执行安全，LLM 负责依赖背景与位置的专业判断。

权威领域定义见 [CONTEXT.md](../../CONTEXT.md)。

## 项目目标

1. 对多资产、多周期行情生成可审计的 PA 分析、候选订单与风险建议。
2. 让 OHLCV、时间、来源身份、指标、证据引用、订单数学和持久化事实可复算。
3. 保留 LLM 对市场背景、Always-In、周期位置、Setup、结构失效和 VPA 多解语义的裁决权。
4. 在与正式分析隔离的研究进程中，比较 PA-only 与 PA+VPA Joint 是否存在稳定、成本后的信息增量。
5. 支持 Headless 无人值守分析与独立执行进程，并提供最小权限 Web 控制面。旧 Qt 桌面端已退役。

项目优先级是事实一致、权限清晰、失败可诊断、订单安全和研究可复现。盈利能力需要长期前向模拟盘验证，不能由设计或测试直接推导。

## 使用场景

| 场景 | 入口 | 主要用途 |
| --- | --- | --- |
| Linux Headless | 非 Qt CLI/service | 多资产有界调度、冻结快照分析、Paper、records/audit 持久化 |
| VPS Dashboard | 独立最小权限 Web 进程 | 查看脱敏状态、切换 `running/paused` desired state |
| A/A/B/B 研究 | 独立研究 executor | 同一 Snapshot 下估计 PA-only 随机性、Joint 随机性和配对增量 |

具体入口和依赖关系见 [架构图谱](architecture-map.md) 与 [分析链路](analysis-pipeline.md)。

## 正式分析与研究边界

| 模式 | 正式分析 authority | VPA | Joint | 用途 |
| --- | --- | --- | --- | --- |
| `off` | PA Stage 2 | 完全不进入链路 | 不调用 | 原 PA 兼容基线 |
| `shadow` | PA Stage 2 | 独立 Evidence Analyst | 不调用 | 当前无人值守正式分析模式 |
| `soft` 请求 | PA Stage 2 | 有 | 正式运行收敛为 `shadow` | 兼容旧配置，不授权 Joint |
| A/A/B/B B 臂 | 研究臂结果 | 有 | 有界 decision patch | 隔离研究，不进入正式结果 |

正式分析 authority 来自 PA Stage 2。VPA Shadow 可以支持、挑战或指出证据不足，但不得创建 Brooks Setup、翻转方向、修改 Gate、订单价格或胜率，也不得让有效 PA 分析因 VPA 失败而失败。完整边界见 [ADR-0009](../architecture/ADR-0009-production-shadow-research-joint.md) 和 [VPA 集成规范](../wyckoff_vpa_integration.md)。

## 代码与 LLM 分工

**程序负责：**

- 数据源、venue、symbol、market、timezone 和合约单位身份；
- 主周期、HTF、volume provenance、时间顺序和 Snapshot 冻结；
- OHLCV、指标、窗口比较、seasonality 可用性和 Evidence Catalog；
- Provider Contract、Schema、静态 trace 物化和明确事实校验；
- 订单价格几何、Trader Equation、成本、风险尺度和执行安全；
- 调度、去重、恢复、持久化、审计和状态投影。

**LLM 负责：**

- Brooks 市场背景、周期、Always-In、竞争假设、Setup 和方向；
- 当前 Setup 的结构失效、入场时机和未校准胜率先验；
- Wyckoff Background、Location、Effort versus Result、序列比较和 acceptance/rejection；
- 在隔离 Joint 研究中对已有 PA candidate 的有界时机/可信度裁决。

程序不能用阈值实现半套 PA/Wyckoff 状态机；LLM 不能改写程序事实或绕过执行安全。原则索引见 [原则与边界](principles-and-boundaries.md)。

## 主要能力

- TradingView 及原生 venue 数据适配、统一 instrument identity 和冻结快照。
- 主周期 + 可选 HTF 上下文、确定性 Evidence Graph 和角色化 Prompt 注入。
- PA Stage 1/Stage 2 Draft v2、单一 materializer、定向修复和事实/权限校验。
- VPA 核心、趋势/回调、突破、区间事件和高潮模块按 PA Context 注入。
- 多资产 FIFO 单飞、全局有界并发、任务去重和有界重试。
- JSON records、audit、SQLite projection、Paper OHLC 反事实回放与恢复。
- Headless、systemd 部署资源和隔离 Dashboard 控制面。
- 持续 A/A/B/B、cohort 隔离、ITT/per-protocol 和样本晋级门禁。

当前实现与验证范围见 [当前状态](current-status.md)。

## 数据与安全边界

- 一次分析必须绑定同源 instrument identity、Snapshot、Prompt/contract digest 和 cohort。
- Headless 的源码/Prompt 可只读，运行状态写入 `PA_AGENT_HOME`。
- Dashboard 只读取 Headless 发布的脱敏 projection，不直接读取配置、Provider 凭据、内部 SQLite 或 Paper 账本。
- Dashboard 只写 `running/paused` desired state；systemd 管进程生命周期。
- 当前能力是 Paper execution。知识库不声称已接入私有交易凭据或提交真实订单。

## 非目标

- 不用代码机械命名趋势、Spring、UTAD、Absorption、Exhaustion 或 Brooks Setup。
- 不以更多硬 Gate、自由文本 regex 或单一样本补丁追求表面“零报错”。
- 不让 VPA 成为 PA 审批器，不让 Joint 进入正式分析结果。
- 不把 `estimated_win_rate` 伪装成历史胜率，不用 VPA 主观提高该数字。
- 不承诺策略盈利、胜率提高或频率不变；这些结论必须由前向、样本外、成本后研究支持。

## 继续阅读

- [架构图谱](architecture-map.md)
- [分析链路](analysis-pipeline.md)
- [研究与评价](research-and-evaluation.md)
- [运行与排障](operations-and-debugging.md)
