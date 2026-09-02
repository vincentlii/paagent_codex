# PA Agent Knowledge Base

本目录是 PA Agent 的项目大脑与长期记忆入口。仓库根目录可直接在 Obsidian 中作为 Vault 打开；笔记使用 Obsidian Properties 和标准 Markdown 相对链接，因此也能在 GitHub、Codex 与普通编辑器中使用。

知识库负责导航、当前状态、问题经验和证据索引，不取代代码、`CONTEXT.md`、ADR、质量契约、Prompt 知识或原始运行记录，也不建立仓库外的第二份可编辑副本。

## 最短阅读路线

| 任务 | 先读 | 再读权威来源 |
| --- | --- | --- |
| 理解项目 | [项目概览](project-overview.md) → [当前状态](current-status.md) | [领域上下文](../../CONTEXT.md) |
| 修改分析链路 | [原则与边界](principles-and-boundaries.md) → [分析链路](analysis-pipeline.md) → [架构图谱](architecture-map.md) | [ADR 目录](../architecture/) 与涉及的源码/测试 |
| 修改 VPA/Joint | [原则与边界](principles-and-boundaries.md) → [研究与评价](research-and-evaluation.md) | [VPA 集成规范](../wyckoff_vpa_integration.md) 与 [Prompt 知识源](../../prompt_engineering/wyckoff_vpa/) |
| 排查分析失败或降级 | [当前状态](current-status.md) → [运行与排障](operations-and-debugging.md) → [问题历史](problem-history.md) | 对应 record、audit、日志与测试 |
| VPS 运维 | [当前状态](current-status.md) → [运行与排障](operations-and-debugging.md) → [架构图谱](architecture-map.md) | [Linux 部署文档](../linux_headless_deployment.md) 与 [ADR-0023](../architecture/ADR-0023-vps-observability-and-control-plane.md) |
| Codex Backend、Dashboard、Runtime rules、Skills 或部署 | [Codex Analysis North Star](codex-analysis-north-star.md) → [当前状态](current-status.md) | [领域上下文](../../CONTEXT.md)、[ADR-0024](../architecture/ADR-0024-codex-analysis-backend.md) 与当前实现 |
| 发布或研究验收 | [当前状态](current-status.md) → [研究与评价](research-and-evaluation.md) | [质量契约](../quality/acceptance.md) 与最近审计报告 |
| 维护知识库 | [维护手册](maintenance.md) | [AGENTS.md](../../AGENTS.md) |

不需要每次读取全部笔记。先按任务读取相关主题，再回到直接涉及的源码、测试和权威规范。

## 知识地图

- [项目概览](project-overview.md)：目标、正式分析能力、研究边界、入口和非目标。
- [架构图谱](architecture-map.md)：组件、数据面、控制面、持久化与 ADR 导航。
- [分析链路](analysis-pipeline.md)：每一步的输入、判断者、处理、输出和失败语义。
- [原则与边界](principles-and-boundaries.md)：三角色原则的非规范性索引。
- [问题历史](problem-history.md)：跨样本根因、错误修法、正确原则和复发检查。
- [运行与排障](operations-and-debugging.md)：GUI、Headless、Dashboard、Provider、数据和持久化排查。
- [研究与评价](research-and-evaluation.md)：Shadow、Joint、A/A/B/B、Paper 和晋级标准。
- [当前状态](current-status.md)：当前能力、证据范围、风险和下一步。
- [Codex Analysis North Star](codex-analysis-north-star.md)：Codex/Direct Backend、Frozen Evidence、角色 authority、Analysis Profile 与评价口径的长期约束。
- [术语索引](glossary.md)：项目术语的别名、缩写和权威定义入口。
- [维护手册](maintenance.md)：更新触发、冲突登记、归档和完成检查。

## 每类事实的权威来源

不同资料拥有不同类型的事实。发生冲突时先确定事实类型，不能用一条全局优先级自动裁决。

| 事实类型 | 权威来源 |
| --- | --- |
| 强制开发与修改规则 | [AGENTS.md](../../AGENTS.md) 与 [PA Agent guardrails](../../.agents/skills/paagent-change-guardrails/SKILL.md) |
| 领域术语、责任和长期不变量 | [CONTEXT.md](../../CONTEXT.md) |
| 架构意图与取舍 | 已接受的 [ADR](../architecture/) |
| 机器契约和当前可执行行为 | 当前源码、Schema、配置与 [质量契约](../quality/acceptance.md) |
| 验证行为 | 与 commit、环境、命令和结果绑定的测试证据 |
| 某次实际运行 | 与代码、Prompt、Provider、Snapshot、cohort 绑定的 record、audit 和 artifact |
| Brooks PA 与 Wyckoff VPA 理论 | [Prompt 知识源](../../prompt_engineering/) 与 [VPA 集成规范](../wyckoff_vpa_integration.md) |
| 当前综合状态与历史经验 | 本知识库；必须链接以上证据 |

规范意图与实现不一致时，应登记为架构漂移并解决，不能默认代码覆盖 ADR，也不能让旧 ADR 静默覆盖已接受的新实现。历史运行记录只证明其绑定版本的事实。

## 状态口径

| 维度 | 允许值 | 用途 |
| --- | --- | --- |
| `lifecycle` | `active` / `deprecated` / `archived` | 内容当前是否仍适用 |
| `capability` | `implemented` / `research-only` / `no-go` / `not-applicable` | 能力权限和用途 |
| `verification_scope` | `not-verified` / `local` / `CI` / `VPS` / `paper-forward` | 已验证到什么范围，可列多项 |
| `issue_status` | `active` / `resolved` / `monitoring` | 问题状态 |

必须分别回答“是否实现”“在哪里验证”“能否进入正式分析”“交易增益是否有证据”。本地或 CI 测试通过不等于真实 VPS 已验证；Paper forward 可运行也不等于 PA+VPA 已提高成本后收益。

## 记录边界

- 当前状态只写可核验事实和明确的不确定性。
- 单次分析问题先保留在 record、audit 或临时 Prompt 日志；形成可复用根因后才进入问题历史。
- [提示词优化](../提示词优化.md) 是临时实验日志，不是强制规则或权威理论来源。
- 交易结果、反事实 Paper 回放和人工核验结果必须保持分离。
- 负面实验、错误修法和延后决策同样需要记录，防止再次采用。

## Obsidian 使用

1. 在 Obsidian 选择“Open folder as vault”。
2. 选择仓库根目录，使知识笔记能链接源码、ADR 和 Prompt 文件。
3. 从本文件开始浏览；Properties 可用于按 lifecycle、capability 和 verification scope 过滤。
4. 不创建另一份人工维护的 Vault 副本。需要个人笔记时只链接本仓库，不复制项目事实。

知识库设计见 [设计规范](../superpowers/specs/2026-08-26-project-knowledge-base-design.md)，实施步骤见 [实施计划](../superpowers/plans/2026-08-26-project-knowledge-base.md)。
