---
lifecycle: active
capability: research-only
verification_scope:
  - local
  - VPS-partial
最后核验: 2026-09-05
规格基线: 用户提供的《Codex 版 PA Agent vNext 升级计划》
代码基线: 965f2df472f0e5af11b684c7864e62c7de66f8d1
候选分支: codex-analysis-backend-v1
---

# Codex 版 PA Agent vNext 完整验收报告

## 1. 最终结论

当前结论为：**工程骨架有条件通过，完整计划未验收通过，禁止切换 `auto`，禁止宣称 Codex 已优于
DeepSeek。**

允许的下一步仅限：保持 `paused + codex_only + Paper`，先修复结构化失败分类，再进行单一 Frozen
Snapshot 的无交易权 Stage 1 live smoke。VPS 不得恢复自动调度，DeepSeek Direct 必须继续保留。

本次验收确认了两件同样重要的事实：

1. Codex 没有夺取 PA Agent 的 Snapshot、Brooks Router、materializer、Validator、Risk、Trader
   Equation、持久化或交易权；Windows vNext 的主要交易精华总体保留。
2. 当前实现仍未完成计划要求的完整角色替换、历史错误回放、性能数据和 Paper holdout；并存在一个
   会把 Schema/请求错误误判为 availability 的阻断性缺陷。

因此，“架构方向科学合理”成立；“已完整实现并可替换现有 LLM”不成立。

## 2. 审查范围与证据

- 规格：用户提供的《Codex 版 PA Agent vNext 升级计划》。
- 固定比较点：`965f2df472f0e5af11b684c7864e62c7de66f8d1`。
- 审查范围：`git diff 965f2df...HEAD`、当前未提交 diff、源码、测试、ADR、项目知识库、质量契约。
- 当前本地 HEAD：`2128ef3`；另有尚未提交的 provider schema capability 收敛修改。
- 当前 VPS HEAD：`3b15044`，并非本地最新候选。
- VPS 状态：三个 systemd 服务均为 `active`；运行状态为 `paused`；Backend 为 `codex_only`；
  `XAUUSDT 1h`、HTF `4h/60`、VPA `shadow`、Paper only、并发 1。
- VPS 权限审计：`ok=true`；`pa-agent-codex` 无权读取 DeepSeek credential、生产 records 和 Paper
  ledger，无权写 `/opt/pa-agent`。
- Codex Runtime live canary 已证明 ChatGPT auth、Runtime `AGENTS.md` 与 role Skill 可发现；真实 PA
  Stage 1 尚未成功完成。
- 第一次用非锁定 Anaconda 环境运行全套测试得到 `1257 passed, 48 setup errors`，48 项均因缺失
  `pytest-qt/qtbot`。随后按 `uv.lock` 创建 desktop+dev 环境复验，结果为：
  `1305 passed, 7 deselected, 2 third-party deprecation warnings`。
- 独立 subagent 完成 Standards、Spec 和三角色只读交叉审计；主审再次从源码和 VPS 状态复核。

测试绿色只证明已编码行为没有触发当前非 live 回归，不替代计划的 live、replay、盲评和 Paper
准入证据。

## 3. 阻断性 Findings

### P0：Schema/请求错误可能被误判为 availability 并触发错误 fallback

证据：

- `pa_agent/agent_runtime/codex_worker.py:140-144` 在 Codex 非零退出时只把 `stderr` 交给
  `_classify_failed_process(...)`。
- Codex `--json` 的结构化 `turn.failed/error` 位于 `stdout`；但 `_parse_events(...)` 只在
  return code 为 0 后执行。
- `codex_worker.py:406-407` 即使读到 `turn.failed/error`，也统一抛出
  `CodexWorkerUnavailable`。
- `_classify_failed_process(...)` 的未知默认值仍是 availability。
- VPS 真实重放已复现：OpenAI 返回 `400 invalid_json_schema`，最终被包装成
  `CodexWorkerUnavailable`。

影响：

- `auto` 可能把模型请求/Schema 合同错误伪装成基础设施故障，并启动 DeepSeek 全链 fallback。
- 直接违反计划 9.3 与 Hard Invariant：“Schema、事实、权限错误不得触发 fallback”。
- 污染 A/B 的 ITT、failure taxonomy 和质量归因。

验收结论：**P0，未修复前禁止 `auto`。**

