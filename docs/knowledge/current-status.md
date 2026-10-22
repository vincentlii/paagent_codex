---
lifecycle: active
capability: demo-execution-limited
verification_scope:
  - local
  - VPS-controlled-live
最后核验: 2026-10-22（协调发布；Demo覆盖恢复与历史复盘现场通过）
核验提交: VPS运行代码1c8b5661bf087147a2cd4c82b92cf2fb6a80044f
权威来源:
  - CONTEXT.md
  - docs/quality/runtime_reliability_vpa_remediation_20261021.md
  - docs/quality/desktop_retirement_20261021.md
  - docs/architecture/ADR-0025-joint-stage2-pipeline.md
  - docs/architecture/ADR-0027-selectable-fixed-risk-reward.md
---

# 当前状态

本页区分线上已发布事实与尚未验证事项。以前发布周期的状态已移入
[历史归档](archive/README.md)，不能用旧“未实施/未部署”描述覆盖新验收证据。

## 本地开发：增量等待复核（尚未发布）

已补全上一轮Stage 2等待理由/watch_points到下一轮决策的冻结交接，使用`decision_history_v2`。
原K号保留原时间并核对当前OHLC，历史意见与本轮证据分开；模型复核条件，不由程序代判。
Stage 1/独立VPA仍不接收旧Joint决策；无新模型阶段、Schema、观察数据库或交易Gate。
旧分析CSV兜底和撤旧单指导已替换，保留仍被日志和旧任务使用的兼容代码。
本地2090项完整非live回归及安装资源验证通过；验收及备份见[等待条件连续性验收](../quality/wait_continuity_20261022.md)。
未调用真实模型、未commit/发布；是否减少不合理等待或提高收益尚无验证。

## 线上：最后已核验发布

VPS运行代码 `1c8b566` 已推送私有main并协调发布，恢复running62，四服务active/running且零重启。
269安装成员与固定源码一致，依赖保持48项，四库安装前后逻辑一致、Host及Demo备份逐项验证。
Runtime、权限、认证接口与原配置核验见[本次发布回执](../quality/runtime_reliability_vpa_remediation_20261021.md)。
旧 Qt 桌面已退役，运行入口为 Headless 与网页 Dashboard；Linux 不导入 Qt。

正式分析为 `pa_vpa_joint`，Codex `gpt-6-luna/xhigh`，PA/Router 保持主干。
OKX Demo 是唯一运行时执行；Paper v3/v4 只读归入历史，不混算统计。
BTC/ETH/XAU 已授权启用；最近发布时每笔总价值 500 USDT、限价 3h、fixed_rr/1.5。
已有计划独立推进，新的分析不撤旧单；净仓合并不替代逐计划数量与保护归属。

Demo已从`reconciliation_gap`恢复为ready，七项覆盖全真，5笔历史订单复盘全部可读。
18条fills和18条bills原文完整保持，无新增旧计划补发；恢复时无活动计划、持仓或外部订单。
历史CLI三次exit1的具体外因仍未知，新的安全诊断及失败处理已上线。

## 已发布：运行可靠性与 PA/VPA 语义修复

用户在知悉模型质量尚未验证后，明确授权commit并发布全部工程验收修复；本次属有界Demo运行。详情见
[运行可靠性实施及复查](../quality/runtime_reliability_vpa_remediation_20261021.md)。

- OKX 事件覆盖与最后成交身份分开，近期/历史固定区间有界恢复；冲突保留原件并阻断新入场，活动保护继续。
- 旧订单按原 task/run/source/snapshot 批读自身复盘，30 秒缓存仅用于复盘，交易事实不缓存。
- CLI 未知执行异常、输出错误与普通角色停止分开；安全诊断贯通，重试遵循明确权限，半开等待计入 600 秒整链预算。
- 新前置 VPA 用 `predecision_vpa_v2`：无订单 timing 字段，Host 标记不适用；真实证据不足、反证及合法等待保留。旧 Frozen/Shadow/研究报告不迁移。
- PA 原句按路径澄清：拟议 entry 的位置、已有信号、未来触价与成交后跟随分别评价。依赖新闭盘证据才能成立的 Setup 当前仍为 wait；合法挂单不强求未来跟随。
- Direct 新旧 VPA 共用输出形状，完整 Schema 继续由传输/Host 持有；无新模型阶段、距离 Gate 或强制交易规则。

既有 ICT 可行性文档与导航改动保留，未纳入本次实现；ICT 仍属研究提案。

## 验证与未完成事项

本地2052项、发布前新鲜聚焦106项通过，备份与资源哈希统一记录在[实施及发布报告](../quality/runtime_reliability_vpa_remediation_20261021.md)，
不能将不同阶段的测试数量累加或当作真实模型质量证明。

1. 新语义尚未执行真实模型同快照匹配回放/盲评；是否减少不合理等待、改善入场或收益未知。
2. 三品种连续自然运行及新完整模型链待观察；不通过重跑历史或补发旧建议报成功。
3. 后续发布仍须暂停排空、协调服务与Runtime/Frozen身份，不能跨版本续接旧任务。
4. 旧报告/覆盖水位存在后，回退须使用兼容 reader；不改历史分析、成交、价格或收益。

继续开发先读[知识入口](README.md)、[Codex Analysis North Star](codex-analysis-north-star.md)及本次涉及的权威来源。
