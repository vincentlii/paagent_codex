# V1 Headless 体检、修复与发布验收

日期：2026-10-09。审查基线：`e4d57e29e7a5343ee251fcbe117bc2ef54e7ba9c`。
范围：VPS Headless、全量 A 单 Agent PA＋VPA 联合链、Paper 和网页控制；不做 Windows GUI 验收。

## 结论

本轮查明并修复运行生命周期、任务身份、失败观测和 Paper 缺棒处理等确定性缺陷；
PA/VPA 专业框架、完整知识、唯一 Router/materializer/Validator/Risk 没有改写。
Fast 往返后历史快速失败的精确底层原因仍缺证据，不能宣称全部运行故障已闭合。
已获授权备份、提交、发布、重启并恢复持续 Paper；下一次自然分析是恢复证据，
不是额外付费重跑。盈利与长期稳定性必须由后续连续 Paper 观察验证。

后续 21:00 自然任务的原始检查结论见第七节：三角色分析完成，但 Paper 因同时间戳
闭盘行情修订而拒收，不能称完整端到端通过。第八节记录用户授权后的本地修复；
前七节保留当时证据，不作为当前未修复清单。新代码已按第九节发布到 VPS。

## 一、修复清单与验收依据

| 问题 | 根因／最小修复 | 验收 |
| --- | --- | --- |
| 同步完成回调可能丢失 | submit 前预留 task ownership；拒绝时只回滚仍拥有的 reservation | 同步 callback、reject、Fast pending CAS 与真实 applied on→off→on 回归 |
| 显式联合协议与旧默认身份混用 | settings cohort 加显式 analysis_pipeline，None 保持旧身份 | task_factory 相同快照不同 pipeline 分离 |
| 单 Agent 失败丢 Worker 观察 | 所有 Codex 失败记录真实 wall time、请求模型/强度和可信分类；parse/repair 前保存已观察 usage | Worker 失败、返回后处理失败和 call ledger 回归 |
| 请求强度误报 high | requested effort 取 Frozen manifest，不从通用 request 默认值推测 | xhigh manifest 回归 |
| unknown 看似免费／零延迟 | typed actual/estimated/unknown 与 coverage；未知 prior attempts 不当免费；Host 与 Calls 分离 | projection/metrics 回归 |
| 首稿统计偏乐观 | audit review_context 存 canonical first-pass 和 role metrics；修复、回退、规范化不能记 clean | audit/projection 回归；无 DB schema migration/backfill |
| 中文等待误计 Trade | `不下单` 归 no_trade，订单只称候选，不称成交 | 中文决策和看板只读 projection 回归 |
| 开仓后缺棒仍确定计收益 | 仅新 v3 open order 检查时间连续性，缺棒 ambiguous/censored，不改 equity | long/short gap 与连续 bar 正常退出回归 |
| Linux CLI 超时／退出留 MCP | 每次独立 Popen session，清理自己的进程组；有界 drain，复用现有 tree cleanup | Mock 回归；Linux dummy 对照：旧 run timeout 留子进程，新 timeout/normal exit 均清理 |
| 更新包未 reload 的发布风险 | 安装器写入前拒绝 active 服务；发布暂停/备份/停止三服务/固定提交/重启 | 部署资产 RED→GREEN；实际发布见末节 |
| 模型解释语言不一致 | Runtime AGENTS 指定解释用简体中文，JSON keys/enums/IDs/引文不翻译 | Runtime asset parity；不为翻译改变 protected judgment |

无新增 dependency、数据库字段、数据回填、策略阈值或并行 materializer。
旧冻结任务、旧失败记录、研究结果和历史 Paper 不修改。

## 二、知识、调用和 Skills 审查

| 阶段 | 保留的质量要点 | 调用／执行边界与结论 |
| --- | --- | --- |
| Snapshot / measurements | 同源身份、闭盘 cutoff、HTF、volume provenance、事实 ID | Host 冻结；Codex 不能更新市场或决定截止点 |
| PA Stage 1 | Brooks 背景、八态、Always In、主／替代解释、反证、位置与跟进 | price-only；完整 mandatory 原文和 HTF；不由数值阈值命名 Setup |
| Router | 仅已验证枚举与 pattern keys 决定权限 | 不从自由文本路由；相关原文随 Job digest 冻结 |
| 独立 VPA | Background、Location、Effort/Result、Comparison、已闭盘 Follow-through、acceptance/rejection | 独立会话、同快照证据目录；不创造 PA Setup/方向/订单／概率 |
| 联合 Stage 2 | Setup/结构止损、TP1、Trader Equation、未校准主观先验、等待的合法性 | 只接已验收 VPA 报告，不续接 VPA/raw catalog；同快照 PA 首稿通过才合法续接 |
| repair / Host safety | 指针式修复、protected judgment、引用和确定性订单数学 | 不以 repair 换专业结论；typed resource/identity stop 不伪装 No Trade |
| Paper | 交付后才激活、expiry、单一执行路径、缺失/同棒不确定性 | v3 不倒填未来成交，旧 v1/v2 分层统计 |

