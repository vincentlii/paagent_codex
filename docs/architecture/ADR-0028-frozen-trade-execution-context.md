# ADR-0028：Stage 2 冻结入场期限与佣金后执行数学

- 状态：2026-10-25 用户授权实施；2026-10-26固定代码27c1b23已安装VPS，费率/最小保护链通过；恢复及效果证据见工程验收。
- 范围：现有 Headless、Direct/Codex Stage 2、OKX Demo；不扩展订单类型或旧单管理权限。

## 决策与取舍

新 Demo 任务冻结 `trade_execution_context_v1`：目标合约、源 K1 收盘、1–24h TTL、绝对
expiry、本地冻结时间，以及可空的账户 maker/taker 费率及本地读取时间。TTL 继续从源收盘
起算，分析与交付消耗入场窗口，不从实际挂单时间续期，也不限制已经成交的持仓时长。

这替代 ADR-0023 中“TTL 只影响执行、不影响分析身份”的新任务部分。金额仍只影响执行。
旧无 marker 任务不补标，旧 TTL、候选、摘要、订单和保护不重算。当前面板数值不重标历史。

上下文在进入队列前写入深复制的 frame metadata，由 FrozenAnalysisPayload codec 原样冻结。
Host task snapshot 身份绑定上下文（忽略仅刷新读取时间）；cohort 绑定协议、合约、TTL 和
原始费率，排除源棒/expiry/观察时间。TTL 或费率不同属于不同处理条件；同费率刷新和下一根棒
不会各自创造新 cohort。执行事实不进入 `compact_snapshot_context` 或 canonical 市场 hash，
不能当作新的独立行情样本。完整上下文仍受 payload/resource digest 保护。

上下文只交付 Stage 2：Direct 沿用阶段 Prompt，Codex 放入已有 digest-bound 角色协议资源，
首次 Prompt 仍只有身份、角色和索引。不向 Stage 1、独立 VPA 增加输入，不新增角色、调用或
模型输出字段。模型继续使用现有 `entry_reachability` 等字段判断期限内路径、正常回测与
反向运动的竞争解释；成交可能性与成交后 TP 先于 SL 的主观概率分开。合法 wait 保留。

## 费率事实与失败边界

Demo Worker 使用当前账户私有只读 `GET /api/v5/account/trade-fee`，按 `SWAP + instFamily`
查询，并与 account instruments 返回的 groupId 精确匹配。新 `feeGroup` 非空时不回退别组；
旧响应只接受明确 USDT 保证金的 `makerU/takerU`，不把币本位根 `maker/taker` 当 USDT。
正值是返佣，负值是收费；普通限价可能立即吃单，因此两边采用
`c=max(0,-maker,-taker)`，不假定 maker 成交或返佣。

成功读取缓存5分钟；失败退避30秒。请求各网络阶段 timeout=1s，非总时长上界。每个合约
只有缓存失效时新增一次只读请求，不在每次分析或每次 tick 重查。费率独立于原七项交易覆盖，
读取失败不令已有成交/保护对账失效。Host 只接受原 Worker 投影中匹配执行账户的不可逆
`fee_scope`、30秒内投影和5分钟内费率；scope只留在Worker→Host私有文件，对外安全投影和
模型输入均移除。合约映射、时序、费率非法或
缺失保持 unavailable，不补零。`observed_at_ms` 是本地成功读取时刻，不是费率生效时刻，
不表示未来费用保证。新 context 不保留原 account UID、凭据或完整原始 feeGroup 回包；它
支持已选费率/时间/假设的数学复算，不是交易所历史费率证明或签名回执。

## 唯一物化器与执行资格

`materialize_stage2_draft` 保留原模型候选与 gross terminal，再通过既有
`apply_stage2_execution_policy` 计算执行资格。先完成原价格网格及 fixed_rr 数学，再按有效价
复算费用；不在 normalizer 或 Worker 新建语义物化器。模型不能自报 Host context/费用检查。

按每单位基础资产计算（合约单位和数量只同比缩放金额）：

```text
risk = abs(entry-stop); reward = abs(target-entry)
win_fee = c*(entry+target); loss_fee = c*(entry+stop)
net_reward = reward-win_fee; net_risk = risk+loss_fee
net_expectancy = p*net_reward-(1-p)*net_risk
```

