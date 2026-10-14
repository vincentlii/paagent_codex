# OKX Demo Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 用户限制最多一名 subagent；推荐主 Agent 直接执行，复用唯一只读审阅者，不为每步新建 Agent。

**Goal:** 将 PA Agent 已验证正式分析接到 OKX 模拟盘，每笔目标名义金额500 USDT，保持原 stop/TP1，启用后交易概览以模拟盘为主。

**Architecture:** Host 仅交付来自已持久化正式分析的不可变执行意图；独立 Demo Worker 持有模拟Key、管理一份独立SQLite账本、普通限价与保护单，并持续REST对账。Worker发布脱敏JSON，Host合并原分析/旧Paper历史后供Dashboard只读展示。

**Tech Stack:** Python 3.11+、现有 pydantic/httpx/Decimal/sqlite3、pytest、现有原子JSON文件和systemd；不新增SDK、WebSocket、消息队列或依赖。

**Spec:** [OKX模拟盘接入设计](../specs/2026-10-15-okx-demo-execution-design.md)，执行前必须完整读取。状态：2026-10-15用户已批准主 Agent 直接实施、最多一名只读审阅者。实际实现及验收范围见[验收记录](../../quality/okx_demo_execution_acceptance_20261015.md)，以下复选框保留原计划，不代表发布许可。

## Global Constraints

- 每笔目标订单总价值500 USDT，非保证金；杠杆不放大名义金额；Decimal按lotSz向下取整，不超过500，不因stop距离/权益/置信度缩量。
- entry/stop/TP1逐值保留；目标tick不兼容整单拒绝，实际费用/成交价单独记账。TP1全平，不执行TP2/移动止损。
- 首版仅BTC-USDT-SWAP普通LIMIT；ETH/XAU预留显式已讨论映射但默认不启用；market/breakout明确拒绝，不转单。
- net、逐仓、建议交易所杠杆3倍，每品种单计划/持仓；不得修改未知持仓、账户模式或自动补发历史。
- REST请求只使用固定官方Global域名 `https://openapi.okx.com`，强制模拟标记与TLS；实际账户若要求其他地区域名，拒绝预检并另行确认，不猜测切换。
- Worker默认禁止入场；只有原正式资格、持久化/audit_ok、cutover代次、时效、账户/资金检查全部满足才可提交。
- 保留Paper旧账、研究账户、原Frozen Job/market hash、模型/Router/Validator权限；不修改四个既有ICT文件。
- 交易页启用后默认OKX Demo，不混合Paper；失联显示陈旧/未知，不回退；分析复盘仍来自原records。
- 不访问网页Key详情，不读聊天凭据；本地测试零模型/零私有请求；VPS配置、受控模拟订单、发布分别报告并在执行前核对用户授权。
- 本任务不自动commit/push；每任务保留独立提交边界。后续用户要求commit时按验收Phase提交，不纳入既有ICT改动。

## Review Focus

1. Host在线但Worker/账户已陈旧：主视图不得显示“实时”或改用Paper（Task6）。
2. OCO触发后父入场晚成交：重新识别暴露，父单未终态不得closed/开新单（Task4）。
3. 旧NoTrade乱序或切换时旧分析刚完成：不能撤更新单/补发历史（Task3、5）。
4. 下单前后DB写失败、重复进程和消费回执前崩溃：不重复释放500 USDT意图（Task3、4）。
5. Demo余额重置/资金流及重复费用账单：不能伪造净收益或双扣费（Task6）。

## 文件及职责

