# 独立 Demo 计划与 Paper 历史冻结实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkboxes for tracking; unchecked external acceptance remains pending.

**Goal:** 正式 Headless 仅由 OKX Demo 执行；每次分析计划独立，同向允许追加，Paper 仅提供冻结历史。

**Architecture:** 复用唯一 Host Dispatcher、DemoStore 和 DemoWorker，新增一个确定性数量核对模块及一个独立计划执行模块，隔离旧 v1 全仓管理。新模式按订单身份累计成交及保留退出预算，不按净仓猜测每笔计划；历史查询继续走现有只读投影。没有新模型阶段、数据库表/列或依赖。

**Tech Stack:** Python 3.11+、现有 Pydantic / SQLite / Decimal / httpx，原生 HTML/JavaScript 面板，pytest。

**Spec:** `docs/superpowers/specs/2026-10-15-independent-demo-plans-design.md`（用户已确认）。

## Global Constraints

- 用户已批准实施；由当前 Agent 实施，仅复用一名独立审阅者，不派生实现 Agent。
- 不自动 commit、部署、操作真实订单或改账户模式；新保护许可在 Demo canary 通过前保持空。
- 2026-10-16后续授权：可commit、VPS发布、最小Demo双计划实际验证及通过后启用；
  不涉及实盘、不更改账户模式，原本地阶段记录不追改。
- 固定原 entry/stop/TP1、每单冻结金额和绝对 TTL；net/逐仓/原杠杆、Snapshot、分析 Schema、Router、Prompt/Skills 不改。
- 新 No Trade 不撤旧单；反向新增在该合约仍有旧挂单、预约、未知请求或持仓时拒绝。
- v1 Intent/历史不改写，v2 不使用 K 线水位淘汰；永久 `(task,epoch)` 幂等不变。
- 不新增表/列、不回填、不删除账本或认证；沿用 legs.params/response 与 triggered_orders 保存新增数量语义。
- 旧 v1 活跃计划与 v2 不混入同合约；退出未知不重发、不叠加平仓量。
- 4 份既有 ICT 文件保持原字节；备份已在 ignored 的 `docs/changelog/backups/20261015-independent-demo-before/source.zip`。
- 未经明确授权不增加依赖。官方 OKX 数量保护行为须逐项核对，旧全仓 OCO 验收不代表新模式通过。

## Review Focus

1. A 的 OCO POST 超时但实际 live，随后补发 emergency 会平掉 B：Task 2/3 测试共享退出预算及恢复互斥。
2. 多单 GET 累计成交与账户快照并非原子读取：Task 2/3 测试不一致时禁止新增，不能当成已平仓或发错误补救。
3. 同批反向计划到达时净仓仍为零：Task 1/3 按已接受计划和永久预约拒绝反向，不能只检查 positions。
4. 旧 Frozen v1 任务在切换后延迟交付：Task 1/3 测试代次隔离及旧管理继续，不给旧全仓保护混入新仓。
5. Paper 列表叫“历史”但构造或定时器仍写库：Task 4/5 用失败哨兵及原库逻辑摘要测试零写入和冻结展示。

## 文件责任

| 文件 | 责任 |
| --- | --- |
| `pa_agent/execution/demo_contracts.py`, `demo_plans.py`, `demo_dispatch.py` | v2 输入身份、独立模式许可、冻结交付及唯一入口 |
| `pa_agent/execution/demo_store.py` | 永久幂等、累计事实、退出腿预算与保证金预约的持久化 |
| 新 `pa_agent/execution/demo_exposure.py` | 纯 Decimal 计划数量与退出预算核对，不访问网络/写库 |
| 新 `pa_agent/execution/demo_independent.py` | v2 接受/启动/保护/退出状态流程；复用客户端、Store 与 Worker 的查询能力 |
| `pa_agent/execution/demo_worker.py`, `okx_demo.py` | Worker 的 v1/v2 分发及同一串行循环、数量 OCO 请求与查询 |
| `pa_agent/headless.py`, `demo_dispatch.py` | 正式运行不构造或调用可写 Paper 执行链 |
| `pa_agent/operations/demo_trading.py`, `trading.py`, `pa_agent/trading_page.py` | 计划数量显示、账户净仓与收益口径、Paper 冻结历史标签 |
| 对应 `tests/unit/test_demo_*.py` 与新 `test_independent_demo*.py` | 回归、恢复及跨计划边界 |

### Task 1：版本兼容及独立计划登记

**Files:** 修改 contracts/plans/dispatch/store；测试 `tests/unit/test_demo_plans.py`、`test_demo_dispatch.py`、新增 `test_independent_demo_contracts.py`。

