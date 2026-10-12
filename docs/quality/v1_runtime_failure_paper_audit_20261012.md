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