| 新增文件 | 职责 |
| --- | --- |
| `pa_agent/execution/demo_contracts.py` | 非敏感运行配置、严格意图/回执/账户快照模型、域内ID和Decimal值语义。 |
| `pa_agent/execution/demo_plans.py` | 原正式资格到只读Demo意图的编译、双身份绑定与固定金额张数。 |
| `pa_agent/execution/okx_demo.py` | 固定Demo REST签名、接口响应语义、数据白名单及错误分类。 |
| `pa_agent/execution/demo_store.py` | 仅新库：幂等、资金占用、状态/成交/账单及消费水位。 |
| `pa_agent/execution/demo_spool.py` | 原子有界交接、消费回执和损坏文件隔离，不含网络逻辑。 |
| `pa_agent/execution/demo_worker.py` | 账户启动对账、执行状态机、持续保护/撤单/异常恢复、Worker CLI。 |
| `pa_agent/execution/demo_dispatch.py` | Headless唯一正式执行选择、cutover、旧Paper观察、失败交接恢复。 |
| `pa_agent/operations/demo_trading.py` | Worker的脱敏Demo投影、Host组合与Dashboard字段白名单。 |
| `deploy/systemd/pa-agent-demo-worker.service` | 独立执行用户、凭据与写目录限制。 |
| `deploy/systemd/pa-agent-demo-backup.service` | 同执行用户对新库/回执作一致性备份，排除凭据。 |
| `deploy/systemd/pa-agent-demo.env.example` | 只含非敏感参数、默认不入场。 |
| `docs/quality/okx_demo_execution_acceptance_20261015.md` | 代码/测试/部署/真实模拟验收范围逐层区分。 |

保留既有`plans.py`的公开Paper接口；只抽取重复的确定性正式资格检查，不能另做分析materializer。
`headless.py`/`monitoring/headless.py`仅增加dispatcher与投影接缝；不重构模型编排。
不复制整份`trading.py`；复用现有分析投影和Paper历史，再组合Demo白名单数据。

## 共同接口（Task1定义，后续任务直接使用）

- `DemoConfig`：Pydantic冻结模型，`enabled=False`、`entry_enabled=False`、
  `execution_target='paper'`、`notional_usdt=Decimal('500')`、`leverage=Decimal('3')`、
  `symbols=('BTC-USDT-SWAP',)`、`cutover_id`与`cutover_started_at_ms`。配置是显式服务配置，
  不新增Dashboard风险编辑。notional首版只接受500，其他值必须显式升级配置协议。
- `DemoPaths`：由`resolve_demo_paths(paths: RuntimePaths, *, environ: Mapping[str,str])`产生，
  包含`root/intents/receipts/state_db/projection/control`；默认`paths.state_root/'execution'`，
  部署覆盖`PA_AGENT_EXECUTION_HOME=/var/lib/pa-agent-execution`。Worker只有专属路径，不可读records。
- `DemoIntent`：`schema_version='okx_demo_intent_v1'`、代次、task/run、pipeline、源身份/spec、
  market/record digest、源K1时间、生成/新鲜度/绝对到期、`disposition='limit'|'no_trade'|'rejected'`、
  原始Decimal字符串entry/stop/tp1、source_reason与内容digest；无候选时三价格为null。完整意图
  `model_dump(mode='json')`经规范JSON `sort_keys/allow_nan=False`计算内容hash，hash字段自身不入hash。
- `DemoContract`：从目标账户可交易规格生成：instId、linear/USDT、tick、lot/min/maxLmtSz/maxMktSz、
  ctVal/ctMult、观察时间与digest。不得把同名public合约当作账户一定可交易。
- `DemoOrderRequest`：严格固定环境/account/instId/side、clOrdId、`px/sz`字符串、原stop/tp1、
  `exp_time_ms`；只有LIMIT entry，退出单由Worker按明示保护政策生成，不供模型输出。
- `DemoSnapshot`：含`observed_at_ms/server_time_ms/account_key/account_mode/position_mode`、
  USDT equity/available、已核验contracts、ordinary orders/algos/positions/fills/bills、分页coverage；
  字段白名单、Decimal字符串与明确unknown，不保存原HTTP头/credential/完整原始错误。
- Host专属`FrozenAnalysisPayload`增加默认null的`execution_target/execution_epoch`，旧编码省略；
  codec持久恢复原代次，不进入Codex FrozenAgentJob/Prompt/行情hash。交接携带原task.snapshot_hash。
- 账户指纹由只读私有预检取得并在配置与新账本中双重固定。共享文件原子替换前显式0640；
  它不能只依赖Host的0077 umask或目录setgid。触发子订单用`ordIdList`持久化、逐项核对终态。