根因修复方向：统一解析 stdout JSONL 的 error payload，以 provider `status/code/type` 映射 typed
failure；只有明确 auth、429、网络、进程启动、MCP 启动或 timeout 才属于 availability；未知错误
默认 fail closed，不能默认 availability。必须增加 `invalid_json_schema`、generic `invalid_request` 和
未知错误不 fallback 的端到端回归。

### P1：Codex 目前只替换 PA Stage 1/2，VPA 仍硬编码 Direct

证据：

- `pa_agent/orchestrator/two_stage.py:2082-2097` 只对 PA baseline 选择 Codex backend。
- `two_stage.py:2098-2100` 始终创建 Direct VPA client。
- `two_stage.py:2232-2238` 的 VPA/Joint role helper 无条件创建 `DirectAnalysisRoleBackend`。
- VPA 的 `AnalysisRoleRequest` 未携带 Codex publisher 所需的 snapshot、cutoff 和 agent context。
- `docs/knowledge/current-status.md` 也明确记录 Codex 只接 PA Stage 1/2。

影响：

- `codex_only` 并不表示本次分析完全由 Codex 取代 LLM；VPA 仍消耗 DeepSeek。
- 不满足计划 Phase 4 的 Stage 1 → Stage 2 → VPA → Joint research-only 顺序。
- 不满足用户“VPA/HTF 标配、最终以 Codex 替代 LLM 部分”的最终愿景。

验收结论：**Phase 4 部分完成。** Brooks official authority 没有因此被破坏，但 Dashboard 命名和
运行审计必须避免造成“全 Codex”的误解。

根因修复方向：在统一 role-backend routing seam 解决，不得继续在 `TwoStageOrchestrator` 的 VPA
分支堆 backend `if`。VPA 仍保持 secondary shadow evidence；Joint 仍只允许 research executor。

### P1：尚未用 Windows 历史错误证明“精华完整保留”

计划 Phase 0/1 要求固定 20 条历史故障 replay manifest，并对 Golden Snapshot 的 Prompt、Draft、
Router 和 Final Decision 做 Direct parity。当前仓库没有完成这组固定 replay，也没有 exact golden
parity 证据。

已确认复用的 Windows vNext 权威组件包括：

- Frozen Snapshot 与 HTF/volume provenance；
- Provider Draft v2 和唯一 `pa_provider_contracts.py` materializer；
- Stage 1 Validator → Brooks Router → Stage 2 Validator/Risk 的顺序；
- VPA shadow official=`pa_only`；
- Trader Equation、execution disposition、Paper 与 AnalysisRecord。

但“调用了原组件”不等于“历史行为完全等价”。没有 replay 前，不得声称以前的 Schema、K0、
Evidence、repair drift、warning explosion 和 provider drift 已被 Codex 全部规避。

### P1：Knowledge Manifest 与 Evidence 调查仍是最小原型

当前具备：Job digest、知识 allowlist、cutoff、role-safe facts、工具次数/字节预算和审计。

当前缺少：

- `framework/role_allowlist/context_tags/version/sha256/source_note/section_ids` 的正式 knowledge manifest；
- 计划列出的 market core、measurements、HTF、VPA、upstream artifact 等独立工具；
- `search_allowed_knowledge` 和按 section 读取/引用；
- Knowledge module/version/hash/section 的输出 grounding。

`evidence_mcp.py` 当前只暴露 `list_fact_ids/get_facts/get_bars/read_knowledge` 四个工具；
`publisher.py` 按文件名 allowlist 装入整篇知识文本。安全边界存在，但精细按需检索和知识引用尚未
完成，不能充分证明已解决超长 Prompt。

### P1：性能、幻觉和交易增益均无准入数据

当前只可靠记录 `initial_context_bytes`、Evidence call count/bytes 和 Codex provider usage。
`backend_aabb.py` 汇总的 `initial_context_tokens/retrieval_context_tokens` 没有完整生产者。

尚无 matched Snapshot 的 Direct/Codex p50/p95，无法判断：

- 静态 Context p95 是否 ≤ Direct 40%；
- 含 retrieval 的总输入 p95 是否 ≤ Direct 75%；
- 总 Token 和完整周期 latency 是否达标；
- hallucination、critical fact error、first-pass schema、B/B repeatability 是否改善；
- Trade/No Trade、confidence calibration 或成本后 Paper 是否改善。

科学结论只能是：**bounded core + on-demand evidence 有降低静态 Prompt 的机制潜力，但尚无性能或
交易增益证据。** Structured Output 只能降低格式错误进入主链的概率，不能自动消除主观市场误判。

## 4. 其他重要 Findings

