# PA Agent Obsidian Knowledge Base Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在仓库内构建可直接作为 Obsidian Vault 使用、同时兼容 GitHub/Codex 的 PA Agent 项目知识库，完整沉淀架构、链路、原则、状态、问题经验、排障与研究记忆。

**Architecture:** `docs/knowledge/README.md` 是唯一入口，十个主题笔记各自承担单一职责，并用标准 Markdown 相对链接形成 Obsidian 可索引的知识图谱。`AGENTS.md` 是唯一强制工作流入口；知识库只索引和综合 `CONTEXT.md`、ADR、质量契约、源码与版本化运行证据，不建立第二套规范。

**Tech Stack:** Markdown、Obsidian Properties、Git、PowerShell/Python 只读链接校验。

**Spec:** `docs/superpowers/specs/2026-08-26-project-knowledge-base-design.md`

## Global Constraints

- 仓库是唯一知识入口，不创建仓库外 Obsidian 副本。
- 不新增依赖，不修改 PA/VPA/Joint 运行代码，不重排既有 ADR 和 Prompt 文件。
- `AGENTS.md` 管强制行为，`CONTEXT.md` 管术语与责任，ADR 管架构意图，代码与版本化产物管实现事实。
- 知识库不得新增交易规则、程序化 PA/Wyckoff 判断、自由文本硬 Gate 或盈利承诺。
- 正式分析保持 PA-only authority + VPA Shadow；Joint 仅为隔离 A/A/B/B 研究。
- 每条当前状态必须区分 capability、verification scope 和证据 commit。
- 机器绝对路径、凭据、单次聊天流水账和未经复核猜测不得进入知识库。
- 每个实施任务完成后运行直接相关验证、清理临时文件并独立提交。

---

### Task 1: 建立知识入口、项目概览与当前状态

**Files:**
- Create: `docs/knowledge/README.md`
- Create: `docs/knowledge/project-overview.md`
- Create: `docs/knowledge/current-status.md`

**Interfaces:**
- Consumes: 设计规范、`CONTEXT.md`、`docs/quality/pre_release_final_audit_20260813.md`、ADR-0023、Git history。
- Produces: 后续所有主题笔记使用的导航规则、状态词和当前证据基线。

- [ ] **Step 1: 核对当前事实基线**

Run:

```powershell
git log --oneline --decorate -12
git status -sb
Get-Content -Raw CONTEXT.md
Get-Content -Raw docs/quality/pre_release_final_audit_20260813.md
Get-Content -Raw docs/architecture/ADR-0023-vps-observability-and-control-plane.md
```

Expected: 确认 `311b12a` 为知识库设计提交、`f8fffc0` 为 Dashboard 提交；2026-08-13 全量回归是历史证据；VPS/systemd 实机和 Paper forward 增益仍未验证。

- [ ] **Step 2: 创建唯一索引 `README.md`**

写入：知识库用途、Obsidian 打开方式、事实类型与权威来源矩阵、四维状态词、按“修改分析/排障/VPA研究/VPS/发布审计”划分的最短阅读路径，以及十个主题笔记的 Markdown 链接。正文不得复制各主题细节。

- [ ] **Step 3: 创建项目概览**

写入：目标、用户场景、正式分析能力、研究能力、非目标、主要入口、数据和安全边界。明确“无人值守分析 + Paper execution”不等于实盘生产或盈利系统。

- [ ] **Step 4: 创建当前状态**

写入：发布基线/核验 commit、模式矩阵、已实现能力、2026-08-13 审计后的 Dashboard 变更、已知风险、VPS 前置条件和 Joint 研究待办。每项标注 capability、verification scope 和证据链接。

- [ ] **Step 5: 验证并提交 Task 1**

Run:

```powershell
rg -n "自动实盘|已经盈利|完全验证|D:\\|C:\\Users|T[B]D|T[O]DO" docs/knowledge
git diff --check
git status --short
git add docs/knowledge/README.md docs/knowledge/project-overview.md docs/knowledge/current-status.md
git commit -m "docs: add PA Agent knowledge hub and status"
```

Expected: 敏感/占位扫描无命中，格式检查通过，只提交三个知识文件。

### Task 2: 建立架构图谱与逐步分析链路

**Files:**
- Create: `docs/knowledge/architecture-map.md`
- Create: `docs/knowledge/analysis-pipeline.md`

**Interfaces:**
- Consumes: Task 1 状态口径、`CONTEXT.md`、ADR-0001 至 ADR-0023、`docs/wyckoff_vpa_integration.md`。
- Produces: 组件级导航和完整运行链路，供排障、研究和后续修改引用。

- [ ] **Step 1: 映射真实入口和核心模块**

Run:

```powershell
rg -n "def main|class .*Orchestrator|compile_pa_handoff|resolve_vpa_mode|RunOutcome|execution_disposition|status.json|control.json" pa_agent
rg -n "ADR-00(0[1-9]|1[0-9]|2[0-3])" docs/architecture
```

