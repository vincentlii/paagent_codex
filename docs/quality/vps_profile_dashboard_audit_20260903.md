---
lifecycle: active
capability: release-candidate
verification_scope: local
最后核验: 2026-09-03
核验基线: 03c618a
权威来源:
  - ../superpowers/specs/2026-09-03-vps-analysis-profile-dashboard-design.md
  - ../knowledge/codex-analysis-north-star.md
  - ../architecture/ADR-0024-codex-analysis-backend.md
  - ../linux_headless_deployment.md
---

# VPS Analysis Profile 与 Codex Runtime 发布审计

## 结论

本发布候选通过本地工程审计，可以部署到目标 VPS 做受控 DeepSeek/Codex smoke；不能据此声明
VPS 已验证、Codex 优于 Direct、VPA 提高盈利或允许真实交易。部署必须保持 Headless paused，直到
Linux 权限负向测试、数据 probe、真实 ChatGPT auth、AGENTS/Skill live canary 和两次单资产 smoke
均完成并由用户审阅。

本轮没有用 Codex 替换 PA Agent 交易域。PA Agent 仍拥有 Frozen Snapshot、Measurements、Brooks
知识选择、Stage 1/2、Validator、Brooks Router、Risk、Trader Equation、持久化与 Paper execution；
Codex 只在一个 fresh、ephemeral、snapshot-bound role 中调查 allowlisted 冻结证据并返回 Provider
Draft。VPA 固定为 Shadow 二级证据，不创建 Setup、方向或订单。

## 发布范围

| 项目 | 发布候选行为 |
| --- | --- |
| 市场范围 | 仅 `binance_usdm / BINANCE / XAUUSDT / 1h` |
| 上下文 | HTF `4h / 60` 必开，VPA `shadow` 必开 |
| 并发 | analysis、data、Codex worker 均为 1 |
| Direct | `deepseek-v4-flash`、`deepseek-v4-pro` 与 allowlisted effort |
| Codex | 发布 catalog 中 6 个 model 及各自允许 effort；未知 pair fail closed |
| 控制 | Profile/Backend desired state 仅在 scheduler idle 后 applied |
| 单次运行 | paused + idle + fully applied 时每 generation 最多接受一个任务，完成后仍 paused |
| 执行 | Paper-only；Dashboard 无真实订单、Prompt、Risk、资产或 Joint 写入口 |

## 架构与安全证据

1. Profile 是 credential-free、versioned、strict schema；固定市场/VPA/HTF 字段不可由 UI 改写。
2. Dashboard 仅监听 loopback，使用独立 Basic token、Origin/custom-header/body-size/CSP 防护；只读
   脱敏投影，不读取 Provider key、生产 DB 或 Paper ledger。
3. Codex 每个 role 都是独立 `--ephemeral` task，approval `never`、sandbox `read-only`、shell/Web/
   image disabled；唯一外部能力是 required `pa_evidence` MCP。
4. production 不传 `--ignore-rules`，保留 workspace `AGENTS.md` 和 Role Skill discovery；静态审计验证
   规则内容，随机 sentinel live canary 验证 CLI 实际发现链。
5. Runtime AGENTS 与四个 Skills 的 SHA-256 为
   `a2a5d50622d0cb242dce85f4cf545b01c7a1f4c58932e56df3fbb19dec855200`，完整 digest 与 CLI binary
   digest、model/effort、Profile fingerprint 一起进入 Frozen runtime identity。
6. VPS 使用非 editable package 安装，并从 source 目录之外比较 installed package assets 与
   `/opt/pa-agent` source；不一致时安装停止。
7. Unix 权限契约把 Headless、Dashboard、Codex 分为三个用户。Codex 可读 frozen jobs、可写自己的
   home/runtime/outbox 与 spool result；不能读取 DeepSeek key、生产 records/Paper ledger，不能写源码。

## 本地验证

