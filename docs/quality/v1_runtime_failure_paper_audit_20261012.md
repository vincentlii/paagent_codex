# V1 自然运行失败与 Paper 拒收排查

核验日期：2026-10-12，Asia/Shanghai。范围：Headless Codex Backend、Draft repair、
Paper 接单与只读显示。最初诊断的运行提交为
`c20d04f41f06d344c9da1b7a76997ab1f722765c`，Runtime 为
`codex-runtime-v1-1f44fa580db45e16`。诊断阶段只读线上，未修改产品或历史记录；
随后用户授权的本地修复、验证及 `227e197` VPS 发布回执分别见末尾章节。

## 自然运行口径

截至 13:19，按上轮恢复运行后的 `monitoring_jobs.task_created_at_ms` 清点 45 个
任务：38 个 completed_with_warnings、1 个 completed、6 个 incomplete。完成只代表
分析协议终态，不代表市场判断正确、盈利或 Paper 接单成功。

五个 Stage 1 验证失败位于 02:00（ETH 两条不同冻结价格快照）、03:00（BTC）、
04:00 / 05:00（XAU）；另一个 10:00 BTC 在联合 Stage 2 的 Codex CLI exit 1。
不是把历史累计失败当新增失败，也不把同一时间的重复市场观察当独立研究样本。

Worker auth healthy、NRestarts=0、TasksMax=256、pids.peak=181、pids.events max=0；
memory.peak=705122304，memory.events 的 max/oom/oom_kill 均为 0。这些自然运行
计数不支持此次失败由已修复的线程上限或 OOM 引起，也不证明长期资源稳定。

## Stage 1：真实越界与程序修复缺陷叠加

校验器要求支撑区间整个上界低于冻结 K1.close。五个首稿都有区间跨越当前 close，
这确实违反当前字段数值契约，但 Schema、Stage1 Skill 与市场诊断教材未明确说明
“整个区间严格位于收盘价下方”，不能将其等同 PA 专业解释错误。后续修复存在两个
独立程序缺陷：

1. `stage1_normalizer._normalize_structure_level_order` 按 midpoint 排序，
   `JsonValidator._merge_provider_issues` 只将 trace 下标映回 Provider Draft，
   未映回支撑/阻力列表下标。02:00 第一条 ETH 的原列表为
   `["2518.49", "2534.63-2539.87"]`，K1.close=2537.45；越界的是原 index 1，
   排序后为 canonical index 0，修复却获授权 `/support_levels/0`。模型修改第一个
   原始项为 2536.70，真正越界的第二项仍保留。
2. 另外四条的错误在原 index 0。模型的修复响应正确删除越界区间，但
   `detect_unallowed_draft_changes` 将长度变化报告为整个数组变更；
   `project_allowed_draft_changes` 随后从新数组读取 index 0，替换旧 index 0，
   保留旧 index 1。删除导致的合法位移变成重复，产生新的 near-to-far 校验失败。
   线上 `bounded_contract_projection` 均记录 ignored_change_count=1。

03:00 BTC 的完整最小例：

```text
原 Draft: [83531.9-83725.5, 82958.5-83142.3, 82723.7-82751.1]
K1.close: 83643.1
修复响应: [82958.5-83142.3, 82723.7-82751.1]
授权: /support_levels/0
保护后: [82958.5-83142.3, 82958.5-83142.3, 82723.7-82751.1]
```

通过已安装校验器内存复算原始 record / validation_audit，并在本地直接运行真实
`project_allowed_draft_changes`、`detect_unallowed_draft_changes` 与几何校验函数，
确认正确候选可通过，而保护后的重复列表失败。纯函数复现不调用模型或交易所。
软件只读协作者另外通过完整 JsonValidator 复现排序后的错误指针。

修复应澄清字段空间契约，保留明确价格越界检查，修正 canonical→Draft 下标绑定，
以及授权单数组项删除的结构位移语义。不能以扩大整个数组权限、改变市场结论、
重试更多次或只处理重复项替代根因修复；支持/阻力两个方向均需覆盖，旧失败与
冻结身份保留。完全相同的价格项另可评估为可审计表示问题，不按 midpoint 合并
不同区间、不截断或改价。既有相关四组 164 项测试通过，却未覆盖这两个真实修复
输入；已有回归通过并不能证明自然运行不会遇到这些缺陷。

## `trade` 未挂单：历史 OHLC 冲突扩大成新任务拒收

