# 结构止损指导与开仓附带限价保护

## 授权、范围与边界

2026-10-26用户要求完整清理止损相关确定问题，commit并发布本轮优化；包含上一轮已确认的双边
限价退出。旧任务/挂单/持仓保持自身冻结协议，不补发历史计划，不为凑RR改结构SL或概率。
已有ICT四项研究改动独立保留，不纳入本轮。实际安装及真实Demo验收在完成后分别记载。

三角色设计：PA保留当前Setup的结构失效解释，清理矛盾原句而不加ATR/%硬下限；VPA无新Gate
或订单权限；程序只冻结执行协议、数量预算、费用及可验证回报。未经校准的模型p仍是主观先验，
佣金后方程不是收益证明；限价未成交、滑点/资金费仍明确未覆盖，不伪造概率校准。

## 教材清理

由本轮唯一子Agent负责全集可交付策略扫描、原位替换与冻结knowledge回归，主Agent复核。
11份教材39处不适用指导替换：非关键结构紧缩SL、先TP后stop、长信号棒自动否决、信号棒
通用最低标准、成交后补选SL、限价触价即成交。263个knowledge ID/顺序和案例/反证保留。
1–2 tick仅作已定结构锚点及Host真实tick下的缓冲示例，不成为通用阈值。备份及结构验证在
`docs/changelog/backups/20261026-stop-protection-prompt/`；不是删除Brooks全部止损方法。

## 原生附带保护：一个Worker、一个逐计划账本

官方2026-07-01公告：主单完全成交，或部分成交后撤销剩余时，交易所生成其附带止盈止损，
数量取主单实际成交量；不采用分批TP。本次使用单个condition对象的双边限价TP/SL、last触发。
这省去Host观察成交后再POST保护的环节，不承诺交易所生成原子性；部分成交→撤余仍存在窗口。

新context以可空`protection_binding=entry_attached`绑定任务/cohort并只到Stage 2；旧缺字段省略，
原market及此前limit-only任务不升级。DemoConfig沿用已有能力验收模式，新增空默认的
`attached_accepted_symbols`；仅现场通过品种才启用，空字段不改变旧cutover指纹。新任务未验收
目标明确拒绝，不假设旧market保护证据能代表新native协议。

request绑定唯一`attached_algo_client_id`，随入场HTTP提交。入场POST前持久登记同CID的OCO g1，
不是第二次网络提交。g1 params冻结最大量request.sz及entry身份；effective退出预算取该计划
当前已知entered，entered不得超过冻结max。所有reserved/protected/child数量校验使用同一
effective量，从而终态成交`.4→.6`补报无需修改params或另发g2。零成交待成交不预约退出量，
但仍是活跃入场单；可信零成交终态且无冲突时明确结束未激活附件。

部分成交先撤未成交余量以促原生附件生效；附件暂未出现/GET空/主单成功均不证明失败。
观察原生OCO和子单先于共享净仓对账；已在交易所退出不能被误判成额外裸仓再应急。实际live
须核验自身CID、数量、side、原SL/TP、数值委托价、last、reduce-only/net。矛盾保留预约并阻断，
不通过取消未知保护或重复POST修正。

只有native终态失败且无未决child，或精确附件CID下可信数字失败码且从未见冲突live证据时，
才释放未退出预约并走原SL reduce-only限价应急；晚到入场只可使用未预约增量。触发未成交、
partial/unknown子单继续占预算。应急拒绝阻断无限重发；维护仍等空仓空单，不升级市价。

## 验证计划与备份

先红后绿锁住：单HTTP附带参数/旧字节、capability证书、g1最大量与effective量、partial撤余、
native先退出、零成交终态、晚到成交修正、故障/未知/重启及双计划数量隔离。
完整not-live回归、wheel/静态检查、Windows/Linux CI及VPS最小合成全链验收分开。
原native协议代码备份`docs/changelog/backups/20261026-native-protection/`含9成员/校验清单；
上一轮limit备份继续保留。无数据库迁移或历史回填；新数据产生后不能直接回退旧reader。

