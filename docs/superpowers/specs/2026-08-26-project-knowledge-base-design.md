# PA Agent 项目知识库设计规范

设计状态：`Accepted`

设计日期：2026-08-26

实施状态：`Implemented`；入口见 [`docs/knowledge/README.md`](../../knowledge/README.md)，步骤见[实施计划](../plans/2026-08-26-project-knowledge-base.md)

## 1. 背景与目标

PA Agent 已形成较完整的 Brooks PA、Wyckoff VPA、A/A/B/B 研究、GUI、Linux Headless、Paper 执行和 VPS 控制面体系，但长期知识分散在 `AGENTS.md`、`CONTEXT.md`、ADR、质量报告、部署文档、Prompt 知识文件、Git 历史与运行记录中。未来任务若只读取其中一部分，容易重复探索、遗漏边界，或再次采用已被证明不合适的修复方式。

本次新增仓库内知识库，作为项目的导航、状态、经验和决策记忆层。它应帮助开发者和 Agent 快速回答：

1. 项目是什么，当前正式能力和研究能力分别是什么。
2. 一次分析、研究、执行和控制请求如何流动。
3. 哪些事实由程序负责，哪些专业判断由 LLM 负责。
4. 当前实现到了哪里，哪些结论已经验证，哪些仍是研究假设。
5. 历史上出现过哪些问题，根因是什么，哪些修法不可重复。
6. 遇到运行失败、分析降级、VPA 失败或部署问题时，应该先检查什么。
7. 修改后需要更新哪些文档和执行哪些验证。

知识库不取代代码、ADR、质量契约、理论知识文件或原始运行记录，也不承诺交易盈利。它通过链接和摘要组织这些权威材料，避免再建立一套平行规范。

## 2. 非目标

- 不把全部源码、ADR 或 Prompt 内容复制进知识库。
- 不把临时任务流水账、单次对话和未经复核的猜测写成长期事实。
- 不把 `docs/提示词优化.md` 升级为权威规范；它仍是临时实验问题日志。
- 不引入 Obsidian、数据库、文档生成器或新依赖。
- 不在本次工作中重排既有 ADR、研究资料或历史报告目录。
- 不把测试通过等同于 VPS 实机验证、策略有效或盈利能力得到证明。

## 3. 核心设计原则

### 3.1 仓库是唯一知识入口

知识库位于 `docs/knowledge/`，随 Git 分支、worktree、VPS 部署包和代码版本共同演进。首版不维护外部 Obsidian 镜像，避免仓库与外部笔记形成 split-brain。

### 3.2 每类事实只有一个权威来源

知识库负责解释、索引和标注状态，不重复定义机器契约或交易理论。不同资料拥有不同类型的事实，不能用一条全局排名掩盖架构漂移：

| 事实类型 | 权威来源 |
| --- | --- |
| 强制开发与修改规则 | `AGENTS.md` 与项目 guardrail skill |
| 领域术语、角色责任和长期不变量 | `CONTEXT.md` |
| 架构意图与取舍 | 已接受 ADR |
| 机器契约和当前可执行行为 | 当前代码、Schema、配置和质量契约 |
| 验证行为 | 与 commit、环境和命令绑定的测试结果 |
| 某次实际运行事实 | 与代码/Prompt/provider/cohort 版本绑定的不可变 record、audit 和 artifact |
| Brooks PA 与 Wyckoff VPA 理论 | 专项理论资料和实际 Prompt 知识源 |
| 当前综合状态与历史经验 | 知识库摘要，必须链接上述证据 |

规范意图与当前实现冲突时，记录为“架构漂移”并明确解决方向，不能默认代码覆盖 ADR，也不能默认旧 ADR 覆盖已经接受的新实现。历史运行产物只能证明其绑定版本的一次事实，不能代表当前实现。

### 3.3 完整记录不等于无限复制

“完整”指覆盖项目目标、架构、链路、原则、状态、问题经验、运行维护、研究评价、术语和维护规则；细节通过稳定链接回到权威文件。知识库不复制数万字 Prompt 或每个字段的 Schema。