**Interfaces:** `DemoIntent.schema_version` 允许 v1/v2；`DemoConfig.independent_accepted_symbols: tuple[str, ...] = ()`。
保持 `register_intent(intent)` 和 `compile_demo_intent(...)` 签名不变；前者仅 v1 使用旧 watermarks，后者新正式任务输出 v2。
`cutover_fingerprint(config)` 默认空新许可时保留旧摘要，非空时绑定新字段；模型/行情身份不增加执行字段。

- [x] **1. 写失败测试**：`test_v1_digest_and_client_id_unchanged` 对照固定 v1 原件；`test_v2_same_bar_distinct_tasks_are_independent` 断言两任务 accepted；`test_duplicate_task_is_idempotent_and_conflict_rejected` 保持永久幂等；`test_old_config_cutover_digest_unchanged`；`test_old_symbol_acceptance_does_not_enable_independent_orders`。
- [x] **2. 确认红测**：运行 `.venv/Scripts/python.exe -B -m pytest tests/unit/test_independent_demo_contracts.py -q -o addopts= -p no:cacheprovider --basetemp=.pytest-independent-contracts`；失败必须对应缺少 v2 行为，而非 fixture 拼写或导入问题。
- [x] **3. 实现契约**：新许可仅允许现有映射品种；新任务显式 v2，旧冻结派发 marker 仍按原摘要读取。保留水位及旧行，仅让 v2 登记跳过淘汰，不隐式升级旧 Intent。
- [x] **4. 验证**：新测试及已有 plans/dispatch/cutover/spool 测试全绿；v1 JSON、客户 ID、cutover 默认摘要逐值相同。

### Task 2：计划归属、退出预算及持久化

**Files:** 新 `demo_exposure.py`、修改 `demo_store.py`；新 `tests/unit/test_independent_demo_exposure.py`。

**Interfaces:** `plan_exposure(entry: dict | None, legs: list[dict], children: list[dict]) -> PlanExposure`。
只读 frozen dataclass 字段：`entered`, `exited`, `remaining`, `reserved_exit`, `protected` 为 Decimal，`uncertain` 为 bool。
Store 增加 `plan_exposure(intent_id: str) -> PlanExposure` 读取已持久化事实；不创建新的数量权威缓存。

- [x] **1. 写失败测试**：`test_distinct_exit_order_ids_count_once`；`test_partial_then_late_entry_fill_adds_only_delta`；`test_unknown_oco_reserves_its_full_unresolved_quantity`；`test_effective_without_children_does_not_release_budget`；`test_terminal_child_releases_only_confirmed_unused_remainder`；`test_backward_observation_cannot_reduce_accumulated_fill`；`test_invalid_or_excess_exit_is_unknown_not_zero`。
- [x] **2. 确认红测**：运行 exposure 文件，核对每条失败对应尚缺的数量行为。
- [x] **3. 实现纯算术**：按入场/退出真实 ordId 去重，累计量用有限非负 Decimal；OCO effective 不当成交，读取它的全部真实子单。只按确定终态和已知累计成交释放未使用退出预算；未知腿继续占用。缺失/矛盾事实标 uncertain，不 clamp 掩盖过量退出。
- [x] **4. 持久化预算**：v2 的 OCO/emergency `params` 保存原数量和保护价格；`prepare_leg` 在同一写事务内复算可用退出预算并拒绝超额预约。v2 允许为新增已成交增量创建不重叠保护 generation，保留旧 v1 generation 约束及所有旧客户 ID。
- [x] **5. 累计事实与资金**：现有 `update_leg/observe_triggered` 不允许累计成交倒退或终态丢失；快照尚未证明的资金占用按计划扣减，同一净仓不能分别为 A/B 免除预约。测试两个订单同 tick 启动时第二单可用资金已扣第一单未反映预约。
- [x] **6. 验证**：exposure + store + backup 回归全绿；`PRAGMA user_version`、表/列与既有数据库 dump 保持；只允许用户授权的新业务行变化，无迁移。

### Task 3：v2 多计划执行与数量保护

**Files:** 新 `demo_independent.py`，修改 `demo_worker.py`、`okx_demo.py`；新增/完善 `test_independent_demo.py`、`test_independent_demo_recovery.py` 及客户端测试。

**Interfaces:** `IndependentPlanManager(worker)` 仅持有当前 Worker 的现有 Store/Client/Config；方法
`accept_reason(intent, snapshot) -> str`、`start(intent, snapshot) -> None`、`manage(intent, snapshot) -> None`。
客户端扩展 `place_oco(*, inst_id, side, stop, tp1, client_id, quantity: Decimal | None = None)`；None 保留 v1 全仓调用。

