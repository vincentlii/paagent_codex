---
lifecycle: active
capability: no-go
verification_scope:
  - local
  - VPS-partial
最后核验: 2026-09-05
核验提交: 2128ef3 + working-tree audit
权威来源:
  - docs/quality/pre_release_final_audit_20260813.md
  - docs/quality/acceptance.md
  - docs/architecture/ADR-0023-vps-observability-and-control-plane.md
  - docs/architecture/ADR-0024-codex-analysis-backend.md
  - docs/quality/codex_backend_final_audit_20260902.md
  - docs/quality/vps_profile_dashboard_audit_20260903.md
  - docs/quality/codex_backend_full_acceptance_audit_20260905.md
  - docs/quality/codex_backend_supplement_implementation_review_20260905.md
---

# 当前状态

2026-09-21：性能优化恢复原 Windows Direct 主链的 PA 阶段连续性，但不恢复长期会话。
不同 Frozen Snapshot 严格独立；同一 Snapshot 内仅 Stage 1 attempt 1 直接通过后可由
Stage 2 续接。VPA、研究 Joint 与 repair 始终独立；Stage 1 一旦进入 repair，Stage 2
便从已验证 Artifact 新开会话。Publisher 校验 parent/thread/task/Snapshot/cutoff/runtime，
并仅对 digest 完全一致的 price frame、HTF 和知识做无损继承；新 Stage 2 产物仍必读。
该实现已通过会话身份、VPA 隔离、delta baseline、Worker/Spool 与 Direct 相关离线回归；
本机 Codex CLI 0.155.0-alpha.9.2 的 localhost 合成验收进一步证明同 thread 续接与
Stage 2 delta 确实切换到新 Frozen Job。持久 PA workspace 使用 Host 原子更新的只读
evidence binding，防止 `resume` 恢复 Stage 1 MCP 参数后继续读旧 Job。尚未做新一轮 VPS
真实时延和专业质量验收，阶段 8 仍 no-go。

2026-09-21：已定位 Luna 工具不可用的配置根因：模型要求 Code Mode，而分析运行时
禁用了宿主。Worker 现将传输与权限分开：OpenAI 受限 Code Mode、DeepSeek 直接 MCP，
两者均使用原冻结 Gateway 与完整 Skill/知识。显式关闭协作工具，隔离父桌面会话身份。
51+24 项定向回归和两个 Provider 的真实 CLI 本机合成传输检查通过；完整指令、基线
无损，Luna 嵌套工具仅冻结证据。不能据此宣称性能或交易质量通过；阶段 8 仍 no-go。
本条取代下文历史“所有新模型直接 MCP、关闭 Code Mode”的实现口径，详情见运行审计。
随后一次 fresh Luna/max 真实验收：工具基线和追加 PA 调查成功，但 Stage 1 在
239.703 秒超时，无 Draft、VPA、Stage 2。工具处理累计约 0.119 秒；剩余延迟尚不能
细分为模型计算或服务端等待。传输缺陷修复不等于性能达标；未追加付费运行或部署。
该超时曾被泛化为 invalid_request；现已新增独立非重试 `role_stopped`，统一 Codex、
Direct、预算停止和调用账本语义，不再误导为 Schema 错误或触发 availability fallback。
186 项运行链定向回归及两条 Provider 本机真实 CLI 协议检查通过。

2026-09-21 延长预算单次 fresh Luna/max：临时单角色 900 秒、全链 2400 秒，正式配置
未改。Stage 1 431.406 秒、VPA 607.954 秒、Stage 2 561.391 秒、定向修复 58.547 秒；
Host 共 1662.375 秒，755,382 Token。Gateway 44 条事件仅 0.601 秒、队列 1.950 秒，
慢速集中在模型进程侧。前三角色均返回，但 Stage 2 首稿及修复先后错误绑定 K4/K2.high，
最终被原 Validator 阻止，状态 incomplete、无正式交易决策。故放宽预算证明链路非死锁，
但性能与精确事实质量仍不达标；不能永久放宽预算作为上线方案。详见运行审计。