### 3.4 状态维度必须显式

禁止用单一“完成”“可用”或“已优化”混合实现、验证、生命周期和研究权限。知识库按需要使用四个独立维度：

| 维度 | 允许值 | 含义 |
| --- | --- | --- |
| `lifecycle` | `active` / `deprecated` / `archived` | 当前是否仍适用 |
| `capability` | `implemented` / `research-only` / `no-go` | 能力已实现、仅研究或明确禁止 |
| `verification_scope` | `not-verified` / `local` / `CI` / `VPS` / `paper-forward` | 已验证到什么环境和范围，可同时列出多项 |
| `issue_status` | `active` / `resolved` / `monitoring` | 问题当前状态 |

“已实现”不代表“已验证”，“本地/CI 已验证”不代表“VPS 已验证”，“Paper forward 已验证”也不自动代表策略增益显著。交易增益只允许由预注册、样本外、成本后研究证据支持。

### 3.5 负面结果也是项目资产

失败实验、错误修法和延后决策必须保留。它们的价值是阻止后续任务再次采用同类方案，而不是证明某个模型或开发者“犯错”。

## 4. 信息架构

知识库使用以下固定结构：

```text
docs/knowledge/
├── README.md
├── project-overview.md
├── architecture-map.md
├── analysis-pipeline.md
├── principles-and-boundaries.md
├── problem-history.md
├── operations-and-debugging.md
├── research-and-evaluation.md
├── current-status.md
├── glossary.md
└── maintenance.md
```

### 4.1 `README.md`

唯一总入口。包含知识库用途、事实来源优先级、按任务类型划分的最短阅读路径、文件索引和状态词说明。它不承载大量项目状态或历史细节。

推荐阅读路径：

- 修改分析链路：原则与边界 → 分析链路 → 架构图谱 → 相关 ADR。
- 排查运行失败：当前状态 → 运行与排障 → 问题历史 → 相关运行记录。
- 修改 VPA/Joint：原则与边界 → 研究与评价 → VPA 集成规范 → Prompt 知识源。
- VPS 运维：当前状态 → 运行与排障 → Linux 部署文档 → VPS 控制面 ADR。
- 做发布审计：当前状态 → 质量契约 → 研究与评价 → 最近审计报告。

### 4.2 `project-overview.md`

说明项目目标、用户场景、主要能力、正式分析 authority 和非目标。明确当前是无人值守分析与 Paper execution 系统，不是自动实盘盈利系统；正式分析结果为 PA-only，VPA Shadow 提供独立证据，Joint 仍为隔离研究。

### 4.3 `architecture-map.md`

按模块说明 GUI、Headless、数据源、冻结快照、PA Provider、VPA Analyst、Joint 研究、校验物化、持久化、Paper、调度器和 Dashboard 的职责及依赖。使用文字和 Mermaid 图描述组件关系，但字段级契约链接到代码和 ADR。

控制面必须明确：Headless 只发布脱敏状态投影；Dashboard 不读取 Headless 内部数据库、配置或 API Key，只写 `running/paused` desired state；desired generation 与 Headless applied generation 分开；进程生命周期由 systemd 管理；默认仅监听 loopback 并通过 SSH tunnel 访问。

### 4.4 `analysis-pipeline.md`

以输入、操作者、判断权、输出和失败语义描述完整链路：

1. 行情身份解析与 Snapshot 冻结。
2. 程序 Evidence Context 构建。
3. PA Stage 1 Draft 框架判断、单一 materializer 物化、事实校验与 Gate；Gate 不通过时短路 Stage 2。
4. PA Stage 2 Draft 的 Setup、订单意图、结构止损和胜率先验判断；随后独立物化 Trader Equation、terminal 和执行语义。
5. `compile_pa_handoff(...)` 编译 PA 到 VPA/Joint 的唯一交接对象，不补专业判断。
6. VPA Shadow 独立分析与降级；正式分析结果仍来自 PA Stage 2。
7. 隔离 A/A/B/B 研究 B 臂调用 Joint Arbiter，无人值守正式分析 Headless 不调用 Joint。
8. Records、audit、SQLite projection、Paper 与控制面状态。

