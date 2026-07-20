# ADR-0003：PA、VPA 与联合裁决角色分离

- 状态：Accepted
- 日期：2026-07-20

## 背景

旧 VPA 实现把 Brooks 与 VPA 结论放进同一 Stage 1/Stage 2 Schema，或用冻结 Brooks 输出生成
`vpa_context` / `vpa_impact` 差量。前者无法区分 VPA 的独立信息增量，后者容易把 VPA 变成
隐性审批器。两种方式也都把生产 Shadow 与反事实 A/B 混在同一 orchestrator 中，增加分支、
重试契约和失败边界。

## 决策

正式链路使用三个有权限边界的结构化对象：

1. `PaHypothesisSet`：由已验证 PA Stage 1 形成主假设、替代假设和 Brooks Setup candidates。
2. `VpaEvidenceReport`：VPA Analyst 针对每个 PA hypothesis 给出支持、挑战、中性或信息不足，
   且只能引用程序 evidence catalog。
3. `JointDecisionProposal`：Joint Arbiter 在既有 PA candidate 内决定保持、等待确认、优先回测、
   降低置信度或拒绝本次 Entry Attempt。

PA-only Stage 2 先形成冻结 baseline。VPA 和 Joint 各允许一次定向结构修复；失败时 Soft 明确
退化为 PA-only，Shadow 继续以 PA-only 为正式结果。Joint 不得创建 PA 没有的交易、翻转方向、
修改 `estimated_win_rate` 或绕过 Stage 1 Gate。置信度执行阈值只在正式结果确定后应用一次。

生产 Shadow/Soft 不属于 A/B。完整 A/A/B/B 由独立实验器在离线或抽样环境执行，记录 Prompt、
Snapshot、Provider、顺序和组内随机波动。

## 后果

- PA 定义框架与 Setup，VPA 提供独立量价证据，Joint 负责上下文裁决，权限可单独测试。
- VPA/Joint 的模型格式失败不再使有效 PA 结果整体失败；取消、输入不可用、Provider 不可用和
  内部错误仍是独立终态。
- `vpa_context` / `vpa_impact` 只作为 GUI 与历史 records 的兼容视图，不得重新成为正式协议。
- 在线 marginal sidecar 与在线 full-prompt paired runner 已从生产 orchestrator 删除；历史 records
  仍可由 audit 层读取，但不得恢复为生产入口。

## 验证

- `off` 路径不得构建或注入任何 VPA 内容。
- Shadow 正式结果逐字段等于 PA-only baseline；Soft 正式结果来自合法 Joint proposal。
- VPA/Joint 首轮及一次修复失败均退化为 PA-only，不产生新订单。
- Gate 未通过时不调用 Joint；VPA 不得绕过 PA Gate。
- 每个 terminal return 都持久化一个合法 `RunOutcome`。
