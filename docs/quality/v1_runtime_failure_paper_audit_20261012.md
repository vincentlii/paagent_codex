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

## 2026-10-13 判断空间与指令负担审查（proposal-only）

基于运行提交 59605db 和本地文档收据 3191e46，审查当前 Headless pa_vpa_joint；不修改代码、Frozen Job、Schema、Router、预算、历史结果或 VPS 状态。既有 ICT 工作区改动保留。复用一名只读协作者核验 VPA/Joint，主审核验交接、Router 和反馈。下述静态/纯函数证据证明可达边界，不能证明已造成自然模型误判、少交易或收益损失。

### 1. 过早限制判断：部分存在于知识路由和权限边界

Provider Draft 的 market_hypotheses 包含 primary 和最多两项可为空的 alternatives，每项有 thesis、支持/挑战引用、确认与反证条件（pa_provider_contracts.py:142）。materializer 深拷贝这些字段，compile_pa_handoff 完整复制全部 alternatives；Joint 同时收到已验证 Stage 1、完整冻结价格/HTF 和报告。不存在将整份市场论证丢弃、只传一个枚举的行为。

具体限制：materialize_stage1_draft 仅把 alternatives[0].cycle_position 投影到旧 alternative_cycle_position（pa_provider_contracts.py:649）；route_strategy_files 读取该单字段，并为替代周期沿用 primary.direction（router.py:105-111），不直接遍历完整 alternatives。stage2 策略知识投影默认过滤与主方向相反的通道/Spike 文件（prompt_assembler.py:463-497）；VPS 对应配置未覆盖默认 false。Joint 不得改变 Stage 1/Router，逆向订单由 coherence hard reject；专业分歧可保留并合法等待。后者是当前明确的权限设计，不应未经授权当作 bug 放开。

纯函数复现：合法 Draft primary=normal_channel/bullish，alternative1=normal_channel/bearish，alternative2=trading_range/neutral；交接保留两项，但 Router 没有区间文件或空头通道文件。仅交换两项 alternatives 顺序，区间文件加入。集合与论证未变，第一项的旧投影却改变知识覆盖。这是多假设契约与旧单替代 Router 的覆盖不充分风险。

建议：先在独立候选版本中，让唯一 Router 按完整、已验证的 alternatives 的 cycle/direction 选择争议所需知识，区分诊断复核知识与执行授权；不得从自由文本推断、让 VPA 新建 Setup 或自动翻转交易方向。必须同时核对后续 opposite-file 过滤，单改 Router 不足。以相同冻结行情的旧/新版本、盲评和预算指标验证；保留旧 Job。允许后续角色改变主假设、重跑 Stage 1 或改 Gate 属于另一项架构/策略授权，当前不建议直接实现。

### 2. 补丁规则冲突：发现一处可达反馈矛盾及重复负担

coherence_checks.py:344 在主方向与订单相反时返回“同一快照必须回到阶段一重做方向诊断”；JsonValidator 将原信息加入 s2 invalid_fields（json_validator.py:944-950），build_retry_feedback 原样保留，同时禁止修改 Stage 1。当前 Joint Skill 明确不得重跑/改写 Stage 1，pointer repair 只处理授权字段，不做新市场判断（draft_pointer_repair/SKILL.md:8-18）。用 bullish Stage 1 与做空限价构造 coherence finding，再生成实际 retry feedback，确认两种指令同现。该矛盾不证明某次真实失败由此导致。

第一步建议仅修改反馈：说明“违反冻结方向权限；不得重跑/改写上游。仅在原指针授权和语义保护内修复，若需要新的市场判断则 unrepairable”。正常 Joint 分析阶段仍可在 Router 内合法等待；不得据修复文案让 repair 擅自改变 trade 为 wait、改价格或意图。方向门禁和失败分类保留。

当前 Runtime AGENTS 原文112行/17571 UTF-8 bytes，Worker 将全文写入每个角色工作区（codex_worker.py:771-779）。其中同时解释子 Agent 研究、旧/新交付、Stage 2停止特例、全部角色与兼容路径，Role Skill/role_protocol 又重复部分交付和输出要求。这是可确认的指令负担，字节数不是实测token，尚未证明对质量或耗时的净影响。历史产物和新合同显式分版本，不把所有条件化条款误判为同时生效的矛盾；本轮没有证实更多必须放宽的 hard check。

第二步建议按角色与冻结 policy 投影运行指令：公共 authority/因果/安全规则保留，Host 交付回执统一说明当前入口、输出模式、继承与停止条件，当前 Skill 只保留当前任务操作。off 子 Agent和旧协议说明留在 Host兼容实现/对应旧Frozen Job，避免新普通任务每次阅读。仍保留完整mandatory知识、案例和反证，不以压缩替代semantic parity；先逐条规则映射与契约/失败注入测试，再同快照评估。

### 3. 强迫产生结论：无硬性交易强迫，有轻度措辞压力

VPA Schema observations无minItems；validate_vpa_evidence 对空观察/无独立增量只记 warning（role_validator.py:949），每个假设恰好一项是覆盖要求，允许 neutral/insufficient_data。next_confirmation允许not_applicable/null；独立VPA无订单或概率字段。Joint非空support/challenge等字段可以说明未发现/不适用，空VPA引用符合Schema；当前Skill25已明确无相关证据不强求影响。前置无Entry Attempt仍用insufficient_data，不是数据不可用或禁止背景分析。

纯函数复核：完整同源catalog、compile_pa_handoff(stage1,None)、observations=[]、各假设neutral且空evidence_ids、next_confirmation=not_applicable/null，完整VPA Validator接受，仅有no-independent-finding/no-contextual-observations warnings。协作者亦确认Joint support/challenge等理由写“未发现相关依据”、VPA不改变方案和空引用通过Schema。因此不存在必须每位置有结论、必须supports/challenges、必须下一棒确认或每次影响订单的hard Gate。

残留压力：_VPA_ROLE_PROTOCOL（two_stage.py:194）要求Available data must add genuine information；Direct VPA Prompt（prompt_assembler.py:1276）要求“数据可用时至少给出一项信息增量”。数据可读并不保证有诊断性增量，这与合法neutral空报告的口径不完全一致。建议改为“存在有诊断性、可引用的量价证据时说明；否则明确未发现增量，允许neutral/insufficient及空观察”。保留完整读证据、反证搜索、引用和不足提示，避免‘无增量’成为未读资源的借口；不机械要求VPA改变入场。Joint可再说明不存在支持/挑战时直说，不必凑双边论据；等待时价格事项可说明不适用，无需放松Schema。

### 优先级与验证边界

先做VPA可用/诊断性区分和方向错误反馈一致性（仅指令/反馈，低权限风险）；再做当前角色指令去重；多假设知识覆盖作为需对照的独立候选。当前方向、Gate、价格几何、引用、单一materializer和执行权限继续保留。

