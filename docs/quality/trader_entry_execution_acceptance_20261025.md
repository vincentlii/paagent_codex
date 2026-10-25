# 入场选择第一批修复与验收（2026-10-25—26）

后续状态：第一批与PH-26/27修复已固定为27c1b23并安装VPS，真实费率和最小Demo保护链通过，
见[工程交接发布回执](engineering_handoff_release_20261026.md)。下文原“本轮无commit/VPS”仅指
第一批本地验收当时；不能据此覆盖后续发布，也不能把发布倒写为模型质量或收益已验证。

## 范围和完成标准

用户授权按[交易员提案](trader_entry_selection_proposal_20261025.md)先实施第一批：原文去冲突、
真实TTL冻结交付、佣金事实及净数学、现有候选比较的判别力。设计见
[ADR-0028](../architecture/ADR-0028-frozen-trade-execution-context.md)。本轮无commit、VPS操作、
真实模型调用、交易所查询或测试订单；原500 USDT、面板TTL/RR及独立挂单偏好没有调整。

验收要求：模型原候选/结构SL/p不被费用改写，Stage 1/VPA无新执行输入，原联合链仍3调用；
TTL不从交付续期；未知费用不计零；新费用检查不影响旧协议及活动订单保护；可重放、幂等，
不混淆独立市场样本与执行条件。保留原文知识及所有仍有调用者的兼容代码，不叠加新角色。

## 实际修改及三角色判断

- PA：二元决策原位按结构SL先于目标定价，删除固定1–2跳；下跌通道删除限价单的绝对优越性。
  原字段明确正常回测/强反向运动、触达/条件胜率、已成立尝试/仍依赖未来确认。倾向规则不再
  泛化等待下一棒，也不再授权取消/重定价旧单。微型通道形成期原入场边界保留。
- VPA：Joint Skill复用现有位置证据及候选比较，允许中性，不要求每个位置找确认。未改VPA
  Schema、原事实目录或调用次数，未新建ICT/角色。
- 量化/架构：有版本冻结上下文，只到Stage 2；账户只读费率带缓存、失败隔离和显式未覆盖成本。
  唯一materializer后使用原execution_disposition记录毛/佣金后检查；Intent/Worker再次绑定
  及验证，新费率不可用/上升拒新不撤旧；未提交原因沿用现有流水并增加中文说明。

实现前三角色设计审查及一名独立审阅者的实现复核均通过，未发现P1/P2。审阅者提出的带marker
端到端、费率故障下旧单保护和重启幂等均已加入主验收。费率观察时间与原始回包审计范围限制
见ADR，不增配置/角色掩盖边界。主复查将执行上下文从compact市场context移到Host任务身份，
确保canonical市场hash不因账户费率/TTL变成新样本。
最后还补齐metadata/显式arg两种冻结入口的cohort一致性，以及fee_scope仅私有投影保留、
面板/模型移除；均先红后绿并由同一审阅者复核，无新增权限或业务分支。

## 备份和原文清理

修改前本地备份在 `docs/changelog/backups/20261025-trade-entry/source-before.zip`，
641成员逐项SHA与ZIP完整性核验；ZIP SHA256：
`83cbb38f9a114a9df401a093dfd421e6f72ca04924861201f9cc97e36d205a30`。
`manifest.json`绑定branch/base HEAD及成员摘要。该641成员归档不含中文路径教材；两份教材及
倾向/Skill原稿另在 `docs/changelog/backups/20261025-trade-entry-prompt/`，四文件逐项SHA核验。
恢复本轮涉及源码/教材时应按两份manifest选择原文件，不覆盖本轮之外的既有改动。

知识章节ID/顺序不变。替换原句与重复指导，保留仍有调用者的历史任务、Intent和Paper reader。
既有ICT可行性文档及导航改动保持；未把它们纳入ICT实现或发布授权。未删除原始记录、配置、
凭据或历史订单。原稿、JUnit及安装/性能凭证保留在受忽略备份目录，临时测试/安装/构建目录清理。

## 验证