官方依据：[OKX附带保护公告](https://www.okx.com/zh-hans/help/okx-announcement-on-optimizing-order-placement-for-order-attached-take)、
[OKX API](https://www.okx.com/docs-v5/en/#order-book-trading-trade-post-place-order)。

## 独立审阅与实际探测中的失败证据

唯一审阅者指出并回归复现三项边界：陈旧terminal零成交不能覆盖已知partial、未激活附件的
重开例外不能永久允许真实终态回退、50004不能归为确定业务拒绝。修复复用单调持久化数量、
首次真实观察清除例外标记及原错误分类。child先于parent回报时先保留已知child事实，以
当前entered计算inconsistent并阻新；parent追上后按原CID恢复。独立21项native回归通过，
三角色限定验收通过；不以自审替代独立审阅，也不将现场partial或收益视为已验证。

首次直接REST探测仅BTC执行：8.053499 USDT最小合成入场，其native OCO的原SL/TP、精确量、
last/net/reduceOnly和数值委托价核验通过。显式清理用远离当前价的原SL限价被51006价格带
拒绝，整次probe因此失败，未执行ETH/XAU；该失败保留，不计为三品种验收。原client只保留
顶层code=1，现统一保存单项安全sCode；不能倒推两笔历史丢码OCO拒绝也由51006导致。
经确认原OCO已撤、只剩测试自身0.01张后，用当时有效价格带内的reduce-only限价完成显式
清理并核验空仓空单。没有改正式计划、原SL或账本结果，也没有升级市价。

最终Worker三品种canary使用新预登记协议和隔离账本，独立报告旧probe失败；实际验收新
入场附带HTTP→原生保护→维护持续管理→重启同身份及同向双计划不互相取消。清理是明确的
合成实验清理，价格取新鲜报价且符合公开价格带，不是策略原价止损或正式收益样本。

原价限价保护在触发时也可能被价格带拒绝或不成交；这属于用户已接受的限价退出风险。
程序保留安全码、仓位和未知预算并阻断，不偷偷改单价/市价或反复重发。

## 本地最终验收

最终完整非live回归2217通过、7项live未执行，157.92秒；原PA/off/Shadow/研究AABB、HTF、
数学、记录、恢复与保护链均在全量范围。21项native回归由唯一审阅者独立复跑通过。
严重静态错误及diff检查通过，最终wheel含271个源码/资源成员，与源码及隔离安装逐字节
一致，Headless无Qt。wheel SHA256=`9a671e1f49c02f4b302c363c0f40222718d4b8daf42265ce56ff4fde7cdc02cd`。

保留先前受本机权限限制而中止的全量日志，以及首个可运行全量2216通过/1配置形状断言失败
的原结果；根因为新空能力字段进入旧配置投影，现由唯一serializer省略空字段，原cutover
摘要保持。未新增依赖、DB迁移或历史回填。测试/安装中间目录在提交前按工作区范围清理，
ZIP、manifest、原失败及最终JUnit/wheel保存在忽略的备份目录。

## VPS发布及实际验收（2026-10-27完成）

固定代码`71c8abf46747e888a5dbd1f3b6747c771b2ad766`已提交、推送私有main并安装VPS。
[CI回执](https://github.com/vincentlii/paagent_codex/actions/runs/40336144868)全部success：
Windows/Linux各2216通过、1环境条件跳过、7项live未执行，264.02/73.68秒；两条既有
tvDatafeed弃用warning。wheel/Linux资源及部署shell检查通过。271个代码/资源成员与固定
源码、Linux wheel、安装目录逐字节一致，48个依赖不变，Headless无Qt。
Linux wheel SHA256=`629cd49b5854f7d095c1302454eec519335e13f599c8a4e5c172b6c5e99fb074`。

发布前paused70且队列/Runtime inbox排空，无恢复阻塞、空仓空单，协调停止四服务。
Host备份`/var/backups/pa-agent/pa-agent-20261026T140404Z`含22186成员；
Demo备份`/var/backups/pa-agent-demo/demo-1793023486362`含572成员。
manifest/SQLite检查通过；安装前后四库逻辑一致，2471个保护文件保持。
后续仅在暂停/空闲边界更新`attached_accepted_symbols`及对应cutover配置摘要；
原epoch、cutover时间、账户身份和其他配置保留，旧任务不重绑。

### 现场证据与保留失败

首次Worker脚本v1的symbol映射错误、v2的event参数冲突均在发单前停止，entry POST均为0，
账户flat；原报告保留。修正脚本后以原event+原make_plan做三个品种离线预检，并独立审查
未激活附件notfound、own child/emergency撤单、唯一cleanup CID恢复和总可成交金额上界。

v3实际执行BTC/ETH：各一笔未成交挂单、两份同向最小成交计划，精确原SL/TP/数值委托价、
last、net、reduceOnly、独立native CID/数量及重启恢复通过；每品种全体计划可成交名义额
合计不超过50 USDT。两笔BTC清理完成；ETH最后一笔原清理限价因行情移动未立即成交，
v3因此在清理阶段失败，原`checks_passed`不补成功、`cleanup_complete=false`不改写。
工具自动审批额度耗尽中断了后续清理操作；恢复时GET确认该原CID已自行filled、账户flat，
没有实际改单或重新POST。该失败不等于原生保护失败，也不能忽略为成功的单次三品种canary。

只对尚未执行的XAU登记2026-10-27独立补充：一挂单加两份同向最小成交计划，总名义额约
12.05 USDT；两份保护和重启原CID核验通过，显式reduce-only限价清理完成，最终flat。
BTC/ETH原结果没有重跑；原生能力的最终证据是v3已完成的两品种保护核验、原ETH清理
自行成交的后续回报与XAU补充验收，三者分别保留。总entry POST=9、独立OCO POST=0；
正式策略账本不导入合成计划，原始32份fills/bills逐行保持，新增12份实际合成成交回报如实保存。
实际账户余额/费用含合成交易影响，不能把账户总额差直接视为策略收益。
现场“重启同CID”具体是同一验收脚本内释放Worker锁、重建DemoWorker并从持久账本恢复；
不是带仓systemd进程重启或断电验收。正式服务的安装重启发生在空仓排空边界。

### 启用与健康

三品种能力表已启用，Host新建context实际冻结`exit_order_type=limit`和
`protection_binding=entry_attached`；真实maker/taker=-0.0002/-0.0005、保守费用率0.0005，
TTL仍为5h且绝对expiry按原source close计算。该检查只离线组装新context，不改原任务或发单。
最终恢复running71：四服务active/running、Result=success、NRestarts=0；三个品种均connected，
无恢复阻塞；Demo ready、七项coverage全true，测试订单/持仓已清空。500 USDT/5h、固定RR1.5、
原模型/effort/Fast/Profile均保持。面板未认证401、授权接口200，30份历史订单自身复盘非空，
Paper历史维持只读冻结。

VPS临时构建目录、上传包和脚本已清理；失败测试unit先保存状态再清除failed标记，隔离
账本、原报告、manifest和备份作为审计保留。本地测试/安装/构建目录已清理，例外是忽略的
`.pytest-native-red`目录：普通及提升执行、ACL读取/重置、所有权和非递归删除均被Windows
拒绝访问，未扩大到其他路径或提权绕过；不影响发布包，但不能宣称所有本地缓存均已删除。

工程和受控最小Demo验收完成；真实partial、触发后的自然限价成交、模型质量、成交率和
样本外收益仍未验收。止损由模型按结构失效判断，不新增ATR/%硬阈值；p仍未校准，
费用检查不代表正期望证明。限价原价触发后仍可能不成交或遭价格带拒绝，不承诺亏损上限。