- `DemoReceipt`：意图digest、task/cutover、处置/拒绝码/受理时间，独立于订单最终成交；
  `DemoAccountProjection`只包含第6任务定义的有限JSON，不能把SQLite行原样输出。

### Task1：正式意图、双身份与固定500 USDT张数

**Files:** Create `demo_contracts.py`, `demo_plans.py`；Modify `execution/plans.py`必要的纯资格提取；Test `tests/unit/test_demo_plans.py`、既有`test_execution_plans.py`。

**Interfaces:**
- Produces共同模型；`compile_demo_intent(record: object, source_spec: InstrumentSpec, *, task_id: str, config: DemoConfig, frozen_execution_epoch: str, now_ms: int) -> DemoIntent`。
- Produces `bind_demo_limit(intent: DemoIntent, contract: DemoContract, *, account_key: str, available_margin: Decimal) -> DemoOrderRequest`；不发请求、不写DB。

- [ ] 写失败测试 `test_fixed_500_is_not_margin_or_stop_risk`：等量500、price=50000、每张0.01BTC、lot=0.01时sz='1'；改变stop或equity不改sz，leverage3不产生1500名义金额。
- [ ] 补测试：非整步向下取整、小于minSz拒绝不超配、非有限/负值、tick不兼容不改价、CT乘数、市场/突破拒绝、TP1全平不带TP2、源spec/身份hash匹配、研究/失败/未持久化资格拒绝、NoTrade不丢。
- [ ] 运行 `python -B -m pytest tests/unit/test_demo_plans.py -m 'not live' -o addopts= -p no:cacheprovider`，确认因缺少实现失败。
- [ ] 用最小Decimal公式实现；共享原正式资格/时效校验，保留原Paper入口参数/返回类型。Host持久化/audit_ok仍由现有调用位置保证，编译器不虚构其独立审计能力。
- [ ] 运行新测试和`test_execution_plans.py`，检查原record逐字段不变。记录独立验收/提交边界，不自动commit。

### Task2：只允许模拟盘的OKX REST客户端

**Files:** Create `okx_demo.py`；Test `tests/unit/test_okx_demo_client.py`。

**Interfaces:**
- `OkxDemoCredentials`私有内存对象，三个secret字段不参与repr；`load_demo_credentials(directory: Path) -> OkxDemoCredentials`只从显式systemd credential目录读取，不从模型/网页/通用settings寻找。
- `OkxDemoClient(credentials, *, transport: httpx.BaseTransport, now_ms: Callable[[],int])`。
- Methods: `snapshot(*, since_bill_id: str | None, since_fill_ids: Mapping[str,str]) -> DemoSnapshot`、
  `place_limit(request: DemoOrderRequest) -> dict[str,str]`、`get_order(inst_id: str, client_id: str) -> dict[str,str]`、
  `cancel_order(inst_id: str, client_id: str) -> dict[str,str]`、
  `place_oco(*, inst_id: str, side: str, stop: Decimal, tp1: Decimal, client_id: str) -> dict[str,str]`、
  `get_algo(inst_id: str, client_id: str) -> dict[str,str]`、`cancel_algo(inst_id: str, algo_id: str) -> dict[str,str]`、
  `close_position(*, inst_id: str, side: str, quantity: Decimal, client_id: str) -> dict[str,str]`、`close() -> None`。
- Typed exceptions `DemoRequestRejected(code: str)`, `DemoRequestUnknown(operation: str)`, `DemoUnavailable(code: str)`；不携带原响应正文/签名头。

- [ ] 写失败测试：固定HTTPS域名、禁止重定向、每次私有请求模拟标记、ISO毫秒时钟/HMAC待签名串（含GET query与POST实际JSON一致）、凭据/认证错误不泄露。
- [ ] 补 `test_http_200_business_error_is_not_acceptance`、超时/5xx/无效JSON为unknown、缺订单ID不能submitted、撤单sCode=0只是cancel_pending；分页去重和coverage缺口保留。
- [ ] 运行 `test_okx_demo_client.py`确认红；实现现有httpx注入客户端，timeout=5秒、禁隐式POST重试，使用交换时间校验和显式expTime，不关闭TLS。
- [ ] snapshot覆盖public/time、account/config、account/instruments、balance、positions、普通/条件未完成及历史/成交/账单；先列出账户范围再按本项目白名单消费，不能忽略外部敞口。
- [ ] OCO严格使用net/reduceOnly/closeFraction=1、last、原价和市价退出；单个conditional不得同时冒充TP+SL。緊急close使用可核验实际数量/reduceOnly。运行测试确认通过；无实际请求。

