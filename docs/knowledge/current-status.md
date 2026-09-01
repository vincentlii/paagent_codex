---
lifecycle: active
capability: no-go
verification_scope: local
最后核验: 2026-09-01
核验提交: 965f2df
权威来源:
  - docs/quality/pre_release_final_audit_20260813.md
  - docs/quality/acceptance.md
  - docs/architecture/ADR-0023-vps-observability-and-control-plane.md
  - docs/architecture/ADR-0024-codex-analysis-backend.md
---

# 当前状态

## 结论

当前 Codex 升级分支从 `965f2df` 创建；该提交是 vNext 的现有功能与知识基线。Codex Analysis
Backend 已完成 ADR-0024 设计冻结、Frozen Runtime、受约束 CLI Worker 与 role Adapter；尚未接入
ProductionOrchestratorFactory，也未完成真实账号、VPS、回放或交易增益验证。

项目已具备进入真实 VPS 安装、预检和无人值守 Paper 验证的工程基础，但尚不能写成“VPS 已验证”或“PA+VPA 已提高盈利”。2026-08-13 的 `1169 passed, 7 deselected` 是绑定当时审计基线的历史证据；Dashboard 是审计后新增能力，需要在目标 Linux/VPS 环境补充实机验证。

## 版本锚点

| 锚点 | 类型 | 说明 |
| --- | --- | --- |
| `e7e415b` / `pre-vps-audit-20260813` | 回退检查点 | 上线前 P0-P4 审计前状态 |
| `5daeccd` / `pre-vps-ready-20260813` | 发布审计锚点 | P0-P4 软件实现和当时全量非 live 审计完成 |
| `f8fffc0` | 当前功能基线 | 新增隔离 VPS 控制 Dashboard，晚于 2026-08-13 全量审计 |
| `965f2df` | Codex 升级基线 | `convergence-vnext` 上创建隔离 worktree 的固定起点 |
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
| Linux Headless 与发布依赖 | implemented | 历史 CI/wheel smoke；VPS not-verified | 需要真实 Linux kernel、systemd、目录权限和重启恢复验收 |
| VPS Dashboard | implemented | not re-verified in this knowledge task | `f8fffc0` 含 focused unit tests，但晚于完整审计；需重跑测试及实机权限、loopback、SSH tunnel、desired/applied 验证 |
| DeepSeek role reasoning profiles | implemented | local/provider-dependent | requested effort 与 provider 实际 applied effort 必须分别记录；某些模型可能不支持细粒度强度 |
| Analysis Backend seam | implemented | local focused | Direct 与 Codex Adapter 均返回现有 Provider Draft；Codex 仍只接 PA Stage 1/2，VPA shadow/soft 在未接通前显式拒绝，避免静默改走 Direct |
| Frozen Agent Job / Evidence | implemented | local focused | 确定性 manifest、原子目录发布、resource digest、角色事实/知识 allowlist、cutoff 与调用/字节预算已实现 |
| Codex CLI Worker | implemented | local simulated | Ephemeral CLI、ChatGPT-only auth、read-only/no-shell/no-Web、required MCP、AGENTS/role skills、JSON/outbox 已用模拟 subprocess 验证；Provider Draft Schema 仍由 PA Validator 判定；尚未做真实账号调用 |
| Codex Analysis Backend | research-only | local focused | 已有 Job 身份绑定、一次定向修复上限、脱敏错误分类、熔断和完整 Direct PA 冷回退；fallback provenance 在持久化前写入且可见 callback 不混链。尚未接入 ProductionOrchestratorFactory |

“历史全量回归”只表示 [2026-08-13 审计报告](../quality/pre_release_final_audit_20260813.md)记录的 `1169 passed, 7 deselected`，不能自动覆盖之后的功能提交。

## 当前已知风险

### 外部环境未验证

- 尚未在目标 VPS 验证 Linux 用户、systemd、`PA_AGENT_HOME`、共享 operation directory 和 SQLite read-only projection 权限。
- 尚未在目标 VPS 验证真实 Provider、行情源、venue symbol/contract metadata、网络超时与速率限制。
- Dashboard 尚需验证仅监听 loopback、SSH tunnel、desired/applied generation 和 Headless 不可写越权。
- ChatGPT account auth、Codex CLI、独立 Unix 用户、只读 sandbox、worker restart 和 rate limit 尚未在目标 VPS 验证。

### 交易与研究未验证

- 尚无足够前向 Paper 样本证明 VPA Shadow 或 Joint 提高成本后 EV、胜率或回撤表现。
- Joint 晋级至少需要 300 个独立 Snapshot、100 个明确匹配结果，最终样本量仍需按预注册效应量和统计功效确定。
- `estimated_win_rate` 是 PA Setup 的未校准主观先验，不是历史统计胜率。
- LLM 仍可能作出理论上合理但结果错误的判断；程序只能消除明确事实矛盾、权限越界和非法订单，不能消除市场不确定性。

### 文档与变更治理

- Dashboard 之后的完整 Windows/Linux 非 live 套件尚未在当前知识状态中重新声明通过。
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

- `311b12a`：记录仓库内、单一事实源的项目知识库设计；实施完成后设计状态已同步为 `Accepted`。
- `2e2d8b5`：给出六阶段实施和验收计划。
- `f28aea2`：知识库主题、维护流程和 `AGENTS.md` 强制入口完成接入；中间提交保留各阶段内容与审查修正。

后续功能、验证范围、可复用根因或弃用状态改变时，按 [维护手册](maintenance.md) 更新本页。
