# OKX Demo 执行与交易概览验收

## 范围与当前结论

用户已批准2026-10-15设计及实施计划，主 Agent 实施、同一名只读协作者审阅。代码只提供
OKX USDT永续模拟执行，不含实盘选项。本地实现与离线验收已完成，代码558c255已固定发布VPS；发布证据见文末。
发布保持原Paper运行，Demo默认禁用；未读取模拟盘凭据或访问交易所私有账户、未额外调用模型或生成模拟订单。
本地测试与部署资产不等于已接通模拟账户或已验证交易所保护能力。

权威设计：[设计](../superpowers/specs/2026-10-15-okx-demo-execution-design.md)、
[计划](../superpowers/plans/2026-10-15-okx-demo-execution.md)。修改本接入须先阅读本文件与
`CONTEXT.md`。不修改已有四份ICT文件、原冻结行情、Prompt/Skill、Router/Validator或研究账户。

## 实现边界

| 层 | 实际行为 |
| --- | --- |
| Host | 保留正式分析成功、record_persisted、audit_ok入口，复用原正式资格和时效。新Host task绑定执行目标/代次；旧任务与已存在的任务封套不重新绑定。交付原task.snapshot_hash与record digest，不冒称Worker能独立验证原库审计。 |
| 确定性计划 | 默认500 USDT订单名义金额（后续用户授权面板可配），Decimal按冻结目标金额及目标lotSz向下取整；3倍只影响保证金。原entry/stop/TP1精确保留，不执行TP2。数量过小、目标tick不符、保证金不足则拒绝，不改金额或价格。 |
| 双身份 | Binance分析身份/冻结spec与OKX账户可交易合约各自保留。首版只允许BTC普通LIMIT；ETH/XAU有显式映射，默认不开。市价/突破明确拒绝，不转单。新鲜目标报价只检查已穿原stop的执行几何，不创造PA距离Gate。 |
| 私有客户端 | 固定HTTPS Global域名、强制模拟标记、无重定向/TLS绕过或POST隐式重试。签名覆盖实际query/body。50004及内部服务不确定码、HTTP/JSON/网络不确定均保持unknown；原响应与认证细节不落账。 |
| 文件交付 | Host写不可变输入，Worker只读；回执/投影由Worker写，Host读。单文件256KiB、输入64项，有界失败不丢未确认；共享文件显式0640再rename。 |
| 账本 | 仅新执行库，拒绝误传外来数据库；事务登记、永久客户ID、原请求语义、成交/账单复合去重、决策水位和连续分页游标。未知提交保留预约，查询不到一次不证明未下单。 |
| 执行状态 | 每品种一笔计划/持仓。部分成交核对原价只减仓close-all OCO，再撤余量；撤单ACK不等终态。保护失败撤余量并尽力只减仓平仓，失败/未知仍阻塞，不称原止损已触发。 |
| 退出恢复 | OCO effective不是平仓成交；持久化ordIdList/单个退出ID，逐项查普通子订单终态。父单确认终态后，须有后续持仓快照及全部退出/残留保护对账才closed。OCO消耗后迟到成交使用新持久generation，未知请求只查不重发。 |
| 切换 | 只在Host暂停且队列空、Worker无活动交易/挂单/仓位、数据新鲜且无恢复阻塞时准备证书。新代次不补发历史结果，恢复只处理原冻结匹配代次；旧Paper活跃单继续观察，不新增正式Paper单。 |
| 展示 | Demo启用后schema2主视图为OKX账户；旧Paper v3/v4显式选择，失联、旧schema或账单缺口不回退。原分析次数/复盘仍来自Host records；模拟成交不写入analysis_outcomes或counterfactual_outcomes。 |

## 数据口径与故障边界

资金使用交易所USDT权益/可用余额与逐仓margin。入场预约与已成交仓位分开；账户快照
已反映的挂单冻结不再次扣款，尚未反映的unknown保留最大潜在占用。结构止损损失是计划
统计，不是保证成交或保证损失；费用和实际成交金额不属于500 USDT目标的加仓授权。

收益只由去重后的账户账单计算：type=2的pnl+fee、type=8资金费，type=1资金流另列；
不再次叠加fill fee或已含费用的position realizedPnl。未知账单类型/币种/覆盖缺口保持unknown。
统计范围从本执行账本建立时点开始；不是账户开户以来收益，也不是PA/VPA因果贡献。
账单/成交游标持久化，不能追到上次锚点时保留reconciliation_gap，不伪造连续历史。