### Task3：持久幂等、资金预约和有界文件交接

**Files:** Create `demo_store.py`, `demo_spool.py`；Test `tests/unit/test_demo_store.py`, `tests/unit/test_demo_spool.py`。

**Interfaces:**
- `DemoStore(db_path: Path)`拥有schema初始化和SQLite事务，`register_intent(intent: DemoIntent) -> DemoReceipt | None`（None表示新登记）、`save_prepared(intent: DemoIntent, request: DemoOrderRequest) -> None`、
  `prepare_leg(intent_id: str, leg: str, params: Mapping[str,object], *, generation: int) -> str`（返回客户ID并先持久化请求语义）、
  `record_observation(intent_id: str, kind: str, payload: Mapping[str,object], *, observed_at_ms: int) -> None`、
  `pending_intents() -> tuple[DemoIntent,...]`、`apply_snapshot(snapshot: DemoSnapshot) -> None`、`projection_rows(*, now_ms: int) -> dict[str,object]`。
- `DemoSpool(paths: DemoPaths, *, capacity: int=64)`，`publish(intent: DemoIntent) -> None`、`pending() -> tuple[DemoIntent,...]`、`ack(receipt: DemoReceipt) -> None`、`collect_acknowledged(*, before_ms: int) -> int`。
- 永久客户ID映射与原语义hash同表唯一；entry/oco/emergency三个leg及其持久generation分别hash，客户ID为'pad'+SHA256前29位，碰撞语义不符拒绝。entry generation恒1；同一保护/平仓请求未知时复用其generation只查不重发，只有前代终态已确认且出现真实新暴露才登记下一代，避免latefill需要新OCO却复用已消耗ID。

- [ ] 写失败测试：并行register一次、同ID不同digest拒绝、64项满显式失败且不丢未确认、payload坏digest/符号链接拒绝、回执前后崩溃重读仍只一份意图；文件最多256KiB。
- [ ] 写状态/资金测试：部分成交把预约转成持仓占用不重复加，unknown保留最大可能占用；旧NoTrade水位不撤更新；乱序累计量不倒退、fill/account/instrument/tradeId和billId去重。
- [ ] 运行两模块测试确认红；实现schema v1仅在新的Demo DB，初始化/迁移不得打开原三库；transaction+唯一约束，无网络调用持DB锁。
- [ ] 原子文件采用同目录temp+replace与hash，Host唯一写入intents、Worker只读；ack只写receipts。消费排序按源闭盘时间，过期/跨cutover逐项记处置。
- [ ] 运行两模块测试确认绿；加入SQLite quick_check和原数据库路径不存在时不被创建的检查。

### Task4：Demo Worker执行与保护状态机

**Files:** Create `demo_worker.py`（先实现可注入核心，不先接真实CLI）；Test `tests/unit/test_demo_worker.py`。

**Interfaces:**
- `DemoWorker(config: DemoConfig, paths: DemoPaths, client: OkxDemoClient, store: DemoStore, spool: DemoSpool, *, now_ms: Callable[[],int])`。
- `reconcile() -> DemoSnapshot`、`tick() -> dict[str,object]`（单次有界循环）、`close() -> None`。
- 每账户单进程文件锁；同品种一笔，entry control新鲜且enabled才允许入场；保护/退出不受入场开关影响。

