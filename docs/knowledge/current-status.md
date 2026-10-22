---
lifecycle: active
capability: demo-execution-limited
verification_scope:
  - local
  - VPS-controlled-live
最后核验: 2026-10-22（本地复查；VPS 沿用 2026-10-21 发布证据）
核验提交: 本地 HEAD bf453c1；未提交改动；VPS 123adb1ee88f359e227d2d9fca16599ed8603579
权威来源:
  - CONTEXT.md
  - docs/quality/runtime_reliability_vpa_remediation_20261021.md
  - docs/quality/desktop_retirement_20261021.md
  - docs/architecture/ADR-0025-joint-stage2-pipeline.md
  - docs/architecture/ADR-0027-selectable-fixed-risk-reward.md
---

# 当前状态

本页区分线上已发布事实、本地修改和尚未验证事项。以前发布周期的状态已移入
[历史归档](archive/README.md)，不能用旧“未实施/未部署”描述覆盖新验收证据。

## 线上：最后已核验发布

最后已核验 VPS 发布代码为 `123adb1`，证据时间为 2026-10-21；本轮未重新读取或修改 VPS。
四服务、Runtime 安装资源及网页权限检查见[桌面退役发布记录](../quality/desktop_retirement_20261021.md)。
旧 Qt 桌面已退役，运行入口为 Headless 与网页 Dashboard；Linux 不导入 Qt。

正式分析为 `pa_vpa_joint`，Codex `gpt-6-luna/xhigh`，PA/Router 保持主干。
OKX Demo 是唯一运行时执行；Paper v3/v4 只读归入历史，不混算统计。
BTC/ETH/XAU 已授权启用；最近发布时每笔总价值 500 USDT、限价 3h、fixed_rr/1.5。
已有计划独立推进，新的分析不撤旧单；净仓合并不替代逐计划数量与保护归属。

最后现场证据仍有 Demo `reconciliation_gap`（fills=false），安全阻断新入场，活动计划/持仓为 0。
这项线上故障尚不能因本地修复而标记恢复；历史 CLI 三次 exit1 的具体外因仍未知。

## 本地：运行可靠性与 PA/VPA 语义修复

用户已批准修复；源码、测试与 Prompt 改动尚未 commit、推送或发布。详情与最新验收见
[运行可靠性实施及复查](../quality/runtime_reliability_vpa_remediation_20261021.md)。

- OKX 事件覆盖与最后成交身份分开，近期/历史固定区间有界恢复；冲突保留原件并阻断新入场，活动保护继续。
- 旧订单按原 task/run/source/snapshot 批读自身复盘，30 秒缓存仅用于复盘，交易事实不缓存。
- CLI 未知执行异常、输出错误与普通角色停止分开；安全诊断贯通，重试遵循明确权限，半开等待计入 600 秒整链预算。
- 新前置 VPA 用 `predecision_vpa_v2`：无订单 timing 字段，Host 标记不适用；真实证据不足、反证及合法等待保留。旧 Frozen/Shadow/研究报告不迁移。
- PA 原句按路径澄清：拟议 entry 的位置、已有信号、未来触价与成交后跟随分别评价。依赖新闭盘证据才能成立的 Setup 当前仍为 wait；合法挂单不强求未来跟随。
- Direct 新旧 VPA 共用输出形状，完整 Schema 继续由传输/Host 持有；无新模型阶段、距离 Gate 或强制交易规则。

既有 ICT 可行性文档与导航改动保留，未纳入本次实现；ICT 仍属研究提案。

## 验证与未完成事项

本地工程验收、备份与资源哈希统一记录在[实施报告第18–19节](../quality/runtime_reliability_vpa_remediation_20261021.md)，
不能将不同阶段的测试数量累加或当作真实模型质量证明。

1. 新语义尚未执行真实模型同快照匹配回放/盲评；是否减少不合理等待、改善入场或收益未知。
2. 后续发布须暂停并排空 Host/Worker、备份状态，协调服务与 Runtime/Frozen 身份；不能跨版本续接旧任务。
3. Demo 恢复须现场验证历史覆盖、复盘、原保护及自然三品种运行，不能删除游标或补发旧建议报成功。
4. 旧报告/覆盖水位存在后，回退须使用兼容 reader；不改历史分析、成交、价格或收益。

继续开发先读[知识入口](README.md)、[Codex Analysis North Star](codex-analysis-north-star.md)及本次涉及的权威来源。
