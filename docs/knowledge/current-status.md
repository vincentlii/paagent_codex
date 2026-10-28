---
lifecycle: active
capability: demo-execution-limited
verification_scope:
  - local
  - VPS-controlled-live
最后核验: 2026-10-29（同周期准入及等待歧义已发布VPS）
核验提交: VPS运行代码49ce987c32702329a5c27081dfe2954c6d754dc3
权威来源:
  - CONTEXT.md
  - docs/quality/runtime_reliability_vpa_remediation_20261021.md
  - docs/quality/desktop_retirement_20261021.md
  - docs/architecture/ADR-0025-joint-stage2-pipeline.md
  - docs/architecture/ADR-0027-selectable-fixed-risk-reward.md
  - docs/quality/engineering_handoff_release_20261026.md
  - docs/quality/stop_protection_completion_20261026.md
---

# 当前状态

## 2026-10-29：同周期重复分析与等待歧义修复已发布

[43小时归因及修复验收](../quality/post_release_low_trade_audit_20261028.md)保留原始取证：
128wait/1limit，0执行suppressed；唯一限价未触价。用户要求本轮不处理额度耗尽。
本地自动轮询选同源、同闭盘、同cohort首次accepted冻结任务，后续修订继续观察，既有hash/历史不改；
重试沿原任务及上限，重启已终止不刷新预算，未完成只交恢复。新闭盘/显式配置变更仍可分析。
5份教材、Assembler与2份Role Skills原位清理neutral例外/追加确认/前置VPA时机歧义，
无新模型调用、Schema、Gate、止损或TTL规则。49ce987已推送私有main并安装VPS，Windows/Linux
各2237通过、1环境条件跳过，wheel检查通过；四服务正常、Demo ready且七项覆盖全真。
恢复running74，三品种/500 USDT/5h/固定2R及模型/Fast配置保持；四库及2977个保护文件保持。
31份历史复盘可读，44份fills/bills逐行不变。模型判断、实际成交频率与收益改善未验证，
完整备份/安装回执见上述报告末节；下节71c8abf和RR1.5为此前发布历史，不作为当前配置。

结构止损教材及native附带限价保护的本轮状态，以
[结构与保护验收](../quality/stop_protection_completion_20261026.md)的最终发布回执为准：
旧无标记任务不升级；本地通过不等于现场启用或收益改善。

本页区分线上已发布事实与尚未验证事项。以前发布周期的状态已移入
[历史归档](archive/README.md)，不能用旧“未实施/未部署”描述覆盖新验收证据。

## 已发布：结构止损教材及原生双边限价保护

代码71c8abf已推送私有main并安装VPS；11份教材39处冲突指导原位清理，结构SL仍由模型判断，
263个knowledge ID和案例保留。新任务冻结双边原价限价退出及入场native附带保护，旧任务不升级。
没有新分析角色、额外模型调用、ATR/% Gate或数据库迁移；未知保留预算，应急拒绝后阻断重发。
本地2217项非live通过，Windows/Linux CI及wheel全绿；三品种最小双成交计划的独立原价保护、
旧挂单不受影响及重启同CID核验通过，测试敞口已清空。v3的ETH清理延迟失败及原两次零发单
脚本失败保留；ETH原单后续自行成交，XAU仅补未执行部分，不能伪称单次全成功canary。
恢复running71、四服务正常、Demo ready且七项覆盖全真，原三品种/500 USDT/5h/RR1.5及
模型设置保持。自然限价退出成交、模型质量与收益未验收，见
[完整发布回执](../quality/stop_protection_completion_20261026.md)。

## 2026-10-26：第一批优化及工程交接已安装VPS

2026-10-25—26用户授权的第一批：原文去冲突、Stage 2读取冻结入场期限/账户费率、程序佣金后
执行检查及现有候选比较指导，见[ADR-0028](../architecture/ADR-0028-frozen-trade-execution-context.md)
和[验收](../quality/trader_entry_execution_acceptance_20261025.md)。旧任务/订单不升级，结构SL/
原候选/概率保留，无新增模型调用；市场hash与执行条件分开。代码27c1b23已commit、推送私有main并
安装VPS，详见[工程交接验收](../quality/engineering_handoff_release_20261026.md)；真实模型判断及收益单独验证。
最终Windows本地2154项完整非live回归通过，271个wheel代码/资源成员与源码及安装目录一致、
Headless无Qt导入；独立三角色复核通过。备份与测试/打包临时目录清理见上述验收。
本次追加维护/空HTF修复后本地2173项通过，最终Windows/Linux CI及wheel资源全通过。
PH-26仅新None入口统一为{}、旧损坏身份仍拒绝；PH-27维护停止新入场并持续持锁管理至安全排空，
活动订单会延长stop/restart，不通过停服离线保留可成交挂单。最终排空设计为主Agent复核。
271个安装成员、48依赖及安装前后四库/配置保持；账户三品种费率读取和Host冻结通过。
BTC最小单实际成交及原价sz=0.01的live OCO核验通过，维护不撤旧pending且持仓保护不退出。
测试已清空自身敞口、正式Worker恢复ready；属于合成执行验收，不是自然收益或真实部分成交证据。
同一XAU旧快照各一次真实模型对照已完成：before/after均3调用、带warning完成且wait，总token
532262/510546。after读取了原source close起算的截止时间，两次均因neutral/Setup未成立而等待，
未把前置VPA时机不适用当数据缺失。单样本不证明解释质量改善、消除过度等待或盈利。
正式running69保持三品种、500 USDT/5h、RR1.5及原模型设置；新自然成交收益尚未验收。

## 已发布：执行价格网格与未提交流水

2026-10-23用户授权程序将限价执行价格适配目标tick。原decision保留，有效Entry/SL/TP单独审计；
fixed_rr重算目标并以有效价核对方程，无新增模型调用。旧任务不迁移、不补发23点XAU建议。
已登记但提交前拒绝的计划显示“未提交”及原因，普通wait不混入订单统计。
范围、性能、备份和VPS发布回执见[验收](../quality/execution_price_grid_20261023.md)。代码167d482已推送私有main并发布。
最终2113项非live通过；价格处理本机中位约0.049毫秒，270成员安装资源验证通过。
恢复running66，四服务正常、Demo ready且七项覆盖全真；原三品种、500 USDT、3小时和1:1.5配置保持。
旧XAU拒绝流水及8份历史复盘可读；未补发旧计划，未调用模型或发送测试订单。自然新计划成交尚待观察。

## 已发布：增量等待复核

已补全上一轮Stage 2等待理由/watch_points到下一轮决策的冻结交接，使用`decision_history_v2`。
原K号保留原时间并核对当前OHLC，历史意见与本轮证据分开；模型复核条件，不由程序代判。
Stage 1/独立VPA仍不接收旧Joint决策；无新模型阶段、Schema、观察数据库或交易Gate。
旧分析CSV兜底和撤旧单指导已替换，保留仍被日志和旧任务使用的兼容代码。
本地2090项完整非live回归及安装资源验证通过；验收及备份见[等待条件连续性验收](../quality/wait_continuity_20261022.md)。
代码d9c9fe6已推送私有main并发布；恢复running64，四服务正常、Demo ready、七项覆盖全真。
原金额/TTL/盈亏比/模型设置保持。停服退出钩子撤销了原BTC零成交挂单，未补发；详见验收回执。
真实模型是否减少不合理等待或提高收益尚无验证。

## 前次发布基线（1c8b566）

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