12 日 11:00 / 12:00 的 BTC 两条完整分析分别为
`683ba94538354818bfd2bd08e6898e79`、`a91b381869314635b75e619a27bc0f6a`。
候选均为多头限价 entry=83408.4、stop=83228.4、TP1=83997.0。
`execution_disposition.status=eligible`，effective_order_type 为限价单；置信度
50 / 51 高于已配置阈值 40，Trader Equation 通过，结果交付也早于固定 expiry。
因此这两笔不是置信度、方程、过期或尚未触价造成的无挂单。

两条 `paper_decisions` 均为 rejected、order_id=null，原因为：

```text
conflicting closed bar for paper market identity and timestamp:
price already consumed by execution
```

逐棒与完整市场身份核对，两条均只有同一个已消费价格冲突：

| 项目 | 原 Paper 行情 | 两份新冻结快照 |
| --- | --- | --- |
| BTC 1h ts_open | 2026-10-10 17:00（1791622800000） | 同一时间 |
| open / high / low | 82803.9 / 82912.9 / 82789.6 | 相同 |
| close | 82815.0 | 82815.1 |

这根棒位于 10 日已提交订单 `d806ed81d0cb471b9da08399e40e050a` 的执行观察范围。
该订单 status=cancelled、exit_reason=decision_expired、fill_price=null，
evaluated_through_ts=1791622800000。即使确认未成交，其触价观察也使用了该行情；
不能因为订单最终取消就覆盖原价格或重算旧结果。后续数据为何相差 0.1 的上游
根因未查证，不能凭差值猜测舍入、实时棒、交易所修订或 provider bug。

`PipelinePaperExecutionCoordinator.submit_official` 在创建新订单前，先交付完整
record 闭盘窗口给 `PaperBroker.observe_market`；日常 Headless prepare 也交付完整
frame。Broker 跨全部账户、包含历史终态订单检查消费范围。一根旧棒 OHLC 冲突
抛错会回滚整次观察事务，因此后面的新棒也不保存；Coordinator 写永久 rejected，
尚未调用建单入口。旧冲突棒持续留在窗口时，可持续影响新决策，而非每次出现新的
交易质量问题。联合账户当时累计 44 条为该明确消费冲突拒收，另有 3 条旧版通用
同时间冲突原因；不可将其全部算作本次新版本 trade 或模型失败。

该拒绝遵循 [ADR-0012](../architecture/ADR-0012-recoverable-paper-decision-ledger.md)
的已消费 OHLC 保护；全窗口事务使历史冲突持续阻断未来执行，是需要独立处理的
可用性问题。改善应明确隔离历史执行依赖的行情版本与未来观察；这是执行事实/
数据版本设计选择，须先确认方案，不直接忽略冲突、加价格容差、缩短分析窗口、
删除订单或清库。已 rejected 的两笔不改写为 submitted、不补成交。

只读执行协作者还指出：evaluated_through_ts 包含 v3 激活前跳过的棒，消费范围
可能过于保守；本次阻塞棒在旧订单有效观察窗内，这一额外边界尚未单独验收。

交易投影显示模型 candidate，Paper rejected 只聚合计数，没有在每条分析旁展示
拒收 reason。因而用户能看到 `trade`，却无法从该行解释没有挂单；后续可补只读
处置/原因展示，不修改 canonical candidate 或模拟执行规则。

## 10:00 Codex CLI 失败的证据边界

BTC `77e2dca26184437bb7938385a4484f86` 的 Stage 1 与 VPA 完成，但联合 Stage 2
以 exit status 1 退出，可信 Worker 原因为 `Codex failed without an availability signal`。
当前证据未识别认证、限流、超时、权限或特定 Schema 拒绝，不能推断唯一底层原因，
也不能把该失败升级成 availability fallback。原始 stderr/认证细节不读取、不保存。
应优先增补固定 allowlist 的结构化失败分类证据，不暴露原始错误载荷。

## 结论与后续验收

本次确认模型首稿价格越界、程序 repair 数组身份缺陷和历史 Paper 数据冲突传播；
另一个 CLI 非零退出仍是未知细分类别。PA/VPA 的结构判断、Router、概率与订单数学
未被程序改变。两个协作者分别只读核对 repair 与执行源码；这是技术根因核对，
不是交易效果或策略质量晋级。