- [x] **1. 先校正现有5个验收用例**：`test_independent_demo.py` 的 Fake 必须维护唯一 ordId、真实数量 OCO、累计成交与净仓；未知请求应支持实际已 live/实际未执行两种，取消 ACK 不自动终态。当前用例因缺配置字段 setup error，尚不能计作行为红测；Task 1 后须重新确认它们在旧流程因行为不符变红。
- [x] **2. 增补失败测试**：同向 A/B 及同 bar 两任务；新 NoTrade 不碰旧单；两笔独立 TTL；有同向挂单但无仓时反向拒绝；部分/迟到成交；OCO 未知→重启→迟触发时不叠加 emergency；子单未知不释放预算；A 退出但 B 仍受其原 stop/TP1 保护；重复投递无第二 POST。
- [x] **3. 实现分发**：Worker 单一串行循环仅对 v2 分发新 Manager；v1 已有活动单继续原管理。只允许代次匹配/仍新鲜的新 v2，旧代次延迟任务拒绝；存在 v1 活跃计划的同合约阻止 v2 入场。v2 NoTrade 结束自己，不 supersede、不触碰旧挂单，不用 `is_latest`；同向计划保持 FIFO 接受顺序及永久幂等。
- [x] **4. 实现仓位核对**：按计划累计入场和退出之差管理，净仓仅核对合计。净仓滞后于后发 GET 时标对账未完成，暂停新入场，不将差异认作某计划已平仓或平掉整仓。反向限制覆盖已接受未提交、unknown、挂单及持仓，不能只看 snapshot.positions。
- [x] **5. 实现保护**：quantity 非空时发送 `sz`、reduceOnly/net、原 stop/TP1，省略 closeFraction；核对 algoClOrdId、instId、side、sz、触发类型与价格。新增成交增量仅预约未覆盖份额。已知部分成交先保护该量再撤自己的余量，不影响其他计划。
- [x] **6. 实现退出互斥**：所有可能成交的 OCO、子单、emergency 占同一个计划预算。保护提交未知不发同量 emergency、不重发 POST；先核对/撤销并确认终态及实际累计成交。明确拒绝且无潜在重复退出时，只补救该计划剩余量；旧保护存在时绝不全仓补救。已确认某份额退出后仍处理同计划迟到入场增量。
- [x] **7. 结束条件**：该计划父单终态、剩余量为0、全部保护/退出/潜在迟到请求核清、账户快照确认合计后才 closed；B 仍持仓不妨碍 A 正确结束。期限仅撤各自余量；Pause 和关闭原控制语义保留，不借新分析触发撤单。
- [x] **8. 验证**：运行 independent + 原 demo_worker + okx_demo_client + CLI + 全部恢复测试；旧 v1 全仓请求字节与恢复行为不变。未知/外部敞口继续 fail closed，新增模式默认不可入场。

### Task 4：切断正式 Paper 写入与推进

**Files:** 修改 `pa_agent/headless.py`、`demo_dispatch.py`；测试 `tests/unit/test_headless_cli.py`、`test_headless_monitor.py`、`test_demo_dispatch.py`、新增 `test_independent_demo_history.py`。

**Interfaces:** `OfficialExecutionDispatcher(paper, demo_spool, config, *, now_ms, task_lookup=None)` 保留签名；正式 Demo 入口的 paper 为 None，`observe_official/expire_pending` 返回0、`active_market_keys` 返回空集合。
研究/显式注入 Paper 的测试工具可继续使用独立 coordinator，生产工厂不创建它。

- [x] **1. 写失败测试**：`test_demo_monitor_never_constructs_writable_paper` 将 Paper 构造器设为抛异常哨兵；`test_pause_observe_expire_and_restart_do_not_write_paper` 用历史 v3/v4 pending/open fixture，调用刷新、暂停、到期及恢复；`test_disabled_demo_does_not_fallback_to_paper`。
- [x] **2. 确认红测**：旧工厂构造或调用 Paper 必须被哨兵捕获，不能因fixture未走正式入口而误绿。
- [x] **3. 实现**：先解析 Demo 配置，再构造唯一 Demo dispatcher；未选 Demo 的正式 Headless 为无执行分析模式，不以 enabled Paper 设置恢复写引擎。保留原 Demo cutover 校验；观察、到期及活跃市场扫描不委托 Paper。恢复只重交付匹配代次的 Demo 意图，不补旧 Paper。
- [x] **4. 验证**：fixture Paper 数据库全表逻辑摘要不变，无 schema 建立/更新、事件写入或 WAL 新增；Demo 失败只报告不可用，不回退 Paper。既有隔离 Paper 引擎测试仍通过。

