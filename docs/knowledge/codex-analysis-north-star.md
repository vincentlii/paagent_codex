---
lifecycle: active
capability: research-only
verification_scope: local
issue_status: monitoring
最后核验: 2026-09-03
权威来源:
  - CONTEXT.md
  - docs/architecture/ADR-0024-codex-analysis-backend.md
  - docs/superpowers/specs/2026-09-03-vps-analysis-profile-dashboard-design.md
---

# Codex Analysis North Star

## 结论

Codex Agentic Analysis 是待验证的分析 Backend，不是新的交易系统或 authority。Direct LLM 保持对照组；两者只能在同一 Frozen Snapshot、同一 Provider Draft/Validator 链和同一角色权限内比较。任何 Dashboard、Runtime、Skills、部署或 Analysis Profile 改动都必须先读本文件，再回到 `CONTEXT.md`、ADR-0024 与当前实现确认细节。

## 不可协商的 authority

| 角色 | 唯一职责与边界 |
| --- | --- |
| PA Agent | 拥有冻结事实、Snapshot、Evidence allowlist、Provider Draft 链、审计与持久化。 |
| Brooks | 主分析框架，定义市场背景、周期、Setup 与正式 PA 判断。 |
| VPA | 仅为独立 Shadow evidence；不能建立 Setup、改变方向、胜率或正式交易结果。 |
| Router / Validator / Risk / Trader Equation / execution | 仍由 PA Agent 的既有链拥有；Router 只读已验证 PA 枚举与 pattern keys，其他角色不得旁路或重写。 |
| Codex | 只能调查同一 Frozen Snapshot 中、当前 role 被授权的 Evidence，并返回 role draft；不得拥有事实、Router、Validator、Risk、Trader Equation、执行、持久化、下单或改代码权限。 |

AGENTS 固化不可变 authority、no-lookahead、Evidence grounding、阶段协议与停止条件。Role Skills 只编码 Stage 1、Stage 2、VPA、研究 Joint 和 pointer repair 的操作程序；Brooks/VPA 领域知识仍由 PA Agent 按 Context/Router 管理，Skills 不得成为第二份领域知识库。

## Frozen Evidence 与角色隔离

一个 Frozen Snapshot 是完整分析的唯一事实边界。Evidence-on-demand 在该边界内替换一次性拼接全部 Prompt：Codex 只读当前 Job、role allowlist、call/byte budget 内的事实与知识，并把每次读取写入审计。它不读取未来 K 线、实时行情、Web、数据库、外部 App、Plugin 或未授权文件。

Stage 1、Stage 2、VPA 与研究 Joint 各自使用独立 ephemeral task；下游只接收已验证的结构化 Artifact，不继承聊天历史。按需读取减少的是初始上下文压力，不会放宽 Snapshot cutoff、角色权限、Schema、事实校验或执行安全。

## 验证假设，而非追求短 Prompt

测试 Agentic Analysis 的原因是：在不改变 authority 的条件下，按需 Evidence 与角色程序可能减少事实转录错误、证据遗漏和长 Prompt 的结构化输出失败。Direct LLM 是必要控制，用于区分完整 Backend 的实际价值与单纯更换模型、文本风格或调用方式的差异。

Prompt 变短本身不是成功。比较必须在冻结输入、独立运行、cohort 与 A/A/B/B 口径内，至少衡量：

- factual errors 与 evidence omissions；
- Stage coherence、calibration 与 schema compliance；
- retry 次数；
- latency 与 cost（分别报告初始 Context、retrieval Context、Token 和总延迟）；
- Paper outcomes，以及完整性、ITT、missing/censored arms 与真实/反事实结果分离。

文字更专业、单次盈利或单次运行成功均不是晋级证据。Codex availability failure 才能触发完整 Direct PA 链冷回退；Schema、事实、Evidence、authority、专业判断、Validator、Router 或 Risk failure 必须 fail closed，不能借 fallback 掩盖。

## Analysis Profile 与控制面

Dashboard 可以在不暴露凭据的前提下提交 allowlisted、版本化 Analysis Profile，以选择发布目录中的 DeepSeek/Codex model、允许的 thinking/reasoning 组合。Profile 只能在 Headless idle boundary 应用；后续 Snapshot 才可使用新 Profile，已冻结 Job 不得续接或重标。

初版资产、VPA Shadow 和 HTF 固定。Dashboard 不能编辑 Prompt、AGENTS、Skills、Knowledge、Validator、Router、Risk、Paper、API key、Base URL、文件路径或 VPA/Joint 权限；它也不能读取生产数据库或改变交易权限。每次 requested/effective Backend、model、reasoning、Profile fingerprint 与 Runtime asset identity 都必须进入 Frozen Job、record 与 cohort。

## 当前边界

阶段 4 的本地 Runtime / Skills / 必读矩阵和三角色自审见
`docs/quality/phase4_runtime_acceptance.md`。result 封套携带原 Draft 或非决策停止对象；
联合 Stage 2 使用显式 Skill 变体，仍复用 stage2 与唯一 materializer。独立 VPA、旧研究仲裁
和修复角色不得互换。测量只做冻结锚点算术，回执不是新的 catalog fact ID。
本阶段不改变正式 pipeline；真实 MCP SDK/CLI/Linux 与专业效果仍须后续验证。

本地 Codex Analysis Backend 已存在，但 Analysis Profile UI 与真实 Codex 分析的 live evidence 仍待本阶段验证。未取得目标 VPS、真实账号、Frozen Snapshot 和 Paper 证据前，能力保持 `research-only`，生产默认 `deepseek_only`，不因实现存在而宣称模型、VPA 或交易结果已晋级。