本轮50项既有handoff/packet/role/Router非live测试通过；三个纯函数场景独立确认Router顺序影响、VPA中性空报告合法及矛盾反馈可达。VPS只读确认当前仍pa_vpa_joint/running，未开启全策略库；最近新成功报告同时存在neutral/supports背景及wait，不能以No Trade本身证明被约束或强迫。没有模型调用、付费重跑或策略质量实验；审查文档未commit，本轮临时测试目录已清理。

## 2026-10-13 前两项离线候选实施（未发布）

用户授权仅离线完成两项，沿用上节设计：方向错误反馈一致性，以及VPA数据可用/有诊断性增量区分。没有授权扩大Router、精简Runtime AGENTS、修改Schema、放宽校验、自动重跑或发布。本地基线3191e46，既有ICT与上节审查记录保留，不纳入其他任务修改。

### 实际改动

- coherence_checks 的方向冲突判定表达式不变，仅将“回到阶段一重做方向诊断”改为“违反冻结方向权限；不得重跑/改写Stage 1，仅在既有修复授权与语义保护内处理”。错误类别、数量、字段目标和原始Draft均不变。Direct反馈不要求Codex Patch Envelope，现有pointer Skill在需要新判断时仍unrepairable；不因文案授权改方向、三价或交易意图。
- Direct VPA Prompt、Codex _VPA_ROLE_PROTOCOL、当前VPA Skill和mandatory core知识原句同时条件化：完整读取必读后，有可引用诊断性证据则说明增量并评估反证；无增量明确说明，允许空observations和按实际证据neutral/insufficient_data。未读、工具失败和缺件不能伪装无增量。core chapter-009及所有知识anchor身份/顺序保留，其他教材/规则不删减。
- 复核发现core原文也有无条件“at least one finding”，新增完整Direct及Frozen Gateway knowledge交付测试先复现，再同步该句；避免只改Skill仍给模型相反指令。

### 自动化验收与限制

新增用例核验两个主方向的逆向订单仍产生同类拒绝、生成feedback不再重做上游且原字段目标集合不变；读取完整Direct/冻结Codex资源中无旧强制增量句；通过实际Gateway读取core正文；旧Job的Skill、role_protocol、知识及整个bundle逐字节不变。合法中性空报告、原supports/challenges报告均被完整Validator接受；unknown引用和缺少必读资源仍拒绝，空观察warning不删除。

首轮方向与Direct文案断言因原问题失败后通过；知识交付新增断言先复现残留旧句，修复后候选/知识12项通过。323项相关Prompt、Role、retry、Publisher、MCP、订单回归通过。最终Headless非live回归1752 passed / 9 deselected，耗时86.35s；Qt GUI排除，不调用真实Provider或行情。关键静态检查与diff检查通过。

一次长临时路径全量运行1750 passed、2 failed，失败路径262字符、父目录存在；短路径复核相同两项通过，确认Windows路径长度原因，不添加无关Worker补丁。最终回归使用短临时路径。外部tvDatafeed两项既有DeprecationWarning单独记录。

本地wheel构建通过，302个代码/资源与源码逐字节一致；Runtime asset audit通过，候选asset digest为826f32fa83b4b0d6a3627bf2b464dcea7deb5f57abfdd362111bb1a44b4910c5。该身份只用于后续新本地冻结任务，不修改已有Job；VPS仍是59605db/4ab1384c...的已发布身份。

三角色：PA方向与Router权属保留；VPA无新增Setup/Gate/概率且反证仍必要；软件层仅契约措辞修订、失败与审计不放宽。协作者审查了三个压力场景并指出core遗漏，随后因额度限制未完成第二次终审；主审核验完整修订及自动化，不把协作者失败记为通过。没有真实模型同快照对照、token/延迟或交易效果证据；本次只交付可验证候选，不宣称有效减少幻觉、中性偷懒或增加收益。不commit、不推送、不部署。

本轮临时测试目录、短路径复验目录和候选wheel已清理。自动安全审查拒绝包含build目录的递归删除，原因为该目录未明确属于本轮；采用安全替代只清理具名本轮目录，build保留。既有ICT修改和此前审查记录保留。当前源码/测试/验收文档作为本地未提交候选，生产发布身份不变。

## 2026-10-13 Runtime 指令分层与完整假设知识覆盖研究（未实施）

### 结论、目标与证据等级

两项都值得优化，但理由不同。Runtime 分层可以减少当前任务不适用的指令和多处维护的口径漂移；尚无证据证明17571 bytes的AGENTS是模型耗时或判断错误的主要原因。完整假设知识覆盖已有可复现的选择缺口，应优先建立确定性覆盖标准。增加知识访问不等于扩大下单权限。

本研究的设计目标是在现有authority、冻结方向/Gate、因果输入和有界预算内，让模型获得足够的竞争解释与反证，同时只承担当前角色的专业判断。该目标可以用于比较设计优劣；不存在脱离模型、行情与预算的通用最优Prompt。现有生产候选为gpt-6-luna/xhigh，不能将针对更强模型的简化经验直接当作它的效果证据。

本轮只读取源码、契约和第一手资料，并执行不调用模型、不落盘的纯函数探针；没有修改Runtime、Router、Schema、预算或执行行为，没有模型实验、commit或VPS操作。前节未发布的指令一致性候选及既有ICT改动保留。研究结论是后续设计依据，不是发布授权或效果验收。

### 完整数据流与实际缩窄点

| 位置 | 当前行为与权威源码 | 研究判断 |
| --- | --- | --- |
| 冻结输入 | Host冻结同源价格、HTF、量价目录、知识和运行身份；Codex只读当前Job（ADR-0024/0026/0027、publisher.py） | 保留。去掉该边界会改变任务本身，模型能力不能代替因果与来源保证 |
| Stage 1 | Draft保留primary及最多两项可为空的alternatives；每项含自己的cycle、direction、thesis、支持/挑战引用与确认/反证条件（pa_provider_contracts.py:100-149） | 表达能力已有，不先扩大Schema或强迫输出两项 |
| 物化/交接 | hypotheses深拷贝；旧alternative_cycle_position仅映第一项；compile_pa_handoff逐项保留全部替代解释（pa_provider_contracts.py:614-652；pa_handoff.py:254-390） | 第一项镜像可保留兼容，但不能充当完整假设集合 |
| PA知识选择 | 唯一Router只读主周期、主方向和旧单替代周期；替代周期沿用主方向，周期相同则不追加；后续PromptAssembler过滤主方向相反的通道/Spike文件（router.py:95-165；prompt_assembler.py:463-516） | 完整假设与知识覆盖不匹配；仅修Router会被下游过滤抵消 |
| VPA知识选择 | context只读取cycle_position和alternative_cycle_position，决定trend/range/breakout等模块（vpa_context_profile.py:44-86） | 第二项替代周期可能漏掉相应量价教材，需要一起处理 |
| VPA事实选择 | pa_window_fact_refs遍历全部alternatives，但仅收fact_refs；challenging_fact_refs中的独有窗口不参与vpa-w选择（vpa_measurements.py:1446-1501） | 知识范围之外还需检查反证窗口的实值覆盖；不补造目录中没有的测量 |
| 冻结/读取 | Publisher按allowed_knowledge_ids保存原文、锚点、顺序和digest；搜索仅遍历当前Job已选知识（publisher.py:152-189；knowledge_bundle.py:23-99；evidence.py:729-801） | 漏选的模块无法靠“模型自己搜索”获得；修正必须发生在冻结前 |
| 联合决策 | Codex Stage 2收到完整validated Stage 1、Router、完整handoff和accepted VPA；VPA逐假设覆盖校验。Joint不能改方向/Gate、重做上游或越权读取raw VPA（two_stage.py:1855-1869/2000-2043；role_validator.py:658-676；joint_stage2 Skill；ADR-0025） | 判断材料应充分，执行权限仍冻结。反证占优时允许有依据的等待，不据此反向下单 |
| 校验/执行 | 唯一materializer、Validator、Trader Equation、Risk与execution_disposition继续拥有确定性校验和执行权限 | 两项优化均不能另建判断器、Router或订单物化器 |

