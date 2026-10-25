# 维护交接、空高周期恢复与第一批优化发布验收

## 授权及验收范围

2026-10-26用户授权补齐PH-26/PH-27、发布此前第一批优化，核验真实费率与订单保护链，
随后验证模型和交易效果。保留已有ICT研究变更，不将其纳入交易能力或本次独立提交。
第一批实现及2154项既有证据见[验收](trader_entry_execution_acceptance_20261025.md)，
本次完整回归/固定提交/现场结果在末尾补录，不以旧结果代替新验证。

## PH-26：只统一新任务的空HTF表示

首次冻结时将`htf_context=None`规范成`{}`，同一深复制对象用于payload和身份计算。
显式`available/unavailable/disabled`及原`{}`任务保持；codec明确拒绝非object输入。
不改通用hash、不添加双hash容错、不重写旧任务。旧None-hash/{}-payload已经丢失原表示，
恢复仍严格拒绝并保留原件，而不是猜测历史身份。没有数据库迁移或回填。

新增12项测试先在原实现复现6红6绿，修复后任务/factory/recovery/Headless等136项通过。
实现及初审由本轮唯一子Agent完成；主Agent核对diff并在最终全量中独立复验。

## PH-27：维护停止接新，但不停止现有保护

原main在SIGTERM后设置`entry_enabled=false`并最后tick，导致未到期旧挂单被撤。
新信号只设内存`_stopping`，不改账户配置或订单TTL；后续不注册新spool、不启动新计划。
已经进入持久化准备/提交的请求属于在途操作，仍按原unknown/不可重发协议完成观察。
不承诺信号瞬间可以中断一个已开始的HTTP请求，也不改腿摘要或数量预算。

最初的“最后一次tick后直接退出”经独立审阅发现P2：限价没有交易所原生预绑定保护，
停服后新成交将无人补OCO。该做法未发布。最终改为**同一进程持锁持续管理并排空**：
既有订单继续观察成交/精确数量保护/退出，挂单自然TTL、部分成交撤余量及主动禁入场语义保留。
只有新鲜账户快照、完整覆盖、无error、无已提交活动计划、无普通单/algo/持仓才允许退出。
未知请求、覆盖缺口、外部敞口均阻止维护退出，不靠推测释放预算。未提交意图留待原恢复规则处理。

systemd使用`TimeoutStopSec=infinity`，避免180秒后把仍在保护的Worker强杀。代价是活动单或
长期故障可使正常restart长期等待；发布必须先暂停Host并确认Demo空闲再stop，不用强杀、
撤单或补发解决等待。手动强杀、硬件断电和网络长断不在此保证内；仍有既有REST成交至保护
轮询窗口，未宣称连续原生原子保护。主机重启前同样应先排空。

7项直接测试覆盖CLI实际SIGTERM、v1/v2未到期保留/正常到期仍撤、partial数量原价保护、
明确禁入仍撤、快照中收到stop不入场，以及stop之后才成交→保护→退出后才允许handoff。
旧cancel-on-shutdown语句已删除；没有新交易规则、LLM输入、配置开关或退出物化器。
独立审阅者指出P2后触及账户使用上限，最终排空设计由主Agent三角色复核及测试验证，
不把后续自审伪称为独立复核通过。PA/VPA无新增市场裁决；量化侧保留身份/锁/unknown/保护预算。

## 备份、兼容及恢复

本地`docs/changelog/backups/20261026-engineering-release/engineering-before.zip`保存7份实施前
源码/文档，SHA256=`ed6b00dc32c2c3d6f2eaf2e4794ef288deb97213ca09fe61cb7a243231e26cc0`；
成员摘要及ZIP重读校验通过。PH-26另有修改前原件；第一批教材/源码备份见前述验收。
VPS安装前使用原Host备份和独立Demo锁备份，核验manifest、SQLite及安装前后逻辑等价。
原配置/认证/四库/历史订单不迁移；回退不能让旧Worker接管含新context的Intent或活动订单。
恢复需匹配版本并保持原执行保护，不能只恢复源码或覆盖已有账本。

## 现场预检（发布之前，只读）

VPS源码HEAD为`8887ad0141ce3644bab96501771b165832db52fc`，四服务active/running且零重启；
Host无排队/执行/恢复阻塞；Demo ready，七项覆盖为true，无活动计划/普通单/algo/持仓。
真实模拟账户的BTC/ETH/XAU各自按instFamily和groupId=4查到`feeGroup`：
maker=-0.0002、taker=-0.0005，保守收费0.0005。账户身份匹配，凭据留在systemd credential，
没有实盘请求或订单操作。只读预检不能替代新Worker缓存、Host冻结和真实保护生命周期。

## 发布及效果验证

本地最终完整非live回归2173项通过、7项live未执行，124.59秒；两条既有tvDatafeed弃用warning。
严重静态错误及diff检查通过；新无HTF与维护交接直接测试、原PA/off/Shadow/研究/订单数学/
记录链均在完整回归内。没有新增产品依赖、数据库迁移、历史结果或执行参数变更。

待记录：最终本地/安装验收、固定commit/推送、VPS备份安装和健康、费率冻结、隔离最小Demo
保护/重启交接验证、模型分析及自然样本。不同证据分开，不将canary的合成计划计作策略收益。
模型调用的失败/限流保留，不通过重跑洗掉；数量/时间不足时只报告观察，不能证明收益提高。

官方依据：[systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
的TimeoutStopSec=infinity，以及原[OKX Demo协议](okx_demo_execution_acceptance_20261015.md)。
