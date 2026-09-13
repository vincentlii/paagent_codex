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

## 联合分析升级进行中

阶段 8 已接入 actual/estimated/unknown 指标、调用 coverage、进程/队列/全程耗时与按 Backend 渲染。
Codex 不再预先构建未使用的 Direct 大 Prompt，mandatory 知识与完整 Evidence 保留。
2026-09-13 的 239 项定向离线检查通过，详见 [阶段 8 验收](../quality/phase8_metrics_acceptance.md)；真实实验仍按独立研究记录评价。

阶段 7 的本地实现已补齐 Host adapter、fixed/agentic 调查权限、完整冻结资源与 Paper outcome 绑定。
Codex 实验固定 `gpt-5.6-luna/max`；本机已登录并执行两批真实工程试跑，均 0/4 完成，修复了依赖、Schema 和 MCP SDK 适配问题，未部署。
新四臂复核因数据外发授权审核暂停，详见 [Luna 实验补充](../quality/phase7_luna_pilot_20260913.md)。
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
| Frozen Agent Job / Evidence | implemented | not-verified working tree | v4 将完整 role context 独立冻结；显式 `semantic_parity_v1` 强制按 `load_order` 读取角色安全完整价格帧、全部 HTF、阶段协议和 PA/Router mandatory 知识切片，再按需调查争议 Evidence；PA roles 的 bars 已删除 volume；所有资源受 digest、cutoff、role 与预算约束 |
| Codex CLI Worker | research-only | not-verified working tree | Ephemeral CLI、ChatGPT-only auth、read-only/no-shell/no-Web、required MCP、AGENTS/role skills、Runtime identity、strict-output schema 投影、stdout failure taxonomy，以及无敏感信息的 auth/heartbeat 状态已补充实现；尚未重新运行本地或 VPS 验证 |
| Codex Analysis Backend | research-only | local focused | 已有有界 core context、独立 Unix spool worker、端到端 runtime identity、一次定向修复、熔断、完整 Direct PA 冷回退与环境 Factory；部署默认仍为 `deepseek_only` |
| Codex audit projection | implemented | local focused | `analysis_calls` 投影 backend、provider、job/thread identity 与 Evidence 调用/字节量；完整 JSON record 仍是唯一原始记录 |
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
