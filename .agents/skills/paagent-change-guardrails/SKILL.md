---
name: paagent-change-guardrails
description: Use when modifying, debugging, optimizing, reviewing, validating, or preparing deployment changes for this PA Agent project.
---

# PA Agent Change Guardrails

## Core Principle

以可复现证据驱动修改：程序负责确定性事实，LLM 负责专业解释；任何变更必须同时通过 PA 交易员、Wyckoff VPA 交易员和量化软件架构师审查。

## Read First

1. 阅读 `AGENTS.md`、`CONTEXT.md`、`docs/wyckoff_vpa_integration.md`。
2. 阅读与改动相关的 `docs/architecture/ADR-0001` 至 `ADR-0009`。
3. 检查 branch、工作区和最新 record/log；不得覆盖不属于本任务的变更。
4. Bug 修复使用 `superpowers:systematic-debugging`；实现使用 `superpowers:test-driven-development`；完成前使用 `superpowers:verification-before-completion`。

## Three-Role Review

| Role | 必须回答 |
|---|---|
| PA trader | 是否保持 Brooks 背景、Always In、Setup、Trader Equation 与唯一 Router 的理论含义？是否机械化了本应由交易员判断的结构？ |
| Wyckoff VPA trader | 是否坚持 Background、Location、Effort/Result、Comparison、Acceptance/Follow-through 和 Evidence first, label last？是否形成第二套 Setup、方向或 Gate？ |
| Quant/software architect | 事实是否可复算、因果、同源并可重放？Schema、失败边界、审计、性能和测试是否由单一责任方拥有？ |

三者任一不通过，不得写代码。涉及策略权限、数据身份、实验口径或生产行为的新选择，先向用户说明取舍。

## Authority Boundary

- 程序拥有 OHLCV、时间、指标、provenance、窗口测量、引用、Schema、订单数学、成本和执行安全。
- LLM 拥有市场背景、周期、方向、Setup、位置价值、量价解释、竞争假设和时机判断。
- PA defines. VPA informs. LLM arbitrates.
- Brooks Router 是唯一策略路由；独立 VPA、旧 Shadow 和研究 Joint Arbiter 不创建交易、不修改 PA-only `estimated_win_rate`、不成为隐性 Gate。显式新版联合 Stage 2 可在已验收 VPA 报告与 Router 边界内独立提出未校准主观先验，不机械修改 PA-only 先验。
- 旧 `vpa_mode=off` 必须保持原 PA 链路；旧 `shadow` 的正式结果仍是 PA-only，旧 Joint Arbiter 只属于隔离研究。显式 `analysis_pipeline_v1:pa_vpa_joint` 是 opt-in 新链路，不受旧 Joint 禁令否定；生产启用仍须独立验收与晋级。

## Validation Scale

| Treatment | 适用范围 |
|---|---|
| Hard reject | JSON 不可解析、冻结事实矛盾、权限越界、不可能状态/时序、非法订单几何或数学 |
| Normalize + audit | 唯一且无语义的表示差异、无损格式转换、确定性排序或程序字段物化 |
| Warning | 专业措辞、术语质量、非唯一 PA/VPA 解释、可理解的模型判断偏差 |

不得为了让 Prompt “更听话”而不断增加 hard checks。Validator 只裁定事实、权限和安全，不裁定专业解释是否唯一正确。

## Change Workflow

1. 从最新原始 record、日志、堆栈或实验结果建立快速、确定、可自动运行的失败反馈环。
2. 区分代码缺陷、Prompt/契约矛盾、模型合理偏差和外部故障；根因未知时禁止 patch。
3. 写最小回归测试并确认它因原问题变红。
4. 选择能解释整类问题的最小修复；不得硬编码资产、周期、时间戳、单次输出或特定行情。
5. 修复后用原始失败输入重放，再扫描相邻调用者、同类字段和失败边界。
6. 运行聚焦测试及受影响范围的 Unit、Property、Integration、E2E、静态检查和 `git diff --check`。
7. 清理本次临时文件；更新仍具长期价值的 ADR、`CONTEXT.md` 或测试。
8. 用三个角色复盘实际输出，而不只检查“程序没有报错”。

同一问题三次验证仍失败，立即停止局部修补，审查数据流、状态、契约所有权和架构。

## Prohibited Shortcuts

- 用指标或阈值在代码中命名 PA/Wyckoff 状态、事件或交易意图。
- 为通过单一样本而添加特例、别名、宽松分支或历史数据改写。
- 把可修复的表示差异升级成整次分析失败，或把事实矛盾降级成可忽略警告。
- 静默重建 frozen hash、catalog、cohort、缺失 outcome 或模型未声明的证据。
- 用重复 snapshot、单次运行或模型自述证明 VPA 有效或提高盈利。
- 未做新鲜验证便宣称“完整通过”“可部署”“更赚钱”或“以后不会报错”。

## Completion Report

只报告：根因、实际改动、三角色判断、验证证据、未验证边界和是否清理临时文件。测试通过不等于分析正确或策略盈利；真实 Shadow 与 A/A/B/B 证据必须单独说明。
