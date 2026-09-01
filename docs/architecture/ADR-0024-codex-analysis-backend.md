# ADR-0024：受约束的 Codex Analysis Backend

## 状态

Accepted for implementation，2026-09-01。

Codex 先作为 `research-only` Backend 实现；通过本文门禁前，生产默认仍为
`deepseek_only`。本 ADR 不授权真实交易或 Joint 进入生产。

## 背景

当前 PA Stage 1/Stage 2 通过 PromptAssembler、ModelCallRunner 与 Provider Draft v2
完成直接模型调用。历史记录显示长 Prompt、结构化输出失败、事实转录错误和定向修复漂移
仍是主要故障源。Codex 可以用独立 AGENTS、Role Skill 和按需 Evidence 调查降低一次性
Context 压力，但更高自由度也会带来权限漂移、上下文污染、版本漂移和不可重放风险。

旧 `codex-paagent` 尝试曾截断知识、用程序信号投票锚定方向、提高 Wyckoff 权限并把 CLI
工作流放到交易链之上，导致 PA Agent 的 Brooks Router、两阶段协议和正式 PA authority
被削弱。本次实现只允许把 Codex 接入现有分析 seam，不迁移或复制旧项目架构。

## 决策

### 1. Backend seam

在 `TwoStageOrchestrator` 的角色调用位置引入一个小型 `AnalysisRoleBackend` interface。
它接受冻结角色请求并返回 Provider Draft 与运行审计；它不拥有 materialization、Validator、
Brooks Router、Risk、Trader Equation、VPA failure semantics 或 persistence。

提供两个 Adapter：

- `deepseek_direct` 封装当前 PromptAssembler + ModelCallRunner 行为，保留 Stage 2 prefix
  continuation 和现有 Provider fallback 语义。
- `codex_agent` 为每个 Stage 1、Stage 2、VPA、研究 Joint 启动独立 ephemeral Codex 任务，
  只返回同一 Provider Draft v2/role draft。

一个 Frozen Snapshot 是逻辑分析周期，但不是一个长期 Codex 对话。Stage 1、Stage 2 和 VPA
不得 resume 或共享自然语言会话；Stage 2 只接收已验证 Stage 1 artifact 与 Brooks Router manifest。

### 2. Frozen Agent Job

PA Agent 在模型调用前建立不可变 Job manifest，绑定 `request_id`、role、attempt、Snapshot、
cutoff、Provider Contract、output schema、Prompt/knowledge、AGENTS、Skills、Codex CLI、model、
reasoning profile 与预算 digest。Job 通过原子文件发布；任何 digest、角色或过期状态不匹配的
结果均 fail closed。

Codex 初始只接收有界核心 Context。完整 K 线区间、Measurements、Evidence 和已授权知识通过
当前 Job 内的只读 Evidence interface 按需读取。所有读取必须受 Snapshot cutoff、role allowlist、
knowledge manifest、call count 和 byte budget 约束并进入审计。禁止 Web、实时行情、外部 App、
Plugin、数据库查询和写工具。

### 3. Runtime rules and skills

市场分析使用独立 Runtime `AGENTS.md`，不加载源码仓库的开发 Agent 指令。AGENTS 只定义长期
authority、no-lookahead、Evidence grounding、阶段协议、禁止改代码/下单和停止条件。Role Skills
只定义 Stage 1、Stage 2、VPA、研究 Joint 与 pointer repair 的分析程序；Brooks/VPA 知识仍由
PA Agent 管理并按 Context/Router 选择，Skill 不成为第二份知识库。

Runtime 资产只读、发布固定并参与 cohort fingerprint。VPS Codex 无权自我修改 AGENTS、Skills、
model rules 或知识。

### 4. Validation and repair

Codex 的 `--output-schema` 是 Provider 边界约束，不替代 PA Agent 的 Draft validation、
materialization、事实校验和执行安全。每个角色最多一次定向修复；程序只投影 Validator 明确授权的
JSON Pointer，未授权新增、删除、置空、数组重排或语义变化不进入接受结果。权限、Snapshot、
no-lookahead 和不可解析错误不得通过自由重写恢复。

### 5. Failure and fallback

Backend 选择支持 `deepseek_only`、`codex_only` 与 `auto`。初始发布为 `deepseek_only`；`auto`
表示 Codex primary、DeepSeek cold fallback。Fallback 只允许 CLI/worker/auth/model capacity、
429、连接、5xx、required evidence runtime 或 timeout 等 availability failure。