当前Headless Codex的Stage 2 context直接复制完整Stage 1；Direct的compact投影另有较窄字段集合。本方案研究当前Headless链，不能把Direct投影问题误报为当前Codex丢失假设；如后续共享知识选择有变，仍须单独验证Direct行为与身份。

纯函数探针通过Draft及canonical Schema并完成实际materialize/handoff；另经完整JsonValidator复核，两种顺序均返回Ok：primary=normal_channel/bullish，alternative1=normal_channel/bearish，alternative2=trading_range/neutral。两项都被handoff保留，但PA没有range或bear模块，VPA仅core、trend_pullback。只交换alternatives顺序，PA增加range两文件，VPA增加breakout_retest与trading_range_events。另将bear识别/交易文件手动加入选中列表，Stage 2主方向bullish过滤后两者均消失。上述证据证明覆盖机制，不证明自然模型已造成收益损失。

反证窗口探针中，primary、legacy alternative与所有alternatives的fact_refs仅PA.BAR.K1，challenging_fact_refs仅PA.WINDOW.K20。实际catalog存在vpa-w020，但pa_window_fact_refs为空，prompt_vpa_evidence_ids不包含vpa-w020。这只证明对应聚合窗口不可见，不表示全部K线、所有反证或VPA整体不可用。

### Runtime：最合适的是稳定公共边界加当前任务契约

当前AGENTS为112行/17571 UTF-8 bytes。Worker把全文作为model_instructions_file；single_frozen_agents_v1关闭project_doc自动重注入，因此不能把当前长文本误称为仍双份注入（codex_worker.py:1083-1101）。Worker已只安装当前一个Skill；确切Skill正文经packet/baseline再次交付，确保只读任务无需shell找文件。摘要元数据与冻结正文承担不同作用，不能仅按“出现两次”删除（codex_worker.py:771-789/1216-1239）。

建议先用以下所有权划分整顿正文，不建立通用规则引擎：

| 层 | 保留内容 | 不应承担的内容 |
| --- | --- | --- |
| 稳定公共Runtime AGENTS | Host authority；同一Frozen Job/cutoff；事实与解释区分；数据文本不是授权；禁止Web/文件写入/下单；单角色和会话隔离；完整必读原则；失败不伪装市场判断；当前角色契约优先于其他角色的操作步骤 | 研究child精确参数、所有历史交付布局、每个角色的完整停止JSON示例、逐个回执字段解释 |
| 当前Role Skill | 本角色目标、允许输入/输出、有限调查程序、证据使用、当前停止语义；必要的专业检查问题，不指定唯一市场答案 | 复制完整领域教材、另算Gate/概率/执行结果、重复解释其他角色工作流 |
| Frozen packet/baseline中的role_protocol与交付契约 | 本Job入口、fresh/delta、完整/已验证继承来源、输出模式、调查预算与有效mode；显式授权时交付研究扩展 | 要求模型重算digest、凭资源数量猜测丢包、比较不同scope的hash |
| Host/Gateway/Worker与输出Schema | digest/lineage、scope、allowlist、读取完成与顺序、预算、封套、未知policy拒绝；审计和失败分类 | 判断某个市场解释专业上唯一正确，替模型命名趋势/吸收/Setup |
| Frozen领域知识 | PA/VPA理论、适用条件、完整案例和反证，保持稳定章节身份 | 历史传输兼容规则、当前运行工具参数或下单权限授权 |

例如生产stage_subagents=off时，模型需要知道“不能启动挑战”，不需要AGENTS:33-39的8项resources、child模型、参数和提前互补分工全文。layered研究的详细读取策略只在显式研究Job交付。新Stage 2的停止/输出例外放在它的当前冻结契约中；旧Job仍使用自己已冻结的规则，不把旧兼容分支全部讲给新普通任务。保留关键边界的简短提醒；去重不是禁止纵深防御。

机械完整性目前已经主要由Host检查，优化是停止让模型重复猜测其实现细节，而非把更多业务判断移给代码。模型仍须阅读实际原文、使用合法引用、理解可用/不足/中性及市场等待；成功回执不能证明它已理解教材，也不能覆盖实际看到的缺值。工具故障由当前角色既有停止/Host拒收机制处理，不能删除该语义后要求模型凑Draft。

特别限制：Publisher续接要求完整backend_runtime_identity相等（publisher.py:373-435），Worker在同一PA工作目录替换当前Skill并受控resume。故首选**同一发布版本、同一PA会话内稳定公共AGENTS**，阶段差异继续经已冻结Role Skill/role_protocol交付。把role-specific digest放入严格比较的共同identity会拒绝续接；使用variants完整包的共同digest则不必然失败，但当前真实CLI探针仅验证两阶段同一AGENTS全文与正确delta，没有验证resume替换AGENTS后CLI采用新正文、旧正文还是两者（scripts/check_codex_delivery.py:280）。因此不把动态替换model_instructions_file作为第一步。若以后采用mode专属核心，两PA阶段须使用同一字节身份，并先扩展明确的冻结与兼容合同。换Skill也不会抹去旧会话内容，必须显式说明当前阶段的任务，而非假设旧指令消失。

可沿用现有两层身份：完整发布asset_digest/runtime_version覆盖公共AGENTS、所有基础Skills、条件扩展原文、rules与配置；每个Job的runtime.json/core_context资源hash再绑定实际选择和组装的Skill/protocol/marker，进入job_digest（runtime_assets.py:33-64；frozen_job.py:276/389）。Job构建前按marker确定性组装，不需新增MCP工具、平行规则引擎或将role摘要塞入backend_runtime_identity。所有新增片段必须纳入发布包digest，Gateway交付的role_skill仍须逐字等于job.runtime.skills。目前仅有承载接口，没有已实现这项条件装配；该建议不能写成现有能力。