独立Worker采用有超时REST轮询，目标活动2秒、空闲10秒，错误退避；实际轮询还包括接口
延迟，不承诺2秒SLA或原子bracket。成交至发现/保护之间仍存在裸仓窗口，断网期间服务器
保护仅在先前确实创建成功后有效。账户必须专用，未知外部敞口不能自动接管。

## 新库、切换及回退

新SQLite默认在派生execution/state目录，VPS显式使用独立根，不迁移、不回填、不删除原三库。
新库创建失败无入场；请求后写盘失败依靠已落盘的客户ID恢复查询，不按回执再补发。
升级前停止Demo Worker，使用专属用户的`--backup`；备份全程取得同一worker.lock，运行中
拒绝，以保证SQLite、消费回执及投影一致。manifest不含credential；配置/切换证书与Host输入
目录另行保留，不能单独恢复回执或用旧状态覆盖新执行结果。恢复后先禁止新入场并对账。

代码回退不能把旧Paper reader用于Demo状态。仍有Demo活动单时须保留兼容的保护/对账进程，
先关闭新入场；不得因Host回退/停服就停止保护或让Paper历史冒充模拟盘。独立执行状态和凭据
不进入Codex、Dashboard、旧三库备份权限。

## 离线验证与三角色复核

测试覆盖原正式资格、500单位/取整/精度、HTTP签名/错误、双Worker、DB失败、重复交接、
同批旧LIMIT+新NoTrade、乱序决策、部分成交、撤单ACK、OCO消耗后迟到成交、触发子订单、
Pause、重启unknown、连续游标、账单去重/资金流、备份锁、schema2及只读DOM行为。
整链含真实签名客户端配合httpx.MockTransport；这是内存交易所，无外网、无模型、无真实撮合。

PA复核：原结构、价格与正式资格不变，程序未添加Setup/方向解释或改结构失效价。
VPA复核：仍独立Evidence Analyst，没有新增执行入口、概率/Gate或强制影响。
架构复核：正式交接、幂等、因果查询及报表边界独立，未知不转成功。唯一协作者发现的问题
逐项形成回归；独立审阅不冒充三名外部专家认证。

Linux用户与目录隔离目前以资产检查为主，仍需VPS实际runuser/文件权限验收。
后续私有GET预检、BTC最小合法数量受控生命周期、部分成交/OCO实测、安装与自然调度须分别
记录；未见到的真实路径不能用mock标成交易所通过。默认配置enabled/entry_enabled均false、
accepted_symbols为空；任何真实启用必须先完成对应验收。

## 最终验证结果

2026-10-15最终Headless非live回归：**1842 passed / 7 deselected，94.38s**。包括68项新增
接入测试及原PA、VPA off/Shadow、Joint、A/A/B/B、HTF、订单数学、records/audit、Headless与
只读Dashboard回归；独立审阅者的定向28项是重叠复核，不重复加总。唯一审阅者最后确认
备份mount修正、保护失败blocked、停用仍管理活动单、成交/费用页面后，无剩余确定阻断。
两条既有tvDatafeed正则转义DeprecationWarning保留，不属于本次改动。

运行方式为项目Python执行`pytest tests/unit tests/integration tests/property -m "not live"
-q --tb=short -o addopts= -p no:cacheprovider`，使用独立短basetemp。沿用既有Headless范围，
排除unit的`test_chart_skip_redraw.py`、`test_chart_widget_no_lines_when_not_trading.py`、
`test_chart_fit_view.py`、`test_debug_widget_masks_key.py`、`test_decision_panel.py`、
`test_future_trend_panel.py`、`test_gui_worker_lifecycle.py`、`test_order_opportunity.py`、
`test_overlay_lines.py`、`test_support_resistance_chart.py`、`test_thread_lifecycle.py`、
`test_token_indicator_thresholds.py`，integration的`test_next_bar_prediction.py`、
`test_switch_mid_analysis.py`及property的`test_next_bar_prediction_perf.py`。未宣称Qt GUI/E2E
或全平台CI通过。完整日志保存在本轮忽略的开发验收目录，主要结论记录于本文件。