Schema、事实、Evidence、专业判断、低置信度、Validator、Router、Risk 或 authority failure 不得
触发 fallback。Stage 2 availability failure 不得接续 Codex Stage 1；系统必须在同一 Frozen
Snapshot 上从 DeepSeek Stage 1 重跑整条 PA 链。生产 VPA failure 仍只形成 warning。

实验 arm 禁用 fallback，并保留所有失败用于 ITT。

### 6. VPS isolation and persistence

Linux 使用独立 `pa-agent-codex` Unix 用户、独立 `CODEX_HOME` 与 worker。Codex 只读已发布 Job
和只读 Runtime 资产，只写临时目录与 outbox；不能读取 PA 数据库、DeepSeek credential、Paper
账本或源码写路径。Codex 不直接写数据库，可信 PA Agent 摄取、复验并持久化 outbox。

只使用 ChatGPT account device auth，不配置隐式 OpenAI API key fallback。认证状态只投影为
`healthy|expired|rate_limited|unavailable`，凭据和 reasoning text 不进入 Dashboard 或长期日志。

### 7. Dashboard control

ADR-0023 的控制面增加唯一窄例外：Dashboard 可以请求 `desired_backend = deepseek_only | auto |
codex_only`。它仍不能修改模型、reasoning、Prompt、AGENTS、Skills、Knowledge、Risk、资产、VPA
或 Joint 权限。

切换采用 desired/applied generation：Headless 停止接收新 Snapshot，等待 in-flight Job 完成或
超时后应用 Backend，再按切换前运行状态恢复。已有 Job 的 Backend 身份不可重标或中途混合。

### 8. Audit and experiment

AnalysisRecord v5 保持唯一原始记录；Codex runtime 元数据进入现有 `role_runtime`、`call_attempts`
和 `experiment_context`。SQLite 只增加可重建的 agent run/tool-call 投影。

新增独立 `analysis_backend_aabb_v1`：A1/A2 为 DeepSeek Direct，B1/B2 为 Codex Agent，使用同一
Frozen Snapshot、独立运行和 ABBA/BAAB 顺序。它评估完整 Backend system value，不能把差异
单独归因于 Agentic runtime，因为模型家族与执行方式同时变化。

Codex 晋级必须通过历史 replay、故障注入、至少 20 个独立 Snapshot 的工程 repeatability、盲评
以及最终至少 300 个独立 Snapshot/100 个明确结果的 Paper forward。Prompt 更短、文字更专业或
单次盈利都不构成晋级证据。

## 明确不变

- Frozen Snapshot、AnalysisFactCatalog 和 `pa_draft_v2` 不变。
- `pa_provider_contracts.py` 继续是唯一 PA materializer。
- Brooks Router 继续只读已验证 Stage 1 enums/pattern keys。
- PA Stage 1/Stage 2 继续 price-only。
- VPA 继续是 independent shadow evidence；生产正式结果仍为 PA Stage 2。
- Joint 继续 research-only。
- Risk、Trader Equation、execution disposition、Paper 与真实执行权限不变。
- Free Chat 不进入 Codex VPS v1。

## 后果与取舍

- 静态 Prompt 可缩小，但 Evidence tool call 可能增加延迟；Token、初始 Context、retrieval Context
  与总延迟必须分开报告。
- Separate worker 和 frozen spool 增加运行模块，但把 ChatGPT 认证、CLI drift 和 Agent 权限从主进程
  隔离，并允许立即回到 Direct Backend。
- 两方 A/B 能证明整体 Backend 是否值得采用，不能证明纯 Agent 因果增益；如未来需要因果分解，
  必须建立新协议和 cohort，不在本 ADR 内增加第三 arm。

## 验证

- Direct Adapter 与当前 Prompt、Draft、Router、VPA off/shadow、records 和 Paper 行为回归一致。
- 每个 Codex role 使用新 ephemeral task；Stage 2 无 Stage 1 chat history。
- Snapshot/contract/knowledge/runtime digest 漂移和越界 Evidence 请求均 fail closed。
- Codex 用户无法修改源码、读取生产 DB/credential 或产生订单。
- 结构或语义错误不触发 fallback；availability fallback 从完整 Stage 1 重启。
- Backend control 只在安全边界 applied，Dashboard 继续不读生产 DB/config。
- Backend A/A/B/B 保留 missing/failed arm、ITT、A/A、B/B、cohort 和独立 Snapshot 口径。
- 完整非 live 回归、wheel resource smoke、Linux shell/systemd 静态检查与 `git diff --check` 通过。
