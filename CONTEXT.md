# PA Agent Domain Context

本文件定义项目长期使用的领域语言与责任边界。修改分析链路前，同时阅读
`docs/wyckoff_vpa_integration.md` 与 `docs/architecture/ADR-0001` 至 `ADR-0007`。

## 核心目标

PA Agent 对同一份冻结行情快照完成高质量 Brooks Price Action 与 Wyckoff VPA
分析。系统优先保证事实一致、分析可审计和订单安全；任何外部依赖故障都必须
变成明确状态，不能静默污染分析或产生无依据订单。

## 领域对象

- `AnalysisRequest`：一次分析意图，包含品种、周期、模式和调用策略。
- `ResolvedInstrumentIdentity`：数据源实际解析出的 provider、symbol、venue、
  market、timezone；一次分析内不可漂移。
- `AnalysisSnapshotBundle`：同源主周期、可选高周期、成交量语义及质量报告组成的
  冻结输入。
- `DataQualityReport`：程序可复算的数据完整性、新鲜度、时间序列与来源一致性结论。
- `PaHypothesisSet`：PA 分析者提出的主假设、替代假设、Brooks Setup 候选及证据引用；
  它不是最终订单。
- `VpaEvidenceReport`：VPA 分析者针对 PA 假设给出的支持、挑战或信息不足结论；
  它不能创建 Setup、翻转方向或修改胜率。
- `JointDecisionProposal`：联合裁决者在既有 PA 候选中选择当前 Entry Attempt、
  时机或等待条件，并只携带可影响时机/置信度的有界补丁。
- `ComputedTradeMetrics`：程序根据候选价格计算的止损距离、目标距离、成本和盈亏比。
- `ExecutionPolicyResult`：程序对订单合法性、风险边界与运行模式的最终约束结果。
- `RunOutcome`：`completed`、`completed_with_warnings`、`degraded_pa_only`、
  `unavailable_input`、`unavailable_provider`、`cancelled` 或 `internal_error`。

## 权限边界

程序负责可确定、可复算、无歧义的内容：OHLCV 与时间序列校验、指标测量、来源身份、
Schema、证据引用、订单数学、成本和执行安全。

LLM 负责依赖上下文且允许多种合理解释的内容：市场背景、Always In、周期位置、位置价值、
Brooks Setup、Wyckoff 量价关系、替代假设和入场时机。

程序不能从指标或自由文本反推交易语义；LLM 不能改写程序事实、凭空创造证据或绕过订单安全。

## 分析顺序

1. 程序解析一次数据源身份并冻结主周期、高周期和成交量事实。
2. PA Framework Analyst 产生市场框架；程序封装主假设、替代假设与 Setup candidates。
3. PA-only Arbiter 形成并冻结不含 VPA 的 Stage 2 baseline。
4. VPA Evidence Analyst 独立检查量价证据，对 PA 假设支持、挑战或声明信息不足。
5. Joint Decision Arbiter 只输出有界补丁，在既有 PA candidate 内决定时机。
6. 程序把合法补丁应用到 baseline，复算交易指标并执行安全约束，随后持久化完整审计链。

这是一条信息依赖链，不代表 VPA 从属于 PA，也不允许形成第二套 Strategy Router。

## 失败契约

- PA 输出无法在一次定向修复后形成可信结构：`unavailable_provider`，不得产生可执行订单。
- VPA 失败：降级为 PA-only，并明确记录 `degraded_pa_only`。
- 联合裁决失败：一次定向修复后降级为 PA-only。
- 数据身份、时序或关键 OHLC 失败：`unavailable_input`。
- 取消与界面关闭：`cancelled`，不得遗留线程、重复记录或陈旧回调。
- 未分类的内部异常：`internal_error`，必须保留可诊断信息且不得伪装为成功。

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
- GUI 的业务结果与线程完成是两个事件。取消后保留 QThread 到真实退出；关闭窗口不得销毁仍在
  运行的分析、准备、快照、刷新或追问线程。

## 实验边界

- `off`：PA Framework + PA-only Arbiter，VPA 数据、Prompt、Schema、历史完全不进入链路。
- `shadow`：同一冻结输入上同时保留 PA-only 与 Joint 结果，PA-only 为正式结果。
- `soft`：Joint 为正式结果；VPA 或 Joint 失败时退化为 PA-only。
- A/A/B/B 是离线或抽样研究协议，不是每次生产分析的固定开销。协议在调用前登记
  A1/A2/B1/B2，使用同一冻结输入、四个独立客户端与 ABBA/BAAB 顺序，并区分 ITT 和
  per-protocol；生产 Shadow/Soft 及模型自述 comparison 都不构成反事实。
- A/A/B/B 的独立抽样单位是 `market_snapshot_hash`；`cohort_id` 隔离 Provider、settings、源码与
  Prompt 版本。同一行情的重复臂、重跑或跨版本复核都不得增加独立样本数。
- 未经样本外校准的数字胜率或置信度只用于研究，不得成为硬下单门槛。

## 不变量

- 已接受的结构化 Claim 对程序事实矛盾数为零。
- VPA 权限越界数为零。
- 同一分析内主周期与高周期的数据源身份一致。
- 任何失败都不接受非法订单、不静默损坏记录、不导致未处理崩溃。
- 历史回放、人工标签和真实成交结果必须分开存储，不互相冒充。