2026-09-18 第四步：实际 CLI→localhost 合成 Responses 请求已确认 AGENTS 双重注入，
新 Job 以 `single_frozen_agents_v1` 冻结单份交付：保留完整 model_instructions_file，
仅关闭自动项目文档重注入。真实 MCP 基线在此路径本来就只交付一份，因此不改 MCP
双表示协议。35 项定向测试及新旧传输对照通过；完整指令、Skill、基线无损。未调用付费
模型、未部署，真实性能与交易质量仍未验收。CLI 升级时用 `scripts/check_codex_delivery.py`
复核，参见运行审计“第四步”节；不要在缺少显式完整指令绑定时关闭自动 AGENTS 装载。

2026-09-18 优化步骤 1–3：分析专用指令与功能配置、全模型冻结 Skill 交付、按角色工具
筛选、已交付/仅索引证据导航及 Stage 1 缺口驱动流程已实现。83 项相关离线测试、真实
MCP SDK 内存调用、资产审计和当前 CLI 功能配置检查通过。保留完整知识、PA/VPA、HTF
及原 Host 交易链；未追加付费分析或部署，尚不能宣布提速、质量非劣或阶段 8 通过。
新执行语义用 `analysis_execution_v1` 冻结；再次运行前需显式同步专用 CODEX_HOME 的
版本化策略文件，不能绕过摘要校验。细节与三角色自审见运行审计的“优化步骤 1–3”节。

2026-09-18：Codex + DeepSeek Flash/high 同冻结快照已贯通 Stage 1→VPA→联合 Stage 2→
原校验/持久化，无输出修复；Host 至持久化前 392.578 秒，CLI 合计 390.876 秒，仍超过
300 秒目标。本次含被沙箱阻止的写 Skill 尝试，且交易文本有语义矛盾，不能作为合格样本
或质量非劣证明。后续只读模型目录、直接 MCP 提示、完整 Skill/Schema 交付及审计标签
已离线修正，未追加真实复测、未部署。完整数据与限制见运行审计文档的 2026-09-18 节。

2026-09-17 离线补充：PA 基线增加冻结的精确 fact ID 索引，省去必须单独发现 ID 的依赖，
索引不进入初始 Prompt；共享提示纠正旧 pa_evidence.fact_refs 输出位置。56项定向回归通过，
无付费调用。原知识/HTF/Validator保留，真实提速尚未验证；不能把该局部优化称为性能达标。

2026-09-17 最新：阶段 8 尚未通过真实完整链路验收，阶段 9 暂缓。已修复并发审计、长修复
反馈、价格几何/引用及 VPA 身份交接缺口；无损合并后补齐 MCP / exec / history 交付容量。
最新 fresh Stage 1 原校验通过但耗时 507 秒，性能不合格。VPA 暴露 Codex 发布完整 catalog、
原 Validator 只允许 PA 已引用窗口的范围不一致；`vpa-w005` 并非不存在。Publisher 已复用原
选择器，保留选中事实全部字段、原 catalog 和知识全文；引用错误路径同步修复，54 项定向
回归通过。当前分段验证 VPA → Stage 2；尚不代表全链通过，也不自动切回 Direct。
补充：唯一获批的 Luna/high 工程对比已 fresh 贯通并落库，无修复；三个角色 CLI 合计
563.358 秒，仍超过性能目标。与 max 主周期判断存在分歧，未证明质量非劣；正式配置
不变，停止追加付费测试。max 分段记录虽 completed，但 trace 改编号存在语义错配，
已补修复身份保护并通过55项回归，不把该旧记录算质量通过。
后续贯通与性能证据见 [真实运行审计](../quality/runtime_chain_audit_20260914.md)。
下文关于“先进入阶段 9—11”的历史安排不再适用。

## 联合分析升级进行中

