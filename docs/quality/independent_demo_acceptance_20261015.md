# 独立Demo计划与Paper历史冻结验收

## 范围

用户先批准设计、计划及本地实施，2026-10-16后续明确授权实际Demo双计划验证、启用、commit和VPS发布。
固定代码`4dcb3b9`已发布，BTC独立数量模式已通过真实Demo canary并启用，详细证据见文末。
此前本地阶段没有交易所请求或模型调用；真实canary不证明分析正确或交易盈利。

权威规格：[设计](../superpowers/specs/2026-10-15-independent-demo-plans-design.md)、
[计划](../superpowers/plans/2026-10-15-independent-demo-plans.md)。后续相关任务须先读本报告。

## 行为与兼容

- 正式Headless只构造Demo dispatcher，不创建Paper写引擎；未选择Demo时只分析、不回退Paper。
  显式注入Paper的隔离工具仍保留原接口。历史查询沿用mode=ro，不建立新表/列或回填历史。
- 新正式Intent显式v2，独立于K线水位；永久task/epoch幂等保留。新No Trade只结束自己，
  新限价不撤旧单；各计划原价格、金额、绝对TTL不变，成交后仅撤自己的未成交余量。
- 同向可同时存在；反向按已登记顺序核对预约、挂单、未知请求及持仓，不能只检查净仓。
  旧v1活动计划保持原管理，同合约阻止v2混入；新独立模式拒绝未提交的旧v1计划。
- 数量仅按该计划入场累计成交减实际退出订单累计成交计算。退出订单按真实ordId去重，
  effective不是成交，必须核对子单。累计成交不倒退；矛盾保持unknown，不clamp成正常。
- 每个OCO精确sz、reduceOnly/net，保留原stop/TP1及last/market触发，不发送closeFraction。
  OCO、触发子单及emergency同占一份退出预算，预约与预算检查处于同一SQLite写事务。
  未知请求不重发；迟到成交只分配新增未覆盖份额，不重复占用未知旧腿的份额。
- 合并净仓只核对合计。新增入场要求完整对账；已有确定成交的保护允许其他计划有有限数量
  的待核退出，账户净仓须在已知剩余量与潜在退出量给出的边界内。事实矛盾仍阻止保护新增。
  不能让A未知永久阻塞B已成交量保护，也不能按净仓猜测或全仓补救。
- 未反映到余额快照的资金承诺逐计划保留，入场成交首次观察时间与快照开始时间用于保守释放；
  同一个净仓不能为两份计划分别免除预约。该标记只存在执行账本response/metadata中。
- 面板主视图是OKX Demo；Paper v3/v4在“历史模拟记录”下独立选择，pending/open是冻结时状态，
  不显示当前风险或浮盈亏。关闭Demo也不把Paper重新显示成当前执行。Demo计划列出退出及剩余量，
  未核清时未知；账户净仓、均价及账单收益只按账户显示一次，不创造逐计划PnL。

v1 Intent摘要、客户ID、旧cutover默认摘要不变；新许可默认空时不进入摘要。
新`independent_accepted_symbols`非空必须绑定新cutover证书，旧BTC全仓OCO许可不能启用新模式。
SQLite user_version保持1，原表/列未变；新增事实沿用legs.params/response、children及metadata。

## 审查及失败到通过证据

2026-10-16中断后复核：此前将本地验收称为“完整完成”不够严谨。补查发现历史Paper零写入
只验证了构造哨兵，现已补真实v3/v4 pending/open账本，经行情轮询、暂停发布、到期、
恢复及重启两轮后，核对全表逻辑dump与数据库/WAL文件哈希均不变。
同一独立审阅者还复现：旧live保护在本轮GET查无结果时沿用缓存保护并允许新单。
现对非终态退出腿持久化`observation_unknown`，保留全部预算、阻止新入场且不重发退出。
已补先失败后通过测试，覆盖live→查无结果→重启→核验恢复；原始报告不作为最终版本证据。

唯一独立审阅者按PA、VPA和量化架构检查；PA/VPA权限、Router、Prompt/Skills及分析Schema未变。
审阅报告的两项确定问题已补直接回归并修复：

1. A未知OCO导致B无法建立保护：复现只有一个OCO请求；分开新增入场与既有保护核对后，
   两计划各自拥有独立预算，持续查无结果不产生重复POST或emergency。
2. 默认非Demo配置显示当前Paper：复现缺少schema2/frozen；正式paper=None路径始终给出
   不可用Demo及冻结历史，显式隔离Paper工具不受影响。

