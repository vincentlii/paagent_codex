# ADR-0003：PA、VPA 与联合裁决角色分离

- 状态：Accepted
- 日期：2026-07-20

> 生产 Soft/Joint 部分已由 ADR-0009 取代；角色权限契约与研究 Joint 设计继续有效。

## 背景

旧 VPA 实现把 Brooks 与 VPA 结论放进同一 Stage 1/Stage 2 Schema，或用冻结 Brooks 输出生成
`vpa_context` / `vpa_impact` 差量。前者无法区分 VPA 的独立信息增量，后者容易把 VPA 变成
隐性审批器。两种方式也都把生产 Shadow 与反事实 A/B 混在同一 orchestrator 中，增加分支、
重试契约和失败边界。

## 决策

角色模型使用三个有权限边界的结构化对象，其中 Joint 只用于研究：

1. `PaHypothesisSet`：由 `compile_pa_handoff(...)` 对已验证 PA Stage 1/Stage 2 和同一冻结
   价格快照进行编译。Stage 1 提供主/替代假设；Stage 2 若存在可执行订单，则形成携带真实
   订单字段、可选 Setup/trigger/invalidation、程序交易数学和 PA 已引用窗口数值位置的 candidate。
   限价单与市价单不因 `entry_rule=null` 被丢弃，程序也不得编造缺失的 Setup 文本。
2. `VpaEvidenceReport`：VPA Analyst 针对每个 PA hypothesis 给出支持、挑战、中性或信息不足，
   且只能引用程序 evidence catalog。
3. `JointDecisionProposal`：Joint Arbiter 在既有 PA candidate 内决定保持、等待确认、优先回测、
   降低置信度或拒绝本次 Entry Attempt，并只输出有界 `decision_patch`。程序把合法补丁应用到
   冻结 PA baseline 后重新执行完整 Stage 2 校验。

PA-only Stage 2 先形成冻结 baseline。生产 Shadow 只追加 VPA report，并始终以 PA-only 为
正式结果；研究 B 臂才允许 Joint 及一次定向结构修复。Joint 不得复制或修改订单三价、创建 PA
没有的交易、翻转方向、修改 `estimated_win_rate` 或绕过 Stage 1 Gate。生产置信度执行阈值只对
正式 PA 结果应用一次。

生产 Shadow 不属于 A/B。完整 A/A/B/B 由独立实验器在离线或抽样环境执行，记录 Prompt、
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
- Shadow 正式结果逐字段等于 PA-only baseline；只有研究 B 臂可生成 Joint proposal。
- 生产 VPA 失败保留 PA-only 并记 warning；研究 Joint 失败保留该臂的 PA-only，不产生新订单。
- 生产永不调用 Joint；研究中 Gate 未通过时也不调用 Joint，VPA 不得绕过 PA Gate。
- 每个 terminal return 都持久化一个合法 `RunOutcome`。