Expected: 找到 GUI/Headless、分析编排、PA handoff、VPA 模式、持久化、Paper、调度和控制面实现，不依据文件名猜测职责。

- [ ] **Step 2: 创建架构图谱**

写入 Mermaid 总览图、模块职责表、数据/控制面分离、Dashboard 最小权限边界、权威 ADR 索引和失败边界。明确 Dashboard 只读脱敏投影、只写 desired state，systemd 管进程生命周期。

- [ ] **Step 3: 创建分析链路**

逐步记录每一步的输入、操作者、判断权、处理、输出和失败语义。必须准确覆盖 Snapshot → Evidence Context → Stage 1 Draft/materialize/Gate → Stage 2 Draft/materialize/execution disposition → handoff → VPA Shadow → 隔离 Joint 研究 → records/audit/Paper/control projection。

- [ ] **Step 4: 验证并提交 Task 2**

Run:

```powershell
rg -n "Stage 1|Stage 2|compile_pa_handoff|VPA Shadow|Joint|desired|applied|systemd" docs/knowledge/architecture-map.md docs/knowledge/analysis-pipeline.md
git diff --check
git add docs/knowledge/architecture-map.md docs/knowledge/analysis-pipeline.md
git commit -m "docs: map PA Agent architecture and analysis pipeline"
```

Expected: 两份文档均覆盖正式/研究边界和失败语义，格式检查通过。

### Task 3: 建立三角色原则索引、术语索引与研究评价记忆

**Files:**
- Create: `docs/knowledge/principles-and-boundaries.md`
- Create: `docs/knowledge/glossary.md`
- Create: `docs/knowledge/research-and-evaluation.md`

**Interfaces:**
- Consumes: `AGENTS.md`、guardrail skill、`CONTEXT.md`、ADR-0001/0003/0005/0009/0017/0020/0021、VPA 集成规范、质量契约。
- Produces: 非规范性原则入口、项目术语入口、Shadow/Joint 研究与晋级标准。

- [ ] **Step 1: 创建三角色原则索引**

用 `PA-*`、`VPA-*`、`ARCH-*` 稳定编号组织“一句话摘要、适用模块、权威来源”。覆盖程序/LLM 职责、Evidence first、唯一 Router/handoff、off 零泄漏、宽严校验尺度、样本坍塌防护和正式分析权限。不得在索引中独立发明新规则。

- [ ] **Step 2: 创建术语索引**

只写中文别名、英文缩写、项目内使用位置和 `CONTEXT.md`/ADR 链接。覆盖 PA、VPA、Joint、Shadow、Soft、Gate、Candidate、Execution Disposition、Snapshot、Cohort、Run Outcome、Paper、结构止损和假设失效。

- [ ] **Step 3: 创建研究评价文档**

写清 Shadow、Soft、A1/A2/B1/B2、配对增量、ITT、per-protocol、censored、cohort、独立样本、Paper v1/v2 分离和 Joint 晋级要求。单列“当前能得出的结论”和“当前不能得出的结论”。

- [ ] **Step 4: 验证并提交 Task 3**

Run:

```powershell
rg -n "PA-[0-9]|VPA-[0-9]|ARCH-[0-9]|A1/A2|B1/B2|ITT|per-protocol|300|100" docs/knowledge/principles-and-boundaries.md docs/knowledge/research-and-evaluation.md
rg -n "CONTEXT.md" docs/knowledge/glossary.md
git diff --check
git add docs/knowledge/principles-and-boundaries.md docs/knowledge/glossary.md docs/knowledge/research-and-evaluation.md
git commit -m "docs: capture PA VPA principles and research protocol"
```

Expected: 原则均有权威链接，术语不重复定义，研究门禁和不确定性表达完整。

### Task 4: 沉淀历史问题族与运行排障手册

**Files:**
- Create: `docs/knowledge/problem-history.md`
- Create: `docs/knowledge/operations-and-debugging.md`

**Interfaces:**
- Consumes: 设计规范列出的 17 个问题族、ADR、质量审计、Git history、部署文档和当前运行状态模型。
- Produces: 防止重复犯错的根因记忆，以及按失败边界执行的排障路径。

- [ ] **Step 1: 为 17 个历史问题族建立完整记录**

每个条目必须包含 `issue_status`、症状、影响、跨样本根因、错误修法、正确原则、当前实现/ADR、回归证据和复发检查。相近条目可在同一问题族下分子项，但不得遗漏 Draft 修复、Paper 路径、instrument identity、volume provenance、线程/恢复、资源打包和 thinking applied 状态。

- [ ] **Step 2: 创建运行与排障手册**

按“保留证据 → 识别 RunOutcome/失败角色 → 检查输入身份 → 检查 Provider → 检查契约/事实 → 检查持久化/线程 → 决定是否修复”组织。覆盖 Windows GUI、Linux Headless、Dashboard、PA-only 降级、VPA warning、API 错误、数据源、records/audit/SQLite 和第三次失败升级架构审查。

