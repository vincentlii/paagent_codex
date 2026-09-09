# PA Agent Domain Context

本文件定义项目长期使用的领域语言与责任边界。修改分析链路前，同时阅读
`docs/wyckoff_vpa_integration.md` 与 `docs/architecture/ADR-0001` 至 `ADR-0024`。

## 核心目标

PA Agent 对同一份冻结行情快照完成高质量 Brooks Price Action 与 Wyckoff VPA
分析。系统优先保证事实一致、分析可审计和订单安全；任何外部依赖故障都必须
变成明确状态，不能静默污染分析或产生无依据订单。

## 领域对象

- `AnalysisPipelineIdentity`：显式版本化运行协议，与 Backend 正交。阶段 5 的 `pa_vpa_joint`
  由 Stage 1 / Validator / Router → 独立 VPA → 联合 Stage 2 / 原校验组成；旧 off/shadow/研究
  协议不重标，默认配置不变。没有 PA baseline 订单和研究仲裁；`AnalysisRoleStop` 表示非决策停止。
  当前本地实现、验收证据及未完成执行边界见 `docs/quality/phase5_joint_pipeline_acceptance.md`。
- `AnalysisRequest`：一次分析意图，包含品种、周期、模式和调用策略。
- `ResolvedInstrumentIdentity`：数据源实际解析出的 provider、symbol、venue、
  market、timezone；一次分析内不可漂移。
- `AnalysisSnapshotBundle`：同源主周期、可选高周期、成交量语义及质量报告组成的
  冻结输入。
- `DataQualityReport`：程序可复算的数据完整性、新鲜度、时间序列与来源一致性结论。
- `PaHypothesisSet`：`compile_pa_handoff(...)` 从已验证 PA Stage 1/Stage 2 与冻结价格快照
  编译出的唯一角色交接对象，包含主/替代假设、真实 Entry Attempt、程序交易数学、
  PA 已引用窗口的数值位置与证据引用；它不新增交易判断。
- `PaSetupCandidate.execution_mode` 由程序从已验证 `order_type` 唯一物化：限价单为
  `passive_limit`、突破单为 `price_triggered_stop`、市价单为 `immediate_market`。它只澄清
  执行语义，不改变 PA 选择的订单类型；旧记录按同一映射回放。
- `VpaEvidenceReport`：VPA 分析者针对 PA 假设给出的支持、挑战或信息不足结论；
  它不能创建 Setup、翻转方向或修改胜率。
- `JointDecisionProposal`：联合裁决者在既有 PA 候选中选择当前 Entry Attempt、
  时机或等待条件，并只携带可影响时机/置信度的有界补丁。
- `PaTradeMath`：程序根据候选价格和 PA 主观胜率先验计算的风险、TP1 回报、RR、
  盈亏平衡胜率和期望值；TP2 存在时另算研究扩展回报，不生成胜率。
- `ExecutionPolicyResult`：程序对订单合法性、风险边界与运行模式的最终约束结果。
- `RunOutcome`：`completed`、`completed_with_warnings`、`degraded_pa_only`、
  `unavailable_input`、`unavailable_provider`、`invalid_model_output`、`cancelled`
  或 `internal_error`。
- `ContinuousAabbSample`：以 `(pipeline_id, cohort_id, market_snapshot_hash)` 唯一标识的
  持续配对研究单元；SQLite 租约只管理执行与恢复，不参与市场判断。
- `RuntimeResourceSnapshot`：一次运行实际加载的 `prompt_dir`、Provider Contract digest 与代码/Prompt
  fingerprint；三者必须同源，并进入 record/cohort 身份。
- `AnalysisRoleBackend`：Stage 1、Stage 2、VPA 与研究 Joint 的模型分析 seam；Adapter 只能返回
  Provider Draft 和运行审计，不能拥有 materializer、Router、Validator、Risk 或持久化。
- `FrozenAgentJob`：绑定同一 Snapshot cutoff、role、attempt、contract、knowledge、AGENTS、Skills、
  model 和预算 digest 的不可变 Codex 任务。不同角色不得共享会话。
- `AgentRuntimeFingerprint`：一次 Agent 调用实际使用的 Codex CLI、model、reasoning profile、
  AGENTS、Skills、Evidence interface 与知识清单身份；必须进入 record/cohort。

## 权限边界