文档必须区分正常成功、带 warning 成功、PA-only 降级、输入不可用、Provider 不可用、模型输出无效和内部错误，避免把所有失败都写成“分析未通过”。

### 4.5 `principles-and-boundaries.md`

该文件是三角色原则索引，不拥有独立规则定义权。每项只记录“规则编号、一句话摘要、适用模块、权威来源”，强制语义以 `AGENTS.md`、`CONTEXT.md`、ADR 和专项规范为准。索引覆盖：

- PA 交易员：Brooks 框架、Setup、方向、结构失效和入场时机仍由 LLM 判断。
- Wyckoff VPA 交易员：量价必须结合 Background、Location、Effort/Result、序列比较和 acceptance/rejection；evidence first、hypothesis plural、label last。
- 量化/软件架构师：确定事实交给程序，专业多解交给 LLM；硬校验只保护结构化契约、明确事实、权限和安全。

索引必须能定位以下关键边界：Brooks Router 是唯一 Strategy Router；`compile_pa_handoff(...)` 是唯一 PA 交接入口；`off` 模式 VPA Prompt、Schema、测量和历史字段零泄漏；VPA 不修改 `cycle_position`、`direction`、`gate_result`、Setup、订单价格或胜率；正式分析 authority 始终来自 PA Stage 2；无人值守正式分析 Headless 不调用 Joint。

禁止事项索引包括：程序化 PA/Wyckoff 状态机、自由文本硬 Gate、样本特化补丁、VPA 修改胜率或创造 Setup、正式分析启用 Joint、用测试通过推导盈利。知识库不得自行新增 `must` 或“不得”规则；新增约束必须先落在相应权威来源。

### 4.6 `problem-history.md`

按“问题族”记录历史经验，而不是按聊天时间线堆叠。首版至少覆盖：

1. 程序越权解释 PA/VPA 语义。
2. 自由文本校验过严导致合法措辞被拒。
3. Prompt/Schema/materializer/validator 资源漂移。
4. normalizer/router/repair 猜测并改变模型结论。
5. PA/VPA/Joint 权限和实验身份混淆。
6. Snapshot、HTF、数据源、catalog hash 与 evidence identity 漂移。
7. candidate、执行资格、结构失效和 Paper 回放语义混合。
8. Provider、持久化、线程、重试和状态分类错误。
9. 针对单一样本叠补丁导致过拟合和样本坍塌风险。
10. VPS 权限、运行状态投影及 desired/applied 控制状态混淆。
11. Provider Draft v2 定向修复越权，以及首次失败与最终接受状态混淆。
12. OHLC 路径不确定、Paper v1/v2 混算，以及真实 outcome 与反事实 outcome 混淆。
13. 多 venue instrument identity、合约单位、tick 和 quantity precision 漂移。
14. Volume provenance、真实零/缺失零与非因果 seasonality 混淆。
15. 多资产去重、重启恢复、QThread 生命周期和 Headless Qt 污染。
16. wheel Prompt 资源缺失、依赖 profile、路径分裂和 TradingView alias cache 污染。
17. DeepSeek requested thinking 与 provider 实际 applied effort 不一致。

每个问题条目使用统一模板：

```text
问题族：
issue_status：active / resolved / monitoring
lifecycle：active / deprecated / archived
症状：用户或日志看到什么
影响：交易、研究或工程后果
根因：跨样本成立的机制原因
错误修法：哪些做法曾恶化问题，为什么
正确原则：长期应采用什么边界
当前实现：在哪些模块或 ADR 中落实
回归证据：测试、记录或提交
复发检查：以后遇到什么信号先查这里
```

单次运行的具体错误不直接加入长期文档；只有形成可复用根因、边界或排障步骤后才升级为知识。