六份 Runtime role Skills 与 AGENTS 已审阅；本轮没有修改 Skill 正文或删减领域原文。
五份 VPA 知识已逐份复核；PA 重点复核背景、H1/H2、突破/失败、Measured Move、
结构失效、TP1 与先验边界，29 个 PA 模块的完整原文、锚点及路由保全另由回归覆盖。
这不等于对每段教材进行逐字专业认证，也不证明每次模型解释正确。

理论、产品边界和 Host Draft 协议维持分层：产品不授权某种逆势/MTR 交易，不等于
改写 Brooks 教材；教材频率不能直接变当前品种的胜率；VPA 名称须在事实后，不先
贴 Wyckoff 标签再找依据。知识索引、section aliases 和引用范围随 Frozen Job 固定。

性能结论：保持无损合并必读交付和同快照 PA 受控续接；短初始 Prompt 不等于低总
Context/Token。没有证据支持删原文、缩减 HTF、降低强度或上线分层/子 Agent。
仍固定 `gpt-6-luna/xhigh`、A 全量、单 Agent；Direct PA-only 主协议保持兼容。

## 三、Fast 失败证据与不确定性

历史定时任务 Stage 1 在约 455–621 ms 失败，明显不是长时间模型分析。旧完整记录
只有笼统 `CodexWorkerOutputError`，最底层可信分类没保留下来；旧 Spool 结果已清理，
CLI logs 数据库没有可恢复的相应日志。不能从旧记录还原精确错误。

当前 generation 17 的 desired/applied Fast 均 true，profile generation 2，control_error
为空；最近 8 个 Job 的 service tier 与 Runtime identity 匹配，固定 config/rules 相符。
Fast 控制只影响新快照，不改旧 Frozen Job、登录或 Runtime 文件。离线合成服务的
配置解析与 required MCP initialize 成功；两次分别获批的 3 秒启动检查都未立即退出，
已清理私有进程树，无完整分析、无 Paper 提交。这些结果不证明正式服务恢复。

另有 Worker/Headless 10:31 启动而安装包 mtime 11:16 的未重新加载风险。mtime 本身
不能证明字节变化或 Fast 失败的唯一根因；固定包发布并重启用于消除这类混用风险。
发现旧 PPID=1 的 CLI/MCP 残留，但未观察 TasksMax/MemoryMax 触顶，不能归因限额。
不盲目增加权限、内存或任务上限。

## 四、三角色校验

| 角色 | 判断 | 尚未能背书的部分 |
| --- | --- | --- |
| PA 交易员 | Brooks 背景、Router、结构止损/TP1、独立先验和唯一交易链保留；无新机械 Setup/Gate | 每次输出专业合理性、长期决策效果 |
| Wyckoff VPA 交易员 | 量价是同快照证据层，背景/位置/比较/跟进与替代解释保留；不变成第二套策略 | 不同市场形态的标签准确率与增量 |
| 量化／软件架构师 | 任务身份、callback ownership、观察覆盖、owned process cleanup、censored Paper 和停服发布各归单一责任方 | 新版自然任务成功、持续恢复与统计稳定性 |

本表是主 Agent 三角色自审。只读协助审查曾尝试但额度中断，不能称独立子 Agent
完整验收，更不能把角色自审当市场盈利证明。

## 五、验证与清理

- Headless 扩大离线 Unit/Property 905 项通过，约 35 秒；包括原 PA、VPA off/shadow、
  联合、HTF、订单数学、records/audit、冻结与运行恢复。
- 最后新增 Fast 往返、失败分类及部署保护后，直接影响范围 143 项再次通过。
- Worker/Backend/challenge/audit 定向 79 项通过；看板脚本 Node 语法和关键 Ruff
  F821/F823、git diff --check 通过。
- GUI E2E 试跑遇到既有 MagicMock clone 链未配置导致 JSON serialization 失败，未改
  GUI 或其 mock；遵用户指示不继续 GUI 测试，不声明全仓测试通过。
