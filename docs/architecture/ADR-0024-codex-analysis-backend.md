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
- `codex_agent` 在每个 Frozen Snapshot 内建立受控 PA 会话：Stage 1 首稿通过后才允许 Stage 2
  resume；VPA、研究 Joint 与 repair 始终启动独立 ephemeral Codex 任务。所有角色只返回同一
  Provider Draft v2/role draft。

一个 Frozen Snapshot 是 PA 会话的最大生命周期，不是长期 Codex 对话。不同 Snapshot 必须使用
独立会话；同一 Snapshot 只允许 PA Stage 1 → Stage 2 续接，VPA 不进入 PA 会话。Stage 2 的权限
仍只来自已验证 Stage 1 artifact 与 Brooks Router manifest，不能把未验证聊天内容当作 authority。
只有 attempt 1 直接通过的 Stage 1 可以续接；Stage 1 经 repair、Snapshot/cutoff/runtime identity
不一致或 parent job 校验失败时，Stage 2 必须基于已验证 artifact 启动全新 PA 任务。Stage 2 完成
或失败后销毁该 Snapshot 的 PA 会话；不得跨 Snapshot、跨资产或跨 cohort resume。

### 2. Frozen Agent Job

PA Agent 在模型调用前建立不可变 Job manifest，绑定 `request_id`、role、attempt、Snapshot、
cutoff、Provider Contract、output schema、Prompt/knowledge、AGENTS、Skills、Codex CLI、model、
reasoning profile 与预算 digest。Job 通过原子文件发布；任何 digest、角色或过期状态不匹配的
结果均 fail closed。

Codex 初始只接收身份、角色、Snapshot 与 Evidence 索引。完整角色 Context 作为独立
`core_context.json` 进入 Frozen Job v4。首个 research release 采用显式的
`context_policy=semantic_parity_v1`：Codex 必须先读取
PromptAssembler 生成的阶段协议、当前角色的完整 price-only frame、全部 HTF Evidence，以及 PA/Router
选择并按 Windows 成熟 Prompt 边界切片的 mandatory Brooks/VPA 模块；知识 manifest 以 `load_order`
固定成熟 Prompt 的模块顺序。Worker 从 Evidence audit 机械校验读取覆盖与顺序，缺失或乱序即拒绝 Draft。
旧 Frozen Job 没有该策略标记时按其原始契约回放，不用新规则重解释历史结果。完成这条效果基线后，争议所需 Evidence 和非 mandatory 章节才
按需读取。所有读取必须受 Snapshot cutoff、role allowlist、
knowledge manifest、call count 和 byte budget 约束并进入审计。禁止 Web、实时行情、外部 App、
Plugin、数据库查询和写工具。

### 3. Runtime rules and skills

市场分析使用独立 Runtime `AGENTS.md`，不加载源码仓库的开发 Agent 指令。AGENTS 只定义长期
authority、no-lookahead、Evidence grounding、阶段协议、禁止改代码/下单和停止条件。Role Skills
只定义 Stage 1、Stage 2、VPA、研究 Joint 与 pointer repair 的分析程序；它们不得以摘要替代
PromptAssembler 的阶段协议或 mandatory 模块。Brooks/VPA 知识仍由
PA Agent 管理并按 Context/Router 选择，Skill 不成为第二份知识库。

Runtime 资产只读、发布固定并参与 cohort fingerprint。VPS Codex 无权自我修改 AGENTS、Skills、
model rules 或知识。

### 4. Validation and repair

Codex 的 `--output-schema` 是 Provider 边界约束，不替代 PA Agent 的 Draft validation、
materialization、事实校验和执行安全。每个角色最多一次定向修复；程序只投影 Validator 明确授权的
JSON Pointer。Codex 只返回 Patch Envelope，可信 Host 在原稿副本上应用精确 allowlist 后重新进入
原 Validator；未授权新增、删除、置空、数组重排或语义变化不进入接受结果。权限、Snapshot、
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

只使用 ChatGPT account device auth，不配置隐式 OpenAI API key fallback。`codex_only` 不创建或
依赖 DeepSeek client；只有 `deepseek_only` 和 `auto` 的完整冷回退需要 DeepSeek credential。
Worker 周期性执行无敏感信息的登录健康检查，并只投影
`healthy|expired|unauthenticated|unavailable`、heartbeat 与 Runtime identity；凭据、账号标识、原始认证输出和 reasoning text 不进入 Dashboard 或长期日志。

### 7. Dashboard control

ADR-0023 的控制面增加唯一窄例外：Dashboard 可以请求 `desired_backend = deepseek_only | auto |
codex_only`，以及无凭据、allowlisted、版本化的 `AnalysisProfile`。Profile 只能选择服务端发布目录中的
Backend 模型与该模型允许的 thinking/reasoning 组合；只能在 Headless idle boundary 应用，且 requested/effective
Profile identity 必须冻结进 Job 与审计。

初版资产、VPA Shadow 与 HTF 保持固定。看板分开展示 Direct/Codex 的运行成功率、首轮通过率、
延迟 p50/p95、初始/检索 Context 和 Evidence 拒绝；这些是工程指标，不代表交易优势。除上述
Backend/Profile 例外外，其余禁止项不变：Dashboard 仍不能
修改 API key、Base URL、Prompt、AGENTS、Skills、Knowledge、Validator、Router、Risk、Paper、任何文件路径或
VPA/Joint 权限。

切换采用 desired/applied generation：Headless 停止接收新 Snapshot，等待 in-flight Job 完成或
超时后应用 Backend，再按切换前运行状态恢复。已有 Job 的 Backend 身份不可重标或中途混合。

### 8. Audit and experiment