### 4.7 `operations-and-debugging.md`

提供 Windows GUI、Linux Headless、VPS Dashboard、配置目录、records/audit/SQLite、常见启动失败和降级状态的排查顺序。强调先保存失败证据和识别失败边界，再修改；同一问题第三次验证仍失败时停止局部 patch，转为架构与数据流审查。

该文档只引用无敏感信息的命令和路径变量，不写 API key，不写死某台机器的绝对目录。

### 4.8 `research-and-evaluation.md`

记录 Shadow、Soft、Joint、A/A/B/B、ITT、per-protocol、Paper 回放、cohort、独立 Snapshot、样本量和晋级规则。明确：

- Shadow 不是反事实 A/B。
- A1/A2 测量 PA-only 组内随机性，B1/B2 测量 Joint 组内随机性；A 与 B 的配对差异才估计 VPA/Joint 增量。
- 无订单和未触发在 ITT 中为 `0R`，不确定路径保持 censored。
- 无人值守正式分析请求 `soft` 必须收敛为 `shadow`；持续 A/A/B/B 是独立研究进程，不属于正式分析 Headless。
- 任何 Joint 晋级至少需要 300 个独立 Snapshot、100 个明确匹配结果，并通过成本后样本外验证；最终样本量仍按预注册效应量和统计功效计算。
- 工程正确性、分析质量和交易收益是三个独立验收层。
- 当前没有足够前向证据证明 VPA/Joint 提高成本后盈利。

### 4.9 `current-status.md`

只保存当前、可核验、会影响下一步工作的状态，不写完整历史。包含：

- 当前发布基线 commit、最后核验 commit 与验证范围；分支属于 worktree 临时状态，通过 Git 实时读取，不写成长期项目事实。
- 正式/研究模式矩阵。
- 已实现能力及其证据等级。
- 最近一次完整审计与之后发生的重大变更。
- 当前已知风险和部署前待办。
- 下一阶段研究问题。

首版必须准确反映：2026-08-13 的全量回归是历史证据；2026-08-26 Dashboard 是审计后新增能力；真实 Linux/systemd/VPS 权限验证尚未完成；尚无充分 Paper forward 盈利证据。

状态文档只保留当前发布周期。进入新发布周期时，将旧状态按 `docs/knowledge/archive/status/YYYY-MM-DD-abcdef0.md` 格式保存，归档后不可改写，并由 `docs/knowledge/archive/README.md` 索引。首个归档出现前不创建空目录。历史 incident review 和被替代摘要分别进入 `archive/incident-reviews/` 与 `archive/superseded-summaries/`。状态正文超过约 300 行时必须先收敛重复内容，再按发布周期归档。

### 4.10 `glossary.md`

只提供 PA、VPA、Joint、Shadow、Soft、Gate、Candidate、Execution Disposition、Snapshot、Cohort、Run Outcome、Paper、结构止损、假设失效等术语的中文别名、缩写和 `CONTEXT.md` 章节链接。术语定义权仍属于 `CONTEXT.md`，该文件不独立重写领域定义或理论教材。

### 4.11 `maintenance.md`

提供知识更新示例、检查清单、状态归档和冲突登记操作说明。它不是强制规范；`AGENTS.md` 是知识工作流的唯一强制入口，所有强制触发条件只定义一次。

## 5. 文档元数据

除 `README.md` 外，每个知识文件顶部包含：

```text
lifecycle：active / deprecated / archived
capability：implemented / research-only / no-go / not-applicable
verification_scope：not-verified / local / CI / VPS / paper-forward
最后核验：YYYY-MM-DD
核验提交：至少 7 位 Git SHA，例如 abcdef0
权威来源：仓库相对链接列表
```

其中：

- `最后核验` 表示内容与所列提交和来源完成过一致性检查，不代表所有功能在该日期重新运行。
- `核验提交` 使用完整或至少 7 位 Git commit；若文件同时描述多个历史版本，在正文逐项注明。
- `权威来源` 只列直接支撑本文的主要文件，避免形成无边界链接集合。
- 文件包含多类能力时，元数据使用最保守值，正文逐项标明 capability 和 verification scope。