聚焦验收已覆盖：Direct/Codex完整联合链、费用前后结果及原概率保持、双向/返佣/极端费用、
真实tick之后再算佣金、上下文篡改/过期/错品种/未来时间、TTL/cohort稳定、市场身份隔离、
Host→Intent→Worker→流水、重复tick/重启不重复入场、缺费率及涨费拒新、已有成交保护继续、
旧Intent形状/摘要、账户scope匹配、feeGroup/旧USDT字段及缓存/失败退避。

| 验证 | 结果 |
| --- | --- |
| 最终完整非live回归 | 2154项全部通过，7项live未执行，154.67秒；Windows本地 |
| 较早完整回归（原失败保留） | 2153通过，1项本机HTTP连接中止；154.61秒 |
| 上述HTTP失败及相邻面板设置/鉴权/CAS测试单独复核 | 66项通过，26.83秒；无业务修改 |
| 最后任务身份/factory/recovery/执行设置复核 | 73项通过，42.01秒 |
| 最后私有投影及Host/Worker/流水复核 | 27项通过，3.77秒 |
| 独立审阅者最终冻结context/费率/完整marker链重跑 | 33项通过；主验收还覆盖8项原文/Skill去冲突 |
| 严重静态错误、变动新文件F/I、diff | 均通过；uv.lock/pyproject.toml未改 |
| wheel构建及本地离线安装 | 271个代码/资源成员与源码、安装目录逐字节一致，Headless/Dashboard/两Worker导入通过，无Qt |
| 原稿及教材章节 | 641成员ZIP完整性和SHA通过，4份独立原稿SHA通过；教材标题/章节顺序未改 |

最终完整回归2154项全部通过。较早完整回归的唯一失败是原有面板本机HTTP连接被Windows中止
（WinError10053），没有业务断言失败；保留原失败日志，不删除或伪装首轮全绿。独立66项
复核及最终全量均通过，未修改该面板设置链。另有两条既有tvDatafeed转义弃用warning。
测试使用隔离临时目录，关闭本机额外pytest插件，只加载仓库需要的Hypothesis/anyio；此前
沙箱阻止本地回环、临时目录及一次重复加载插件属于环境启动失败，不当业务验收成功。

命令：`python -B -m pytest -p _hypothesis_pytestplugin -p anyio.pytest_plugin -m 'not live' -q -o addopts= -p no:cacheprovider --tb=short`，
设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、`PYTHONUTF8=1`，临时目录及JUnit输出位于本地隔离路径。
构建沿用setuptools，环境缺独立wheel分发包，使用已安装setuptools的wheel构建能力
（`build --wheel --no-isolation --skip-dependency-check`）；未新增依赖。随后uv从本地wheel无依赖
安装到隔离目录并逐项验证，验证期间不从源码目录导入。

最终wheel SHA256：`c5431fa030190af5b6bf8356900c659e394ee203d70dacf4892493d83d89cd33`。
本机2000次佣金后/置信度执行检查中位0.0326毫秒、p95 0.0359毫秒。仅是确定性计算，不代表
全链耗时或Token；阶段输入新增小块冻结事实，真实Token增量未调用模型测量。网络增量是缓存
失效时每合约一次只读费率请求，成功5分钟/失败30秒缓存，不在每次模型分析中发请求。

原归档18份限价候选另做纯数学情景复算：统一**假设**maker=-0.0002、taker=-0.0005时，
18份毛方程通过、10份佣金后不通过。不是历史真实费率、不是新模型输出，没有成交/PnL回放，
原记录未改；产物明确标为`hypothetical_commission_math_only`。它说明费用足以改变资格，
不能说明Gate提高收益，更不能据此强迫模型上调p。BTC/ETH/XAU分别8/7/3份，非18个独立行情。

## 未验证边界

没有新真实模型对照或自然前向成交样本，不能证明更会下单、更赚钱或更少wait。
费用Gate可能合理减少原本薄优势的交易；模型仍使用未校准p。滑点/资金费未知，佣金估算不等于
总成本。Demo与实盘路径差异仍见[归因报告](demo_plan_performance_attribution_20261024.md)。
stop/market触发、事件撤单、动态退出、组合敞口改变及微型通道权限扩张未实施，仍需独立设计授权。
未来发布须固定提交并协调Runtime/Host/两Worker版本；本地通过不代表已上线。