阶段 8 已接入 actual/estimated/unknown 指标、调用 coverage、进程/队列/全程耗时与按 Backend 渲染。
Codex 不再预先构建未使用的 Direct 大 Prompt，mandatory 知识与完整 Evidence 保留。
2026-09-13 的 239 项定向离线检查通过，详见 [阶段 8 验收](../quality/phase8_metrics_acceptance.md)；真实实验仍按独立研究记录评价。

阶段 7 的本地实现已补齐 Host adapter、fixed/agentic 调查权限、完整冻结资源与 Paper outcome 绑定。
Codex 实验固定 `gpt-5.6-luna/max`；前两批均 0/4 完成。获外发授权后 pilot-03 已验证真实 MCP 读取，前两臂约 240 秒失败；为减少冗余实验中止后两臂，最终 0 完成/2 失败/2 缺失。
已修复 typed stop 的身份比较误报，45 项定向检查通过；本地停止同配置重跑，后续阶段 9—11 发布准备及 VPS 单任务贯通/持续模拟盘分开进行，未部署。详见 [Luna 实验补充](../quality/phase7_luna_pilot_20260913.md)。
真实盲评、前向样本和效果校准仍未完成，不代表上线或专业效果通过。
详见 [阶段 7 验收与研究边界](../quality/phase7_research_acceptance.md)。

阶段 5 联合链路与阶段 6 交付后 Paper 已完成本地实现；最近阶段 6 的 188 项必要离线检查通过，
三角色自审及边界见 [阶段 6 验收](../quality/phase6_delivery_paper_acceptance.md)。下一步为阶段 7。
下文旧日期和证据是历史状态，不代表这次 VPS 已更新；默认权限、部署和专业效果均未晋级。

阶段 3 已完成共享 PA/VPA 知识一致性修订、持久章节 ID、冻结别名与无损交付；209 项聚焦回归通过。
内容迁移、三角色自审及未验证边界见 [阶段 3 验收](../quality/phase3_knowledge_acceptance.md)。
这不代表模型分析效果、Linux/VPS 或完整发布已经验收；Runtime 角色协议继续在阶段 4 实施。

阶段 2 已实现因果闭盘/as-of、VPA provenance 读取、冻结经验池及回放绑定，90 项聚焦测试通过。
扩大回归有两项已在修改前源码复现的失败，详见实施契约；不宣称全套验收通过或可发布。
长期规则见 [ADR-0026](../architecture/ADR-0026-causal-snapshot-and-experience.md)。

上一轮未提交成果已保存为 `e445907`，新实施顺序与验收边界见
[联合分析实施契约](../quality/joint_agent_implementation_plan.md) 和
[ADR-0025](../architecture/ADR-0025-joint-stage2-pipeline.md)。阶段 1a 仅建立新契约并通过
42 项聚焦测试；阶段 1b 补齐可选记录时间/停止契约和联合引用边界，29 项聚焦测试通过。
旧记录不补造时间，不静默切换 pipeline。未启用联合运行链路、未部署、未证明交易增益。
下面历史验收结论仍只对应各自基线。

## 结论

当前 Codex 升级分支从 `965f2df` 创建；该提交是 vNext 的现有功能与知识基线。Codex Analysis
Backend 已完成 ADR-0024 设计冻结、Frozen Runtime、受约束 CLI Worker、role Adapter、真实 Frozen
Job publisher、环境驱动的生产 Factory 以及 Backend A/A/B/B 冻结协议与隔离统计。Codex 接入保持
opt-in，尚未完成真实账号、VPS、长时间回放或交易增益验证。

项目已完成 versioned Analysis Profile、单次运行控制、模型选择看板、Codex Runtime 资产/权限审计与
live AGENTS/Skill canary，具备进入真实 VPS 受控 smoke 的工程基础，但尚不能写成“VPS 已验证”或
“Codex/PA+VPA 已提高盈利”。2026-08-13 的 `1169 passed, 7 deselected` 是绑定当时审计基线的历史证据；
本轮 276 项聚焦回归覆盖新增链路，仍需目标 Linux/VPS 实机验收。