## 6. 维护工作流

### 6.1 任务开始前

`AGENTS.md` 增加唯一的强制知识工作流入口。后续非琐碎任务先读取 `docs/knowledge/README.md`，再按阅读路径读取与任务直接相关的知识文件和权威来源。知识库不能代替读取本次修改涉及的源码、测试和 ADR。

### 6.2 任务完成前

发生以下任一变化时更新知识库：

- 架构、职责边界、数据流或失败契约变化。
- 新增/移除正式能力、研究能力、部署入口或控制能力。
- 出现可复用的新根因、错误修法或排障步骤。
- 验证状态发生变化，例如首次通过真实 VPS、Paper forward 门禁或完整发布审计。
- 某项能力被禁止、弃用或从研究晋级。

纯格式修改、局部实现细节且不改变外部认知时，不强制更新知识库。

### 6.3 记录规则

- 先修改权威来源，再更新知识摘要。
- 当前状态只写有证据的事实，并链接证据。
- 新问题先记录原始日志/record；确认跨样本根因后再加入 `problem-history.md`。
- 错误修复必须说明为何符合三角色边界，不能只写“增加校验”。
- 删除或弃用旧实现时，更新术语、状态和问题历史，避免文档继续引导使用旧链路。
- 单次试验的 Prompt 待优化点仍写入临时日志；形成稳定原则后再提炼进知识库或权威 Prompt 规范。

## 7. 与现有文档的边界

| 现有资料 | 保留职责 | 知识库职责 |
| --- | --- | --- |
| `AGENTS.md` | 强制执行规则和必读入口 | 提供可导航的项目记忆 |
| `CONTEXT.md` | 领域对象、长期责任边界和不变量 | 用面向任务的方式解释并链接 |
| `docs/architecture/ADR-*` | 单项架构决策与原因 | 提供主题索引、依赖关系和当前适用性 |
| `docs/quality/acceptance.md` | 机器和研究验收契约 | 解释验证层级与当前通过范围 |
| `docs/wyckoff_vpa_integration.md` | VPA 集成的详细专业规范 | 摘要角色、模式和研究路径 |
| `prompt_engineering/` | Brooks/VPA 理论与实际 Prompt 知识源 | 说明加载方式、边界和维护入口 |
| `docs/linux_headless_deployment.md` | 部署步骤 | 给出运维阅读路径与故障分类 |
| 审计报告与 Git 历史 | 某一时间点的证据 | 综合当前状态并保留历史锚点 |
| `docs/提示词优化.md` | 临时 Prompt 实验问题 | 仅标注其临时性质，不作为事实源 |

## 8. 验证设计

本次为纯文档与 Agent 工作流修改，不运行完整交易测试套件。验收包括：

1. `docs/knowledge/` 十一个文件全部存在，职责不重复。
2. `AGENTS.md` 明确要求任务按需读取并维护知识库。
3. Markdown 相对链接均能解析到仓库内文件或有效锚点。
4. 不存在未完成占位标记、占位提交、机器绝对路径、真实凭据或盈利承诺。
5. `current-status.md` 的提交、能力和未验证边界可由 Git、审计报告和当前文件验证。
6. `problem-history.md` 的每个问题族都包含根因、错误修法、正确原则和复发检查。
7. 三角色复核确认没有把专业交易解释写成程序规则，没有把研究状态写成正式分析能力。
8. `git diff --check` 通过，临时检查产物已清理，工作区只包含本次文档修改。

## 9. 实施边界与提交策略

实施阶段只新增 `docs/knowledge/` 并更新 `AGENTS.md`。如发现现有文档与当前代码矛盾，只在知识库中明确冲突并列为待核验项；除非它直接阻止知识库准确引用，否则不顺手重写原文档。

设计规范单独提交。用户确认规范后，再编写实施计划并完成知识库内容、链接检查、三角色复核和独立文档提交。