### P1：Frozen Job stale/expiry/lease/接受幂等未达到计划完整合同

Job 已实现资源 Hash、目录原子发布、cutoff 检查和 spool `.running` 恢复；但 manifest 没有
created/expires/lease，store 不拒绝过期 Job，也没有计划要求的 `(run_id, role, attempt)` 持久唯一
接受账本。Phase 2 只能部分通过。

### P2：Circuit breaker 没有单一 half-open probe

cooldown 到期后会直接重新开放，并非只放行一个并发 probe。首版并发 1 降低了实机风险，但不满足
计划 Phase 5 的退出条件。

### P2：Runtime Skills 资产与计划仍有缺口

Stage 1、Stage 2、VPA、Joint role Skill 已打包并由 live canary 验证可发现；但没有独立
`draft-pointer-repair` Skill。当前 repair 复用角色 Skill 与 Windows pointer projection，权限边界方向
正确，但未完整实现计划列出的资产。

### P2：Dashboard 是可用运维面板，不是完整验收看板

现有面板已支持：pause/resume/run-once、Backend、DeepSeek/Codex model/effort、安全 profile apply、
单资产固定范围、recent run、Paper account 和 stale/CAS 防护。

仍缺计划要求的独立 Quality、Performance、A/B、Grounding/Evidence 页面及完整 p50/p95、错误分类、
ITT/per-protocol 和分层结果。用户后来明确授权面板选择 model/effort，因此该能力不算 scope creep，
但它是对原计划“Dashboard 不改模型/effort”的已授权需求变更，应在 ADR 中显式记录。

### P2：VPS 运维闭环不完整

三用户隔离、`ProtectSystem=strict`、`NoNewPrivileges`、精确 ReadOnly/ReadWrite paths 已实机验证。
仍缺完整 logrotate、Job/outbox retention、backup/restore 自动资产，以及明确的 worker CPU/memory/task
资源上限。旧部署和临时诊断文件尚未清理，因为本次审计主动暂停了继续发布。

### Standards 判断

- 没有发现把 Brooks、VPA 专业判断、Router、Risk 或 Trader Equation 在 Codex 内重写；这是当前
  设计最重要的通过项。
- 当前未提交 diff 正在把 worker 内的 strict schema projection 收敛至既有
  `provider_capabilities.py`，方向比重复实现正确；它同时影响 Direct OpenAI strict-output 路径，必须
  保留 DeepSeek=`json_object` 和原始 PA Validator schema 不变的回归证据后再提交/部署。
- `TwoStageOrchestrator` 同时拥有 PA baseline、VPA Direct client 和 backend-specific routing，形成
  Divergent Change/boundary leak；这正是“Codex 只替换一半角色”的架构根因。
- 原 `codex_backend_final_audit_20260902.md` 的核验锚点为 `2bc71ae`，不能代表当前 HEAD、VPS 或真实
  Codex Stage 1 成功。当前状态文档存在 verification drift，需要本轮整改后更新。

## 5. Phase 0–9 验收矩阵

| Phase | 状态 | 验收判断 |
| --- | --- | --- |
| 0 Worktree/ADR/基线 | 部分 | Worktree、ADR、长期边界完整；缺 20 条固定 replay 与 Direct golden parity |
| 1 Backend seam/Direct | 部分 | PA role seam 与默认 Direct 存在；缺精确 Prompt/Draft/Router/Final 等价证据 |
| 2 Frozen Job/Evidence | 部分 | Hash、atomic、cutoff、allowlist、budget 通过；manifest section、expiry/lease/接受幂等不足 |
| 3 Worker/结构输出 | 部分 | ephemeral/read-only/no shell/Web/MCP/runtime identity 已实现；真实 Stage 1 未成功，错误分类 P0 |
| 4 AGENTS/Skills/roles | 部分 | Runtime AGENTS 和四 role Skill 存在；实际只接 Stage 1/2，repair Skill 缺失 |
| 5 Repair/Fallback/CB | 部分 | pointer projection、完整冷回退、禁 mixed chain 方向正确；P0 failure taxonomy 和 half-open 不足 |
| 6 Audit/Dashboard | 部分 | 调用审计、安全控制和 Paper 摘要可用；完整质量/性能/AABB/Evidence 看板不足 |
| 7 Backend AABB | 部分 | 四臂预登记、ABBA/BAAB、fallback 禁用、missing/ITT 基础存在；无真实样本和完整统计验收 |
| 8 Linux VPS | 部分 | 安装、三用户、systemd、auth、权限审计和 live canary 已部分实机通过；候选未最终部署，运维闭环不足 |
| 9 Replay/Pilot/Holdout | 未完成 | 20 replay、30 pilot、50 blind calibration、300/100 forward holdout 均未完成 |