当前完整验收结论见 [Codex Backend 完整验收](../quality/codex_backend_full_acceptance_audit_20260905.md)：
工程骨架保持 research-only，禁止 `auto`。当前 working tree 已针对该审计补充 stdout typed failure、
统一四角色 Backend、Frozen Job v4 按需 Context、官方 MCP SDK、pointer repair、持久化熔断和运维资产；
这些补充尚未按用户要求运行测试或部署，因此仍属于 `not-verified`，不能覆盖审计结论或晋级。

当前发布候选固定 `binance_usdm/BINANCE/XAUUSDT/1h`、HTF `4h/60`、VPA Shadow、并发 1；
Profile UI 已完成，真实 Codex 分析、权限负向测试和 live canary evidence 尚待目标 VPS 取得。

## 版本锚点

| 锚点 | 类型 | 说明 |
| --- | --- | --- |
| `e7e415b` / `pre-vps-audit-20260813` | 回退检查点 | 上线前 P0-P4 审计前状态 |
| `5daeccd` / `pre-vps-ready-20260813` | 发布审计锚点 | P0-P4 软件实现和当时全量非 live 审计完成 |
| `f8fffc0` | 当前功能基线 | 新增隔离 VPS 控制 Dashboard，晚于 2026-08-13 全量审计 |
| `965f2df` | Codex 升级基线 | `convergence-vnext` 上创建隔离 worktree 的固定起点 |
| `2bc71ae` | Codex VPS 接线 | 独立 Unix worker、生产 publisher/factory、runtime identity 与 VPA Shadow 接通 |
| `db43846`–`03c618a` | Profile 发布候选 | Profile/control、safe apply、Dashboard、Runtime audit 与固定单资产 VPS 配置 |
| `311b12a` | 文档设计 | 项目知识库设计规范 |
| `2e2d8b5` | 文档计划 | 知识库实施计划 |

当前 worktree 分支不是长期状态事实。部署和审计应记录确切 commit、Prompt digest、Provider/settings 和 cohort。

## 模式状态

| 请求/运行模式 | capability | 正式结果 | verification_scope | 当前结论 |
| --- | --- | --- | --- | --- |
| `off` | implemented | PA Stage 2 | local + 历史 CI/全量回归证据 | VPA Prompt、测量、Schema 和历史字段应零泄漏 |
| `shadow` | implemented | PA Stage 2 | local + 历史 CI/全量回归证据 | VPA 独立追加；失败保留 PA-only 并记录 `completed_with_warnings` |
| 正式运行请求 `soft` | no-go | 收敛为 `shadow` | local + 历史回归证据 | 不授权 Joint |
| 隔离 A/A/B/B B 臂 | research-only | 研究臂结果 | local + 历史回归证据 | Joint 只输出有界 patch，不进入正式分析 |
| 真实订单执行 | no-go | 无 | not-verified | 当前仅 Paper，不声明提交真实订单 |

## 能力与证据矩阵