初轮受限环境在本机SDK/HTTP通信处阻塞，随后在允许本机回环的环境运行；较长临时路径又
触发Windows路径长度错误（121项失败），缩短basetemp后原样运行通过，没有修改Runtime资产
或放宽产品校验来绕过环境错误。原Dashboard独立52项也通过。静态关键Ruff、diff检查及
Git Bash的安装脚本`-n`语法解析通过；后者只解析，没有执行安装或访问VPS。

最终LF源码构建318文件，wheel内314包成员逐字节对比源码通过。离线隔离安装后验证CLI
`--check`默认disabled且不创建执行库，Headless未导入Qt；13项Runtime资源审计通过，asset
digest仍为`f2957d56a69acef229425b46c93df4531a9ea177c4bdb93e57042492193643f3`。
最终wheel SHA256为`a6cb0833ca32a52a69ccb3c3741a0f9b5c2d1d02575f1943b37ce67c0a0e8b81`。
`uv lock --check --offline`通过，无依赖变化；未替换原开发环境安装。

浏览器查看本地明确标注“离线演示”的假数据页面，核对默认OKX、权益/费用/资金流口径、
中文保护状态、实际成交表、Paper历史及数据过期提示；DOM测试覆盖刷新保持所选历史版本和
旧schema不回退。截图保留在忽略的验收目录，预览服务和临时页已关闭，不作为真实账户截图。
测试DB、源码 staging、隔离安装和假数据清理，保留日志、摘要、截图和wheel证据。

四个既有ICT文件与本轮开始前备份逐字节一致，未覆盖。没有commit、push、VPS发布、私有GET
预检或模拟订单验收；下一步须直接安全配置模拟凭据，再单独完成只读及受控生命周期验证。


## 2026-10-15 面板金额与有效期增补

用户明确要求两项面板配置，交易总览新增“模拟执行设置”；默认500 USDT / 3h。
金额是订单总价值，不是保证金/止损风险，仍固定杠杆3，不按置信度或止损距离调整。
金额须有限且为正，最多8位小数、不超过1e9（表示/输入上界，不是额外风险授权）；
TTL为1–24整数小时。原价、合约向下取整、最小数量、最大数量和余额保护继续适用。

控制协议为Runtime control v5，兼容读取v1–v4（含Fast）并只在写入时升级；保留模型Profile
原指纹，所有暂停/Backend/Profile/Fast/Run Once操作均保留Demo设置与其actor/time。
保存采用CAS，Host idle apply并区分desired/applied；旧Host无能力声明时API拒绝保存，
鉴权、Origin/header、正文白名单和数值约束复用原控制路径。浏览/编辑没有自动POST。

设置及代次只进入新接收Host envelope/Intent，不进入AnalysisProfile、Codex Job、市场hash、
cohort或cutover证书。完成回调、重复任务、恢复均使用原冻结值；旧无参数任务保留原500和
原expiry。Intent摘要只在新optional字段为None时剔除该字段，旧合法null字段不全局排除，
旧摘要/客户ID保持。新Worker可读旧数据，旧StrictModel Worker会拒绝新增policy字段，
必须协调Host/Worker整包停启，不能声称双向兼容或混版本热升级；无DB结构迁移/回填。

新限价到期=源K1开盘+(1+冻结TTL)小时，即K1收盘+TTL；交付延迟/重启不重置，新鲜度
仍到下一收盘。Worker按绝对时点撤未成交余量，ACK后等待终态，已成交部分仍保护。
页面显示每笔自身expires_at_ms，当前面板值不冒充历史订单设置。

单一独立审阅者已完成PA/VPA/量化架构复核，无新确定阻断；59项独立相关离线测试通过。
新增直接测试覆盖控制持久化/其他控制保留/CAS拒绝、非法值不写盘、新旧任务不同金额与TTL、
冻结codec、旧Intent摘要与客户ID、Host idle/pending、HTTP鉴权/Origin/旧Host拒绝、显式DOM
保存、Worker重启后未到期不撤/到期撤余量及cancel_pending。完整回归结果见下方。
本轮未提交、未发布、未连接账户/读取凭据/调用模型或真实下单。