实施前逐条建立“原条款→当前适用条件→新唯一所有者→模型仍看到的语义→Host验证→回归场景”的映射。没有承接位置的关键规则不得删除。以权限/失败语义覆盖为验收，不预定删掉多少行或宣称token降低比例。

### 知识路由：保留全部真实竞争解释，但区分理解与行动

建议仍由唯一Router及现有VPA知识selector完成选择；从validated的primary和全部alternatives读取显式枚举，不从thesis、价格指标或自然语言推断cycle、pattern与方向。现有最多两项alternative已是自然上界，暂不增加数量。空alternatives有效，不为“均衡”补造相反观点。

1. 保留既有主路径与授权规则，补充每项真实替代假设所需的识别、适用条件、反例和风险知识。每个替代使用自己的cycle/direction；同cycle不同direction也可能需不同识别教材。primary的spike_stage不能自动视为某个alternative的独立阶段；只使用已明确归属或共享的字段，不猜测缺失标签。
2. 将选择用途随现有manifest冻结，说明模块是当前执行依据、竞争解释复核，或两者共有；复核知识可用于挑战候选和决定等待，不因读到反向策略便获得反向订单权限。这是知识选择元数据，不是第二套路由、概率分数或额外市场Gate。初步候选保留现有执行权限，不改Stage 1/Gate/Router判断规则。
3. 后续PromptAssembler必须保留明确标记的复核知识，不能按primary.direction无差别删除。普通PA仍price-only；VPA知识不经这项扩展进入普通PA。VPA selector同时汇集完整假设集合的显式周期，加载所需trend/range/breakout模块；不能只修PA一路。
4. 同时核对VPA实值覆盖：支持和挑战的已声明、当前catalog可用的窗口都进入同一有限证据选择。引用缺失、聚合不存在或来源不可比时明确未知/不足，不能造新目录或从知识推回事实。改的是可见证据，不允许程序替VPA给出解释。
5. 冻结前形成完整选择集合、来源与purpose，stable dedup，共享模块只交付一次。原主路径装载顺序尽量保持，补充模块采用确定顺序。交换alternatives不应使同一语义集合的模块覆盖变化；保留各假设ID/顺序与摘要以追踪原始输出，不能宣称整个Job digest随换序不变。新增模块也不能删除原来必要模块。
6. 新选择的完整模块按当前semantic-parity方式交付；原锚点、案例、反证和mandatory不压缩。以后是否将纯复核策略章节按需交付，必须独立研究，不能顺带复用尚未晋级的layered策略。相关内容较多时，先准确测量新增交付与剩余预算，不静默截断或因模型自称熟悉而省略。
7. Joint比较主解释与影响当前候选的实质竞争解释。反证支持不足可保持原判断；实质挑战可影响入场位置、候选、等待及未校准主观先验。若继续交易必须改写冻结方向/Gate，则本轮有依据地等待，不直接选反向交易或自动重跑Stage 1。新方向由后续新冻结分析正常重新诊断。无需新增每个假设固定篇幅、打分表或“必须影响交易”的格式任务。

该设计修复“有假设但相关教材/反证窗口不可见”，并保持可审计行动边界。它不能消除Stage 1未想到某个解释的风险，也不能让当前Joint自由重选主方向。将诊断彻底延迟到联合决策、允许同快照改Gate/方向是另一项策略架构选择，不能借“扩大知识”偷偷实现。若要评估这种自由度，需要新的authority、协议及独立实验。

不建议直接打开全策略库：它会把缺口变成每次读全库，增加无关情景和预算压力。也不建议将所有替代教材只做可选索引：现有基线未证明模型可靠补读，且Frozen allowlist本身可能不含漏选模块。首个候选应补齐真实已声明的假设，不扩成整个可能性空间。

### 为什么在原理上成立，以及不能承诺什么