| 能力 | capability | verification_scope | 证据与限制 |
| --- | --- | --- | --- |
| PA Draft v2 + 单一 materializer | implemented | local；2026-08-13 历史全量回归 | [最终审计](../quality/pre_release_final_audit_20260813.md)，当前功能提交后未在本文件中声称重跑全部套件 |
| VPA Context 模块化与 Shadow | implemented | local；2026-08-13 历史全量回归 | [VPA 集成规范](../wyckoff_vpa_integration.md)；未证明收益增量 |
| 主周期 + HTF + volume provenance | implemented | local；2026-08-13 历史全量回归 | 真实数据源/venue 仍需目标环境 probe |
| 多资产有界调度和恢复 | implemented | local；2026-08-13 历史 20 资产回归 | VPS 长时间稳定性尚无实机证据 |
| Paper OHLC v2 与结果隔离 | implemented | local；2026-08-13 历史回归 | 反事实不等于真实成交或真实 PnL |
| 持续 A/A/B/B | research-only | local；2026-08-13 历史回归 | 缺少足够独立 Snapshot 和明确结果，不能晋级 |
| Linux Headless 与发布依赖 | implemented | local + VPS partial | working tree 已补 retention、logrotate、SQLite online backup/timer 和资源限额；尚未测试、安装或演练恢复 |
| VPS Dashboard | implemented | local + VPS partial | 可选择 allowlisted DeepSeek/Codex model/effort、pause/resume、三 Backend mode 与 fixed-scope Run Once；working tree 新增 Quality、Performance、Backend A/A/B/B 只读区，以及最近 200 次的成功率、首轮通过、p50/p95、初始/检索 Context 与 Evidence 指标，尚未测试或部署 |
| DeepSeek role reasoning profiles | implemented | local/provider-dependent | requested effort 与 provider 实际 applied effort 必须分别记录；某些模型可能不支持细粒度强度 |
| Analysis Backend seam | implemented | not-verified working tree | Direct 与 Codex Adapter 均返回现有 Provider Draft；Codex 统一接管 Stage 1、Stage 2、VPA 与 research Joint，`codex_only` 不创建 Direct client；VPA 仍不改变 PA official path |
| Frozen Agent Job / Evidence | implemented | local focused | v4 将完整 role context 独立冻结；新 Stage 1 使用 `bounded_context_v2`：一次 digest-bound Decision Packet 交付完整 price-only frame、全部 HTF、PA fact spine、原文 foundation sections 与完整 section index，并把 optional 调查硬限为两次。Stage 2/VPA/研究角色及历史 Job 继续 `semantic_parity_v1` 原样回放；所有资源受 digest、cutoff、role 与预算约束 |
| Codex CLI Worker | research-only | local focused | 不同 Snapshot 独立；同 Snapshot PA Stage 1 首稿通过后可受控续接 Stage 2；VPA/Joint/repair 独立 ephemeral。ChatGPT-only auth、read-only/no-shell/no-Web、required MCP、AGENTS/role skills、Runtime identity、strict-output schema 投影、stdout failure taxonomy 与无敏感信息的 auth/heartbeat 已实现；真实 CLI+MCP 合成续接已通过，仍待 VPS 真实模型验收 |
| Codex Analysis Backend | research-only | local focused | 已有有界 core context、独立 Unix spool worker、端到端 runtime identity、一次定向修复、熔断、完整 Direct PA 冷回退与环境 Factory；部署默认仍为 `deepseek_only` |
| Codex audit projection | implemented | local focused | `analysis_calls` 投影 backend、provider、job/thread identity、Decision Packet bytes/tokens、optional 调查次数、Evidence 调用/字节量与 `last_evidence_to_final_ms`；完整 JSON record 仍是唯一原始记录 |
| Analysis Backend A/A/B/B | research-only | not-verified working tree | `analysis_backend_aabb_v2` 强制四臂共用 HTF + VPA Shadow catalog，预登记 Direct/Codex 各两臂、ABBA/BAAB、独立 runtime、禁 fallback、失败 coverage、PA/VPA signature、cohort 去重、盲评 packet/独立解盲 key、已有 closed-OHLC ledger 结果联结与 CLI 入口已实现；尚无真实 Codex 样本，不能晋级 |

“历史全量回归”只表示 [2026-08-13 审计报告](../quality/pre_release_final_audit_20260813.md)记录的 `1169 passed, 7 deselected`，不能自动覆盖之后的功能提交。

## 当前已知风险

### 外部环境部分验证

- 目标 VPS 已验证三 systemd 服务、用户/目录权限、Runtime asset parity、ChatGPT device auth 与 live AGENTS/Skill canary；尚未完成重启恢复、磁盘保留和 backup/restore。
- 尚未在目标 VPS 验证真实 Provider、行情源、venue symbol/contract metadata、网络超时与速率限制。
- Dashboard 尚需验证仅监听 loopback、SSH tunnel、desired/applied generation 和 Headless 不可写越权。
- Codex home/runtime/outbox 与生产 DB、Paper ledger、DeepSeek credential 的 OS 权限已通过负向审计；spool restart、rate limit 与 typed request failure 仍未完成实机验收。

### 交易与研究未验证