优先修复 repair 的索引/删除处理；Paper 历史版本隔离另行确认后实现。修复验收
须复用以上真实输入的离线测试，核对相邻数组、权限越界与事务保护，保留失败证据，
再观察新自然任务。以上为最初诊断；后续用户授权的本地修复及验证见下文。

## 用户授权后的本地修复（初次验收时未提交、未发布）

用户要求完整修复已发现的问题，限制最多一个 subagent；随后明确选定历史冲突
隔离方案。本轮一个只读协作者先审实施边界、再审实际 diff，未创建其他协作者。
PA / VPA / 量化三视角均通过：不改市场解释、Router、概率、候选价和冻结 Evidence，
只修正原 Draft 权限绑定、执行数据依赖和可信运行投影。

| 问题 | 实际修复 |
| --- | --- |
| 排序后修错项 | Canonical 支撑/阻力数组错误绑定原 Draft 的同值 occurrence；错误路径、pointer 与消息均指向原项 |
| 删除被恢复为重复 | 通过原索引授权匹配数组缩短；可同时删除、替换其他授权项及修改 survivor 授权子字段，所有未授权 survivor 不变；投影一次后不再按旧下标覆写短数组 |
| 多项 Patch remove 位移 | Patch Envelope 指针绑定原 Draft，先 replace，再按深度和原下标倒序 remove；不扩大 Validator allowlist |
| 字段空间要求不明 | Provider Draft Schema 的 description 明确整个支撑区间在 K1.close 下方、整个阻力区间在上方；不改价、截断或造结构，仅澄清已有校验 |
| 历史旧棒阻断未来 | 用户授权修改 ADR-0012：仍拒绝冲突的新价格版本；跨账户无潜在 pending/open 依赖时写幂等 quarantine 审计，保留旧 accepted OHLC 并继续新棒；accepted 版本选择排除 quarantine；延迟新单若需回放隔离棒仍拒绝 |
| trade 无可见处置 | Host 只读按 task_id / run_id / 正式 account / arm 绑定 Paper status、order_id 和 reason；运行与交易页显示，原模型候选与分析成功状态不改 |
| CLI 结构化原因被遗漏/误分类 | 识别 terminal turn.failed / error 与 item.error，末次 terminal 优先；未知 terminal 不被插件 stderr 401 误判认证；context_length_exceeded 给固定原因，其他未知保持 fail closed，不输出原文 |

CLI 事件依据 [OpenAI 官方非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)。
本轮只读检查 10:00 失败时段的专属会话事件类型，未发现留存结构化 error，不能补造
该历史 exit 1 的底层根因。分类修复不保证外部 CLI 永不失败，也没有据此重跑或
接受非零退出的模型结果。上游两次 K 线价格不同的原因仍未确认。

BTC 原冻结 InstrumentSpec 的 tick_size=0.1，所以 82815.0→82815.1 是一 tick，
不是数值相等的表示差异。程序不设绝对或百分比容差：任意小差异也可能跨成交/止损
比较边界。已不影响未来执行的旧冲突不再阻塞新单，其依据是依赖隔离而非价差大小。

### 验证证据

- 先运行失败回归，再修产品代码。覆盖原下标、数组头/中/尾删除、越权 survivor、
  remove+replace、remove+survivor 子字段、多项 remove，以及 Direct / Codex Patch
  经 validate_with_retry 的完整重新校验。
- 从 VPS 只读导出五个真实失败的支撑列表和 K1.close，离线确认一例正确原指针、
  四例模型已正确删除的候选不再被程序破坏；没有重新调用模型。
- 在独立临时 SQLite 用真实 BTC spec、旧订单价/交付/expiry、冲突棒和新候选价/时点
  重现：旧 v3 订单自然到期未成交，close 82815.0 保留，82815.1 隔离后新候选为
  pending，旧订单、事件和账户逐字段不变。这不是线上补挂、真实成交或回填收益。
- Paper 测试覆盖 v1/v2/v3、取消/闭仓/ambiguous、跨账户潜在活跃依赖、未回放
  watermark、重复隔离与重启、延迟新决策、旧冻结观测、accepted 时间边界、同批
  硬冲突回滚及未来新挂单/入场。最终该范围 50 passed。
- 最终 Headless not-live Unit/Integration/Property 全量回归 1730 passed、9 deselected，
  83.24s，包含最终 v1 版本夹具；GUI 排除方式沿用上一节引用的发布报告。
  没有把重叠集合相加。两项 warnings 是原有
  tvDatafeed 转义字符弃用提示。