`p` 为原模型百分比转换的小数；仍未校准。净回报及净期望必须严格大于0；费用未知或不通过
只让 execution_disposition suppressed，不把模型 trade 改成 wait、抬高概率、扩大结构 SL、
调远目标或更改 RR。原毛方程、原模型价格和概率保持；佣金后风险/回报/期望、盈亏平衡胜率、
费用/R、假设及未覆盖的 slippage/funding 单独审计。“佣金后通过”不是总成本后盈利证明。

Host 派发前核对原冻结上下文与 record，消费端重新计算费用投影；Intent 摘要绑定上下文，
并核对合约、源棒、TTL、expiry及原冻结时间。Worker提交新限价前再次核对新鲜账户费率：
缺失或收费上升则明确拒绝新请求；费用下降可沿用更保守的冻结假设。既有 pending/open
计划不因费率、后续分析或当前设置变化而撤销/改价；其保护不依赖费率可用性。

## 清理、兼容和发布

教材原位替换固定1–2跳SL、无条件限价优越性；倾向规则只评估本轮拟议方案，不管理旧单。
保留章节ID、结构SL原则及微型通道形成期的原产品边界，不偷偷扩大交易权限。

无 context 的旧 Intent 序列化及 digest 排除新增 null，原客户ID保持。无数据库迁移、回填、
新依赖或历史删除；有调用者的旧 reader 保留。新协议产生后旧 Worker不能直接接管新
Intent/活动计划；回退须协调 reader 并按原保护/排空流程处理，不删除账本或只恢复源码。
Runtime Skill 已变更，发布必须暂停并排空 Host/Codex 队列，禁止跨版本续接会话。
2026-10-26 PH-27改为维护持续管理至安全排空，见[工程交接验收](../quality/engineering_handoff_release_20261026.md)。
不能直接停管理进程、离线留下可成交挂单；有活动单/未知状态时维护停止会等待，发布须先排空。

验收与未验证边界见[第一批验收](../quality/trader_entry_execution_acceptance_20261025.md)。
本地工程验证不能证明模型入场选择、成交率或收益已经改善；这些需要同输入研究和自然样本。

## 2026-10-26：用户选择双边限价退出（本地，未发布）

用户明确接受触发后可能无法成交。新增可空`exit_order_type=limit`仅在Host新建context时冻结，
旧无字段维持原序列化/身份和市价协议；新标记仅允许独立v2计划。它只交付Stage 2，模型仍
评估目标先于SL达到，而非保证限价退出成交；佣金检查另列`limit_exit_nonfill`未覆盖风险。

定量OCO按last触发，委托价分别为原有效SL/TP。触发后的live/partial子单仍预约自身退出数量；
不重发、不按触价宣称平仓。OCO确定拒绝保留安全码，原应急腿也以原SL价reduce-only限价；
限价应急本身拒绝后显式阻断，避免循环提交。未知保留预算，晚到成交仅可保护未预约增量。
已知live限价不是两边价格均受保护的保证，无法成交可持续持仓并阻止维护排空。
完整验收与回退边界见[限价退出](../quality/limit_exit_execution_20261026.md)，不据此宣称VPS已启用。

依据：[OKX fee API](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-fee-rates)、
[Python Decimal](https://docs.python.org/3/library/decimal.html)，及现有 Draft/冻结/执行投影协议。

## 2026-10-26：开仓附带精确数量保护

新增可空`protection_binding=entry_attached`，只允许limit context，绑定任务/cohort/请求；
旧None不序列化。新DemoConfig空默认能力表不改变旧配置/证书，只有独立现场验收品种才
由Host冻结native协议；能力变更须paused/idle重新核验证书，不重绑旧任务或cutover epoch。

入场POST前登记同CID的native OCO g1并随入场HTTP提交一组condition TP/SL；新native路径
不再POST第二个OCO。g1冻结request.sz上限与entry身份，实际预算取自身已知成交量；
partial撤余促交易所生成保护，unknown/child未决不释放预算，晚到数量不修改旧params。
附件确定失败才允许原SL价限价应急，50004等内部错误不当确定失败；退出参数矛盾保留并阻新。
新reader产生后不得直接回退旧Worker；历史reader及旧协议仅供其自身冻结订单继续管理。

这减少Host观察成交后再提交保护的间隔，不保证交易所原子生成。限价触发未成交/价格带拒绝
仍可持续持仓。设计、探测失败、独立审阅及实际发布证据见
[结构与保护验收](../quality/stop_protection_completion_20261026.md)。