程序负责可确定、可复算、无歧义的内容：OHLCV 与时间序列校验、指标测量、来源身份、
Schema、证据引用、订单数学、成本和执行安全。

LLM 负责依赖上下文且允许多种合理解释的内容：市场背景、Always In、周期位置、位置价值、
Brooks Setup、Wyckoff 量价关系、替代假设和入场时机。

程序不能从指标或自由文本反推交易语义；LLM 不能改写程序事实、凭空创造证据或绕过订单安全。

Direct LLM 与 Codex Agent 只是同一分析权限的两种 Backend。Codex 可以在 FrozenAgentJob 内按需读取
授权 Evidence，但不能扩大数据时间边界、选择其他交易体系或改变 PA/VPA/Joint 的权限。

Codex Backend、Dashboard 与 Runtime 资产的长期设计依据见
[Codex Analysis North Star](docs/knowledge/codex-analysis-north-star.md)；它不替代本文件的领域 authority 或 ADR 的具体决策。

明确写成 `K# + open/high/low/close + 数值` 的声明属于程序事实，必须与冻结快照一致；
程序允许显示精度舍入，但不得把价格绑定到错误 K 线。未明确绑定 OHLC 字段的 swing、
结构位、通道和 Setup 解释仍由 LLM 负责。

从自由文本推断出的窗口计数、连续阴阳线、量价 comparator 或专业标签冲突只进入质量审计，
不得单独终止分析。硬阻断仅保留结构化契约、明确 OHLC/几何事实、Evidence 身份、权限、
订单数学与执行安全。

明确的相邻窗口净收盘推进方向，以及某个 `HTF.H#` OHLC 点位相对当前周期 K1 收盘价的
上/下方，也属于可复算事实。程序可以拒绝“同向写成反向”或“上方点位写成下方目标”，
但不能据此把窗口命名为推动/回调，也不能决定该点位是否真是支撑、阻力或磁力位。

## 分析顺序

1. 程序解析一次数据源身份并冻结主周期、高周期和成交量事实。
2. PA Stage 1 以 `pa_draft_v2` 产生市场框架、主假设、可为空的真实替代假设及证据引用；程序物化静态 trace 与 Gate。
3. PA Stage 2 以 `pa_draft_v2` 形成不含 VPA 的 Setup、entry、结构 stop、TP1、可选 TP2、PA 胜率先验与交易意图；程序继承 Stage 1 置信度并复算 Trader Equation、terminal 与执行安全。
4. `compile_pa_handoff(...)` 只复制 PA 语义并补充可复算的订单数学、身份和窗口位置事实。
5. VPA Evidence Analyst 独立检查量价证据；生产 Shadow 到此结束，正式结果仍是 PA-only。
6. 隔离的 A/A/B/B 研究 B 臂才调用 Joint Arbiter，在既有 PA candidate 内输出有界补丁。
7. Stage 2 的模型候选保持不可变；程序订单数学与置信度只生成 `execution_disposition`，
   Paper/未来执行器只消费 effective order 语义。
8. 程序验证补丁与执行安全，随后持久化完整审计链。

选择 Codex Backend 时，步骤 2、3、5、6 各自使用新的 ephemeral 任务；下游只接收已验证的结构化
上游 Artifact，不继承聊天历史。Codex 输出仍先进入现有 Provider Draft/materializer/Validator 链。

这是一条信息依赖链，不代表 VPA 从属于 PA，也不允许形成第二套 Strategy Router。

## 失败契约

- PA 输出无法在一次定向修复后形成可信结构：`invalid_model_output`，不得产生可执行订单，
  且不得误报为网络或供应商不可用。
- 生产 Shadow 的 VPA 失败：保留有效 PA-only，记录 `completed_with_warnings` 与失败角色。
- 研究 Joint 失败：一次定向修复后该 B 臂保留 PA-only，不创建新订单。
- 数据身份、时序或关键 OHLC 失败：`unavailable_input`。
- 取消与界面关闭：`cancelled`，不得遗留线程、重复记录或陈旧回调。
- 未分类的内部异常：`internal_error`，必须保留可诊断信息且不得伪装为成功。
- 可解析的 VPA/Joint 首稿发生可修复契约错误时，重试必须携带原始草稿并要求未点名字段
  保持不变；完整结果重新通过所有校验后才能接受。权限越界不进入自由重写。

## 多资产运行时