- Linux 无模型 dummy 证实旧 runner 留活子进程，新 timeout/normal-exit 全组清理。
- 原始行情和解释保留 VPS 原位，未导出完整私有数据；不输出 auth/API key/密码。
- 本轮临时检查脚本已从本机与 VPS 清理，15 个隔离 pytest 目录与本轮 build
  中间产物已移除。固定 wheel/bundle 和账本备份保留；不清原始记录、研究结论、
  登录或生产运行目录。

初次 VPS 成功调用约 373.96 秒、Host 377.21 秒、540882 Provider Token 只是单次
基线，不等于 p95、Fast 固定 1.5× 或新修复后真实性能。本轮不额外触发付费分析。

## 六、发布验收

用户已明确授权备份、提交、发布、重启与恢复原 Paper。调度已原子置为 paused
（generation 18），模型/Fast 保持不变；确认 scheduler running/queued/unfinished
均 0，running dispatch 0。备份服务成功生成
`/var/backups/pa-agent/pa-agent-20261009T121826Z`，`--verify` 通过。
运行代码发布提交：`49fc9ef8ed2bfa47056b87a4edad9e0c7cccaaff`。VPS 仓库从
`e4d57e2` 干净快进后，在三服务停止状态由既有 uv 离线重新构建安装项目；不改
uv.lock 或运行依赖，48 项已安装依赖兼容检查通过。源码／安装产物逐文件比对
PA Agent 259 个、Prompt/知识 38 个全部相同；Runtime digest
`a368713c54d10c9fc4e72a4a07aebdf86bf51b16aacc91f4cd37e1806d72ce09`，
asset parity 与 POSIX 权限检查均通过。Headless 无 Qt 导入；部署脚本 Linux 语法通过。

20:22 三服务重新启动，Headless PID 2629149、Worker PID 2629148、Dashboard PID
2629108，ExecStartPre 成功、NRestarts=0。先核对 paused、generation 18、无运行
dispatch/未完成任务，再恢复 running（generation 19），applied generation/Fast true
一致，control_error 为空，`/healthz` 显示 running。没有重复旧任务，三服务与每日
backup timer 均 enabled。Worker 仅一主进程，TasksCurrent=1、MemoryCurrent 约
49 MiB；旧残留 CLI/MCP 已随停止服务清理，未放宽资源限制或权限。

上述是发布与无模型运行环境验收。**下一次自然完整分析尚未验收，不能宣称 Fast
故障已恢复或确定其唯一根因。**本轮没有额外触发完整分析、修改旧 Job 或创建订单。
看板重启会使旧网页登录会话失效，重新登录即可，不更改密码或 SSH tunnel。

完成标准：固定源码与安装产物一致；备份完整；无旧任务重复；服务预检成功、
desired/applied 恢复一致；随后自然任务的角色状态、错误分类、回执和延迟可核对。
若自然任务仍失败，按新增可信分类继续定位，不提前宣称 V1“无 bug”或改交易规则。

## 七、发布后自然任务复核：分析通过，Paper 拒收

复核对象：2026-10-09 21:00:07 的 XAUUSDT/1h＋4h，运行 ID
`a2cffa6b0fe4460fa17e93397f9440fb`，VPS 记录文件
`2026-10-09_21-00-07-635000_XAUUSDT_1h_a2cffa6b0fe4.json`。
运行代码仍为 `49fc9ef`，仓库 HEAD `24dd873` 只补发布文档。
调用账本确认三次实际模型均为 `gpt-6-luna/xhigh`、Fast on、全量 A 单 Agent。

| 环节 | 实测 | 结论 |
| --- | --- | --- |
| PA Stage 1 | 184.144 秒 | 首次 Draft validated；偏多宽通道为主、偏多趋势型交易区间为替代 |
| 独立 VPA | 101.438 秒 | 首次 Draft validated；背景有限支持，即时延续受挑战，缺少后续闭盘确认 |
| 联合 Stage 2 | 97.425 秒 | 首次 Draft validated；等待／不下单，未创造订单价格或概率 |
| Host as-of 到结果可用 | 386.369 秒 | 约 6 分 26 秒；不与 Calls 累计延迟重复相加 |
| Paper | rejected | `conflicting closed bar for paper market identity and timestamp` |