- [ ] **Step 3: 验证并提交 Task 4**

Run:

```powershell
rg -c "^### PH-" docs/knowledge/problem-history.md
rg -n "症状|影响|根因|错误修法|正确原则|回归证据|复发检查" docs/knowledge/problem-history.md
rg -n "completed_with_warnings|degraded_pa_only|unavailable_input|unavailable_provider|invalid_model_output|internal_error" docs/knowledge/operations-and-debugging.md
git diff --check
git add docs/knowledge/problem-history.md docs/knowledge/operations-and-debugging.md
git commit -m "docs: record PA Agent failures and debugging playbook"
```

Expected: `PH-*` 问题族数量至少为 17；所有模板字段和 RunOutcome 分类存在。

### Task 5: 建立维护机制并接入 Agent 工作流

**Files:**
- Create: `docs/knowledge/maintenance.md`
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/specs/2026-08-26-project-knowledge-base-design.md`

**Interfaces:**
- Consumes: Task 1-4 的知识结构。
- Produces: 后续任务的唯一必读入口、更新触发器、冲突登记和归档方法。

- [ ] **Step 1: 创建非规范性维护手册**

写入任务开始/完成检查清单、知识更新触发示例、事实冲突登记格式、问题升级标准、状态归档命名和“何时不更新”。明确所有强制语义只来自 `AGENTS.md`。

- [ ] **Step 2: 更新 `AGENTS.md`**

在现有必读规则附近新增一条：非琐碎任务先读 `docs/knowledge/README.md`，按任务阅读相关主题和权威来源；架构、能力状态、可复用根因、验证范围或弃用状态变化时，在完成前更新知识库。不得要求每次读取全部知识文件，也不得把临时 `docs/提示词优化.md` 设为必读。

- [ ] **Step 3: 标记设计已实施**

把设计规范顶部状态改为 `Accepted`、实施状态改为 `Implemented`，链接 `docs/knowledge/README.md` 和本实施计划，不改写设计历史。

- [ ] **Step 4: 验证并提交 Task 5**

Run:

```powershell
rg -n "docs/knowledge/README.md" AGENTS.md docs/superpowers/specs/2026-08-26-project-knowledge-base-design.md
rg -n "提示词优化.*必读|每次.*全部" AGENTS.md
git diff --check
git add AGENTS.md docs/knowledge/maintenance.md docs/superpowers/specs/2026-08-26-project-knowledge-base-design.md
git commit -m "docs: integrate knowledge workflow into project guardrails"
```

Expected: 第一条命令命中入口，第二条命令无命中，格式检查通过。

### Task 6: 全库链接、事实与三角色验收

**Files:**
- Modify only when verification finds a documentation defect: `docs/knowledge/*.md`

**Interfaces:**
- Consumes: Task 1-5 全部文件。
- Produces: 可发布的 Obsidian/GitHub 双兼容知识库和最终验收证据。

- [ ] **Step 1: 校验所有仓库相对链接**

Run a read-only Python link checker that parses `docs/knowledge/*.md`, ignores external URLs and anchors, resolves each relative Markdown target from its source file, and exits non-zero for missing files. Do not save the checker in the repository.

Expected: missing link count 为 0。

- [ ] **Step 2: 执行占位、敏感信息和职责漂移扫描**

Run:

```powershell
rg -n "T[B]D|T[O]DO|PLACE[H]OLDER|C:\\Users|D:\\new_paagent|sk-[A-Za-z0-9]|Bearer [A-Za-z0-9]|保证盈利|已经盈利" docs/knowledge AGENTS.md
rg -n "VPA.*修改.*win_rate|VPA.*创建.*Setup|Joint.*正式分析结果|程序.*判断.*Spring|程序.*判断.*UTAD" docs/knowledge
git diff --check
```

Expected: 无占位、路径、凭据、盈利承诺或权限漂移命中；格式检查通过。

- [ ] **Step 3: 三角色独立复核**

PA 交易员检查 Brooks authority、Setup/stop/entry 语义；VPA 交易员检查 Background/Location/Effort-Result/sequence/acceptance 和不越权；量化/软件架构师检查事实源、状态证据、运行边界、维护规则与重复内容。只修复可定位的文档缺陷，不扩张运行代码范围。

- [ ] **Step 4: 检查工作区和历史**

Run:

```powershell
git status -sb
git log --oneline -8
git diff --check
```

Expected: 无临时文件；Task 1-5 均有独立提交；未触碰 PA/VPA 运行代码。

- [ ] **Step 5: 提交验收修正（仅在有修正时）**

```powershell
git add docs/knowledge
git commit -m "docs: finalize PA Agent knowledge base review"
```

Expected: 若无修正则不创建空提交；最终工作区干净。