- 每个资产先形成冻结 `AnalysisSnapshotBundle`，再进入调度器；排队期间不得重新读取或替换行情。
- `AnalysisTask` 以来源、venue、品种、周期、snapshot 和 cohort 形成稳定身份。同一资产严格串行，
  不同资产只在配置的全局上限内并行；重复与容量不足必须显式返回状态。
- 每个任务重新创建可变模型调用对象，禁止不同资产共享 client 对话状态、validator retry 状态或
  orchestrator。程序在调用模型前复核 frame 与 task 的 symbol/timeframe。
- `monitoring_jobs` 记录调度终态和时延；分析内容仍以 JSON record 与 `analysis_runs` 为准，二者
  通过 `run_id` 关联。
- 调度线程 `completed` 不等于分析成功；只有 `RunOutcome` 成功、完整记录已落盘时，
  `analysis_success=1` 并进入跨重启去重。Provider、内部或持久化失败保留原 task 身份并执行有界退避。
- Headless 每个资产持有独立数据源；Qt 不进入服务进程。源码与 Prompt 可只读，全部运行状态归属
  `PA_AGENT_HOME`。
- Headless 只向 operation directory 的 `status/status.json` 发布无凭据运行投影，并读取
  `control/control.json` 的 `running/paused` desired state。暂停只阻止新任务，不能改写、
  中断或重判已有 PA/VPA 分析；进程生命周期仍由 systemd 管理。
- VPS Dashboard 使用独立 Unix 用户，只能访问共享 operation directory。生产审计库与
  Paper 账本由 Headless 以 SQLite read-only 模式投影，Dashboard 不得直接打开数据库、
  配置目录或 Provider credential。
- GUI 的业务结果与线程完成是两个事件。取消后保留 QThread 到真实退出；关闭窗口不得销毁仍在
  运行的分析、准备、快照、刷新或追问线程。

## 实验边界

- `off`：PA Framework + PA-only Arbiter，VPA 数据、Prompt、Schema、历史完全不进入链路。
- `shadow`：正式 PA-only 完成后追加独立 VPA Evidence；不调用或保存 Joint。
- `soft`：仅 A/A/B/B 研究 B 臂可授权，Joint 作为该实验臂结果；生产请求会记录
  requested=`soft`、effective=`shadow`。
- A/A/B/B 是离线或抽样研究协议，不是每次生产分析的固定开销。协议在调用前登记
  A1/A2/B1/B2，使用同一冻结输入、四个独立客户端与 ABBA/BAAB 顺序，并区分 ITT 和
  per-protocol；生产 Shadow/Soft 及模型自述 comparison 都不构成反事实。
- A/A/B/B 的独立抽样单位是 `market_snapshot_hash`；`cohort_id` 隔离 Provider、settings、源码与
  Prompt 版本。同一行情的重复臂、重跑或跨版本复核都不得增加独立样本数。
- 持续 A/A/B/B 只从已落盘生产 Shadow record 抽样，独立于生产 Headless 调度；
  `completed_with_warnings` 不因 VPA 先验失败而被筛掉。四臂完成后若研究 Paper 暂时失败，
  只重放四条已验证 record，不重新调用模型。
- PA `estimated_win_rate` 是未校准的 Setup 主观先验，仍参与 Brooks Trader Equation；
  不得称为历史统计。VPA、固定外部阈值与单次样本不得修改它或新增概率 Gate。
- 教材概率只允许对竞争假设作定性排序。没有同品种、同周期、同数据源、同执行协议的
  calibration 标识时，LLM 不得复制通用频率或由盈亏平衡点反推当前胜率。

## 不变量

- `pa_draft_v2` 的 Provider Contract 是初始 Prompt、校验与定向修复的唯一结构来源；
  Canonical Schema 只服务下游记录与兼容，不得反向要求模型重复程序字段。
- PA Provider Contract 错误一次聚合；最多允许一次按 JSON Pointer 授权的修复。
  程序只把被点名路径的候选修正投影回原 Draft，未授权的新增、删除、置空、数组重排或
  语义变化不进入接受结果；投影结果必须重新通过完整校验，禁止通过宽松 normalizer 猜测模型意图。
- VPA 修复按 hypothesis ID、证据集合或时序证据对匹配稳定语义，不按数组下标恢复，
  也不得恢复校验明确要求修改的字段。
- Trader Equation 只能引用模型已经声明且 Catalog 可解析的 PA Evidence ID；程序不得
  用固定 K1 或任何合成引用填补缺失证据。