实施自检还复现并修复：首次OCO参数不匹配却显示protected；未知旧退出挡住迟到成交增量；
未知emergency挡住新增未预约份额。均以独立测试先失败再通过，保留退出预算总量约束。
其余覆盖同bar两任务、永久幂等、A退出/B存续、独立TTL、反向预约、部分/迟到成交、
未知OCO重启查无结果、子单未知、快照滞后、资金重复使用、旧v1隔离和Paper构造失败哨兵。

## 验证记录

2026-10-15版本完整Headless回归：1953 passed、7 deselected，99.45秒；仅有原第三方tvDatafeed的
两条DeprecationWarning。范围为unit/integration/property的not live；Qt专属排除见下方。
当时静态E9/F63/F7/F82及git diff --check通过。wheel构建、两新增执行模块源码字节一致、
Prompt/Runtime资源及安装包导入通过，Headless无Qt，新独立许可仍空；uv.lock未改。
真实浏览器用虚构数据验证OKX当前视图、Paper v4/v3历史分组、冻结说明及计划退出/剩余数量；
预览无写入能力，未访问OKX账户或用户原网页。既有ICT四文件与此前备份逐字节一致。

```text
.venv/Scripts/python.exe -B -m pytest tests/unit tests/integration tests/property
  -m 'not live' -q --tb=short -o addopts= -p no:cacheprovider
  --basetemp=.pytest-independent-final
  --ignore=tests/unit/test_chart_skip_redraw.py
  --ignore=tests/unit/test_chart_widget_no_lines_when_not_trading.py
  --ignore=tests/unit/test_chart_fit_view.py
  --ignore=tests/unit/test_debug_widget_masks_key.py
  --ignore=tests/unit/test_decision_panel.py
  --ignore=tests/unit/test_future_trend_panel.py
  --ignore=tests/unit/test_gui_worker_lifecycle.py
  --ignore=tests/unit/test_order_opportunity.py
  --ignore=tests/unit/test_overlay_lines.py
  --ignore=tests/unit/test_support_resistance_chart.py
  --ignore=tests/unit/test_thread_lifecycle.py
  --ignore=tests/unit/test_token_indicator_thresholds.py
  --ignore=tests/integration/test_next_bar_prediction.py
  --ignore=tests/integration/test_switch_mid_analysis.py
  --ignore=tests/property/test_next_bar_prediction_perf.py
.venv/Scripts/ruff.exe check pa_agent tools tests --select E9,F63,F7,F82 --no-cache
git diff --check
```

Windows本地回环测试需要沙箱网络许可；受限运行的WinError10013属于环境失败，保留记录，
取得本地回环许可后重跑，不访问真实交易所。

2026-10-16最终复验：1955 passed、7 deselected、原第三方两条warning，124.32秒。
命令与上述相同，仅basetemp改为`.pytest-independent-full-recheck`。相关54项聚焦通过；
同一独立审阅者另跑3项针对性用例均通过，确认PA/VPA/量化架构局部复核通过，无剩余确定阻断。
修复后的wheel重新构建、源码/资源一致性及安装目录Headless导入复验通过，无Qt，新模式仍默认禁入。
静态检查、diff空白检查通过；相关fixture/build目录已清理，日志独立存档到原备份旁的
`validation-recheck-20261016.zip`，不覆盖旧测试记录。

审查取舍：沿用现有隔离开发分支并精确保留已有改动；v1/v2复用唯一的限价提交函数，
由原v1回归保护兼容。数量对象额外区分`inconsistent`与待核`uncertain`，前者是事实矛盾，
后者可能只是有界未知退出；只有后者允许为未预约的确定增量建立保护。此边界错误会导致
过量退出或遗漏保护，故由事务预算、数量区间及故障回归共同验证，不用于新增入场授权。

## 启用及回退边界

新定量模式默认禁入，mock不证明真实交易所行为。后续授权后须暂停排空Host/Worker、备份，
确认无旧v1活动计划；用最小Demo双计划canary验证精确sz、原止损/TP1、A退出不影响B、
未知读取及重启恢复，再新增品种许可和cutover证书。部分/迟到成交已离线覆盖；最小数量canary
没有观察到真实部分成交，该真实路径单独保持未验收，不以离线测试或本次启用冒充通过。
REST轮询仍存在成交至保护确认的时间窗。
已有v2活动计划时不得回退仅识别v1的旧Worker；须保留兼容管理进程直至退出核清。