### Task 5：历史展示与独立计划投影

**Files:** 修改 `demo_trading.py`、`operations/trading.py`、`trading_page.py`；测试 `tests/unit/test_demo_trading_projection.py`、`test_trading_projection.py`、`test_trading_page.py`、`test_dashboard.py`。

**Interfaces:** 保留现有 schema2 封套与 `paper_versions` 分层；每个历史 Paper 投影附加 `history_frozen: true`（仅 Demo-only 路径），sanitize 明确保留。Demo 订单新增字符串 `exited_quantity`、`remaining_quantity`，来自 Task 2，不重算收益。

- [x] **1. 写失败测试**：历史 v3/v4 各保留自己的订单/收益；pending/open 只叫历史冻结，不宣传为当前活跃；旧版本 unavailable 保持未知。A 退出/B 存续时各单数量正确，净仓及账单收益只统计一次。
- [x] **2. 确认红测**：现有标签/投影必须无法满足冻结和数量断言，不放宽原统计断言来过测。
- [x] **3. 实现**：主选项“OKX 模拟盘 · 当前执行”；Paper v3/v4 用一个“历史模拟记录”分组收纳，保留独立选择，不合并 PnL。历史视图突出停止推进，无挂单/持仓正在管理的提示。旧未知结果保持原状，面板不打开数据库或调用模型。
- [x] **4. 实现数量及风险**：订单按自身 remaining/未成交余量统计计划风险；账户净仓/均价/账单仍按交易所口径展示一次。没有可核对的计划 PnL 时不新增归因数字；历史参考浮盈亏标冻结/未知，不当成当前估值。
- [x] **5. 验证**：投影 allowlist、HTML/DOM、鉴权测试；手工本地预览三个入口与空/未知状态，不触发外部交易。

### Task 6：整链验收、知识同步与后续启用边界

**Files:** `CONTEXT.md`、`docs/knowledge/current-status.md`、`docs/quality/acceptance.md`、`docs/quality/okx_demo_execution_acceptance_20261015.md`、新增 `docs/quality/independent_demo_acceptance_20261015.md`。

- [x] **1. 独立审阅**：仅一名现有审阅者按 PA/VPA/量化架构复查；任何退出越界、事实/权限变化未解决前不得宣称完成。
- [x] **2. 完整回归**：依项目 Headless 排除清单运行 unit/integration/property 的 `not live` 全集，覆盖 PA/VPA off/Shadow/Joint/AABB/HTF/订单数学/records/audit；命令及明确排除文件写入验收报告，失败原样保留。
- [x] **3. 静态/包检查**：`.venv/Scripts/ruff.exe check pa_agent tools tests --select E9,F63,F7,F82 --no-cache`、`git diff --check`；wheel 内容/Headless 无Qt/旧 Job与v1 Intent可读。依赖锁不变，旧ICT原字节不变。
- [x] **4. 记录与清理**：更新知识入口，注明“仅本地/新数量模式未真实验收/未发布”；保存 red/green、独立审阅及非敏感摘要，清理本次临时fixture/build目录；只移除本次产生的孤儿代码，不删历史账本或隔离研究实现。
- [x] **5. 后续授权的启用阶段（2026-10-16完成）**：暂停排空及旧活动计划核对、备份、真实Demo双计划sz保护、A退出/B不受影响、读取故障及重启恢复均通过；新增仅BTC独立许可、新证书并恢复运行。真实部分成交未观察，该项不宣称通过；详情见专项验收文末。

## 计划自审

规格中的独立计划、同向合并、反向阻止、原价/TTL、退出互斥、兼容与历史只读均分别落在Task 1–5；
默认禁入、独立审阅、回归与真实启用边界落在Task 6。两个新模块仅负责数量核对与v2执行，不复制Host物化器。
新接口只供上述消费者调用；沿用已有数据表及单一串行Worker，不引入并行下单者或第二套收益账本。
每个任务遵循红/绿/审阅；本轮没有commit授权，因此完成后保留可审阅工作区，不执行任务间提交。

本地及后续发布记录见`docs/quality/independent_demo_acceptance_20261015.md`；原本地阶段未执行真实canary，
2026-10-16新授权后完成Task 6.5所述启用阶段，保留真实部分成交未验收边界。
