# 双边限价退出：设计与验收

## 授权及范围

2026-10-26用户明确选择“止盈和止损均限价退出，接受触价后可能无法成交”。
仅后续新Demo冻结任务使用；既有任务、挂单、持仓、价格和历史结果不迁移。
本轮不含commit、VPS发布、真实交易所订单或模型调用。

现有7份未提交历史计划中，5份为目标行情已越过原SL、1份旧tick错误、1份决策过期。
这是请求落盘前的执行安全拒绝，不代表交易所已接单。两笔另有确定OCO拒绝并应急退出，
但旧拒绝码未保存；不能凭后来的行情补写拒绝根因。

## 最小设计及三角色边界

复用`trade_execution_context_v1`，新增可空`exit_order_type=limit`，None不序列化。
Host仅对新建context显式冻结limit；旧无字段维持market、原digest/client ID及佣金输出。
它只到Stage 2，不向Stage 1/VPA增输入；市场hash不变，任务/cohort绑定退出条件。
模型继续判断Setup/结构价/概率，程序不放宽SL、不改候选；佣金方程明确未覆盖限价退出未成交。

定量OCO仍由交易所按last触发；限价模式`slOrdPx=原有效SL`、`tpOrdPx=原有效TP`。
原SL/TP不偏移，不改为直接市价退出。触发后子单live/partial保留精确未退出预算，不能因
未成交再发保护或假平仓。自身成交完整时才计算已实现金额，未退出部分用原可平仓报价估值。

OCO确定拒绝时保留安全数字码，既有应急腿在limit模式下也使用原有效SL的reduce-only限价，
不偷偷升级市价；旧模式仍按旧协议。保护失败时选原SL价并非新的专业定价，只是尊重用户
既有退出价上限/下限。市价已越过该价时可长期挂住；价格比不是最大净亏损保证。
兜底本身确定拒绝则停止自动提交链并显式阻断，保留仓位与维护阻断，不每tick无界重发。
未知腿仍保留预约；晚到入场只允许保护新增未预约数量，不重用另一计划预算。
限价兜底GET/恢复核对冻结价格、type、side、reduceOnly和数量；矛盾保留预算并阻断。

独立审阅者三角色条件通过：PA价格/判断权限不变，VPA无新增权限；工程验收必须锁住旧字节、
触发未成/partial/unknown、双计划、维护排空和兜底拒绝不循环。不增加角色、依赖或数据库迁移。

## 验证计划

1. 先写失败回归：真实HTTP body、context/Host/Intent冻结、旧序列化、限价子单估值与数量预算。
2. 最小实现后验证多空、正常触发不转市价、保护拒绝数字码、限价兜底live/拒绝/unknown及重启。
3. 运行完整非live回归、严重静态检查及wheel资源验证；清理临时测试文件。
4. 本地实现、真实Demo验收、线上启用和收益验证分开报告。

备份：`docs/changelog/backups/20261026-limit-exits/source-before.zip`，10份原稿及SHA逐项核验。
回退须匹配新reader；新limit任务/Intent存在后不能让旧Worker接管。恢复时保留原账本与保护。

## 止损审查（本轮仅分析）

两份极速趋势教材仍允许非关键结构的紧缩止损；极速上涨还写先定TP再定stop。
它们仍可路由交付，与当前结构失效优先指导冲突。当前代码未发现为固定RR主动收紧结构SL；
网格仅向结构外侧取整，费用只改资格。需要清理残留原文并按当前Setup审查实际SL，不能从
少量亏损推出统一加ATR/比例或放宽所有止损。本轮不擅自改这些理论内容。

依据：[OKX OCO API](https://www.okx.com/docs-v5/en/#order-book-trading-algo-trading-post-place-algo-order)
的数值`slOrdPx/tpOrdPx`及`-1`市价语义；
[OKX订单说明](https://www.okx.com/en-us/help/how-do-i-trade-with-different-order-types)明确限价可能未成交。

## 本地实现及结果（未commit/未发布）

基线78ffafb，原4项ICT研究变更保持。未新增依赖、LLM角色/调用、候选字段、数据库迁移或历史回填。
上述协议已本地实现：唯一context冻结新退出类型；HTTP同时传数值限价；独立管理器核验原价，
应急GET核验type/px/side/reduceOnly/数量，子单身份和限价参数矛盾保预算并阻断。
安全数字拒绝码落入既有腿审计，流水复盘增加退出方式、保护失败处置路径和拒绝码；旧缺失保持未知。

原plan_view只接受终态退出子单，且外层也不接受ordinary emergency的partial，导致限价退出
挂着或部分成交时PnL提前未知。两层均按已有自身fills/数量核对支持live/partial，剩余仍估值；
未知/冲突/报价过期不计零，未成交不伪称closed，未修改资金费归属。没有平行账本或第二物化器。

| 检查 | 结果 |
| --- | --- |
| 最初8项新特性回归 | 原实现8红，最小实现后8绿 |
| 新增完整矩阵 | 20项通过；含Direct/Codex只交付Stage 2、多空HTTP、双计划子单恢复、unknown late delta、拒绝不循环、篡改恢复、partial估值 |
| 完整not-live回归 | Windows本地2193通过、7项live未执行，131.81秒；两条既有tvDatafeed弃用warning |
| 静态检查 | 全仓关键E9/F63/F7/F82、新测试F/I与diff检查通过 |
| 独立审阅 | 同一审阅者发现ordinary partial展示遗漏，先红复现后修正；最终未发现新的P1/P2，审阅者未运行测试 |
| wheel源码/安装 | 271成员逐字节一致，Headless/Dashboard/执行Worker导入通过，无Qt |

wheel SHA256=`c66e1754ebd4e90124acf86f5c36c47cf3720dc40be9c48e6402321a2f10d84a`。
原venv无wheel，直接no-isolation构建失败；改用已有uv离线隔离缓存构建成功，不添加产品依赖。
扩展测试初次有fixture锁/缺generation和嵌套JSON文字断言错误，修测试后完整回归通过；
ordinary partial展示遗漏是实际相邻缺陷，未将两者混为同类产品失败。JUnit/构建日志/wheel及备份保留，
临时测试、安装和构建目录完成后清理。此处没有新Linux CI或真实交易所成功证据。

限制：原限价入场成交后才轮询建立OCO的观察窗口依然存在；双边限价仅改变退出委托方式，
不等于入场原生附带原子保护，也不修复历史未知拒绝根因。应急限价确定拒绝会阻断新入场并
保留实际仓位，需要操作方处理；不能无限自动重发或用TTL抹掉风险。旧读者不能接管新limit数据。
线上仍27c1b23，本地通过不表示上线启用，更不表示净收益改善。