- [ ] 写失败测试：500限价submit→live→partial→保护→撤余量→最终持仓→TP1/SL退出；验证stop/TP1与原建议完全一致且close-only；实际费用独立保存。
- [ ] 写故障测试：POST前DB失败零网络，POST后落库失败恢复只查不重发、OCO未知先查、保护失败撤余量+尽力平仓但未知不能closed、双Worker拒绝。
- [ ] 写竞态测试：cancel ACK后仍fill、OCO触发后父单迟到fill需要再保护/处置、旧NoTrade乱序、到期晚成交、部分成交资金不双算；closed必须父单终态+净仓0+无残留待确认保护。
- [ ] 写控制测试：账单分页缺口/外部持仓/模式错误阻止entry；Pause及Worker恢复仍管理旧仓；原期限不续期，不支持market/breakout不偷偷转单。
- [ ] 运行模块确认红；实现每tick先对账和保护，再到期/替代，最后新入场；2秒为活跃目标周期，空闲10秒，认证/限流/不可用退避1/2/4/8/30秒，期间只拒新入场，不假装保护成功。
- [ ] 连续失败保持明确状态；已知原保护价不合法时不改价。退出价以交易所为准，策略退出和protection_failed紧急退出分开reason。过期entry不重发。
- [ ] 运行全部Demo模块测试确认绿，记录HTTP调用序列及最大查询间隔测试，模拟网络与时钟，禁止请求真实账户。

### Task5：Headless唯一分发及cutover恢复

**Files:** Create `demo_dispatch.py`；Modify `headless.py`, `monitoring/headless.py`, `execution/plans.py`（仅共享资格）及`config/paths.py`必要路径解析；Test `tests/unit/test_demo_dispatch.py`、既有Headless/路径测试。

**Interfaces:**
- `OfficialExecutionDispatcher(paper: PaperExecutionCoordinator, demo_spool: DemoSpool, config: DemoConfig, *, now_ms: Callable[[],int])`保留现有`enabled/observe_official/expire_pending/active_market_keys/submit_official/reconcile_official`调用形状；research仍使用原PaperCoordinator，不向Demo转交。
- `bind_execution_epoch(task_payload: object, config: DemoConfig) -> object`绑定仅新task的执行目标/代次，不修改旧Frozen Job或market hash。
- `apply_execution_cutover(*, paused: bool, host_idle: bool, worker_idle: bool, recovery_blocked: bool, config: DemoConfig) -> DemoConfig`只在静态服务配置+idle符合时接受；不新增Dashboard写接口。

- [ ] 写失败测试：只在原persisted+audit_ok成功入口交接，NoTrade也交付；off/Shadow/Joint/研究分流权限不变；disabled默认无Demo副作用。
- [ ] 写切换/恢复测试：cutover当旧task刚完成记skipped不转发，历史list_successful_jobs不被补单，重复交接不重模型，恢复按水位顺序，无双目标提交。
- [ ] 写Paper兼容测试：Demo启用时停止正式Paper新单但active_market_keys/observe/expire继续处理旧活跃单；旧quarantine/v4 reader不换；Paper模式完全走原函数。
- [ ] 运行新模块确认红；接缝只替换正式执行依赖，不更改原handle_completion分析成功判定；冻结标记仅新任务codec允许，legacy无标记不能获得Demo资格。
- [ ] Host向Demo专属control写入新鲜入场状态+generation。Worker tick要求control年龄<=30秒且running；Host Pause只阻止新entry，不撤掉已在交易所的有效保护。Host/Worker时钟分离保存。
- [ ] 运行 `test_demo_dispatch.py test_headless_monitor.py test_headless_cli.py test_runtime_paths.py test_pipeline_paper_coordinator.py`确认绿；必须证明真实模型调用数仍为0。

### Task6：交易概览以OKX Demo为主

**Files:** Create `operations/demo_trading.py`；Modify `operations/trading.py`, `monitoring/headless.py`, `dashboard.py`, `trading_page.py`；Test `tests/unit/test_demo_trading_projection.py`、既有`test_trading_projection.py/test_trading_page.py/test_dashboard.py`。