零 repair/fallback/denied；Stage 2 未请求的 `next_cycle_prediction` 有无歧义规范化，
canonical `first_pass_clean=false`，不能称零规范化首稿。Provider 累计用量
594348 Token；其中 Stage 2 403281。累计调用用量不等于单次唯一上下文长度。
证据工具耗时约 2.934 秒、排队约 0.088 秒；主要耗时在模型调用，不在取证 I/O。
最后一次证据读取至返回的时间包含推理、网络和生成，不能全称为隐藏思考时间。
本笔比早先 VPS 单次基线稍慢，不证明 p95、Fast 固定提速或长期性能达标。

### Paper 根因证据与未完成边界

冲突棒为 17:00 开盘、18:00 闭盘。两个同源冻结记录相隔约 15 秒即出现变化：

| 观察时间（Asia/Shanghai） | close | volume | amount |
| --- | --- | --- | --- |
| 18:00:02，账本保留版本 | 4191.46 | 17563.088 | 73605517.51376 |
| 18:00:17，后续记录及当前公开 API | 4191.45 | 17570.318 | 73635821.69982 |

同棒 open/high/low 一致；差异不仅是浮点表示，成交量和成交额也发生变化。
19:00、20:00、21:00 的冻结记录均保持后一个版本，公开 Binance REST 查询亦一致。
`binance_usdm._fetch` 的 server-time 闭盘判断只证明时间边界已过，不证明响应数值
已经稳定；Paper 对历史事实逐字段严格比较，冲突会回滚本次市场观察，之后会继续
阻断接收。没有原始 HTTP 传输日志，不进一步断言上游内部修订或暂时返回滞后的机制。

拒收没有生成错误订单；联合账户当前订单数为 0。分析索引的 completed 仅描述
分析完成，不等于 Paper 接收成功。该问题不能用放宽浮点容差、删除旧棒、跳过
No Trade 的市场观察或覆盖原冻结记录来掩盖。下一步需设计边界附近的有界稳定性
检查与可审计修订处理，明确已执行／未执行依赖的区别，继续保留 as-of 和 no-lookahead；
尚未实施，任何账本纠正仍须备份、授权及一致性核验。

### 其他已确认问题

- 模型身份元数据：`role_runtime.requested_model/effective_model` 与分析索引
  `model_name` 错标 `deepseek-v4-flash`；源码从 profile 的 DeepSeek 字段填入，
  不分 Backend。真实 call ledger、Backend identity 和看板最近运行投影均为
  `gpt-6-luna`，不是实际调用了 DeepSeek。应修正身份来源，不能改写旧回执伪装无错。
- 自然语言误报：`_WINDOW_HIGH_EXTREME_RE` 在“最新高周期”中匹配到“新高”；
  `_FUTURE_CLOSED_BAR_RE` 不接受“后续已闭盘价”。两者只产生 warning，本次没有
  阻断分析。应修正词义边界／等义表达覆盖，不删除有效事实校验。
- 模型语义质量：替代 `trending_tr` 的 confirmation 写成“偏多宽通道解释将增强”，
  与自身 disconfirmation 的恢复上行条件重叠。它是竞争假设鉴别性错误，不是 OHLC
  事实错误，也不能由程序重写市场结论。后续 Skill/Prompt 应要求各假设确认自身，
  并区分与反证的方向；尚未修改运行资源。

### 三角色质量复核

本次独立只读审查完成，并与主 Agent 证据核对一致：

| 角色 | 评价 | 限制 |
| --- | --- | --- |
| PA 交易员 | 主要回撤低点抬高、K8 推进与 K5→K1 回落均有冻结证据；Stage 2 实质评估 §9.0P 后等待，并非无信号就机械禁止限价 | 主／替代判别条件有语义错误；未给具体限价三价，不能证明所有方案均无正期望 |
| Wyckoff VPA 交易员 | 两窗净推进、因果 RVOL 与单棒量比独立复算吻合；承认 seasonality fallback 和缺少后续确认，不强贴 Wyckoff 标签 | 季节性原始历史未独立重算；不能据此证明 VPA 增益 |
| 量化／软件架构师 | 身份、冻结闭盘、三角色调用、无订单决策与 warning 回执可核对；安全拒收生效 | Paper 修订处理未闭合；模型元数据和自然语言警告存在 bug |

截至 21:52，实际 Headless `pa-agent.service`、Worker、Dashboard 均 active，
NRestarts=0；generation 19 / applied Fast true 一致，无 queued/running/unfinished
任务和恢复阻断。历史快速启动失败本次未复现，不意味着已确定唯一根因或稳定恢复。
本次无付费重跑、无代码／账本／部署修改、无 commit，也不进行 GUI 测试。

