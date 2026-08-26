---
lifecycle: active
capability: not-applicable
verification_scope: local
issue_status: monitoring
最后核验: 2026-08-26
核验提交: 2e2d8b5
权威来源:
  - ../../AGENTS.md
  - ../../CONTEXT.md
  - ../superpowers/specs/2026-08-26-project-knowledge-base-design.md
---

# PA Agent 知识库

## 用途与非目标

这里是仓库内唯一的知识入口：用于定位项目目标、职责边界、当前状态、运行维护和研究评价的权威材料。它只做摘要与链接，不创建平行规范；规则、术语、架构意图、实现事实与运行证据仍分别由其原始来源拥有。

它不复制源码、ADR 或 Prompt 教材，不记录单次对话或未经复核的猜测，不替代真实 VPS、Provider、行情、交易所或 Paper forward 验证，也不承诺交易盈利。

## 在 Obsidian 中打开

在 Obsidian 选择“打开文件夹作为库”，直接选择本仓库根目录。笔记与代码、ADR、质量报告和 Git 历史使用同一工作区；不需要外部 Vault、副本或同步流程。

## 事实与权威来源

| 事实类型 | 权威来源 | 本索引的职责 |
| --- | --- | --- |
| 强制开发与修改流程 | [AGENTS.md](../../AGENTS.md) 与项目 guardrail skill | 指向流程，不重述强制语义 |
| 术语、角色责任、长期不变量 | [CONTEXT.md](../../CONTEXT.md) | 提供任务入口与术语索引 |
| 架构意图与取舍 | 已接受的 [ADR](../architecture/) | 关联主题与当前适用性 |
| 可执行行为与机器契约 | 当前代码、Schema、配置、[质量验收契约](../quality/acceptance.md) | 标示实现与验证边界 |
| 验证行为 | 绑定 commit、环境和命令的测试结果 | 区分历史证据与未验证范围 |
| 单次运行事实 | 绑定版本的 record、audit、artifact | 不把单次结果推广为长期结论 |
| PA/VPA 理论与实际 Prompt 知识 | 专项规范与 Prompt 知识源 | 定位相关材料，不重写教材 |
| 当前综合状态与经验 | 本知识库，并链接上述证据 | 只做可追溯摘要 |

## 状态词

| 字段 | 取值 | 含义 |
| --- | --- | --- |
| `lifecycle` | `active` / `deprecated` / `archived` | 内容或能力当前适用、已弃用或仅保留历史 |
| `capability` | `implemented` / `research-only` / `no-go` | 已实现、仅限研究或明确禁止；不等同于验证完成 |
| `verification_scope` | `not-verified` / `local` / `CI` / `VPS` / `paper-forward` | 证据覆盖的环境与范围，可同时列出多项 |
| `issue_status` | `active` / `resolved` / `monitoring` | 问题仍存在、已解决或需持续观察 |

“已实现”不表示已在 VPS 验证；本地或 CI 证据不表示交易有效；`paper-forward` 也不自动证明 VPA/Joint 增益或盈利。

## 最短阅读路径

| 工作 | 先读 | 再读 |
| --- | --- | --- |
| 修改分析链路 | [原则与边界](principles-and-boundaries.md) | [分析链路](analysis-pipeline.md)、[架构图谱](architecture-map.md)、相关 ADR |
| 排查运行失败 | [当前状态](current-status.md) | [运行与排障](operations-and-debugging.md)、[问题历史](problem-history.md)、相关 record/audit |
| VPA/Joint 研究 | [原则与边界](principles-and-boundaries.md) | [研究与评价](research-and-evaluation.md)、[VPA 集成规范](../wyckoff_vpa_integration.md)、Prompt 知识源 |
| VPS 运维 | [当前状态](current-status.md) | [运行与排障](operations-and-debugging.md)、[Linux Headless 部署](../linux_headless_deployment.md)、[ADR-0023](../architecture/ADR-0023-vps-observability-and-control-plane.md) |
| 发布审计 | [当前状态](current-status.md) | [质量验收契约](../quality/acceptance.md)、[研究与评价](research-and-evaluation.md)、[最近审计报告](../quality/pre_release_final_audit_20260813.md) |

## 主题笔记

下列十份主题笔记共同构成知识库。后续任务创建的目标已提前链接，便于从入口直接导航。

1. [项目概览](project-overview.md)
2. [架构图谱](architecture-map.md)
3. [分析链路](analysis-pipeline.md)
4. [原则与边界](principles-and-boundaries.md)
5. [问题历史](problem-history.md)
6. [运行与排障](operations-and-debugging.md)
7. [研究与评价](research-and-evaluation.md)
8. [当前状态](current-status.md)
9. [术语表](glossary.md)
10. [维护说明](maintenance.md)

知识库中的摘要永远服从所链接的权威来源；发生冲突时，记录为待解决的架构漂移，不能默认由索引裁决。