**Interfaces:**
- `build_demo_projection(store: DemoStore, snapshot: DemoSnapshot, *, now_ms: int) -> dict[str,object]`由Worker调用，只出白名单。
- `compose_trading_projection(paper: Mapping[str,object], demo: Mapping[str,object] | None, *, selected_backend: str, now_ms: int) -> dict[str,object]`由Host调用；不从Web读DB。
- `safe_demo_projection(value: object) -> dict[str,object]`在Dashboard按版本/数量/文本/finite值重筛。
- 新顶层`trading.schema_version=2`，`selected_backend='okx_demo'|'paper'`、`demo`及`paper_versions`；保留`analysis`字段来源标识。旧schema1只通过原safe函数，不能按缺失demo猜测自动切换。
- Demo payload最小字段：`environment/account_label/generated_at_ms/exchange_observed_at_ms/reconciled_through_ms/state/coverage`；`account={equity,available_balance,unrealized_pnl,margin_used,planned_stop_loss}`；有限`orders/positions/fills/decisions`、all/7d/30d的净收益及最多180点曲线。订单最多150、分析30、三品种，文字上限2400；无路径/账户UID/原响应。

- [ ] 写失败测试 `test_enabled_demo_is_default_and_never_falls_back_to_paper`：Paper假余额99999、Demo10000时默认显示10000；Demo缺失/过期也不显示99999；旧schema1仍正常。
- [ ] 写数值测试：entry/exit fees按fill不按position累计重复叠加，funding按billId一次、资金重置/充值/提现不入收益，缺账单Pnl未知而非0；账户权益与本策略累计收益不是同一个数。
- [ ] 写安全/新鲜度测试：Host心跳新但Worker/账户>30秒未更新标stale；投影读文件失败不建DB、不请求OKX；恶意额外字段/NaN/超大列表被截断或拒绝，不把total counts截成仅最近150单。
- [ ] 写页面测试：默认“OKX 模拟盘”、资金/持仓/条件单保护状态/成交/费用及净收益，去掉Demo视图的“funding未建模”与Paper标签；显式Paper历史可选择v3/v4，切换后再刷新不跳回错误口径；原分析复盘继续按run_id展示。
- [ ] 运行新旧页面/投影测试确认红；实现组合而非复制SQL，Worker同一成功snapshot原子发布，Host只读其JSON。json模式版本缺失/coverage未知时显示具体不可用，不伪造余额或零收益。
- [ ] 页面测试覆盖JS行为/DOM，随后本地假数据页面截图核对，假数据位于临时PA_AGENT_HOME，结束清理；不是交易所真实验收。

### Task7：CLI、部署隔离、只读预检与新库备份

**Files:** Modify `demo_worker.py`, `pyproject.toml`（新增入口、无依赖变化）、`deploy/install_vps.sh`、`docs/linux_headless_deployment.md`；Create前述Demo service/env/backup资产；Test `tests/unit/test_demo_deployment.py`, `tests/unit/test_demo_worker_cli.py`, `tests/unit/test_demo_backup.py`。

**Interfaces:**
- 入口`pa-agent-okx-demo = pa_agent.execution.demo_worker:main`；`main(argv: Sequence[str] | None=None) -> int`。
- `--check`仅离线配置/权限检查，不读取网页/访问交易所；`--probe`显式读取systemd credential并仅私有GET，不修改模式/杠杆、不下单；`--backup <directory>`只备份新库/非敏感状态并quick_check。
- `--probe`也属于live；测试仅mock transport。服务运行不自动执行`--probe`，新部署默认entry_enabled=false。

- [ ] 写失败测试：无凭据不出网、错误credential权限拒绝、不把Key写参数/配置/日志、默认不启动交易、安装器发现active Demo服务先拒绝覆盖包。
- [ ] 写Linux资产测试：独立`pa-agent-execution`用户；intents Host写/Worker读，receipts/projection Worker写/Host读；state DB只属于Worker；Codex/Dashboard均不能读secret/状态；不放宽原records权限。
- [ ] 部署路径：root `/var/lib/pa-agent-execution`；intents `pa-agent:pa-agent-execution-spool 2750`，receipts/projection `pa-agent-execution:pa-agent-execution-spool 2750`，state `pa-agent-execution 0700`，独立root管理的`/etc/pa-agent/okx-demo-*`凭据经LoadCredential注入。secret不保存到普通env。
- [ ] 本地运行仅解析依赖注入，Linux服务限制源码/环境只读、写路径仅专属目录。Demo Worker不用LLM/模型sandbox，也不继承Codex home。
- [ ] 新备份service以Demo用户读取自己的SQLite，写专属`/var/backups/pa-agent-demo`，不因此获得原生产库权限；取一致备份及manifest，排除credential文件。现有三库备份流程保持。
- [ ] 运行CLI/部署/备份测试确认绿；wheel隔离安装检查CLI和无Qt导入。这里只保存部署资产，不安装或重启VPS。