## 八、自然任务暴露问题的根因修复

用户明确授权最小 Paper 账本修订，要求不使用子 Agent、不增加过严规则或冗余测试。
工作区此前三个审查文档修改原样保留为基线。本节代码修复尚未发布；截至 22:35，
旧 VPS 的 22:00 自然分析也完成，但同样被旧 Paper 的逐字段冲突规则拒收。

### 实际修改与失败边界

| 问题 | 修复 | 保留的边界 |
| --- | --- | --- |
| 同一闭盘棒变化持续阻断 Paper | 新增 `paper_market_bar_revisions`；接受 volume/amount 及未被任何账户执行回放使用的 OHLC 修订，保存 before/after、实际观察时间 | 所有账户已消费价格仍保护；单次观察内部冲突和已消费价格冲突整体回滚；不改旧订单、收益、拒收回执或快照 |
| 慢分析／重启恢复把旧棒当新观察 | Host 传递冻结 as-of，完成回调从原记录读取 as-of；当前版本保存最后确认时间，旧观察不能倒灌已知新版本 | 不把模型完成时间当行情观察；旧账本无历史时间时不伪造旧 known-at |
| Codex 元数据错标 DeepSeek | `records.projection.analysis_model_identity` 统一从真实 call ledger 推导 requested/effective；缺实际服务证据保留 unknown；索引 upsert 同步更新模型 | masked provider 配置仍是配置快照，不冒充调用事实；混合模型不压成单模型；Direct 使用自己的字段 |
| “最新高／低周期”和“后续已闭盘价”误报 | 修正既有 regex 的词义边界和未来条件等义表达 | 真正无范围新高／新低、未闭盘确认的检查仍保留；未新增 hard Gate 或 retry |
| 替代假设确认与反证重叠 | 替换 Stage 1 Skill 原有一段说明：围绕本假设、竞争解释与各自比较边界；无法区分时保留不确定性 | 不新增专业语义 Validator、不让程序重写市场判断、不增加强制自审回合或必读材料 |

量价修订只影响当前可审计市场版本，不重建任何 Frozen Snapshot、catalog hash、
旧模型分析或收益。升级仅新增表及其索引，无旧业务数据回填。发布必须一致性备份；
回退需同时恢复原账本与原运行版本，不允许旧 Broker 接管已产生新事件的账本。
没有添加固定 15 秒等待、额外行情重取或模型调用；“闭盘时间到达”与“返回数值永不变化”
是不同承诺，修复事实版本处理而不假定任意稳定时长。

### 三角色自审

- PA 交易员：背景、Setup、Brooks Router、结构失效、Trader Equation 保持原权责；
  假设说明保留实质竞争解释，不把市场模糊性当执行故障或增加 No Trade 门槛。
- Wyckoff VPA 交易员：原知识、因果 RVOL、比较窗口、独立会话和联合 Stage 2 不变；
  未来闭盘条件按未来条件识别，真实事实矛盾仍校验；未压缩领域材料。
- 量化／软件架构师：同源 market key、跨账户价格消费检查、事务回滚、观察时间与
  统一身份投影可复现；无新 dependency，修复不增加 LLM Token 或调用次数。

本表由主 Agent 自审，未调用子 Agent。它证明修改边界合理，不是模型质量或盈利保证。

### 验证结果与未验证项

新增回归先复现原症状，再验证修复：真实案例的 close/volume/amount 差异可审计接收；
volume-only 不改变订单、权益或事件；跨账户及旧 fill 的价格依赖仍保护；旧冻结回写在
重启后不能回退已确认新版本；冲突观察不部分写入；模型身份按真实回执更新。

- 33 个直接及相邻 Headless Unit/Property/Integration 文件，**311 passed，13.31 秒**。
  覆盖原 PA、off/shadow、联合、HTF、订单数学、records/audit、研究回放与 Worker。
- 最后复核又补齐 Codex 无身份时不能借用 Direct 配置、合法零 as-of 不能当缺值两项
  边界；身份相关 46 项、Paper／Headless 相关 77 项分别通过。该零时间边界只影响
  通用时间来源，不改变实时市场闭盘规则。
- 第一次扩大验证有 6 项 Windows 临时路径过长失败；缩短隔离临时根后整个上述集合
  全绿，没有修改 Frozen Job 或生产路径处理来迎合测试。
- Ruff 关键静态检查与 `git diff --check` 通过；Stage 1 Skill 格式验证及 Runtime asset
  完整性检查通过。官方 Skill validator 在项目解释器缺 PyYAML；改用已有系统解释器
  UTF-8 模式验证，没有增加项目依赖。