- Task 1–5 均按阶段完成独立 Standards/Spec 审查并提交。
- Task 5 最终 38 项 Runtime/Profile 相关测试通过；静态/source audit `ok=true`；最终独立审查
  Standards PASS / Spec PASS。
- Task 6 聚焦回归 276 项通过，覆盖 Dashboard、runtime control/profile、Headless、Direct factory、
  Codex publisher/worker/runtime/evidence、VPA Shadow、HTF、records、Paper、PA/VPA A/A/B/B 与 Backend
  A/A/B/B。
- 回归发现既有 audit ledger 可接收非 scalar Provider mock metadata，导致 Provider retry 错误记录时
  `deepcopy` 异常；根因修复为持久化边界只接受稳定 scalar。两项原始失败与全套回归随后通过。
- 一次 Windows Dashboard socket 用例出现 `WinError 10053`；该用例单独及完整 Dashboard suite 重跑
  均通过，未修改生产代码，判定为本地 socket 抖动而非可复现缺陷。
- `git diff --check` 与 `compileall` 是提交前必需门禁；Linux `bash -n` 留给目标 VPS 执行。

## 三角色审计

### 软件架构师：PASS（本地发布候选）

Profile、control、runtime status、spool 与 audit 的生产者/消费者边界一致。HTTP `202` 不冒充 applied；
generation CAS 阻止旧表单覆盖；运行中的 task/Job 保持冻结 identity。Codex 无权修改主链，Runtime
rules/Skills 是可哈希部署资产而不是散落 Prompt。保留 Direct Backend 和完整冷回退，因此升级不丢失
原 PA Agent 的成熟能力。外部剩余风险是 Linux/systemd 与真实 CLI 行为，已转化为 fail-closed VPS gate。

### 量化工程师：PASS（工程实验基础），NO-GO（模型晋级）

固定资产、时间边界、Profile fingerprint、requested/effective model/effort、token、latency、repair、
Evidence calls/bytes 与 outcome 可审计。单次 DeepSeek/Codex smoke 发生在不同 Snapshot 时只证明运维
可用性，不能估计相对效应。模型晋级仍需同一 Frozen Snapshot 的预注册 Backend A/A/B/B、完整失败
coverage、重复性、盲评、至少 300 个独立 Snapshot/100 个明确 outcome，并报告成本后样本外 Paper、
calibration、MAE/MFE 与回撤。

### 专业交易员：PASS（交易边界），NO-GO（收益结论）

Brooks 是 primary framework；VPA 只支持、挑战或降低置信度，不能生成 Setup、翻转方向或覆盖 PA
Stage 2。Stage 2 必须在已验证 Stage 1 与 Router manifest 内工作，Validator/Risk/Trader Equation 继续
决定可执行性。Evidence-on-demand 能减少初始 Prompt 拼接和遗漏机会，但不能消除主观判断错误或市场
不确定性；更长解释、单次盈利或较低 token 都不是交易增益证据。

## VPS 阻断门禁

以下任一项失败都保持 paused 并回退到 DeepSeek Direct：

1. `bash -n`、Headless `--check`、Codex worker `--check` 或数据 probe 失败；
2. installed/source Runtime digest 不一致；
3. `pa-agent-codex` 能读取 DeepSeek key、生产 DB/Paper ledger，或能写 `/opt/pa-agent`；
4. ChatGPT auth/status、model availability 或 AGENTS/Skill live canary 失败；
5. Dashboard 不是 loopback、Profile/Backend 未 fully applied、Run Once 不满足 exact-once；
6. Codex smoke 发生 fallback、identity drift、Schema/事实/authority 错误或未进入 canonical PA validation。

## 部署后状态

Dashboard 与 Codex worker 可以在健康时保持 active；Headless 长期调度在用户审阅两个 smoke 前保持
paused。密钥不进入仓库、文档、Dashboard 投影或审计报告。具体命令和回滚顺序以
[Linux VPS Headless 部署](../linux_headless_deployment.md)为准。