- 源码关键 Ruff `E9,F63,F7,F82` 通过；额外 `F,I` 扫描发现既有六处 import 排序
  提示，不为本次修复顺手格式化。文档链接及 diff 检查在最终完成时核对。
- 离线构建 wheel；302 个包内源码/资源与本地工作区字节一致，隔离安装位置导入
  Headless 无 Qt，包含新 Paper 逻辑和字段描述；未修改依赖或锁。
- 最终 101 个本地文档链接与 `git diff --check` 通过；关键静态检查通过，额外
  六项 I001 已逐文件与 HEAD 比较确认均为既有。本次导出、临时 SQLite、wheel、
  隔离安装、uv cache 和 build 已清理，保留既有虚拟环境和发行 metadata。
  `.pytest-fix-20261012` 下十个测试临时子目录因系统权限拒绝仍有残留；PowerShell
  与独立 Node 文件工具均确认无法完整遍历，未强制递归修改所有权或访问权限。
  该目录被既有 ignore 排除，不进入提交或安装包；产品验收不依赖这些残留。

未改数据库 schema，不清库、不删除或改写线上历史记录/拒收回执，不额外触发模型、
Run Once 或旧任务恢复。新 quarantine 使用原 revisions 表的新 reason，不代表
接受新 OHLC。回退读取器必须识别并排除该 reason，不能直接将旧版本加载到已有
隔离审计的账本；完整契约和回退边界见 ADR-0012 本日修订。

上述为初次本地验收；当时 VPS 仍运行 `c20d04f`，新代码未发布，
新版本自然模型效果、长期运行、CLI 未知退出根因与数据源价格修订原因未验收。

## 授权提交与 VPS 发布回执（2026-10-12）

用户随后明确要求 commit 并发布 VPS。运行提交
`227e1979ae7b4d7eebb5fe797e5c39c4aaf71d1e` 已提交并推送用户授权的
`vincentlii/paagent_codex/main`。后续回执提交只更新项目文档，不改变运行包。
同一只读协作者复核发布边界，无阻塞项；没有创建第二个协作者。

发布前 174 项直接相关非 live 测试通过，8.53s；关键 Ruff 与 diff 检查通过。
第一次复核因未创建 basetemp 父目录导致 setup 失败，补齐本次临时目录后完整通过，
没有修改产品代码。Linux 隔离验证初次漏传既有 risk_policy 构造参数，补齐后通过。
不把检查脚本调用错误当作产品失败或隐去失败检查。

generation 36 暂停已 applied；Host queued/running/unfinished、持久 ledger 未完成、
active Frozen Task、Worker inbox 待处理任务均为零，各资产无 in-flight、恢复阻断
或 control error。随后停止三项加载包的服务；无旧任务迁移或模型重跑。

| 发布检查 | VPS 实际证据 |
| --- | --- |
| 一致性备份 | `pa-agent-20261012T135657Z`；服务 Result=success / ExecMainStatus=0，8633 项 manifest 摘要及三库 quick_check 通过 |
| 固定源码 | 干净 VPS checkout 从 `c20d04f` 快进到 `227e197`，通过验证过的增量 Git bundle 交付，不传仓库凭据 |
| Linux wheel | 离线构建/重装完整包，302 个源码/资源文件与 Git 固定提交、安装位置逐字节一致；Headless 不加载 Qt |
| wheel SHA-256 | `26595a03d7e64a5596bfb60e3dbede8b725fe381562972ba9a6dbf8e7eed6870` |
| 依赖 | 48 项安装版本不变，uv pip check 通过；未修改 lock、systemd 或配置 |
| Runtime 与权限 | installed/source audit ok=true、issues=[]；asset digest 仍为 `1f44fa580db45e167fe43787e76d96b0c5ea3c74c696dbf50938da230c089463` |
| 原始数据保护 | 275 份原始记录及九项配置/认证文件摘要不变；安装前后七张 Paper 表逐行摘要一致，无 schema migration 或回填 |
| Linux 隔离账本 | 已安装包保留旧 cancelled 未成交订单/事件/账户；修订隔离、延迟新单拒绝、独立未来单 pending 通过；数组授权删除不再复制项 |
| 重启暂停验收 | 三服务 active/running、ExecMainStatus=0、NRestarts=0；Worker auth=healthy；控制/Profile 一致 |
| HTTP | healthz、认证 status/trading 200；未认证 status 401；认证信息只在进程内使用，不输出或留存 |
| 恢复调度 | generation 37 running 已 applied；Profile generation 4、fingerprint、三品种、并发 3、Fast、模型与 Run Once generation 5 保持原样 |
| 自然行情轮询 | XAU/BTC/ETH connected、fetch_failures=0；三库 quick_check 通过；正式库出现一条 quarantine 审计，BTC 历史 close 82815.0 保留；原账户、订单、事件、Paper 决策逐行摘要仍不变 |
| 运行投影 | recent_runs 已含 paper_status / paper_reason / paper_order_id，不改模型意图或成功口径 |

