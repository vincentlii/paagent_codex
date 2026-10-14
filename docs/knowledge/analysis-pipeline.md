---
lifecycle: active
capability: no-go
verification_scope: not-verified
最后核验: 2026-10-09
核验提交: working-tree audit（未提交）
权威来源:
  - CONTEXT.md
  - pa_agent/orchestrator/two_stage.py
  - docs/architecture/ADR-0017-deterministic-facts-and-execution-semantics.md
  - docs/architecture/ADR-0018-evidence-context-and-provider-drafts.md
  - docs/architecture/ADR-0024-codex-analysis-backend.md
---

# 分析链路

本页以下 PA-only Stage 2、Shadow 与旧研究 Joint 的表格描述旧 `vpa_mode`
链路；显式 `analysis_pipeline_v1:pa_vpa_joint` 另按
[ADR-0025](../architecture/ADR-0025-joint-stage2-pipeline.md) 运行。
联合 Stage 2 只接收已验收 VPA Report 而非原始 catalog，在 Brooks Router
内独立形成未校准联合先验；不得据下文“Stage 2 不读取 VPA”删除新版链路。

本页描述当前链路的输入、操作、判断权、输出和失败边界。字段级行为以源码、Provider Contract 和 [CONTEXT.md](../../CONTEXT.md) 为准。

2026-10-14 对下述 Runtime 指令与多假设知识覆盖补充本地实现；没有重新验收本页其他
历史能力或发布到 VPS。当前部署/授权状态以[当前状态](current-status.md)为准，
下文旧能力标签不撤销新版显式 pipeline 的有限 Paper 授权。

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

### 3.1 Analysis Backend seam

Stage 1、Stage 2、VPA 和研究 Joint 的模型调用现已通过 `AnalysisRoleBackend` interface；当前
`DirectAnalysisRoleBackend` 保持原 Provider、structured output、role profile 和 fallback 行为；
`CodexAnalysisRoleBackend` 已返回同一 Provider Draft/role draft，并进入相同 materializer、Validator、
Router、Risk 与持久化链。Codex 只在同一 Frozen Snapshot 内按需读取授权 Evidence；不同 Snapshot
会话完全隔离，同一 Snapshot 仅 PA Stage 1 首稿通过后受控续接 PA Stage 2。VPA、Joint 与 repair
始终独立。Codex 能力仍为 `research-only`。

Codex 调用前必须生成 Frozen Agent Job v4。Job 以 canonical JSON digest 绑定 Snapshot cutoff、请求、
output schema、角色可见 facts/knowledge、Runtime AGENTS/skill 与预算，并以完整目录原子发布。
`FrozenEvidenceGateway` 不获取行情、不重新计算交易信号，只返回 Job 内的事实、K 线和知识；每次
访问进入内存审计，cutoff 越界、digest 篡改、角色越权或调用/字节超预算均拒绝。
`initial_context_bytes` 只表示 Job request 中 core-context JSON 的 UTF-8 字节数，是初始输入下界；
它不包含 CLI wrapper、AGENTS、Skill、schema 或模型端 tokenization，不能当作总 Prompt/token 数。

生产 publisher 不复制 Direct 路径的完整超长 Prompt。初始输入只包含角色、Snapshot identity 与
Evidence 索引；完整角色 Context 进入 digest-bound `core_context.json`。但“初始短”不等于允许语义
变薄：新 Stage 1 使用 `context_policy=bounded_context_v2`，由 Host 在发布时把完整 price-only frame、全部 HTF Evidence、
PA fact spine、去重后的阶段协议、原文 foundation sections 与完整 section index 冻结为一个 Decision Packet；
其余知识原文仍可按精确 section ID 读取，最多一次事实批量和一次知识批量调查。Stage 2/VPA/研究角色继续
`semantic_parity_v1`，完整读取 PA/Router 当前选中的 mandatory 知识切片。Stage 1 的
`二元决策` 只含 §0–§8，Stage 2 只含全局原则与 §9–§15，避免跨阶段污染；Router 策略文件保持全文。
知识 manifest 的 `load_order` 保留 Windows 成熟 Prompt 的模块顺序。Worker 根据 context policy 分别拒绝
缺失/重复 Decision Packet、超出调查 envelope，或跳过/乱序 mandatory Context 的 Draft；旧 Job 继续按原契约回放。完成基线后，额外事实和章节才按
争议点读取。知识选择权仍属于 PA Agent，Codex 不能遍历仓库或自行引入另一套交易体系。不同 Frozen
Snapshot 之间绝不续接；同一 Snapshot 内，只有 attempt 1 直接通过原 Validator 的 PA Stage 1 可以由
PA Stage 2 受控续接。Stage 2 同时必须读取冻结的结构化 Stage 1 handoff 与 Router 结果，聊天历史不构成
权限或事实来源。VPA、Joint 与 repair 始终独立；Stage 1 经 repair 后，Stage 2 从已验证 Artifact 新开任务。

