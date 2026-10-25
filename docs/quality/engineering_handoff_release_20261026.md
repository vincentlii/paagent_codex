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

固定实现提交`07c89b8`首次Linux CI为2171通过/1跳过/1失败：既有
`test_codex_deepseek_provider`创建假binary没有POSIX执行位，生产`resolve_executable`正确拒绝。
仅为该fixture补`chmod(0o700)`，不放宽运行校验、不改DeepSeek能力；原失败日志保留。
安装前重新固定测试修复提交及CI结果，不能把Windows通过当作Linux通过。
最终CI：Windows与Linux各2172通过、1跳过、7项live未执行，分别211.36秒/92.60秒；
与本机2173全过的差别为环境条件跳过，不增加或隐藏失败。wheel/Linux资源及shell检查success。

实现及测试修复`07c89b8`/`27c1b23`已推送私有main；固定运行版本为
`27c1b230863c085a835cf93d7ca37184d737c0d4`。其Windows、Linux全量CI、wheel/Linux资源及shell
检查全部success；原失败不删除。[CI回执](https://github.com/vincentlii/paagent_codex/actions/runs/40215333561)。

VPS暂停排空至paused68，无恢复阻塞、inbox为空、无活动订单后协调停四服务。
Host备份`/var/backups/pa-agent/pa-agent-20261025T171017Z`共19812成员；
Demo备份`/var/backups/pa-agent-demo/demo-1792948253064`共503成员，manifest/SQLite均通过。
安装前后四库逻辑一致、2263个受保护文件及48个非项目依赖保持，原设置/认证未替换。
271个安装成员与固定源码/wheel逐字节一致，Headless无Qt；Runtime资产及POSIX权限核验通过。
Linux wheel SHA256=`3d8071849facc4f5b4ba4d80bf227f5cb76e27eebb4941b2cfd785e7f93e3e6a`，
Runtime digest=`c98fb98e4cd63233d2a03b82d78f2bd51ac42c713c8ff9de801f8d2444953d94`。
Demo service停止超时已实际读回infinity，四服务在paused边界active/running且零重启。
原28份fills/28份bills逐行保持，30份历史订单自身复盘非空，API鉴权401/授权200及Paper历史冻结通过。

### 真实费率与最小保护验收

新Worker从账户取得三个feeGroup费率并缓存，Host私有scope匹配；对各自原冻结frame离线组装
context验证TTL=5h、绝对expiry=原source close+5h。没有改旧任务或补发订单。
冻结链证据与公开面板不暴露scope的离线回归分开；仅查费率不是保护生命周期验收。

用户授权范围内用唯一隔离service及账本运行BTC最小数量`0.01`张canary；正式分析保持暂停，
正式Demo Worker先安全退出，不与测试并发操作同账户。两个计划均明确标记synthetic，不写
正式分析/订单账本，不当作自然交易或策略收益：

| 测试 | 实际结果 |
| --- | --- |
| 未成交普通限价，entry=64604.32，总价值6.460432 USDT | 实际live/零成交；request_shutdown后3轮仍为同ordId/live，can_handoff=false，原期限未改 |
| 上单显式测试清理及同账本重启 | 查询撤单终态及安全排空后重启，原客户ID/腿数保持，无补发 |
| 带新fee/TTL marker的可成交限价，entry=80747.62，总价值8.074762 USDT | 实际成交；交易所live定量OCO，sz=0.01、SL=76710.23、TP=84785.01；严格原价及独立数量协议核验通过 |
| 有持仓/保护时维护请求 | 同algo保持，未增加退出腿，can_handoff=false，管理继续 |
| 测试收尾 | 只取消测试自身订单/保护，原预算释放确认后reduce-only清理自身剩余；普通单/algo/仓位及隔离active均0 |

canary service success/inactive，正式Worker恢复ready，七项coverage全真。未观察真实部分成交，
该路径只有离线覆盖；测试主动清理退出不冒充OCO自然触发或盈利。账户口径费用会包含canary，
其合成计划不进入正式策略逐计划PnL。

已恢复running69：原三品种、500 USDT/5h、fixed_rr=1.5、gpt-6-luna/xhigh/Fast/并发3全部保持。
四服务active/running、Result=success、零重启，Host无恢复阻塞，三品种connected；Demo ready且
七项coverage全真。五类HTTP接口授权200、未认证401，30份历史复盘非空可读，Paper历史冻结。
现场临时build-source和上传发布包已清理；完整备份、安装manifest与隔离canary账本保留。

模型观察预登记为`trade_entry_single_pair_v1`：同一真实旧XAU冻结任务（100根1h、60根4h）、
同一原增量历史/已冻结来源、fixed_rr1.5和gpt-6-luna/xhigh，各版本各一次，顺序before→after。
before源码8887ad0，after源码27c1b23；after额外冻结TTL/已核验费用，教材/Skill按自身版本。
执行在专用Codex用户的独立目录/会话，凭据只在VPS自有隔离home，无生产DB读取权限；
Host先复制只读输入，不生成订单或写生产记录。不是PA/VPA增量实验，不宣称统计非劣/盈利。
两臂均一次完成，3调用、completed_with_warnings，无repair调用或执行订单：

| 指标 | before（8887ad0） | after（27c1b23） |
| --- | --- | --- |
| 整链耗时 | 343.74秒 | 336.92秒 |
| 实际输入token（含缓存部分） | 496821 | 478848 |
| 其中cached input | 233088 | 200320 |
| 实际输出token | 35441 | 31698 |
| 实际总token | 532262 | 510546 |
| 终点 | wait / 9.0P | wait / 9.2 |
| 执行处置 | not_applicable | not_applicable |

两臂usage coverage完整，未知用量0；缓存包含于输入，不能再次相加。单次总token下降4.08%、
耗时下降1.99%只描述本对照，不能推断因果、分位延迟、持续降费或非劣。调用未开启正式Fast，
不能拿这次耗时作为线上Fast性能。两次没有候选，故本对照不验证实际候选的佣金后放行率。

PA复核：两次主要等待理由都是Stage 1冻结neutral背景且无获准已成立Setup；after区分旧等待
条件的局部进展与更宽范围确认，并实际引用source close起算的5h绝对截止时间，没有靠延长
本轮分析时间重新起算TTL。但两臂对旧K号及局部触发的文字复述并不完全一致，本次没有独立
盲评裁定哪份解释更好；仍未证明不会继续出现过度等待，也没有新增放宽方向权限的修补。

VPA复核：两次保留量价对偏空解释的反证，也保留尚未确认的接受/跟随；前置时机not_applicable
没有被写成insufficient_data或数据缺失Gate。不能据此证明每次VPA解释正确或有独立收益增量。

工程复核：无失败或额外repair；Stage 1质量warning分别5/6项，VPA专业文字warning均3项，
Stage 2各有一次无歧义normalization，均保留原审计，不伪称无warning。程序费用计算和真实保护
验收与模型判断分开；它们不决定背景、Setup或概率。原输入/protocol/driver/两臂record、result
及SHA已归档至本次VPS发布目录，两个临时home的复制auth已删除，原auth校验保持，测试上传
脚本已删除。两臂独立service均success/inactive。

最终恢复检查仍为running69，三品种connected、四服务active/running且零重启，Demo ready，
七项coverage全真，无活动敞口或恢复阻塞，30份历史复盘可读。启动后尚未接收新的自然闭盘
分析样本，故没有新自然成交/收益验收；不能把这一时点的0个任务算作不下单率或0R样本。
下一自然闭盘继续按原调度运行；需要新前向完整记录及足够退出样本后，分别检查提交拒绝、
触达/到期、成本后收益及不确定覆盖。收益仍未验证，不以合成canary或两笔历史止损下结论。

不同证据分开，不将canary的合成计划计作策略收益。
模型调用的失败/限流保留，不通过重跑洗掉；数量/时间不足时只报告观察，不能证明收益提高。

官方依据：[systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
的TimeoutStopSec=infinity，以及原[OKX Demo协议](okx_demo_execution_acceptance_20261015.md)。