本轮 Provider Schema description 不参与 Runtime assets digest，所以 asset identity 未变；
Python 分析指纹和 Frozen Job Schema/request digest 已改变，不能据相同 asset digest
跨版本续接。上文空闲检查仍完整执行。

**回退限制：**正式账本已经产生隔离 reason，禁止直接安装 pre-amendment Paper reader
并接管现库。后续代码回退必须使用识别/排除 quarantine 的兼容构建，不删除审计或
回滚线上订单/收益。完整固定运行包与正式备份保留供恢复；数据恢复需另行核验。

恢复调度后的即时核验中，固定版本发布及自然轮询验收通过。未额外触发模型、
Run Once 或历史订单；下一自然完整模型链、长期稳定性、交易收益/模型质量改善，
历史 CLI exit 1 底层原因及上游价格修订原因仍未验收。

固定 wheel、提交身份、安装核验与回退边界已保存到 VPS 的正式 releases 备份目录；
本轮 VPS 暂存目录、本地发布 bundle 和 174 项测试目录已清理。上文初次修复遗留的
十个不可遍历测试目录仍受系统权限限制，不进入 Git 或发布包；未强行改权限或删除
其他任务缓存。正式数据备份、原始记录和凭据均保留。

## 后续自然任务、面板及执行语义核对

用户随后要求调整 Recent Runs 并核对失败、不确定订单、VPA timing 与订单时效。
既有 ICT 评估及其 AGENTS/知识库导航改动经用户确认保留，不纳入本轮修改。
线上仍为 `227e197`；本轮仅只读线上，没有发布、模型重跑或账本写入。

### Recent Runs 展示（本地修改，未提交或发布）

此前 Outcome 将模型意图、Paper 状态和完整拒绝原因拼入 nowrap 单元格，造成表宽
膨胀；Status 又排在最后。现在 Status 为首列并固定在横向滚动左侧，Outcome 只
显示意图及 Paper 状态，完整原因保留在默认关闭的 details。新增只读品种筛选，
只筛当前已加载 Recent Runs，不改变控制面的启用品种、全局统计或服务状态；
Backend、Requested / Effective Model、Effort 移到最后，所有字段继续 textContent。

真实 JS DOM 测试核对列/数据对应、筛选往返与空态、完整原因保留及折叠、模型意图
与 Paper 分离、原数据不变、筛选不新增 HTTP 请求。Dashboard、Trading 页面与投影
合计 74 passed，26.98s；首轮默认沙箱的 loopback 请求遭 WinError 10013，受控权限
下完整重跑通过，没有修改服务器或认证代码。关键 Ruff 与 diff 检查通过。
41 个本轮文档本地链接核对通过，本轮专属测试目录已清理。
未做实际浏览器像素验收；不是 GUI Qt 改动。

PA/VPA/软件边界核对：只改已存事实的展示，市场判断、候选价、Validator、订单时效、
成交路径与原控制写入接口不变。

### 最近失败的版本与原因

| 任务 | 版本与事实 |
| --- | --- |
| 20:00 XAU `4dd87fd9`、21:00 BTC `374d7ae0` | 旧分析指纹 `5156127a`；首稿支撑区间跨 K1.close，旧保护投影又破坏授权删除。均为 `227e197` 发布前的历史失败，不能据 Recent Runs 仍显示它们判断新修复复发 |
| 23:00 BTC `8b73c1b4` | 新指纹 `d08edcc5`，Stage 1 wall 240311ms，冻结 budget.max_wall_seconds=240，原因为 wall-clock budget exceeded；没有 Draft 或 Validator 错误，token unknown，不能把零值当免费。当前 Worker auth healthy，不能将此归因认证或数组缺陷 |
| 23:00 XAU `a1071e9c` | 首稿 incremental_delta.summary 为空，一次授权 repair 后完整 PA→VPA→Joint 成功，Paper no_trade；不是持续失败 |
| 23:00 ETH `a4be22f9` | 完整联合链成功，无 repair，Paper no_trade |