新发布的 Stage 2／VPA 首稿以 `baseline_receipt_v1` 要求合并必读基线最终组装成功的 Host 审计标记；
逐项资源读取成功但最终回执／分层索引组装失败，或绕过 `get_role_baseline` 逐项调用工具，均不得接受 Draft。
这是交付完成标记，不代表模型已充分理解每个章节；历史 Frozen Job 没有该标记，仍按冻结时协议回放。
原 Direct 人设文件不改，Codex 在知识投影时把普通 Stage 1／Stage 2 的“裸 JSON”措辞改为
服从冻结输出 Schema／`result` 封套；仅显式联合 Stage 2 投影 VPA 权限，Stage 1 与普通 Stage 2 仍 price-only。

新 Job 的 `role_guidance_v1` 将当前角色入口、输出/失败模式冻结进 role_protocol；Stage 1
packet 与 repair findings 各自携带一致副本，其他角色经 baseline 读取。公共 AGENTS 在
同版本及 PA 续接中稳定；研究 child/layered 的详细说明仅在原有显式 policy 下交付，repair
没有这些扩展。全部说明原文纳入发布资产 digest，实际选择纳入 Job digest；Gateway/Worker
拒绝未知 marker、角色/模式错配或副本不一致，无 marker 的历史 Job 不迁移。

PA Stage 1/2 的 `bars.json` 在 Job 构建时删除 `volume/amount/volume_state`；完整价格帧只组合 OHLC 与
确定性 indicator/geometry measurements。VPA 原始量价证据保留在独立角色目录中，防止 Brooks 主分析
因工具读取绕过 VPA Shadow 边界。

Codex CLI Worker 对 VPA、Joint、repair 与无法安全续接的 PA 任务使用
`codex exec --ephemeral --sandbox read-only --output-schema`；对合格的同 Snapshot PA 链使用专用
workspace 执行 Stage 1，并在 Host 核验 parent job/thread/task/Snapshot/cutoff/runtime identity 后以
`codex exec resume` 执行 Stage 2。运行时使用
独立且 digest 校验的 `CODEX_HOME/config.toml` 与 `rules/pa-agent.rules`，强制 ChatGPT login，禁用
shell、Web、image 与 skill dependency install，
并要求 frozen-evidence stdio MCP 成功启动。AGENTS 与单一 role skill 从 Job 内已绑定的 Runtime
资产生成；CLI JSONL 只提取 thread ID 与 token usage，不长期保存 reasoning stream。Worker 校验
输出大小、UTF-8、JSON 完整性、mandatory Context 覆盖与 Evidence audit 后发布 outbox；Provider Draft 字段与专业语义继续
由 PA Validator 判断，并且 Codex 每个角色最多一次定向修复。Codex 进程不直接写数据库。

生产 Headless 不直接 fork Codex CLI，而是将只含 Job identity 的 dispatch 写入共享 spool；独立
`pa-agent-codex` systemd worker 读取只读 Job 并运行 CLI。Codex 用户没有生产 state、数据库、
Paper ledger 或 DeepSeek systemd credential 的文件权限。实际 model、reasoning、Runtime version
和 Codex binary digest 同时进入冻结 task cohort、call audit 与运行时一致性检查。
该 identity 也写入 Frozen Job 与 dispatch；隔离 worker 每次按自身 model/reasoning、当前 binary 和
Runtime assets 重算，成功结果必须回传同一 identity，Headless 才允许进入 PA Validator。
Worker 另以 `.worker-status.json` 发布 heartbeat、认证枚举和 Runtime identity；该投影只允许
`healthy|expired|unauthenticated|unavailable`，不得包含账号、token、原始登录输出或 reasoning。

Adapter 接受 Worker 结果前再次核对 role、messages、output schema、Snapshot hash、cutoff 与 attempt。
CLI/auth/rate-limit/timeout 等 availability 故障可请求从 Stage 1 开始的完整 Direct PA 冷回退；主链
所有可见 callback 在成功前缓冲，失败后全部丢弃，Direct fallback 来源在 record 持久化前写入。
Schema、事实、权限、Job identity 与专业判断失败不可触发 fallback。

当生产 VPA 模式为 Shadow 时，所选 Backend 同时承担 PA Stage 1/2 与独立 VPA Evidence role；
VPA 仍只作为二级证据写入 sidecar，不进入 `pa_only` official decision。`codex_only` 不隐式创建
DeepSeek client；`auto` 只在 Codex availability 故障时从 Stage 1 冷启动完整 Direct 链。

## 4. PA Stage 1 Draft、物化与 Gate

