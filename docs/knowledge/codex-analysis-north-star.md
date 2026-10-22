---
lifecycle: active
capability: paper-observation-limited
verification_scope:
  - local
  - VPS-controlled-live
issue_status: monitoring
最后核验: 2026-10-22（原则与状态分离；未重新验收 VPS）
权威来源:
  - CONTEXT.md
  - docs/architecture/ADR-0024-codex-analysis-backend.md
  - docs/superpowers/specs/2026-09-03-vps-analysis-profile-dashboard-design.md
---

# Codex Analysis North Star

## 结论

Codex Agentic Analysis 是待验证的分析 Backend，不是新的交易系统或 authority。Direct LLM 保持对照组；两者只能在同一 Frozen Snapshot、同一 Provider Draft/Validator 链和同一角色权限内比较。任何 Dashboard、Runtime、Skills、部署或 Analysis Profile 改动都必须先读本文件，再回到 `CONTEXT.md`、ADR-0024 与当前实现确认细节。

## 不可协商的 authority

2026-10-21 新授权例外见 [ADR-0027](../architecture/ADR-0027-selectable-fixed-risk-reward.md)：
AnalysisProfile 可选择 PA 或 fixed_rr（1/1.5/2/2.5），固定目标进入分析身份，不能与下述
仅影响执行的金额/TTL混同。固定模式程序仅在唯一materializer中计算TP1，再做原方程；
LLM仍拥有结构Entry/SL、机会判断及对应目标的主观先验，VPA与Codex权限不扩大。
旧任务与PA模式保持原义；本地实现、发布、线上启用及收益验证分别报告。

| 角色 | 唯一职责与边界 |
| --- | --- |
| PA Agent | 拥有冻结事实、Snapshot、Evidence allowlist、Provider Draft 链、审计与持久化。 |
| Brooks | 主分析框架，定义市场背景、周期、Setup 与正式 PA 判断。 |
| VPA | 独立 Evidence Analyst 不建立 Setup、方向、订单或胜率。旧 Shadow 的正式结果仍为 PA-only；显式 `pa_vpa_joint` 可由联合 Stage 2 在 Brooks/Router 边界内综合已验收 VPA 报告，自行提出未校准主观先验与正式 Draft，但不得机械改写 PA-only 先验。 |
| Router / Validator / Risk / Trader Equation / execution | 仍由 PA Agent 的既有链拥有；Router 只读已验证 PA 枚举与 pattern keys，其他角色不得旁路或重写。 |
| Codex | 只能调查同一 Frozen Snapshot 中、当前 role 被授权的 Evidence，并返回 role draft；不得拥有事实、Router、Validator、Risk、Trader Equation、执行、持久化、下单或改代码权限。 |

AGENTS 固化不可变 authority、no-lookahead、Evidence grounding、阶段协议与停止条件。Role Skills 只编码 Stage 1、普通/联合 Stage 2、VPA、旧研究 Joint Arbiter 和 pointer repair 的操作程序；Brooks/VPA 领域知识仍由 PA Agent 按 Context/Router 管理，Skills 不得成为第二份领域知识库。

2026-10-15用户授权的面板执行设置仅限OKX Demo单笔总价值和限价有效期，默认500 USDT / 3h。
它们由Host在任务接收时冻结，与模型/Codex/AnalysisProfile、Snapshot/cohort和cutover身份
分离；不修改模型建议的entry/stop/TP1，不授予Dashboard或Codex交易凭据和下单权限。
具体控制、兼容与发布边界见ADR-0023的同日增补和Demo本地验收报告。

## Frozen Evidence 与角色隔离

一个 Frozen Snapshot 是完整分析的唯一事实边界。Evidence-on-demand 在该边界内替换一次性拼接全部 Prompt：Codex 只读当前 Job、role allowlist、call/byte budget 内的事实与知识，并把每次读取写入审计。它不读取未来 K 线、实时行情、Web、数据库、外部 App、Plugin 或未授权文件。

不同 Frozen Snapshot 使用独立会话。同一 Snapshot 内仅 PA Stage 1 首稿验证通过后允许 PA Stage 2
受控续接，以保留原版两阶段连续推理并避免重复读取 digest 相同的 PA 基线；Stage 2 权限仍只来自
已验证 Stage 1 与 Router。VPA、旧研究 Joint Arbiter 与 repair 各自使用独立 ephemeral task；
新版联合 Stage 2 属于 PA Stage 2 的显式变体，只接收已验收 VPA 报告，不续接 VPA 会话或读取原始
VPA catalog。按需读取与 delta 基线不会放宽 Snapshot cutoff、角色权限、Schema、事实校验或执行安全。

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

初版资产固定；2026-10-10 用户授权增加 Binance XAU/BTC/ETH 白名单选择、并发
1/2/3 及三档 Stage 2 倾向，默认单黄金、并发 1。旧模式 VPA Shadow 与 HTF 保持固定。
Dashboard 不能编辑 Prompt、AGENTS、Skills、Knowledge、Validator、Router、Risk、Paper、API key、Base URL、文件路径或 VPA/Joint 权限；它也不能读取生产数据库或改变交易权限。每次 requested/effective Backend、model、reasoning、Profile fingerprint 与 Runtime asset identity 都必须进入 Frozen Job、record 与 cohort。

会话隔离不依赖“历史文件少”：不同快照只创建新会话，续接只用经过验证的同快照
UUID，长期记忆始终禁用。14 天保留清理属于空闲 Worker 运维，不进入模型任务，
不删除认证、正式证据或有效恢复引用。黄金产品与单市场背景是事实限定，不是
第二套策略规则。实施和验证范围见 [V1 运行升级](../quality/v1_operations_upgrade_20261010.md)。

## 许可与验证边界

2026-10-09 用户授权全量 A、单 Agent、`pa_vpa_joint`、`gpt-6-luna/xhigh`
在 VPS 持续有界 Paper 观察；后续授权迁入 OKX Demo、三品种与固定盈亏比，
当前生效配置及发布证据统一见[当前状态](current-status.md)，不在本原则页重复维护。
Fast 是后续冻结任务的 service tier 选择，不保证固定加速。
这些许可不等于盈利、稳定性、知识删减或子 Agent 晋级，也不扩张 Codex authority。

Stage 内子协作仅在显式研究协议 `host_stage_challenge_v1` 内：Host 启动独立 Codex
子进程，子角色只查同一 Frozen Job 的有限同域资源并给待核意见；父保留完整 mandatory、
专业裁决和唯一 Draft。它不通过删知识、分走父必读或自动改规则换速度。
2026-09-27 本地真实合成传输通过；真实市场增益、Linux 和生产晋级未通过，详见
[逐阶段研究计划](../quality/stage_subagent_research_plan.md)。Direct 和默认单 Agent 保持不变。

阶段实施报告记录当时结果，不自动覆盖后来已验证的状态；完整历史导航见
[历史状态](archive/README.md)。当前新前置 VPA 的作用域、旧报告兼容及工程/质量验收边界见
[ADR-0025](../architecture/ADR-0025-joint-stage2-pipeline.md)与
[运行可靠性实施及复查](../quality/runtime_reliability_vpa_remediation_20261021.md)。
未完成模型匹配回放/盲评，不能仅用测试通过宣称减少不合理等待或改善收益。