Brooks作者术语将context定义为支持或反对某Setup的历史背景，并区分趋势延续、区间演化和反转；相同局部动作需要背景判断，而不是由单一枚举替代全部推理。Wyckoff方法强调Effort/Result与对已有波段的比较。因此竞争解释、反证及相应原值是分析材料，交易方向仍可由产品另设明确权限。这里的路由设计是根据上述理论与本项目合同作出的工程推论，来源没有证明此改动会提高本产品收益。[Brooks术语表](https://www.brookstradingcourse.com/price-action-trading-terms-glossary/)、[Wyckoff方法](https://www.wyckoffanalytics.com/wyckoff-method/)。

OpenAI建议推理模型使用清楚直接的目标与约束；Skills采用渐进披露，把不适用的程序移出常驻上下文；同时明确指出有益于Sol/Luna的指导可能对Astra过度约束。因此应减少无关工作流与重复维护，保留本项目特有的因果、权限及必要专业材料，不能据“模型更强”删掉所有规则。[Reasoning best practices](https://developers.openai.com/api/docs/guides/reasoning-best-practices)、[Build skills](https://learn.chatgpt.com/docs/build-skills)、[Rethinking skills and prompts](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)。

官方准确性指南区分检索错误与模型拿到正确材料后仍使用错误；过多无关上下文也可能干扰。故“指令更少、相关证据更完整”在逻辑上相容，但追加模块会增加读取成本，不能把AGENTS节省预先抵扣为净token收益。实际最优需要分别量化检索覆盖、专业使用、权限错误与全链成本。[Optimizing LLM Accuracy](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy)。

### 推荐实施顺序和完整验收

1. **先冻结验收标准与当前基线。** 将本地尚未发布的文案修订视为共同基线或明确单独对照，不与两项研究混为一个处理效应。记录发布版本、模型、Prompt、预算、输入与全部失败；不调用模型也能先完成逐条规则映射和完整知识覆盖测试设计。
2. **Runtime分层单独做候选。** 先稳定common AGENTS、把不适用研究/旧协议细节移入显式冻结扩展，Role Skill与role_protocol对齐当前步骤；保持同快照PA续接与全部领域原文。不要先开发动态规则引擎或改身份比较。
3. **完整假设覆盖另做候选。** 一起检查唯一Router、方向过滤、VPA selector、窗口可见性、Publisher、manifest与读取回执；不能只把文件名列表变长。无需先改Provider hypothesis Schema。
4. **离线合同/失败注入先行。** Runtime覆盖fresh/resume、PA-only/Joint/VPA/repair、off/fixed/研究marker、缺件/denied/未知policy/错误封套/来源漂移、旧Job逐字节回放；knowledge覆盖0/1/2替代、换序、同cycle反向、第二项独有周期、重复模块、仅反证窗口、不存在的窗口、共享与独有理论。确认权限、现有主路径与Direct隔离/共享语义无意外变化。
5. **同模型同快照分开评估。** 先分别对比旧/分层指令、旧/完整路由，必要时才做2×2检验交互。运行前新cohort登记，保留独立重复、顺序平衡和失败/missing，不把同一行情重复当多张样本；不能冒用旧PA/VPA增量研究身份。覆盖趋势、区间、过渡、反转风险和真实订单/等待，盲评主替代解释、最强反证、结构入场/失效/TP1、VPA位置与引用。交易率、文字长度或某次获利不是质量标准。
6. **按质量与边界决定晋级。** 先看事实、权限、重大专业错误和基线覆盖，再看首稿/repair/超时、工具成本、input/cached/output/reasoning实际用量及全链p50/p95。若规则完整但模型更常跳必读/混角色，Runtime候选不晋级；若材料更全却预算失败或反向越权增加，调整交付/用途边界后重新独立验证，不能自动放宽安全。Paper效果另作前向观察，不能以Prompt实验冒充收益证明。发布前Host/Worker排空，旧Job不跨版本续接，不回写历史。

三角色设计判断：PA保留唯一Brooks框架、反证和冻结行动权限；VPA保留独立量价角色、同源实值与中性/不足，不新增Setup或投票Gate；软件/量化保留单一选择/物化/校验、因果与完整失败分母。本轮唯一只读协作者完成Runtime交付、同快照续接、两层冻结身份与Linux发布边界复核，并认可完整合法假设/反证覆盖不扩张authority；专业三角色仍是实现者自审，不是三位独立专家认证。本节未实施两项方案；探针不生成持久测试产物，现有build目录与其他任务文件未动。后续实现须按上述映射、合同与同快照评估独立验收。

## 2026-10-14 Runtime 分层与完整假设覆盖实施（仅本地）

用户授权完整实施上节方案并先备份再清理。完成标准是：当前角色规则和失败语义完整，
不交付未启用研究的详细步骤；全部真实替代假设的相关教材和现有反证窗口可见；
执行权限、完整教材、旧冻结契约及同快照 PA 续接保留。此前方向反馈/VPA增量文案候选
作为共同基线保留，既有 ICT 文件不修改。不调用模型、不更改运行数据、不 commit 或发布。

### 备份与可恢复清理

清理前将当时全部 tracked/non-ignored untracked 文件保存到
`docs/changelog/backups/20261014-runtime-knowledge-before/source.zip`，共683个文件、
2,755,645 bytes，SHA256为
`875045f5d4846e910ab9d6d3133818b7e917c548c624177ee4ebe11156aa09c6`。
同目录包含每个文件的 SHA256/长度及 branch/HEAD manifest、`working-tree.patch` 和原状态。
逐个ZIP条目验证hash，并确认四个既有ICT文件与备份逐字节一致。备份使用既有忽略目录保留，
不进入Runtime知识或发布资产；没有复制、删除或迁移凭据/数据库/订单状态。

恢复时按manifest取出需要的源码/文档，先比较当前差异再逐文件恢复；不得覆盖后续独立工作。
恢复源码不等于回退VPS或恢复运行数据，也不能给旧Job补标或跨版本续接。本轮只清理：

- 普通新任务常驻AGENTS/四个基础Skill中不适用的研究细节、历史交付解释和重复操作条款，
  按下表迁移；完整研究原文在条件资产内保留。
- 搜索确认无调用的Router `_ALL_VALID_FILES` 和PromptAssembler `_directional_channel_files`。
- 本轮具名测试/打包暂存目录。原有build、egg-info、其他任务暂存目录及正式备份保留。

旧兼容代码、旧Frozen Job、知识案例/反证/锚点和历史研究报告有重放/审计价值，不当作无用内容删除。
前文“未实施”是当日历史状态，本节记录当前本地实现，不改写历史证据。

### Runtime 规则承接与身份

| 原常驻内容 | 当前所有者与模型所见语义 | 校验/覆盖 |
| --- | --- | --- |
| authority、事实/解释、因果、price-only/独立VPA/显式Joint、只读、注入防护、中文、完整mandatory | 公共AGENTS保留；只执行当前冻结role/Skill/protocol | Runtime资源审计、原权限/因果/输出回归 |
| 每个角色的入口、输出封套及停止特例 | `guidance/role_contracts.toml`源文；Publisher确定性冻结当前`runtime_guidance`并经packet/baseline/repair交付 | 四角色正常/repair、错role/模式、未知policy、副本错配拒绝 |
| fresh/delta/完整原值与读取次序的详细操作 | 当前Role Skill及既有Host交付/Worker校验；公共层保留“索引不等于原值/继承须Host确认” | 真实Publisher→MCP必读、缺读拒绝、PA受控resume |
| Host challenge的精确resources、参数与互补程序 | `guidance/stage_challenge.md`保留原说明；仅agentic且原policy授权当前role时冻结交付，repair不带 | 授权角色/mode矩阵、缺扩展拒绝、既有研究/预算回归 |
| layered Stage2研究详细读取步骤 | `guidance/layered_stage2.md`保留原说明；仅已有显式研究marker，repair不带 | 实际研究baseline及缺扩展拒绝、正式全量必读回归 |
| 修复不能重做市场/上游，工具失败不转NoTrade | 公共简短原则＋当前输出合同/repair Skill | 原pointer/失败语义及此前方向反馈测试 |
| 完整领域知识、案例、反证、锚点与章节身份 | 现有知识原文/切片继续交付，不搬进Skill、不压缩 | 全原文/冻结资源比较、mandatory审计 |

新Job显式标记`runtime_guidance_policy=role_guidance_v1`。Gateway与Worker检查角色、入口、
输出模式、适用扩展及packet/repair副本一致；旧无marker Job保持原冻结文本，不用新版文案
重新比较/解释它。没有新增市场Gate、MCP工具或规则引擎。先调用现有入口，再按返回合同分析，
不要求模型先读取尚未取得的字段。

完整发布asset_digest包含公共AGENTS、所有基础Skills、三个条件资产源文、配置和rules；
Job资源hash/job_digest再绑定实际选择。角色差异不加入共同backend_runtime_identity，
同发布PA Stage1→Stage2的公共AGENTS逐字相同，既有续接条件不变。
当前asset digest为`f2957d56a69acef229425b46c93df4531a9ea177c4bdb93e57042492193643f3`。

公共AGENTS由17571减至5408 UTF-8 bytes；Stage1 Skill 5212→4581、Stage2 5782→5998、
Joint 7315→6435、VPA 5432→5065。Stage2为必要用途说明略增，不能宣称所有Skill都减少。
字节数不是Provider token，研究模式仍完整取得其详细说明。

### 全部真实假设与反证的交付

唯一Router新增`route_strategy_knowledge`返回有序选择元数据；原`route_strategy_files`继续返回
同一选择的文件列表。两者复用一套§11枚举规则。主路径不变，全部structured alternatives
各用自己的cycle/direction，不沿用主方向、不补spike_stage/patterns；补充模块确定排序并去重。
无structured alternatives的旧调用保留旧alternative_cycle_position及原文件顺序。

用途为`primary`、`review`、`primary_and_review`，同时保留来源hypothesis IDs。
Orchestrator、PromptAssembler和Publisher引用同一Router计划，Publisher核对实际Router列表中的
元数据；manifest、真实MCP mandatory_modules及Direct/Codex协议都交付用途/来源。
明确选中的review教材不被主方向过滤删除，Direct compact同时保留完整market_hypotheses。
`review`只复核竞争解释、风险/等待，不独立授予Setup、翻转订单方向或改Gate。

VPA知识selector覆盖全部显式周期；pa_window_fact_refs同时收集支持/挑战窗口，
prompt证据选择仅使用当前catalog已存在的窗口，不生成缺失聚合、不修改catalog身份。
所有新选教材仍是完整mandatory原文，不把本轮等同于尚未晋级的layered研究。

新增5项覆盖测试，包括合法同周期反方向/第二替代周期/换序、Direct完整交付、
VPA第二周期及挑战独有窗口/缺失窗口、实际Orchestrator→Joint请求→Publisher→MCP全链。
新增23项Runtime合同测试，涵盖正常/repair四角色、研究授权矩阵、fresh/resume、
缺读/缺扩展/未知policy/副本错配拒绝及历史无marker契约。原工具/Prompt合同测试改用
真实repair/fixed发布或显式历史fixture，不放宽生产检查。

### 验证与明确边界

已验证Runtime资源审计13项、critical Ruff及`git diff --check`。
离线构建wheel后用已有Python/pip安装到本轮隔离目录：306个代码/知识成员与当前源码逐字节一致，
三个条件资产完整、安装后13项资源审计通过、asset digest相同、Headless import不加载Qt。
没有新增依赖或改pyproject/lock；构建显式使用本轮TMP/TEMP和既有setuptools vendor wheel。

首轮沙箱内SDK测试阻塞在Python asyncio的Windows socketpair初始化，堆栈已定位；
允许本机回环的执行环境中真实SDK/序列化测试通过，不修改应用或SDK绕过测试。
扩大回归时另有一次既有HTTP安全测试出现WinError10053连接中断，单独复验通过，
未据此修改无关Dashboard代码。完整最终结果见下方验收补充。

三角色自审：PA保留完整背景/反证与唯一Router和冻结行动权限；VPA仍Evidence-first、
独立/可中性、不新建Setup/概率/Gate；软件层保留单一选择/物化、两层冻结身份、因果、失败和审计。
复用唯一只读协作者完成设计与两轮代码复核，最终未发现剩余阻断；其独立审查不冒充独立测试
或三位外部专家认证。CONTEXT、ADR-0024及知识库同步本地实现，ICT原文保留。

没有真实模型matched replay/盲评、token/延迟、Paper收益或Linux/VPS验收。增加相关知识可能
增加成本，不能将公共指令节省预先抵扣为净token收益；测试通过只证明契约及交付覆盖。
未commit、推送或部署，VPS仍为59605db/4ab1384c...。实际分析效果须另用新身份同快照对照，
发布前仍须排空Host/Worker并禁止跨版本恢复旧会话。

最终离线验收：1774 passed / 7 deselected，87.35s；既有tvDatafeed两条正则转义
DeprecationWarning保留，不属于本轮改动。范围为unit/integration/property的`not live`，
包含Headless、只读Dashboard、PA、VPA off/Shadow、Joint及A/A/B/B研究、HTF、订单数学、
records/audit和Runtime契约。按用户范围排除Qt图表/面板/线程GUI文件及Qt E2E，
不宣称全GUI或全平台CI通过。新28项测试计入上述总数，不重复相加。

可复验命令为项目Python运行`pytest tests/unit tests/integration tests/property -m "not live"
-q -o addopts= -p no:cacheprovider`，使用本轮独立basetemp；排除文件为
`test_chart_skip_redraw.py`、`test_chart_widget_no_lines_when_not_trading.py`、
`test_chart_fit_view.py`、`test_debug_widget_masks_key.py`、`test_decision_panel.py`、
`test_future_trend_panel.py`、`test_gui_worker_lifecycle.py`、`test_order_opportunity.py`、
`test_overlay_lines.py`、`test_support_resistance_chart.py`、`test_thread_lifecycle.py`、
`test_token_indicator_thresholds.py`（unit），`test_next_bar_prediction.py`、
`test_switch_mid_analysis.py`（integration）及`test_next_bar_prediction_perf.py`（property）。
使用允许本机回环通信的环境，不调用外部行情、模型或交易所。

## 2026-10-14 旧快照单次配对试验（已登记，模型调用待审批）

用户要求优化前后各一次对照。采用已保存的2026-10-09 XAUUSDT/1h 100根与4h 60根，
原`as_of_ms=1791476486366`、market hash
`a32b6b37181924e7caadce91c3137218c8c4e8eab30806cd2edc9f71b0fcaf70`。
旧快照完整性、闭盘、HTF及原VPA catalog校验通过，不更新价格或混入旧模型结论。

对照版本取本轮实施前source.zip（包含此前离线文案候选），处理版本取当前源码固定副本。
两边完整知识原文digest相同；实际选择是处理的一部分。共同CLI为0.162.0-alpha.17.2，
固定gpt-6-luna/xhigh、标准tier、pa_vpa_joint、agentic、stage_subagents=off。
默认Publisher预算分配函数AST相同：Stage1 300s、VPA 180s、Stage2 300s、repair 180s，
整链共享600s；字节/调用预算沿共同材料量分配规则，实际值须随Job记录，不能宣称均数值相等。

实验新身份`runtime_knowledge_single_pair_v1`及两次assignment在调用前已固定，
顺序before→after由原evidence SHA256前8位奇偶决定。独立源码/import/进程/home/jobs/outbox/state，
关闭Paper、monitoring、experience和fallback，不延长旧Job expiry。新Job TTL使用当前墙钟，
市场仍保持旧cutoff/as-of，结果不能用来执行当前订单。每臂一个完整fresh链、只允许既有一次
定向repair，失败保留不重跑；单样本及合并处理不估非劣、方差、各改动独立作用或收益。

原件及协议保存于既有忽略目录
`docs/changelog/experiments/20261014-runtime-knowledge-pair-01/`，protocol digest
`55bc5d37d1a3f0a2fab45775890b254b39d2241a336ff655666e3653414d82eb`。
预检两臂登录状态healthy、输入/知识/CLI/settings一致；截至此节没有start、role checkpoint
或Host输出，模型调用数为0。自动审批拒绝首次真实调用，要求明确授权该行情/知识/指令包发送
到OpenAI Codex服务；已请求该具体授权，待答复。该拒绝不是模型运行失败，不登记为已执行arm。

## 2026-10-14 旧快照单次配对结果（两臂已完成，未发布）

用户随后明确回复“允许按上述范围发送并执行”，两臂按原登记before→after执行完毕。
期间用户中断过工具调用；先核查两臂均无start/role checkpoint再启动，因此没有重复模型运行。
没有改预算或输入、没有失败重跑。每臂3次首稿调用均validated，0 repair、0 fallback、
0 Evidence denied。Host均为`completed_with_warnings`、`official_path=joint`，最终`wait`。
没有Paper数据库或订单创建，不操作VPS；代码未commit/推送。

### 本次实际指标

| 指标 | 实施前备份版本 | 优化后固定副本 | 观察差异 |
| --- | ---: | ---: | ---: |
| Host全链秒数 | 233.891 | 284.672 | +21.71% |
| Provider实报总token | 526158 | 514223 | -2.27% |
| 输入token | 500281 | 483673 | -3.32% |
| 缓存输入token（输入子集） | 235776 | 222720 | -5.54% |
| 非缓存输入token | 264505 | 260953 | -1.34% |
| completion/output token | 25877 | 30550 | +18.06% |
| 正式角色调用/repair | 3 / 0 | 3 / 0 | 相同 |
| Warning finding数 | 2（均VPA） | 2（Stage1及VPA各1） | 不可据此判质量改善 |
| 无语义normalization事件 | 2 | 1 | 不是repair或质量评分 |
| Host Evidence访问/真实MCP调用 | 20 / 3 | 20 / 3 | 相同 |
| Evidence传输bytes | 430087 | 430271 | +184 |

| 阶段 | 旧秒数 | 新秒数 | 旧token | 新token |
| --- | ---: | ---: | ---: | ---: |
| Stage1 | 107.609 | 152.890 | 118211 | 112519 |
| VPA | 59.672 | 55.890 | 65587 | 55577 |
| Joint Stage2 | 65.812 | 75.125 | 342360 | 346127 |

用量来自每次Worker的Provider计数和Host版本化metrics，均`usage_coverage_complete=true`；
每臂使用两个独立会话：PA Stage1→Joint Stage2在本臂同快照受控续接，VPA独立；
Stage2用量先扣除已记录的Stage1会话累计基线，再汇总增量，不重复计数或跨臂续接。总token包含输入/输出，
cached是输入子集不能再次相加；completion未单列reasoning，不据此推断最终文本长度。
美元费用3次均unknown，不能把token比例当账单节省比例。更短公共指令只减少一部分上下文；
Stage2仍占主要token，阶段推理、缓存及服务延迟的随机差异不能由单次拆分归因。
此次不存在统计p50/p95、置信区间、质量非劣或总体成本效果结论。

两臂record重算market hash与原登记相同；原evidence文件SHA256未变，代码指纹从预检到
运行结束未变，实际各call均gpt-6-luna/xhigh。Stage2及VPA实际选中模块的ID、sha256、字节数
也完全相同，完整mandatory交付成功；新用途/来源标记实际冻结并经基线送达。

### 专业内容与实际覆盖

两臂主假设同为`trading_range/neutral`，均只声明一项`trending_tr/bearish`替代解释。
共同判断K1有近端空压，但未获得区间外接受及之后闭盘跟随；都保留HTF偏空作为风险而非Gate。
Stage1置信度66→64属于主观未校准判断，不作为质量评分或效果指标。

旧VPA以宽/中/短窗口分歧给主假设neutral、偏空备选supports；新VPA以中期推进减弱给
主假设supports、偏空备选neutral，同时保留K2→K1卖压。两种解释都有同一目录证据，
不能仅因relation枚举不同断言一方正确。Joint两臂均使用上述反证选择等待、不造入场/止损/TP1/胜率。
新Joint明确“没有Entry Attempt的insufficient_data不代表背景/位置数据不可用”；旧Joint对此
表述较含混，这是可观察的说明差异，不是交易质量已提高。

本快照没有产生第二项替代周期或需要额外通道/反向教材的组合；旧新Router实际均选择
区间识别、区间策略、铁丝网3个文件，VPA教材也一致。因此真实测试没有触发“额外教材补齐”
这一分支，不能用它宣称完整假设路由已提升模型判断；该机制覆盖证据仍是前节离线合同测试。
支持/挑战所需已存在窗口没有造成不同的VPA教材目录或新增可选调查。

### 警告及保留问题

- 旧VPA两条warning涉及将窗口记号/数值写进解释及同句多selector的比较绑定；不升级为
  已证实价格错误。旧模型把不存在Entry Attempt称为“入场时机数据不足”，仍合法但解释较弱。
- 新Stage1的“EMA slope number”warning由宽文本数字匹配触发；原句没有报告具体EMA差值，
  包含“4小时”、EMA20及HTF.H#引用。冻结HTF的EMA delta5=-7.078607、delta10=-19.740034，
  “整体走低”的方向与事实相符；此warning不是已证明的EMA数字造假，不在实验中修改checker。
- 新VPA的“unbounded new-high/new-low”warning来自“低点仍高于此前区间低点”这样的范围措辞。
  原vpa-e001已有prior5/prior20比较，K1低点4115.79高于prior5低点4114.64及prior20低点4106.39；
  方向有依据，但正文可明确窗口，避免“此前区间”模糊。它没有证明模型报错方向或数据。
- 新Stage1原Draft的diagnosis_confidence_reasoning末尾含`הש代谢`杂字，Stage2依法完整继承。
  原始outbox、Stage1及Stage2均可核验，不是normalizer拼接或编码修复产生。它是本次真实输出
  的文案质量缺陷，保留原件不洗掉；不能因为交易结果相同就说新版本零质量问题。

结论：新版本完整真实链可运行，本样本总token略降但耗时更长，专业质量未证明优于旧版。
保留确定性的知识覆盖/权限修复理由，不宣称性能或收益改善，不据一次杂字/warning继续堆Prompt规则。
原始证据、两个固定源码、jobs/outbox/checkpoint/record、注册协议和comparison.json保存在上述
隔离实验目录；辅助run_arm.py/compare.py为本次一次性驱动，不是生产功能或新研究框架。
模型调用结束后删除两份本实验复制的auth.json，原独立凭据home不改，正式研究证据保留。

唯一只读协作者复核后确认两臂全部市场对象（含完整VPA catalog）与original-evidence逐对象
相同，支持上述Warning分类及杂字来自原Draft的判断。实际VPA窗口引用由旧K8换为新K10，
均来自各自Stage1的支持引用，不能归因于新增challenging引用覆盖。
此次复核是基于产物的专业/事实核对，不是独立盲评分、重复模型试验或统计认证。

## 2026-10-14 授权提交与发布准备（SSH授权待确认，VPS未改）

用户明确要求最近优化commit并发布。运行候选已提交为
`e9f40d1604b187556bbd15ac3b84c0440c89a68d`并推送私有`vincentlii/paagent_codex`的main，
共38文件；四个既有ICT文件仍在工作区，未纳入。修正了上述配对会话说明：Stage2采用本臂
PA受控续接并按增量计用量，不是三个fresh session；原指标数值没有改变。

发布前重新执行Headless非live回归：1774 passed / 7 deselected，118.87s，critical Ruff及diff
检查通过。唯一只读协作者复核未发现代码发布阻断；仍需VPS排空、一致备份及Linux安装验收。
从固定提交Git archive构建wheel并隔离安装，306个包内代码/知识成员与提交逐字节相同，
13项Runtime审计通过、Headless无Qt，asset仍为f2957d56...。既有GUI dark.qss不在当前wheel
打包规则内，不属于本轮新增遗漏或Headless资源缺失，GUI未验。依赖/pyproject/lock未变。

固定源码archive SHA256为
`1521a4e30e9084a5e2c4d52304d87a22598043c292c82342f02ad676574e96bd`；本地wheel SHA256为
`edcf6ff1a97a43cb736b6044a5eb389ea025bcd961a88916e12bd232cf55e0cf`。
以历史部署59605db为前提的增量bundle verify通过。它只是已准备的交付包，尚未读取或更新
VPS checkout、安装环境或运行身份；服务器当前提交不能用历史记录冒充刚刚核验。

SSH预检遇到两份同名专用密钥的公钥不同、旧文件Windows访问权限及加密口令限制；
原密钥未修改，暂存副本已经删除，没有输出私钥或口令。尝试使用另一个既有、名称属于
其他项目的密钥时被自动审批拒绝，理由是用户未明确授权该凭据来源；没有执行该SSH命令。
已向用户请求使用指定既有密钥、仅发布PA Agent的明确授权，或提供专用密钥解锁方式。

截至本节VPS没有暂停、停止、备份、覆盖安装或恢复调度操作，没有额外模型调用。
当前提交/推送完成，VPS发布待连接授权；不报告已上线。固定archive、bundle、wheel及本地
验收收据保存在`docs/changelog/releases/20261014-runtime-knowledge/`；具名测试、构建/安装
暂存及临时SSH副本已清理，正式源码备份和原始配对研究证据保留。

## 2026-10-14 固定提交VPS发布完成

前节SSH阻塞随后获得用户明确授权：使用指定既有密钥，仅发布PA Agent及核验其服务。
未读取或修改其他项目，没有输出私钥、口令或认证内容。实际连接用户为ubuntu，限定PA Agent
路径的部署操作使用sudo；原专用密钥未修改。运行代码固定为
`e9f40d1604b187556bbd15ac3b84c0440c89a68d`，后续文档收据提交不改变运行代码身份。

### 发布、备份与一致性

VPS发布前HEAD为59605db351c143bced2db65756ffa5a780c0ab98且干净，控制generation39、running。
暂停到40，核验Host queued/running/unfinished均0、Worker inbox为空、无in_flight/恢复阻塞，
再停止Host、Codex Worker和Dashboard三个服务。执行原备份服务并验证
`/var/backups/pa-agent/pa-agent-20261014T065901Z`：manifest含12887文件，三份SQLite quick_check为ok。
保留配置、认证、Frozen Job、失败回执与历史账本，未迁移或回填数据。

通过本地已核验的增量Git bundle传递固定提交；确认pyproject.toml/uv.lock无变化后，在VPS离线
构建匹配新旧提交的wheel，保存两份回退/发布包，再fast-forward源码并离线reinstall、no-deps。
安装前后三份数据库逐表行数/内容hash相同，426个配置/认证/待交付文件hash及48项依赖相同，
pip check通过。该数据库一致性结论限定停服安装窗口；恢复运行后Paper正常推进不受此断言限制。

Linux包SHA256为`4a46959b681705e27640c12090fb9bcacdec3e52749f6f7ad19ec029b0b283ef`；
旧包为`c6cc5d7c0769affcf8625dd0fb378635056d6065c6ba7eaed759c63e6dd68256`。
Windows Git archive受core.eol影响使用CRLF，不能直接与Linux包按字节比较；改用显式
core.autocrlf=false、core.eol=lf生成的固定归档，SHA256为
`8c1640a1cb9da94d92e1e3f05372dd09330b4f24881e57884e20fa2edbdce4ea`。
安装后的306包成员与Git LF原文逐字节相同；既有GUI dark.qss排除仍属原打包规则，未改业务代码。

### Linux安装与恢复核验

全新安装进程从site-packages导入，执行14项离线检查：四角色normal与repair资源/拒绝边界、
同快照PA续接的共同指令身份、显式challenge/layered研究扩展、未标记旧Frozen Job、全部替代
假设的复核教材与交易权限、VPA全部周期及已有挑战窗口。全部通过，model_calls=0、
production_database_reads=0、paper_writes=0；这是部署辅助probe，不是新增生产功能或全量Linux测试。
13项Runtime资源installed/source审计与POSIX只读权限检查通过，issues为空；Headless导入无Qt。

先在暂停状态启动三个服务，核验新Runtime digest
`f2957d56a69acef229425b46c93df4531a9ea177c4bdb93e57042492193643f3`、Worker auth healthy，
再恢复原running到generation41。最后核验三个服务均active/running、Result=success、NRestarts=0，
XAUUSDT/BTCUSDT/ETHUSDT均连接、无恢复阻塞；原Profile fingerprint
`31fe1647d1396aaf04788ddce793a4d04ac7845f9b84581a6fd6ea00e88323f1`、codex_only、
gpt-6-luna/xhigh、Fast、aggressive、并发3、1h/4h及Run Once generation5保留。
CLI digest仍为61b0194f...；只读HTTP healthz/status/trading均200，未认证status拒绝401。
三个ExecStartPre部署检查随服务启动通过；未额外Run Once、模型canary或订单实验。
服务重启会重置内存scheduler计数，历史数据库并未清空。

正式收据、新旧wheel、bundle及离线核验脚本保存在PA Agent专属
`/var/backups/pa-agent/releases/runtime-knowledge-e9f40d1-joDYel/`，脱敏收据也保存在本地既有
`docs/changelog/releases/20261014-runtime-knowledge/vps-receipts/`。发布暂存目录在收据取回后清理；
四个既有ICT文件保持备份时字节，未commit或部署。保留原源码备份、历史研究与执行记录。

结论：提交、推送、VPS安装与原授权Paper调度恢复完成。本地Headless非live1774 passed /
7 deselected与Linux14项安装probe分开报告；全量Linux/GUI CI未跑，新Runtime的自然模型链及
长期质量/性能/收益仍待观察。发布成功不修改此前单快照配对的负面耗时证据或质量不确定性。