| 项目 | 内容 |
| --- | --- |
| 输入 | price-only Snapshot facts、Brooks 核心知识、Context 模块和 Provider Contract |
| 判断者 | LLM 判断市场背景、周期位置、Always-In、主/替代假设、方向和 Gate 所需专业语义 |
| 程序操作 | Draft v2 Schema、证据身份、明确 OHLC 事实、权限校验；单一 materializer 生成静态 trace 元数据和 Gate |
| 输出 | 已验证 Stage 1、pattern keys、`gate_result` |
| 失败 | 可修复契约问题最多一次定向修复；仍不可信为 `invalid_model_output` |

Gate 不通过时短路 Stage 2。Brooks Router 只读取已验证枚举和 pattern keys，不从自由文本或程序指标反推策略。

Router 的知识选择覆盖主背景及全部真实 alternatives 各自的 cycle/direction，原主路径
保留，复核模块稳定去重；manifest 标记 purpose 与来源 hypothesis IDs。`review` 只供竞争
解释、风险及等待，不独立授予 Setup/反向交易或改变 Gate；Direct/Codex 都保留这些被选中
的复核原文。旧无结构化 alternatives 调用保持原列表顺序，完整教材与 mandatory 不变。

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
| 失败 | 保留有效 PA，固定记录 `completed_with_warnings` 与 VPA 失败角色 |

VPA 不是审批 Gate。它不能创建 Setup、翻转方向、修改 `cycle_position`/`gate_result`、订单价格或 `estimated_win_rate`。正式结果仍是 PA Stage 2。

知识 selector 同时覆盖全部已声明假设的周期；有限证据选择收集支持及挑战引用中 catalog
已经存在的窗口。缺失窗口不补造，事实目录/身份不改写。与 Runtime/Router 变更一起的
本地验收、备份和未验证边界见[实施验收](../quality/v1_runtime_failure_paper_audit_20261012.md)末节。

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

Analysis Backend 另有独立 `analysis_backend_aabb_v2`：A1/A2 为 DeepSeek Direct，B1/B2 为
Codex Agent。四臂都运行 HTF + VPA Shadow，复用 Frozen Snapshot、同一 VPA catalog、Router、
Validator 和 PA-only authority，但不复用 PA/VPA
实验的 treatment 语义或统计集合。四臂禁用 provider/backend fallback；不可用臂保留在 coverage，
只评价完整 Backend system value，不能把差异单独归因于 Agentic runtime。

## 10. 持久化、Paper 与运行投影

| 项目 | 内容 |
| --- | --- |
| 输入 | 正式 PA、可选 VPA、调用/校验/资源 digest、执行语义和任务终态 |
| 操作者 | [`pa_agent/records/`](../../pa_agent/records/)、[`pa_agent/execution/`](../../pa_agent/execution/)、[`pa_agent/operations/`](../../pa_agent/operations/) |
| 输出 | JSON record、audit、SQLite projection、Paper ledger/report、脱敏 runtime status |
| 程序原则 | 只有完整落盘成功进入去重历史；真实 outcome、反事实 outcome、Paper v1/v2 分离 |
| 控制 | Dashboard 写 desired state/backend；Headless 在安全边界应用并发布 applied generation；systemd 管进程 |

同柱成交路径无法确定时保持 ambiguous/censored，不猜测先后；反事实 Paper 不能称为真实 PnL。

## RunOutcome 语义

| 状态 | 含义 | 是否可有正式 PA 结果 |
| --- | --- | --- |
| `completed` | 全部要求角色和持久化成功 | 是 |
| `completed_with_warnings` | 正式 PA 有效，附带非致命 warning | 是 |
| `degraded_pa_only` | 隔离研究 Joint 失败，研究臂退回 PA baseline | 是（研究 arm） |
| `unavailable_input` | 数据、身份或 Snapshot 不可用 | 否 |
| `unavailable_provider` | Provider/API/网络不可用 | 否 |
| `invalid_model_output` | 模型输出在有界修复后仍不可信 | 否 |
| `cancelled` | 用户或进程协作取消 | 否 |
| `internal_error` | 未分类内部异常 | 否 |

界面中的“分析未通过”只是用户提示，不能作为根因。排障必须继续读取 RunOutcome、失败角色、validation issues、provider failure、record/audit 和日志，见 [运行与排障](operations-and-debugging.md)。

## 权限摘要

> 程序测量事实并保护契约与订单安全；PA LLM 定义市场框架与 Setup；VPA LLM 补充或挑战量价证据；隔离 Joint LLM 在已有候选内裁决；正式结果始终由 PA Stage 2 提供。

继续阅读：[架构图谱](architecture-map.md) · [问题历史](problem-history.md) · [研究与评价](research-and-evaluation.md)