- 未跑 GUI、真实模型或额外完整分析；未声称 Prompt 专业效果已经提高。待部署后由
  下一次自然任务核对角色质量、模型身份、Paper 回执和耗时，不改写旧失败记录。
- 本轮两处隔离 pytest 临时根已删除，正式 records、旧失败回执、VPS 账本和登录
  均未清理。没有自动 commit；提交与 VPS 发布授权仍待确认。

## 九、授权提交与 VPS 发布验收

用户确认提交发布后，本地 171 项发布前核心回归与关键 Ruff 检查通过，修复提交为
`8e41db45790027fb0402f91cd2e9a94bc46e4392`。没有使用 subagent 或触发额外模型分析。

1. 控制暂停为 generation 20；确认 scheduler queued/running/unfinished 均 0、无恢复
   阻断，再生成 `/var/backups/pa-agent/pa-agent-20261009T153032Z`。390 个 manifest
   文件摘要及 monitoring/analysis/paper 三个 SQLite quick_check 全部通过，备份不含凭据。
2. 停止 Headless、Worker、Dashboard 后，VPS 干净仓库快进到固定提交；使用原 uv
   离线构建替换安装包，不变更依赖。48 项已安装依赖兼容，旧安装包已卸载替换。
3. 源码与已安装 PA Agent 259 个、Prompt/知识 38 个文件逐一摘要一致；Headless
   导入无 Qt。Runtime digest 为
   `b991054d11f21bfd80b87f266cde7d24def02209ed0383cfd260109d2573e6c3`，资产与
   POSIX 权限通过；Codex 登录仍为 ChatGPT，config/rules、凭据和模型配置不改。
4. 暂停状态建立新增修订表；与备份逐行核对账户、订单、事件、Paper 回执和行情
   事实均相同，无业务回填。随后在服务用户拥有的隔离账本副本读取原始冻结行情
   `0c08be0b62b441df8c480b383635a767`，成功接收一处未消费价格修订及 4 根新棒；
   原账户／订单／事件／回执不变，quick_check 通过，隔离副本已删除。
5. 三服务重启并先验证 paused/idle，再恢复 generation 21 running；Fast true、
   profile generation 2、`gpt-6-luna/xhigh`、单资产单 Agent 全量联合链维持原值。
   23:38 Headless/Worker/Dashboard 全 active、NRestarts=0、ExecMainStatus=0；
   `/healthz` 为 running，服务与每日备份 timer enabled。没有新 accepted/queued/
   running/unfinished 分析、控制错误或恢复阻断。
6. 恢复后的正常行情轮询已在正式账本保存 99 条首次版本记录和 1 条未消费价格修订，
   quick_check 通过。23 份备份内原始分析记录摘要未变；旧 rejected 回执保留，
   未倒填过期 No Trade 或订单，也不重新调用模型“洗掉”旧失败。

VPS 生产解释器没有 pytest，未添加 dev 依赖，不宣称 Linux pytest 集合通过；
Linux 证据为实际安装、权限、隔离原始行情接收、正式 schema、服务预检与自然轮询。
发布检查遇到 shell 尾部 CR 和 codec LoadedRecord 封套使用错误时只修正临时检查
调用，不改产品代码；所有实际验收均重新完成。看板重启可能需重新登录，密码与
SSH tunnel 未变。发布 bundle 和隔离测试目录完成后清理，备份保留。

本次闭合的是固定版本发布、已确认账本根因和自然轮询恢复。**下一次整点完整
PA→VPA→联合 Stage 2→Paper 尚未验收**；历史快速启动失败的唯一根因、长期
稳定性、模型假设质量与性能改善不能由本次无模型验收替代。

## 参考与长期依据

- [Brooks 官方术语索引](https://www.brookstradingcourse.com/price-action-trading-terms-glossary/)：本次网页读取超时；PA 审查依据仓内原文与协议，不声称重新核验了该网页全部内容。
- [Wyckoff 官方方法](https://www.wyckoffanalytics.com/wyckoff-method/)：量价、供需与背景下的证据解释。
- [Python subprocess](https://docs.python.org/3/library/subprocess.html)：run 的超时处理与 Popen 私有 session/process lifecycle。
- 项目 authority：[North Star](../knowledge/codex-analysis-north-star.md)、
  [交付与 Paper](phase6_delivery_paper_acceptance.md)、[联合本地/VPS验收](codex_joint_local_release_audit_20261008.md)。
