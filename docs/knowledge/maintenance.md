---
lifecycle: active
capability: not-applicable
verification_scope: not-verified
最后核验: 2026-08-26
核验提交: f8fffc0
权威来源:
  - AGENTS.md
  - docs/superpowers/specs/2026-08-26-project-knowledge-base-design.md
---

# 知识库维护手册

本页提供操作清单和示例，不拥有独立强制规则。知识工作流的唯一强制入口是 [AGENTS.md](../../AGENTS.md)；事实、术语、架构和机器行为仍由 [知识入口](README.md)列出的分类来源拥有。

## 任务开始

1. 读取 [知识入口](README.md)，按任务选择最短路线。
2. 读取直接相关的主题笔记、权威规范、源码和测试；不要只读摘要。
3. 从 [当前状态](current-status.md)确认能力权限、验证范围和未完成事项。
4. 从 [问题历史](problem-history.md)检查是否已有同类根因和错误修法。
5. 写出本次可验证完成标准；交易相关变更做三角色审查。

不要求每次读取全部知识文件。读取范围应能覆盖本次修改的组件、权威来源和失败边界。

## 何时更新

发生以下变化时，任务完成前同步知识库：

| 变化 | 更新位置 |
| --- | --- |
| 新增/移除入口、组件、数据流或控制面 | [架构图谱](architecture-map.md)、[分析链路](analysis-pipeline.md) |
| PA/VPA/Joint authority、程序/LLM 分工变化 | 先修改 ADR/`CONTEXT.md`/`AGENTS.md`，再更新 [原则索引](principles-and-boundaries.md) |
| 正式、研究、禁止或弃用状态变化 | [当前状态](current-status.md)、[项目概览](project-overview.md) |
| 首次获得 local/CI/VPS/paper-forward 证据 | [当前状态](current-status.md)和相应审计报告 |
| 新的跨样本根因、错误修法或复发信号 | [问题历史](problem-history.md)、[运行与排障](operations-and-debugging.md) |
| 研究协议、样本门禁或评价口径变化 | 先修改 ADR/质量契约，再更新 [研究与评价](research-and-evaluation.md) |
| 术语正式变化 | 先修改 `CONTEXT.md`，再更新 [术语索引](glossary.md) |
| 部署用户、目录、systemd 或 Dashboard 权限变化 | 部署文档、ADR-0023、[架构图谱](architecture-map.md)、[排障](operations-and-debugging.md) |

## 何时不更新

- 纯格式、拼写或内部实现细节，没有改变外部职责、状态或排障方式。
- 单次模型交易判断错误，但不存在事实矛盾、权限越界或可复用根因。
- 尚未复核的猜测、聊天结论或未完成实验。
- 临时 Prompt 待优化点；先写入 [提示词优化日志](../提示词优化.md)，形成稳定原则后再提炼。
- 原始 record/audit 已能完整表达的一次性运行事实。

## 事实冲突登记

发现知识、规范、代码和运行产物不一致时，不用“最新文件优先”直接覆盖。先确定事实类型并使用以下格式：

```text
冲突 ID：KD-YYYYMMDD-NN
发现日期：YYYY-MM-DD
事实类型：规则 / 术语 / 架构意图 / 机器行为 / 验证 / 单次运行 / 理论
来源 A：文件、commit、环境或 record ID
来源 B：文件、commit、环境或 record ID
冲突：两者不能同时成立的具体陈述
影响：分析、研究、执行、部署或文档风险
当前裁决：哪一来源暂时用于当前任务及原因
永久解决：需要修改的权威来源、代码、测试或状态
验证：证明冲突已消失的命令或证据
```

若规范意图和代码冲突，登记“架构漂移”。修复完成后先更新权威来源，再更新知识摘要；不要在摘要里长期保留两套答案。

修复 Prompt/Skill 歧义时，优先替换拥有该语义的原句及公共契约，删除被替代的执行说明；
不要靠连续追加“例外/禁止”保留互相冲突的规则。旧 Frozen/历史 reader 等仍有调用者的兼容代码
须明确作用域后保留；失去调用者的分支应连同测试、导航和资源引用成套清理。历史证据归档，不回写成新结论。

## 问题升级为长期知识

单次问题满足以下条件之一，才进入 `problem-history.md`：

- 在两个以上独立 Snapshot、入口、Provider 或环境中复现；
- 能从源码/契约证明机制会跨样本发生；
- 揭示程序/LLM authority、状态、数据身份、执行安全或研究设计的系统性边界；
- 某种错误修法已经造成新的失败，值得明确禁止重复。

新增问题族应包含：`issue_status`、症状、影响、根因、错误修法、正确原则、当前实现/证据和复发检查。相同机制扩展现有 `PH-*`，不要仅因错误文案不同创建新条目。

## 状态证据规则

- `implemented`：只证明功能存在，不证明环境或交易效果。
- `verification_scope` 必须绑定 commit、环境、命令和结果；历史证据明确写“历史”。
- 真实 VPS 首次通过后才写 `VPS`；本地 Docker/CI Linux 不能替代真实权限和 systemd 验证。
- `paper-forward` 证明前向流程和样本存在，不自动证明策略增益。
- 盈利、胜率、EV、回撤改善只能引用预注册、样本外、成本后研究。
- 汇总页包含多种 capability/verification 时，文件 Properties 使用最保守值，正文逐项标注。

## Obsidian 与链接

- 仓库根目录作为 Vault，避免知识笔记无法链接源码和 ADR。
- 使用标准 Markdown 相对链接，确保 Obsidian、GitHub 和 Codex 都能解析。
- Properties 使用设计规范定义的字段；正文标题尽量稳定，避免无必要改名破坏锚点。
- 新增主题笔记后，从 [README](README.md)和至少一个相关主题建立入口。
- 不创建人工维护的外部镜像。需要导出时，外部副本必须是只读生成物。

## 发布周期归档

`current-status.md` 只保留当前发布周期。进入新周期时：

1. 将旧状态复制为 `docs/knowledge/archive/status/YYYY-MM-DD-abcdef0.md`。
2. 创建或更新 `docs/knowledge/archive/README.md`，记录日期、commit、验证范围和替代页。
3. 归档后不改写；错误用新勘误说明链接原页。
4. incident review 和被替代摘要分别进入 `archive/incident-reviews/`、`archive/superseded-summaries/`。
5. 首次实际归档前不创建空目录。

正文超过约 300 行时，先删除重复和移回权威来源的细节，再按发布周期归档；不能只因行数机械拆分。

## 任务完成检查

1. 权威来源是否先于摘要更新。
2. 本次是否改变架构、能力状态、验证范围、可复用根因、排障路径或弃用状态。
3. 当前状态是否把实现、验证、研究权限和盈利证据分开。
4. 是否出现机器绝对路径、凭据、占位内容、单次聊天流水账或无证据承诺。
5. 所有相对链接是否存在，Properties 是否使用保守口径。
6. 三角色检查是否确认没有把专业解释写成代码 Gate。
7. `git diff --check`、直接相关测试/文档检查是否通过，临时产物是否清理。
8. 提交是否只包含本次任务范围。

## 维护本页

若维护流程需要新的强制动作，先更新 `AGENTS.md`；本页随后解释操作方式。若本页与 `AGENTS.md` 冲突，以 `AGENTS.md` 为准并登记冲突。