四份既有ICT文件独立保留，不纳入本次变更。清理仅限本轮临时测试、打包和预览产物，
不删除旧账本、认证、原始分析、历史结果或隔离研究实现。

## 2026-10-16 真实Demo双计划及固定发布

- 代码提交`4dcb3b93b8426029dc2270472259e50b5bcb0d1a`已推送私有`vincentlii/paagent_codex`的main，
  四份既有ICT改动没有纳入。提交前64项执行/历史/面板聚焦通过，沿用最终1955项离线全量证据。
- VPS原Git HEAD为`3e1b5c1`，四服务正常，Host队列及恢复阻塞空，Demo完整对账且无敞口。
  控制从running49暂停至50，排空后停四服务。Host备份`pa-agent-20261015T162429Z`、
  Demo备份`demo-1792081486282`均通过manifest及quick_check/逻辑一致性；旧源码另行归档。
- 固定增量包安装完成，316个安装成员与源码逐字节一致；四库、17741个原文件及47个非项目依赖
  由独立进程复核完全相同，无迁移、历史回填或认证替换。首次依赖比对在导入构建器的进程中失败，
  已定位为setuptools向该进程加入10个vendor发行版并遮蔽packaging/wheel版本；实际安装依赖未变。
  旧失败日志保留，未通过忽略差异来放行。增量包最初HEAD引用名不匹配时停在安装前，修正为包内实际HEAD。
- Linux wheel SHA256为`493e3729ef6f038fa973e0bedfc9edc39b015670b6ea6c2faf632eeb1d107ae9`。
  13项Runtime资产及POSIX权限审计通过，asset仍为`401d952ea8cc91cd15ef785b7897234755a1711a91d96f861ed2689757ea6247`。

实测运行在唯一的独立canary服务、专属账本和永久客户ID中；正式执行Worker停机，凭据仍由systemd
LoadCredential注入专用执行用户，不传回本地。合成计划不伪装成PA/VPA模型建议，不写正式分析或Paper。
每笔BTC `0.01`张，测试目标总价值`8.213252 USDT`；两笔合计`0.02`张。测试价在提交前固定，未改价：

| 合成计划 | entry | stop | TP1 |
| --- | --- | --- | --- |
| A | 82132.52 | 82102.52 | 82162.52 |
| B | 82132.52 | 81132.52 | 83132.52 |

1. 两笔实际限价成交，并分别查到live定量OCO；校验原价、sz、reduceOnly/net、last触发及市价退出，
   不发送closeFraction。净仓`0.02`，不是两份全仓保护。
2. 只注入B的读取故障，原退出预算不释放、保护变unknown；重启沿用同账本和客户ID，恢复读取后确认
   保护，无重复退出腿或额外平仓请求。清理前禁新入场，未提交意图先收尾，避免清理循环开仓。
3. A的OCO真实effective，其实际退出子单`4012420659611275264`累计成交`0.01`；A剩余0，
   净仓仅余B的`0.01`。B原algo `4012420633048158208`保持live，原stop/TP1及数量等字段不变。
   此证据不将触发类型猜成TP或SL，也不以手动平仓冒充A的OCO触发。
4. B清理先核对原保护取消终态及零保留预算，再用持久化reduce-only退出自身剩余量。最终完整覆盖下
   普通单0、algo0、仓位0，独立账本活跃计划0，canary服务success后停止。真实部分成交未观察，不宣称已实测。

真实canary通过后才将BTC加入`independent_accepted_symbols`；ETH/XAU没有新增执行许可。
新代次`okx-demo-independent-20261016-1792081957579`及证书已建立。正式Worker先禁入场只读对账，
签证书后协调四服务加载同一版本，再恢复running51；旧任务不重新绑定，不补旧订单。
原500 USDT/3h、3倍net/逐仓、Profile/Fast/模型/Run Once保持；未额外触发单次模型分析。

恢复时四服务active/running、Result=success、NRestarts=0；Demo ready/完整覆盖、无未决敞口。
面板status/trading/health及页面HTTP200，未认证status401；默认OKX、Paper历史冻结标记通过。
canary是真实模拟账户交易，因此其成交/费用可能出现在账户口径的OKX账单，不伪造成逐计划策略PnL。
收据、原失败日志及脚本归档在忽略的`docs/changelog/backups/20261016-independent-release/`；
VPS证据与回退源码在`/var/backups/pa-agent/releases/independent-20261016/`，独立canary账本保留。
这不是自然策略首单、持续运行收益或真实部分成交验收；已有v2活动计划不得由旧v1 Worker接管。
