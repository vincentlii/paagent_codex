# 运行可靠性与前置VPA修复实施

用户已批准按`docs/quality/runtime_reliability_vpa_remediation_20261021.md`完整修复。
本计划落实该方案；不新增策略权限，不改历史记录，不自动commit或发布。

## 验收边界

- OKX Client/Snapshot/Store：历史接回、完整观察水位、有限分页续接及冲突拒绝。
- Host交易投影→两次allowlist→页面点击：旧Demo订单按完整身份保留原复盘。
- Worker→Spool→Backend→record/Handler：可信诊断、未知用量与不可重试权限完整传递。
- Backend准入与Frozen Job预算：单探针、有界等待、取消、旧代次安全和VPA240/整链600。
- 前置VPA→Joint：新显式scope不再要求订单timing，旧Frozen/Shadow/research原义。
- PA知识：计划位置/现价、信号/触发/跟随作用域澄清，保留原锚点/案例和反证。

## 顺序

1. 备份并校验当前源码；保留既有ICT四文件。
2. 分组先写失败行为测试，分别修复OKX、复盘、诊断和重试。
3. 修复熔断准入与预算；更新冻结策略身份，旧任务保持原值。
4. 贯通前置VPA模型/Host契约、Direct/Codex/repair/records/读者及原知识作用域。
5. 一名审阅者交叉检查三角色；完整非live回归、静态、打包/无Qt、清理和知识同步。
6. 真实模型对照、生产切换与VPS发布按原方案单独验收；未执行时明确列出，不冒充通过。

## 备份

`docs/changelog/backups/20261021-runtime-remediation/source-before.zip`，662成员逐项SHA256核验。
备份SHA256：`492ecdcba086b78cb5b5966d377db7d933b4b782ebf31ae91e46f5d854093e82`。