- 已接受的结构化 Claim 对程序事实矛盾数为零。
- VPA 权限越界数为零。
- 同一分析内主周期与高周期的数据源身份一致。
- 任何失败都不接受非法订单、不静默损坏记录、不导致未处理崩溃。
- 历史回放、人工标签和真实成交结果必须分开存储，不互相冒充。
- 尚未触发的 Entry Attempt 使用唯一空态：`bar=null`、`strength=not_triggered`、
  `freshness=pending`、`follow_through=null`、`still_valid=null`。已有入场棒的
  可执行订单必须使用具体 K 线、布尔跟随状态和 `still_valid=true`。
- `stop_loss_price` 是盘中触价的保护性止损；`invalidation_condition` 是分析假设的
  重判条件，可能要求收盘确认。OHLC 回放只报告保护止损触达，不得冒充分析假设已失效。
- 保护止损必须锚定当前 Setup 的结构失效。信号棒、摆动或边界极点仅在其穿越会否定
  当前尝试时采用；程序提供 ATR/窗口位置等风险尺度，但不得机械决定结构 stop。
- TP1 是执行目标和 Trader Equation 的唯一回报基础。TP2 仅在存在独立、可核对的远端
  结构目标时填写；null 不降低置信度、不触发 retry、不抑制合法订单。
- Stage 1 的 `entry_setup` 是框架层提示，不是交易授权。`gate_result=proceed` 后，Stage 2
  独立负责 Brooks Setup、可选触发/失效文案与订单裁决；任何有效可执行 Stage 2 订单都形成
  PA candidate，候选存在性不得依赖限价单/市价单可为空的 `entry_rule`。
- 没有可执行 Entry Attempt 时，`trade_confidence=null`；程序不得为不存在的交易方案填充
  数字置信度。存在可执行订单时该字段必须为 0–100 整数。
- 没有 PA Entry Attempt 时，VPA `entry_timing_relation=insufficient_data`；这不是 VPA Gate，
  也不妨碍 VPA 对背景、Effort/Result 和未来 acceptance/rejection 条件形成独立观察。
- VPA 明确 K 引用只能绑定到唯一单棒 evidence，或被已引用窗口覆盖；程序只补引用元数据，
  不替模型生成解释。相邻窗口的 `same/opposite/flat_or_unavailable` 由现有 facts 临时派生，
  不写回冻结 catalog，避免破坏历史 catalog identity。
- VPA 单棒 evidence 明确给出该棒之后已经存在的已收盘棒数量与范围。程序只拒绝“没有任何
  后续已收盘棒”这类可证明为假的时序陈述；“已有后续棒但尚未确认 acceptance/rejection”仍是合法解释。
- 明确绑定 `K#-K#` 的数值价格区间必须与该冻结窗口的真实 high/low 一致；没有同句数值区间、
  价格路径或结构标签不进入该检查。该规则不判断趋势、Trading Range 或突破是否成立。
- 成交量可用性由 Provider provenance 与逐 bar `volume_state` 明确表达；真实观测零与缺失/歧义零
  不得合并。Seasonality 只使用严格早于分析窗的精确同槽历史，原始样本不写入 record，但其冻结
  hash 与边界摘要必须支持 catalog 身份重放。
- 所有可用相对量 ratio 同时提供相对 `1.0` 的有符号 `deviation_pct`。`above/below` 只说明方向，
  幅度与 VPA 含义仍由模型结合 Background、Location、Effort/Result 与后续行为解释，程序不设强弱阈值。
- 新 VPA 窗口事实只对 PA 已引用的 `PA.WINDOW.K#` 注入，代码测量成交量覆盖、推进、路径、效率与
  重叠，不命名 leg、Spring、UTAD、Absorption、Exhaustion 或交易信号。
- VPA catalog 2.3 对已引用窗口可按每根棒自己的严格因果精确时槽基线汇总
  seasonality-adjusted relative volume；只有全窗口覆盖时才给关系，部分覆盖保持 unavailable。
- A/A/B/B 的主收益比较是 episode-level ITT：无下单/未触发为 `0R`，pending/ambiguous censored，
  缺失 arm 保留并阻止验收；per-protocol 只作处理交付诊断。
- E2E/fixture 不得触发正式交易 CSV、通知或其他生产副作用；复盘连续性读写必须使用同一个
  `PA_AGENT_HOME` 派生目录。