参数增补最终验证：Headless unit/integration/property 的全部 `not live` 测试1856 passed / 7 deselected，
耗时94.10s；Qt/GUI用例按既有Headless范围排除，未执行live。第三方tvDatafeed有两条既有
DeprecationWarning，不影响通过。关键静态检查E9/F63/F7/F82、diff check通过。
wheel采用LF源码构建，319份源文件/315个package成员逐字节验证通过；安装后Headless不导入Qt、
Demo CLI离线默认禁用。wheel SHA256为
`2a21324841349a445e92a1f40be1e3a812d9de018030d7f6087ef44c193bdbff`。
Runtime 13份资源及digest保持
`f2957d56a69acef229425b46c93df4531a9ea177c4bdb93e57042492193643f3`。
四份既有ICT文件与备份逐字节一致；本轮临时pytest和安装目录已清理，保留有用验证证据。
尚未commit/部署，VPS仍为先前版本；以上不代表模拟账户已连接或真实撤单时延已验收。


## 2026-10-15 固定提交发布VPS

用户授权commit及VPS发布。49个代码/文档/测试文件提交为
`558c25560e024f44f44f6e1c4df20a5a6772c06a`并推送私有main；4份既有ICT文件仍留在本地，
与此前备份字节相同，未纳入提交。沿用用户此前指定的SSH密钥，仅操作PA Agent，
实际VPS登录账户为ubuntu（root登录被拒，未改SSH或授权配置）。唯一审阅者再次通过
PA/VPA/量化架构发布方案，发布定义为代码能力及面板设置上线，不是已接通OKX。

发布前VPS为e9f40d1、checkout干净、原三服务正常、Demo Worker inactive。控制running
41→paused 42，确认Host/Codex队列、inbox及恢复阻塞为空后停三服务。备份
`/var/backups/pa-agent/pa-agent-20261014T174228Z`共14191文件，manifest及3库quick_check通过。
另存原控制、3库逻辑摘要、14198个原记录/配置/认证/冻结交付文件摘要、47项依赖、旧源码及unit。

增量Git bundle经摘要核验后快进固定558c255。VPS无build前端模块，首次构建明确报缺失后，
改用pyproject已指定且已安装的setuptools PEP517后端，离线构建新旧wheel，没有新增依赖或改产品代码。
新wheel SHA256：`15d0855486393c475e0132aacef518f8cf3e3721f7c0db180f35c98f9dfa20b0`。
315个安装成员与固定源码LF原文逐字节相同；13份Runtime资源digest仍为
`f2957d56a69acef229425b46c93df4531a9ea177c4bdb93e57042492193643f3`。
安装前后3库逻辑、14198个原文件及47项非项目依赖完全一致。

只执行安装器新增的用户/组/目录/unit步骤，不执行conda环境更新或覆盖原配置。
新增Demo配置原本不存在，安装默认enabled=false、entry_enabled=false、target=paper；
VPS三份OKX credential源文件均不存在。无真实执行DB、不启动Demo Worker，不写交易所配置。
真实Unix用户验证Host/Worker双向spool权限和0640原子文件、Codex/Dashboard不可读Demo
state/config/输入；原Runtime POSIX审计通过。systemd-analyze验证新unit，使用同等backup
namespace完成隔离DB锁/WAL与备份检查；安装包旧Intent摘要、可配置金额、隔离HTTP的202/409
及pending验证通过，合计9项Linux隔离检查。所有隔离状态/哨兵文件已清理。

原Host/Codex/Dashboard重新启动后先验证paused/idle，再恢复running generation43，控制写入
升级v5。原Profile generation4、gpt-6-luna/xhigh、Fast、aggressive、三品种、并发3及Run Once5
保持。三服务active/running、Result=success、NRestarts=0、ExecMainStatus=0；XAU/BTC/ETH
均connected，恢复阻塞为空。面板health/status/trading=200、未认证status=401；两项设置
supported=true/pending=false、有效500 USDT/3h，交易页HTML含两输入，原Paper schema1仍为主。
新设置不会应用到当前Paper，只有后续显式完成Demo切换的新任务才使用。

Linux生产环境未安装pytest，证据是安装/权限/namespace/隔离协议及实际服务验收，
不能替代本地1856项Headless回归或真实模拟订单生命周期验收。没有额外Run Once或模型调用；
恢复自然调度后后续分析仍按原计划运行。Demo待安全配置凭据、只读预检及单独受控订单验收，
不能把代码发布冒充模拟盘已接通，也不把MockTransport当成交易所支持证据。

新旧wheel、源码归档、原control及发布凭证保留在
`/var/backups/pa-agent/release-558c255`；临时源码staging及上传bundle已清理，VPS checkout干净。
源码运行身份固定558c255，后续文档收据提交与该运行身份分别记录。