2026-10-10 用户授权的运行增补不改变上述 authority：Profile 可选择 Binance
XAU/BTC/ETH、并发 1/2/3 和三档 Stage 2 倾向，详见 ADR-0023 同日增补。
Codex memories 继续由发布配置和命令行强制关闭；模型不选择其他历史会话。
空闲 Worker 每小时有界调用官方 `codex delete <UUID> --force` 清理超过 14 天、
专属 PA workspace 已不存在且无待执行 dispatch 的会话；保留认证、正式审计与
恢复任务。不直接删除 JSONL 或修改 Codex SQLite，不在角色执行中清理 workspace。
黄金产品/参考时段只作为最新确认收盘边界上的冻结来源事实；单市场成交量不能
代表全球黄金参与者，公共假日日历未知则明确未知，不生成交易 Gate。
本地验证与未发布状态见 [运行升级](../quality/v1_operations_upgrade_20261010.md)。

AnalysisRecord v5 保持唯一原始记录；Codex runtime 元数据进入现有 `role_runtime`、`call_attempts`
和 `experiment_context`。SQLite 只增加可重建的 agent run/tool-call 投影。

新增独立 `analysis_backend_aabb_v2`：A1/A2 为 DeepSeek Direct，B1/B2 为 Codex Agent，使用同一
Frozen Snapshot、HTF、VPA catalog、Shadow authority、独立运行和 ABBA/BAAB 顺序。它通过
`pa-agent-backend-aabb` 运行并同时比较 PA decision 与 VPA evidence signature。它评估完整 Backend
system value，不能把差异
单独归因于 Agentic runtime，因为模型家族与执行方式同时变化。

Codex 晋级必须通过历史 replay、故障注入、至少 20 个独立 Snapshot 的工程 repeatability、盲评
以及最终至少 300 个独立 Snapshot/100 个明确结果的 Paper forward。Prompt 更短、文字更专业或
单次盈利都不构成晋级证据。

## 明确不变

- Frozen Snapshot 和 `pa_draft_v2` 不变；AnalysisFactCatalog 只新增确定性 ATR/EMA/几何测量事实，
  不新增市场标签或交易判断。
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
- 不同 Frozen Snapshot 的 PA 会话完全隔离；同一 Snapshot 的 Stage 1 首稿通过后，Stage 2 受控
  续接同一 PA 会话，并校验 parent job、Snapshot、cutoff、runtime identity 与 thread identity。
- VPA、Joint 与 repair 始终独立；Stage 1 repair 后的 Stage 2 不继承含无效首稿的会话。
- Snapshot/contract/knowledge/runtime digest 漂移和越界 Evidence 请求均 fail closed。
- `semantic_parity_v1` 的 mandatory Evidence 覆盖与 `load_order` 由 Worker 机械验收；历史 Job 不被新策略重解释。
- Codex 用户无法修改源码、读取生产 DB/credential 或产生订单。
- Worker 认证/心跳投影不包含 token、账号标识或原始认证输出。
- 结构或语义错误不触发 fallback；availability fallback 从完整 Stage 1 重启。
- Backend control 只在安全边界 applied，Dashboard 继续不读生产 DB/config。
- Backend A/A/B/B 保留 missing/failed arm、ITT、A/A、B/B、cohort 和独立 Snapshot 口径。
- 完整非 live 回归、wheel resource smoke、Linux shell/systemd 静态检查与 `git diff --check` 通过。

## 2026-10-14 Runtime 与知识覆盖增补（仅本地实现）

用户在研究方案之后授权完整优化及备份后清理。本增补不授权新的交易方向、Gate、
真实交易、模型研究调用或 VPS 发布；当前生产能力以知识库最新状态及 ADR-0025 为准，
本文早期 research-only/Joint 限制不用于否定已独立授权的显式新版 pipeline。

Runtime AGENTS 保留稳定的 authority、冻结事实、完整必读、会话、权限及失败原则。
当前 Role Skill 与冻结 `runtime_guidance` 承接本 Job 的入口、输出及停止语义；
Host challenge 和 layered knowledge 的详细步骤只在原有显式研究 policy 适用时交付，
repair 不继承研究扩展。新 Job 标记 `role_guidance_v1`，Gateway 与 Worker共同校验
角色/模式及交付副本；无标记旧 Job 使用原冻结文本，不以当前文案比较历史文案。

所有基础 Skills、条件说明及角色合同源文参与同一个发布资产 digest；每个 Job 的实际
Skill/protocol 另由资源 hash 与 job_digest 绑定。共同 backend_runtime_identity 比较规则
不变，PA Stage 1→Stage 2 不动态替换共同 AGENTS 或引入 role-specific 共同身份。
不新增 MCP 工具或并行规则引擎。

唯一 Router 复用既有枚举规则，按所有已验证 alternatives 各自的 cycle/direction 补齐
复核知识；不推断缺失阶段/形态。选中文件冻结 purpose 与 hypothesis IDs，Direct/Codex
后续投影保留明确选中的相反方向复核原文。`review` 不独立授权 Setup、翻转订单方向或
改 Gate；最终候选仍依冻结主背景与既有 PA 执行契约。VPA selector 同步覆盖全部周期及
当前 catalog 存在的支持/挑战窗口，不增加解释、概率或 Gate。

完整教材、锚点、案例、mandatory、materializer 与交易校验保留；增加相关教材可能增加
成本，公共指令字节减少不能证明净 token、延迟或质量改善。历史兼容实现、研究产物和
旧 Frozen Job 留存；只清理新任务常驻说明中的不适用细节及已确认无调用的旧 helper。
备份、逐条承接映射、自动化验收及未验证边界见
[实施验收](../quality/v1_runtime_failure_paper_audit_20261012.md)末节。