## 6. 三角色审计

### 软件架构师

**结论：有条件通过架构方向，不通过当前完整交付。**

Backend seam、Frozen Job、独立 worker、Runtime assets 和 Unix 权限隔离是正确基础；PA 权威没有迁入
Codex。阻断项是 failure taxonomy 边界错误和 VPA backend 分叉。下一步应先统一错误分类，再统一
role routing，禁止在 orchestration 内继续叠 backend 特判。

### 量化工程师

**结论：实验骨架可用，实验结论为零。**

AABB 已具备同 Snapshot、四臂预登记、ABBA/BAAB、fallback 禁用、missing arm 和 cohort 基础；但
没有 fixed historical replay、matched live 样本、真实 token/retrieval 指标、盲评和 forward Paper。
任何“更少幻觉、更快、更省 Token、更赚钱”的结论当前都不成立。

### 顶级 Brooks PA/VPA 交易员

**结论：交易 authority 保存良好，但分析增量未验证。**

Brooks 保持 Primary，VPA 保持 Secondary shadow evidence，No Trade 仍是合法输出，Router、Validator、
Risk 和 Trader Equation 未放权。可是 Codex VPA 尚未接通，历史 Context/K0/Evidence 错误也未 replay；
因此无法证明 Codex 更少遗漏 Breakout acceptance、leg comparison、volume context 或关键 invalidation。

## 7. Gate 决策

| 动作 | 结论 | 条件 |
| --- | --- | --- |
| 保持 VPS paused | 必须 | 当前已满足 |
| DeepSeek Direct 保留 | 必须 | 不删除、不改默认晋级口径 |
| Codex Stage 1 live smoke | 有条件允许 | 先修 P0；`codex_only`、单 Snapshot、Paper、无交易权 |
| Codex Stage 1→2→VPA smoke | 暂不允许宣称完整 | 先统一 VPA role backend 和 knowledge/evidence 合同 |
| `auto` availability fallback | 禁止 | P0、mixed role、replay、pilot 未通过 |
| 自动调度/默认 Codex | 禁止 | Phase 9 与全部准入门槛未完成 |
| 声称性能/幻觉改善 | 禁止 | 无 matched p95/错误率数据 |
| 声称交易质量或盈利改善 | 禁止 | 无盲评与 300/100 forward holdout |

## 8. 最小整改顺序

1. 修复 P0：从 stdout JSONL 解析 typed provider failure，未知错误 fail closed；验证 Schema/request/
   policy error 不 fallback。
2. 从 Windows 原始 20 条错误建立固定 replay manifest 和 Direct golden parity，先证明成熟主链未丢失。
3. 在统一 role-backend seam 接入 Codex VPA；Joint 保持 research-only；不得在 `TwoStageOrchestrator`
   继续堆 backend 分支。
4. 完善 Knowledge manifest 和按 section/search 的只读 Evidence；所有引用绑定 module/version/hash/
   section 和 Frozen Snapshot。
5. 完成单 Snapshot Stage 1 → Stage 2 → VPA smoke，再执行 20 replay 和 30 Snapshot engineering pilot；
   同时补齐 static/retrieval/total token 与 latency 指标。
6. 完善 Dashboard Quality/Performance/AABB/Evidence projection，以及 VPS retention/logrotate/backup。
7. 完成 50 Snapshot 盲评和 300 Snapshot/100 decisive outcome holdout 后，再进行三角色晋级复审。

## 9. 对“完全规避以前错误”的科学表述

软件不能通过一次重构保证“以后不再出现任何同类错误”。可以且必须做到的是：

- 复用 Windows vNext 的单一权威组件，而不是在 Codex 内重写；
- 把已知错误固化为 replay/contract/fault-injection 测试；
- 用 Frozen identity、typed failure、Validator 和审计让错误 fail closed 且可定位；
- 用 matched AABB、盲评和 Paper holdout 判断 Agentic investigation 是否产生真实增量；
- 每次 runtime/model/AGENTS/Skills/knowledge 变化创建新 cohort，禁止拿旧证据替新版本背书。

本轮最重要的纠偏是：停止把真实 Schema 400 当作“Codex 暂不可用”，并停止用旧审计文档或单次
canary 代表完整计划已经实现。