新版已经有两个自然完整成功样本及一个预算停止。未修改 240 秒预算、增加调用或
自动重跑；为何该调用超过预算仍缺少内部推理/服务耗时细分，不能猜测唯一外部原因。

### 不确定订单与一小时执行证据

唯一 ambiguous 为 ETH `90da25dc`，来源 `0d71be5c`，limit long 2533、stop 2512、
TP1 2559.37。20:15:35.660 激活，21:00 到期；20:00—21:00 棒为 O2545.1 / H2545.38 /
L2517.58 / C2530.57，整棒触及限价，但无法确定触价在激活前或后。因此事件原因为
delivery_window_intrabar，fill/exit/PnL/fees 保持 null，不是亏损或确认未成交。

expiry 固定为最新已闭盘棒开盘时间 + 两个主周期，即下一主周期收盘；不是交付后
完整一小时，本例剩余约 44 分 24 秒。只限制 pending，已开仓仍由 stop/TP1 推进。
它防止旧信号长时间执行，但没有证明是一小时策略的最优订单寿命。

当分析在有效棒柱内交付、又在该棒收盘到期时，当前 1h OHLC 只有部分时间覆盖，
触价通常无法确认为交付后成交。这是 `paper_latency_v3` 的已知可观测性限制，
不能放宽顺序猜测成交。更细执行证据应优先研究；较长时效也须独立协议/版本及
因果验收，不能改旧订单、旧收益或直接延长已冻结 expiry。实际交易所支持多种
timeInForce，并不强制一小时限价寿命，参见
[Binance USD-M 官方枚举](https://developers.binance.com/zh-CN/docs/products/derivatives-trading-usds-futures/common-definition)。

### 前置 VPA timing 与入场位置权限

显式 Joint 的 `_run_predecision_vpa` 使用 `compile_pa_handoff(stage1, None, frame)`；
此时没有 Stage 2 订单，candidate_ids 为空。Prompt 和 Validator 均要求该场景的
entry_timing_relation=insufficient_data，含义为“没有可评价的具体入场尝试”，不能
由该字段推断量价数据缺失。只读全库 207 个已存 VPA 报告中 206 个为该值，另一个
为 neutral；跨历史协议计数不构成同 cohort 效果证据。

独立 VPA 可提供 Background、Location、Effort/Result、Acceptance/Rejection 与后续
确认；不自己创建 Setup、方向或订单价。当前联合 Stage 2 可以据这些证据影响等待
条件及 Brooks/Router 范围内的入场位置，不局限于给 PA 订单贴 supports/neutral/
challenges 标签。旧 Shadow 正式 PA 订单不能被 VPA 修改，不能混用这两种权限。

原始 timing 枚举没有改写；将“无对象”与“证据不足”分开，应通过显示 applicability
或版本化契约研究，不能强行将没有候选的 VPA 改成 supports 或把最终订单倒填给
更早的 VPA 分析。所有契约及执行数据设计的后续修改须另行确认范围。

## 2026-10-13 初始预算、VPA 位置与挂单时效建议（未实施）

00:07 只读新分析指纹 `d08edcc5` 的六个自然任务：五个完整成功，一个 Stage 1
240 秒预算停止。00:00 三品种均成功；23:00 XAU 空 summary 的 repair 已成功，
不计为终态失败。五个非停止首稿耗时为 164.83、173.83、177.26、186.41、234.08 秒；
完整链交付前 wall 为 348.8—475.7 秒。超时样本被截断，不能由成功样本推断真实
全分布 p95、平均总成本或把其未报告用量当零。

建议首稿 Stage 1 先试 **300 秒**，repair 180、VPA 180、Stage 2 300 和整链 600 秒
暂时保留。成功调用已接近 240，少量余量有实际依据，但不直接倍增预算。整链
由同一 `CodexAnalysisRoleBackend` 共享；Spool/Worker 对每阶段按剩余预算 clamp，
不是三个独立上限相加。更大 Stage 1 预算可能将停止转移到 VPA/Stage 2，需要在
新自然样本分别检查预算停止、交付延迟与用量覆盖。900 秒 `analysis_timeout_seconds`
主要用于 `--once` 等待上界，不应误称持续调度每任务 hard deadline。

用户明确暂不优化当前 Paper 的 OHLC 不确定成交，未来接入模拟盘后使用交易所
模拟盘执行回执；本轮没有接入交易所或改写旧 Paper。模拟盘新结果须保留自身
来源和执行协议，不能倒填为旧 Paper 已知成交。

VPA 已接收完整 Stage 1 背景、结构位置和假设，不应重复增加一次后置调用来处理
不存在的 Entry Attempt。优先明确现有报告关注 PA 已定义支撑/阻力、回测窗口的
量价接受/拒绝、反证与尚待确认条件，并由联合 Stage 2 说明这些位置证据如何影响
最终等待与定价。已有 observations 的 Effort/Result、Acceptance/Rejection 和
next_confirmation 可承载这些内容，不要求所有位置强制给结论，不创造新的 Setup、
订单价或 VPA Gate。界面可区分无 Entry Attempt 与实际量价数据不可用；原始枚举
保持契约含义。理论依据见
[Wyckoff 官方方法](https://www.wyckoffanalytics.com/wyckoff-method/)，不由此宣称
本项目 VPA 已带来交易增益。

对 **1h 决策 + 4h 背景**，建议未成交限价单初始硬上限为 **三个后续 1h 收盘**，
从触发该分析的已闭盘边界计时，不从模型回复或提交时间重新起算。例如 12:00
闭盘触发、12:08 接单，最迟 15:00 到期。旧接单新鲜度仍只到下一 1h 收盘；
迟到分析不能因为新挂单寿命变长而获准入单。

这是在回调等待与信息老化之间选取的工程初值，不是 4h 背景必然持续三小时，
也不是 Brooks、Wyckoff 或交易所规定。Brooks 对限价回调的论述强调强弱趋势
和双向交易背景对等待深度的影响，并不提供跨品种/周期统一 TTL；
[原始问答](https://www.brookstradingcourse.com/ask-al/pullbacks-entering/)只能支持
按背景判断等待，不证明此处三小时最优。之后可对 1/2/3 小时做同源、有成本和
覆盖标记的比较，不用单次成交率证明更优。

三小时是硬上限：当前每小时有效分析（包括 No Trade）会替代/撤销旧 pending，
可提前结束；失败分析不刷新原 TTL，已开仓不受挂单时限约束。若未来希望明确保留
已有订单，须另定义管理语义，不能把所有新 No Trade 自动解释成继续挂单。

现有 `decision_expires_at_ms` 同时承担结果接收与挂单过期，不能直接替换其公式。
实施须把结果新鲜度与 pending expiry 分开冻结、校验、持久化，保持不同执行版本
与旧 Frozen Job/订单隔离。以上方案经同一协作者只读边界审查；本轮只更新建议
与知识记录，没有改 240/600 秒预算、expiry、Prompt、Schema 或 VPS 运行状态。

## 2026-10-13 本轮优化实现与本地验收

用户已授权完成上述初值设计、commit 和 VPS 发布；以下是本地实现，发布证据单独登记。

- Recent Runs 的 Status 首列固定，Outcome 精简并折叠完整原因，品种筛选，Backend/Model/Effort 后置。
- Stage 1 首稿 wall 300s；repair/VPA/Stage 2 与整链共享预算不变，不自动重跑旧失败。
- VPA/Joint 的现有协议与 Skills 明确 PA 结构位置的接受/拒绝、Test、反证与待确认条件；理论知识只保留位置与量价解释，不混入执行 action。Joint 说明证据对自身入场/等待的影响；没有新增角色、模型调用或 VPA 订单权限。
- 复盘用原 `review_context.pa_hypotheses.setup_candidates` 判断有无 Entry Attempt；区分不适用、实际 unavailable 和 timing 证据不足，原 canonical 值不变。
- 新 1h 显式 Headless 任务冻结 limit_3bar_v1。结果接收新鲜度下一收盘不变；限价挂单最迟第三个后续收盘，交付后剩余时间通常少于三小时。市场/突破单、旧恢复任务与旧订单不变。
- 新扩展限价使用 paper_latency_v4。新增 nullable result_expires_at_ms，没有回填；锁内再次校验新鲜度。新版挂单缺首棒或中间棒保持 ambiguous，不能跳过未知成交路径。旧不确定订单保留，未接交易所模拟盘。
- v3/v4 收益与流水分开查看；账户资本及风险共同管理。另一版本有活跃挂单/持仓时提示切换查看。

独立协作者最终审查发现新版三棒 pending 缺口未检查；四种自动化场景先复现错误 open/cancelled，再修复为 ambiguous。40 项相关测试通过。Headless 非 live 全量回归 1747 passed / 9 deselected（排除 Qt GUI），之后补充的版本活跃提示及限价测试 17 passed；关键静态检查及 diff 检查另行核验。只有外部 tvDatafeed 的两项既有 DeprecationWarning。没有用测试结果宣称模型判断或收益提高。

三角色判断：PA 的 Setup/Router/结构失效与唯一物化器不变；VPA 保持 PA-defined location 与 Evidence first，无第二 Setup/Gate；软件层冻结时钟、版本、失败边界与数据保留明确。三棒初值及 300s 预算仍需要新自然样本观察，尚无新 cohort 的质量、token 或交易增益结论。

## 2026-10-13 固定提交 VPS 发布验收

运行提交 `59605db351c143bced2db65756ffa5a780c0ab98` 已推送私有 main，并由验证过的增量 Git bundle 快进部署。文档收据后续单独提交；VPS 运行身份仍固定该提交，ICT 既有改动未包含。

| 检查 | 实际结果 |
| --- | --- |
| 安全边界 | 控制 37→38 paused，Host 队列、active Frozen Task、未完成 accepted 任务、Worker inbox 与恢复阻塞均为零后才停三服务；records/pending 是保留的分析记录，未删除 |
| 发布前备份 | pa-agent-20261012T164557Z：8842 文件全部 manifest 校验通过，三份 SQLite quick_check=ok |
| 代码与包 | Linux wheel、安装包与固定 Git 302 文件逐字节相同；48 项依赖版本未变，pip check 通过；Headless 无 Qt 导入 |
| Runtime | installed/source 权限审计通过，asset digest `4ab1384cf286000c5ab46469a91855fae074408311963205fc5fc5eaa9e1e177`；实际 Worker 同身份，认证 healthy，CLI binary digest 未变 |
| 数据迁移 | 只新增 nullable result_expires_at_ms，所有旧列和其他表行摘要一致；旧行新列全 null。原 v3 三单及 v2 一单未重标 |
| 安装后验证 | 11 项隔离 Linux probe 通过：连续棒/缺首棒及中间棒、后续触价/未触价、重启、锁内迟到拒绝、原 v3 到期、复盘 applicability 及只读版本投影；临时订单不入正式账本 |
| HTTP | healthz、api/status、api/trading、Dashboard 与交易页 200；未授权 API 401；新列显示和 v3/v4 投影均验证 |
| 恢复运行 | 控制 39 running，原 profile generation 4/fingerprint 31fe1647...、codex_only、gpt-6-luna/xhigh、三品种、并发3、Fast及 Run Once generation 5 均保持。三个服务 active/running，ExecMainStatus 0、NRestarts 0；资产 connected，无恢复阻塞 |
| 保留证据 | 290 个原始分析/配置/凭据文件摘要一致；恢复后旧订单、账户、事件行摘要一致。正式发布目录已保存 wheel、哈希收据与兼容回退边界 |

本地 Headless 非 live 1747 项通过、最终聚焦 17 项通过、关键静态和 diff 检查通过。GitHub Linux wheel/assets job 成功；Linux 完整 test job 在 pytestqt 初始化阶段因 `libEGL.so.1` 缺失停止，未运行测试，属于本次明确排除的 GUI 环境验证缺口，不能写为全平台 CI 通过。Windows 完整 GUI CI 的状态另以 Actions 为准。VPS 不安装 pytest/GUI 依赖，因此使用安装包隔离 probe，而非冒称 VPS 全量回归。

曾因构建器选中系统 Python 的旧 setuptools 而打包失败；在安装前定位，显式使用既有发布环境 Python 后离线成功，未改 pyproject 或依赖。没有触发额外 Run Once、付费 canary 或旧失败重跑；新版本自然完整模型链、预算停止率、token 和限价收益仍待观察，发布不证明这些指标改善。旧模糊订单仍保留。

回退不可让 v4/quarantine 不兼容的旧 Paper reader 接管现库；普通代码回退和恢复旧数据是不同操作。备份/正式 wheel 已保留，本次 VPS 发布暂存目录、本地测试目录、发布 bundle/wheel 和新构建目录已清理；正式备份及发布 wheel 保留，历史其他任务产物未处理。
