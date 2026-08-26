---
lifecycle: active
capability: not-applicable
verification_scope:
  - local
issue_status: monitoring
最后核验: 2026-08-26
核验提交: 2e2d8b5
权威来源:
  - ../../CONTEXT.md
  - ../architecture/ADR-0009-production-shadow-research-joint.md
  - ../architecture/ADR-0023-vps-observability-and-control-plane.md
  - ../quality/pre_release_final_audit_20260813.md
  - ../linux_headless_deployment.md
---

# 项目概览

PA Agent 是面向交易研究者与运维者的市场分析、审计和 Paper execution 系统。它把可复算的行情事实、引用、订单数学和执行安全交给程序，把 PA 市场框架、Setup、方向、结构失效和 VPA 专业解释保留给 LLM。

## 使用场景

| 场景 | 用途 | 当前边界 |
| --- | --- | --- |
| Windows GUI | 人工发起分析、查看 PA 结果、VPA evidence、审计与 Paper 状态 | 适合交互式研究，不是实盘下单界面 |
| Linux Headless / Paper | 无人值守冻结快照分析、记录、调度与本地 Paper execution | 不导入 Qt；真实 Linux、systemd 与 VPS 权限仍需目标环境验证 |
| VPS Dashboard | 读取脱敏运行投影，显示状态并请求 `running` / `paused` | 独立进程与用户；不读取分析数据库、配置目录或凭据，也不直接分析或下单 |

## 正式与研究 authority

正式分析只有 PA authority：`off` 是原始 price-only PA，`shadow` 在保持 PA Stage 2 正式结果的同时追加独立 VPA Evidence Analyst。VPA 只提供证据与 warning，不能创建 Setup、翻转方向、改写订单或胜率。

Joint 只存在于隔离的 A/A/B/B 研究 B 臂。它不进入无人值守正式分析或生产 Paper authority；生产请求 `soft` 会收敛为 `shadow`。完整边界以 [ADR-0009](../architecture/ADR-0009-production-shadow-research-joint.md) 与 [CONTEXT.md](../../CONTEXT.md) 为准。

## 能力概览

- **数据与快照**：Provider 行情身份、OHLCV、HTF 与 evidence 在冻结 Snapshot 内绑定，供后续分析、审计与重放使用。
- **Provider 与审计**：PA Draft、单一 materializer、校验、records、audit 与失败状态保留可追溯边界；历史审计证据见 [上线前最终审计](../quality/pre_release_final_audit_20260813.md)。
- **调度与 Paper**：Headless 使用独立 `PA_AGENT_HOME` 写入配置、日志、records、audit 与 Paper 数据；多资产任务受身份、FIFO 与并发边界约束。
- **Dashboard**：Headless 发布白名单化状态投影，Dashboard 通过 desired-state 请求控制，实际生效由 Headless 回写 generation；系统进程生命周期由 systemd 负责。

## 数据、安全与控制边界

冻结 Snapshot 是分析输入身份边界，records/audit 绑定对应版本和运行事实。Dashboard 只消费投影，控制面只允许持久化 `running` 或 `paused`；暂停只阻止新 Snapshot 入队，不中断已开始任务。Dashboard 默认 loopback 访问，控制凭据与 Provider 凭据隔离，单向目录权限由 [ADR-0023](../architecture/ADR-0023-vps-observability-and-control-plane.md) 定义。

## 明确非目标

- 不宣称真实订单执行或已经接入实盘交易。
- 不承诺盈利、胜率提升或成本后正收益。
- 不把 PA/Wyckoff 专业判断实现为确定性的状态机、阈值策略或第二个 Strategy Router。
- 不把 VPA Shadow 或单次 Joint 样本写成正式 PA 决策或研究增益证据。