- 尚无足够前向 Paper 样本证明 VPA Shadow 或 Joint 提高成本后 EV、胜率或回撤表现。
- Joint 晋级至少需要 300 个独立 Snapshot、100 个明确匹配结果，最终样本量仍需按预注册效应量和统计功效确定。
- `estimated_win_rate` 是 PA Setup 的未校准主观先验，不是历史统计胜率。
- LLM 仍可能作出理论上合理但结果错误的判断；程序只能消除明确事实矛盾、权限越界和非法订单，不能消除市场不确定性。

### Codex 阻断项与变更治理

- 2026-09-05 使用 `uv.lock` desktop+dev 环境完成全仓非 live 回归：`1305 passed, 7 deselected`。
- working tree 已冻结 Windows 20 条历史故障 manifest，并实现 stdout taxonomy、Codex VPA role 与 matched
  performance 字段；本轮继续加入 Windows PromptAssembler 语义等价协议和 mandatory-read acceptance
  invariant、知识加载顺序、Worker auth/heartbeat 投影、盲评和 closed-OHLC outcome 联结。在测试、VPS smoke、历史 replay 和前向样本完成前，仍禁止切换 `auto` 或宣称已解决幻觉/交易质量问题。
- 临时 [提示词优化日志](../提示词优化.md) 不得被误当成 Prompt 权威规范。
- 任何新修复必须避免针对单条 record 增加硬编码或自由文本硬拒绝。

## VPS 前置验收

1. 固定部署 commit、`uv.lock`、配置样例和 Prompt/contract digest。
2. 在 VPS 执行安装预检、`--check`、数据 probe 和 Paper-only dry run。
3. 验证 systemd 用户、目录 ownership、重启恢复、日志轮转和磁盘容量。
4. 验证 Headless 不导入 Qt，源码/Prompt 只读，状态全部写入 `PA_AGENT_HOME`。
5. 验证 Dashboard 无凭据、无内部数据库权限、loopback 监听和 SSH tunnel。
6. 先运行 `shadow` + Paper；Joint 研究进程与正式 Headless、账户和状态目录隔离。
7. 保存部署证据后，才把相应 capability 的 `verification_scope` 更新为 `VPS`。

详细操作见 [Linux 部署文档](../linux_headless_deployment.md) 和 [运行与排障](operations-and-debugging.md)。

## Joint 研究下一步

1. 持续收集同源冻结 Shadow Snapshot，不因 VPA 成败筛选样本。
2. 用独立研究 executor 运行 A1/A2/B1/B2，隔离 cohort 和 provider/settings/源码/Prompt 版本。
3. 同时报告 A/A、B/B、A-B 配对差异、ITT、per-protocol、coverage 和 censored。
4. 使用 Paper v2、成本和滑点做 episode-level 评价，不混入真实 outcome。
5. 达到预注册门禁后再讨论 Joint 是否晋级；未通过时记录负面结果而不是调整到迎合样本。

## 最近知识变更

- `03c618a`：完成固定单资产 VPS 配置、Codex Runtime 规则/Skills digest、非 editable
  installed/source parity、POSIX 权限契约与 live canary；服务默认 paused，仍待 VPS 实测。
- `db43846`–`5b653ed`：完成 versioned Analysis Profile、idle safe apply、exact-once Run Once 与模型选择 Dashboard。

- `2bc71ae` / Phase 7：完成 Codex 生产接线与独立 VPS worker；当阶段本地相关回归 160 项通过。Phase 8 最终 focused suite 为 168 项；目标 Linux/systemd、真实账号与交易增益仍未验证。

- `311b12a`：记录仓库内、单一事实源的项目知识库设计；实施完成后设计状态已同步为 `Accepted`。
- `2e2d8b5`：给出六阶段实施和验收计划。
- `f28aea2`：知识库主题、维护流程和 `AGENTS.md` 强制入口完成接入；中间提交保留各阶段内容与审查修正。

后续功能、验证范围、可复用根因或弃用状态改变时，按 [维护手册](maintenance.md) 更新本页。