### Task8：整链离线验收与交付记录

**Files:** Create `tests/unit/test_demo_execution_flow.py`、上述质量报告；Modify `CONTEXT.md`, `docs/knowledge/current-status.md`, `docs/quality/acceptance.md`中新增Demo特有验收项。四个既有ICT文件保持原字节。

**Interfaces:** 从Host接收已保存正式record→DemoSpool→Worker→fake OKX transport→账本/投影→Dashboard，只通过前7任务既有接口；不得测试旁路原Validator或注入生产DB。

- [ ] 用冻结正式分析fixture写红：普通500限价自然流程、NoTrade替代、restart未知提交、保护失败、父单latefill、切换不补历史、Demo主视图。模拟控时，断言无模型和真实网络。
- [ ] 跑绿并做差异审查：原record/候选/stop/TP1不变，旧Paper三库未打开写入；真实trade/fill与mock身份清晰分开。
- [ ] 定向运行所有新增`test_demo_*.py`、`test_okx_demo_client.py`与受影响Headless/Paper/投影/部署模块。
- [ ] 扩大到项目既有Headless非live全集（沿用前次1774测试集合的排除规则，明确不含Qt GUI/E2E/live），覆盖原PA、off、Shadow、Joint、A/A/B/B、HTF、订单数学及records/audit。不得把mock计作模拟盘真订单。
- [ ] 运行关键静态检查 `python -m ruff check --select E9,F63,F7,F82 pa_agent tests`、`git diff --check`及固定LF wheel包资源审计；需要的GUI无依赖导入检查仍保留，不为通过而改权限/测试标记。
- [ ] 清理本次临时DB/日志/假数据，保存测试命令及结果到质量报告；同步当前状态、实现/未验范围。完成三角色复核和唯一只读协作者终审，修复真实问题后重跑受影响测试。
- [ ] 在交付中明确：本地通过不等于OKX私有预检/保护支持/VPS已发布。没有凭据和受控订单授权时，停在可审阅、默认不入场的完整代码，列明真实验收剩余项。

## 真实模拟盘验收（代码完成后的独立操作）

以下不是离线测试，也不授权浏览器创建新凭据或实盘交易：

1. 用户将模拟Key/Secret/Passphrase直接配置到VPS安全位置，核对读取/永续BTC权限与IP；不传聊天，不改原Key。
2. 仅私有GET检查环境、账号、net/逐仓、3倍、余额、目标合约、未知持仓/订单和当前保护能力；有冲突先报告不接管。
3. 用户确认受控订单范围后，用最小合法数量验证挂单/撤单、真实成交、严格原价OCO、保护触发/费用/重启查询；不消费旧分析或借此额外调用模型。
4. 验证结果逐项记录：实际部分成交/OCO覆盖未见到就是未覆盖，不能以mock宣称交易所验证。
5. 固定提交备份发布、先禁entry启动保护/对账，idle切换cutover，面板确认OKX Demo与数据时间，再开放BTC自然分析；实际新单仍固定500 USDT。
6. ETH/XAU逐项通过相同规格/价格精度/保护路径后启用；任何新增品种不改变已冻结任务或历史收益。

## 计划自检

- Spec1–6由Task1/2/5覆盖，Spec7–9由Task3/4/5覆盖，Spec10面板/凭据/备份由Task6/7覆盖，Spec11验收由Task8及真实模拟验收分层覆盖。
- 5项Review Focus均已落入对应命名测试步骤；共同类型及方法签名统一。
- 保护失败紧急平仓是异常处置，单独reason；不修改用户指定stop/TP1，不作为另一套策略退出。
- 本文件锁定决定和验收，实际完成证据以验收记录为准；不自动commit或部署。
