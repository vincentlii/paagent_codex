# PA Agent × Wyckoff VPA 融合架构

本文是旧 `vpa_mode` off/shadow 与研究 Joint 的长期事实来源。显式
`analysis_pipeline_v1:pa_vpa_joint` 的新版联合 Stage 2 权限及顺序以
[ADR-0025](architecture/ADR-0025-joint-stage2-pipeline.md) 为准；两条协议不得混写。
代码、测试与本文冲突时，先核对实际实现并同步修正文档。

## 1. 目标与边界

核心分工：

> 程序测量事实；Brooks PA 定义市场框架、Setup 与正式 Entry Attempt；VPA 补充或挑战独立量价证据；研究 Joint 评估可能影响；程序验证输出与订单合法性。

本次融合解决的是事实可靠性、分析可审计性、VPA 信息增量和实验有效性，不直接证明盈利能力，也不以单次行情结果调整规则。

- VPA 不是第二套交易系统、第二个 Strategy Router 或机械 Gate。
- Brooks Router 始终是唯一策略路由，只读取已验证的 Stage 1 枚举与 pattern keys。
- 程序不机械识别 Spring、UT/UTAD、SOS/SOW、LPS/LPSY、Absorption 或 Exhaustion。
- 旧 `vpa_mode` 生产只允许 `off` 与 `shadow`；旧 `soft`/Joint Arbiter 仅存在于隔离的 A/A/B/B 研究 B 臂。显式新版 `pa_vpa_joint` 是不同的 opt-in pipeline，尚须单独验收和晋级。
- VPA 关闭时，Prompt、测量、Schema 要求和历史 VPA 字段都不得进入原 PA 链路。

## 2. 职责分配

| 层 | 拥有的职责 | 不拥有的职责 |
|---|---|---|
| 数据与测量 | 闭盘快照、K 序号、OHLC 合法性、EMA/ATR、bar geometry、相对量、因果基线、来源语义、HTF 取数 | 趋势、Setup、Wyckoff 事件、信号质量 |
| PA Framework Analyst | Brooks 周期、方向、Always In、结构位置、Setup、Gate、竞争假设 | 改写程序事实、引用 VPA 或输出最终订单 |
| Brooks Router | 根据已验证结构化 PA 字段选择策略文件 | 读取自由文本、VPA 标签或指标阈值猜策略 |
| PA-only Arbiter | 仅按 PA 假设与 Brooks 策略知识形成 baseline decision | 读取 VPA Prompt、catalog 或历史 VPA 字段 |
| VPA Evidence Analyst | 独立检查量价证据并支持、挑战或声明信息不足 | 创建 Setup、改单、改方向、改胜率 |
| Joint Decision Arbiter（仅研究） | 在已有 PA candidate 内评估时机与可信度的可能变化 | 进入生产、绕过 Gate、创建反向交易、修改冻结胜率 |
| Validator | Schema、事实一致性、引用范围、权限边界、订单数学和路径一致性 | 裁定某个专业解释是否唯一正确 |
| Audit | 保存调用、差异、复盘上下文、结果标签和性能数据 | 把反事实回放冒充真实成交或盈利 |

Normalizer 只能复制白名单字段和规范无语义记号。它不得补市场结论、改价、改单或创建决策节点。仅当 reasoning 已明确引用快照内 K 线时，程序可以把已有 `bar_range` 扩展为覆盖这些引用的最小包络。

### 2.1 冻结数据身份与质量

一次分析首先形成不可变 `ResolvedInstrumentIdentity`。TradingView 自动探测只允许在主周期执行一次；
高周期必须复用同一 `provider + resolved_symbol + venue`，不得再次探测或读取随后被覆盖的
`resolved_exchange`。主周期与高周期组成 `AnalysisSnapshotBundle`，所有模型阶段只能读取该 Bundle。

`DataQualityReport` 在模型调用前检查 OHLC、闭盘状态、序号、重复/未来时间戳、声明周期、缺口、
新鲜度和跨周期身份。OHLC 错误、重复/未来 K 线、真实来源的周期不规则、同源漂移属于
`hard_error`；市场日历或历史来源未知造成的缺口/陈旧只能先记为 `warning`，不能把周末或休市
机械判为断流。真实 TradingView/MT5 快照同时校验 K1 理论收盘时间。

Volume seasonality 使用数据层提供的、严格早于当前分析窗口的同源历史样本。时间桶先把 UTC
epoch 转换到明确的 `timezone_basis`，再按精确 `HH:MM` 对齐；优先 weekday-slot，样本不足时才
退到 all-days-slot。时区无效、因果边界无法验证或同槽样本不足时必须分别标记 unavailable，
不得用当前窗口自身构造基线。VPA 开启时只在首次抓取中回填有界历史，后续刷新合并同源缓存；
VPA 关闭时保持原 PA 请求量。

Catalog 2.3 还可对 PA 已选择的窗口逐棒匹配各自精确时槽，并汇总
seasonality-adjusted relative volume。只有窗口内每根棒都有足量、严格更早的时槽样本且
volume 完整时才输出窗口关系；部分覆盖不会外推，也不会把已有单棒比较冒充完整窗口比较。

## 3. 运行模式

### `off`

- 执行原 Brooks 两阶段链路。
- 不构建 VPA evidence catalog。
- 不注入 VPA Prompt，不要求 `vpa_context` 或 `vpa_impact`。
- 历史记录中的 `vpa_*` / `wyckoff_*` 字段在上下文注入前被清除。

### `shadow`

同一冻结 Snapshot 上依次运行：

1. PA Framework Analyst 与 PA-only Arbiter，得到不含任何 VPA 信息的 baseline。
2. 程序把已验证 PA 输出封装为 `PaHypothesisSet`。
3. VPA Evidence Analyst 只读取冻结事实、PA 假设和按 Context 注入的 VPA 知识，生成 `VpaEvidenceReport`。
4. PA-only baseline 是唯一正式 GUI、告警和订单结果；VPA report 作为旁路摘要供复盘。

VPA 失败不会使 PA 分析伪装失败；记录标记 `completed_with_warnings`，并保持
`official_path=pa_only`。取消则始终是 `cancelled`，不得被当成降级成功。

### `soft`（仅研究）

只有 A/A/B/B executor 可授权 effective=`soft`，并在 B 臂中运行 Joint。普通 GUI、Headless
或监控配置请求 `soft` 时会显式收敛为 effective=`shadow`，记录 `soft_not_promoted` warning，
不会采用 Joint 结果。用户置信度阈值只对生产正式 PA 结果应用一次。

## 4. A/A/B/B 实验有效性

生产 Shadow 不是 A/B，模型自述的 baseline comparison 也不是反事实。真正实验仅由
`pa_vpa_aabb_v2` 离线运行器针对同一冻结输入执行四次独立完整分析：A1/A2 为 `off`，B1/B2
为 `soft`。顺序按冻结输入 hash 确定为 ABBA 或 BAAB，降低固定顺序和服务端时漂影响。

冻结身份分三层：`market_snapshot_hash` 只覆盖主周期/HTF 完整 Kline 与 compact context；
`cohort_id` 覆盖 protocol、Provider/settings，以及实际 `pa_agent` 源码与 Prompt 资产摘要；
`frozen_input_hash` 绑定行情、VPA catalog 与 cohort。代码或知识版本变化后不得混入同一质量队列，
但同一行情也不能因版本变化被重复计为独立样本。四臂必须：

- 使用相同 `frozen_input_hash`、catalog ID、Provider/settings 指纹和 PA Stage 1 初始请求 hash。
- 使用四个新客户端实例；禁止 fallback、增量历史、A/B 前缀复用和跨臂上下文。
- A 臂完全没有 VPA 对象和 VPA/Joint 调用；B 臂使用同一个预先冻结且重新验 hash 的 catalog。
- 在模型调用前先写入四个 arm assignment；失败、取消和未持久化 arm 不能从样本中消失。
- 允许审计 warning；retry、normalization、hard validation failure、fallback 或非 validated 调用均不算 first-pass clean。

程序从四条正式 record 外部计算 PA/订单签名、A/A 与 B/B 分歧和跨组差异，不读取模型对自身
影响的声明。报告同时给出 ITT 与 per-protocol：ITT 保留所有已分配 arm，用于暴露交付失败和
降级的真实系统效应；per-protocol 只比较协议完整、成功持久化且确实收到指定处理的 arm。
单次组间差异不能归因于 VPA，必须先用 A/A、B/B 估计模型随机波动，并跨独立 Snapshot 复核。
持续研究使用独立 `pa-agent-aabb-monitor` 进程消费生产 Shadow 的冻结 record，不进入生产分析调度。
它按旧到新顺序运行，使四个隔离研究 Paper 账户只观察当时及之后的闭盘数据；SQLite 租约按
`(pipeline_id, cohort_id, market_snapshot_hash)` 防止并发与重启重复。生产 VPA 角色曾出现 warning
不构成排除条件，否则会形成幸存者偏差；真正四臂中的失败按 ITT 保留。若四臂已完成而 Paper
暂时写入失败，只允许重新校验并重放四条 artifact，不得再次调用模型。
统计抽样单位是独立 `market_snapshot_hash`；arm、重复运行和不同 cohort 下的同一行情都不增加样本数。
反事实结果在每个 Snapshot 内先平均重复臂；相互重叠的前向观察区间再合并为 episode，避免把同一
行情路径伪装成独立样本。主配对结论使用 ITT：`no_order` 与未触发入场计 `0R`，pending/ambiguous
保持 censored，未持久化或没有结果的已分配 arm 保持 missing 并阻止统计验收。per-protocol 配对只作
处理实际交付后的诊断。订单状态、outcome 标签与 realized R 的明确冲突同样按 missing 处理。R 差、
条件交易胜率差和 exact sign test 必须与交易频率、成本和样本外表现一起解释。

## 5. VPA 理论底座

始终应用以下核心思维：

1. **Background**：先判断 PA 已定义的趋势、区间、通道或突破背景。
2. **Location**：同一量价行为位于区间边缘、趋势中段或末端时含义不同。
3. **Effort versus Result**：比较相对活动与 spread、实体、close-to-close 推进、收盘位置及重叠，不把 volume 单独解释成买卖方向。
4. **Leg/Wave Comparison**：只比较 PA 认为可比的推动、回调或测试，不把相邻窗口机械命名为 leg。
5. **Acceptance/Follow-through**：事件候选需要后续已收盘 K 线确认接受、拒绝或失败。
6. **Evidence first, label last**：先写事实、位置和后续表现，再谨慎使用 Wyckoff 标签。

模块位于 `prompt_engineering/wyckoff_vpa/`：

- `core.txt`：数据谱系、因果比较、Effort/Result、证据独立性、术语和权限边界。
- `trend_pullback.txt`：趋势、通道、推动与回调的可比性。减弱的逆趋势回调可能支持延续；减弱的顺趋势推动会挑战延续，二者都不单独证明反转。
- `breakout_retest.txt`：突破、失败突破、接受、回踩与重测。
- `trading_range_events.txt`：仅在区间背景下参考 Spring、UT/UTAD、SOS/SOW、LPS/LPSY。
- `climax_absorption_exhaustion.txt`：仅在高潮、末端趋势或相关风险背景使用。

单根 K 线只能形成候选。Spring/UTAD、Absorption、Exhaustion、No Demand/No Supply 都需要背景、位置、比较对象和后续已收盘 K 线。模型必须保留竞争解释，例如 absorption、exhaustion、双向交易或正常暂停，而不是看到高量窄幅就直接贴标签。

## 6. 程序事实目录

`VPA_PROGRAM_EVIDENCE_CATALOG` 是 VPA 唯一客观事实来源：

- 每根近期 K 线提供 raw volume、volume availability、spread/body、close location、close-to-close progress、相对前棒关系和程序比较结果。
- `body_atr` 描述实体；`directional_progress_atr` 描述相对上一根收盘的实际推进，二者不得互换。
- 因果 RVOL、百分位和均值只使用当前 K 线之前的已收盘样本，绝不回看未来 bar。PA 选定窗口与其前一等长窗口同时输出各自的因果 RVOL 中位数及两窗关系；raw volume 仍保留为事实，但不得替代时段归一化比较。
- 因果基线只有在 `baseline_sample_count >= baseline_min_samples` 时可用；当前最低样本为 8。样本不足是 `unavailable`，不是低量。
- 每根 bar 通过 `volume_state` 区分 `observed`、`zero_observed`、`missing`、`unsupported` 与
  `legacy_unknown`。Provider 明确观测到的零可以进入同源比较；TradingView 等无法区分“真零/无数据”
  的零保持 `legacy_unknown`，不得描述为低量。
- `source=volume` 只说明该 evidence 含有量字段，不等于存在 VPA 信息增量。至少一个因果基线、前棒、seasonality 或相邻序列比较关系必须为程序实际算出的 `above/equal/below`；只有 raw volume、比较器均为 `unavailable` 时必须按证据不足处理。
- `real`、`tick`、`proxy`、`unknown` 按数据源元数据解释。tick/proxy 只能做同源相对比较，unknown 不得声称真实成交量。
- `vpa-w###` 是仅针对冻结 PA handoff 已引用 `PA.WINDOW.K#` 注入的中性测量切片，提供成交量覆盖、
  因果 RVOL、可用时的逐棒时段归一化 RVOL、净推进、累计 spread、路径长度、效率和重叠；
  它不自动检测 leg、phase 或事件。
  `vpa-c` 仅为旧冻结 catalog 的可重放兼容格式，新运行不再生成。
- PA-window 切片临时派生 `net_progress_direction_relation=same|opposite|flat_or_unavailable`，仅防止
  明确方向反述；它不判断两个窗口是否构成可比的 Brooks leg。
- Seasonality 仅在精确同槽、因果边界和最小样本均成立时可用。无历史、样本不足、当前 volume
  不可用与 1D/1W 不适用必须使用不同状态，不得互相冒充。

VPA Evidence Analyst 只能引用本次 Prompt 实际可见的 evidence ID。程序从冻结 catalog 写入
`catalog_id`、`data_status`，根据 evidence 并集生成 `observations[].bar_range`，并从
`next_confirmation.state` 生成 `requires_closed_bar`；模型不得复制这些事实字段。放量、缩量、
扩张、收缩等表述必须给出明确比较对象，并与程序关系一致。解释中出现的明确 `K#` 若唯一对应
单棒 evidence，程序写入独立 `auto_bound_evidence_ids`；它不篡改模型声明的 `evidence_ids`，也不把
自动绑定当成独立量价信息。不存在、歧义或无法覆盖的 K 引用仍拒绝。

## 7. VPA Schema 与权限

PA 链先固化为 `PaHypothesisSet`：

- `primary` 与 `alternative`
- `setup_candidates`：真实 `order_type`、方向、entry/stop/TP1、可选 TP2 与可选 Setup/trigger/invalidation
- `trade_math`：程序复算 risk、TP1 reward、RR、风险与入场距离的 ATR 尺度、风险占 entry 百分比、盈亏平衡胜率、PA 主观先验和期望值；TP2 存在时另算研究扩展 reward/RR
- `window_context`：仅对 Stage 1 已引用 `PA.WINDOW` 输出 high/low、极值 K 线及各价格从低点起的数值位置，不生成“上沿/中部”标签
- `cycle_position`、`direction`、`gate_result`
- 冻结价格 snapshot hash 与完整 handoff digest

该对象只能由 `compile_pa_handoff(...)` 生成。限价单/市价单允许 `entry_rule=null`；缺少可选
Setup 或 invalidation 文案时保持 null，不得由程序猜测，也不得静默丢失合法 Entry Attempt。

`VpaEvidenceReport` 只保存：

- `catalog_id`
- `data_status`
- `summary`
- `observations`
- 每个 PA hypothesis 的 `hypothesis_assessments`
- `background_relation`
- `entry_timing_relation`
- `next_confirmation`

`JointDecisionProposal` 只保存：

- `joint_arbitration`：selected PA candidate、`evidence_used`、`decision_effect`、`reason`
- `decision_patch`：仅允许 `trade_confidence`、`trade_confidence_reasoning`、`watch_points`、
  `risk_assessment`、`invalidation_condition`

Joint 模型不复制完整 Stage 2。Validator 在冻结 PA baseline 上确定性应用补丁；需要等待确认、
偏好回测或拒绝当前尝试时，程序统一清空订单三价并形成合法 no-order 结果。materialized
`stage2_decision` 是程序派生的运行/审计结果，不是 Joint 模型协议字段。

`vpa_context` / `vpa_impact` 仅作为 records 与旧 GUI 的兼容摘要，不再是模型角色之间的正式
输入输出契约。VPA 关闭时不构建 catalog、不派生 VPA Schema，也不向 PA Prompt 注入任何
VPA 字段。

`next_confirmation.state` 只允许 `pending_closed_bar` / `not_applicable`。前者使用非空
`trigger`，程序派生 `requires_closed_bar=true`，并明确等待下一根已收盘 K 线，把
acceptance/rejection 锚定到具体 `K#` 高低点或已命名结构；若提及未来量能变化，还必须写明
因果基线、前一根已收盘棒或其他程序可复算比较器。后者使用 `trigger=null` 与
程序派生 `requires_closed_bar=false`，适用于没有独立 VPA 后续验证条件的场景，包括 neutral、
insufficient_data 或没有 PA Entry Attempt。中文 `K线` /
`K 线` 的空白差异只属于语法差异，不得改变语义判定。

`ENTRY_ATTEMPT_CONTEXT` 明确当前是否存在可评价的 PA candidate。没有 candidate 时，程序把
`entry_timing_relation` 收敛为 `insufficient_data`，不触发 LLM 重试；VPA 仍可输出背景量价信息、
竞争解释和下一根已收盘 K 线的证据观察。这不是 VPA Gate，也不降低 PA 交易频率。

VPA catalog 2.3 为每根已收盘棒提供 `later_closed_bar_count/range`，并为相对量 ratio 提供相对
`1.0` 的有符号 `deviation_pct`。前者用于区分“尚无后续数据”和“已有数据但尚未确认”；后者用于
区分比较方向与实际幅度。程序不根据偏离百分比定义放量、缩量、Absorption 或 Exhaustion，模型必须
结合 Background、Location、Effort/Result、Leg/Wave Comparison 与 Follow-through 解释。

`decision_effect` 只允许 `keep_brooks_baseline`、`require_confirmation`、`prefer_retest`、`lower_confidence`、`reject_current_attempt`。数据可用且 Stage 2 实际执行时，必须引用至少一项 Stage 1 已验证 evidence；任何非 baseline effect 还必须引用至少一项含真实相对量比较器的 evidence。无证据时只能明确 `neutral` 或 `insufficient_data`。

VPA 不得：

- 修改 `cycle_position`、`alternative_cycle_position`、`direction`、`gate_result`。
- 修改 `diagnosis_confidence`、market phase、spike/climax、support/resistance、patterns 或结构化 bar semantics。
- 改变 Brooks Router 策略文件。
- 从 no-order baseline 创建交易，或将可执行订单翻转为反向订单。
- 修改 `estimated_win_rate` 或 Stage 2 `diagnosis_confidence`。
- 仅依赖 PA 已有价格结构，伪装成独立 VPA 支持或冲突。

研究 Joint 可以影响实验臂中的 `trade_confidence`、`watch_points`、`risk_assessment`、entry timing 和
trigger requirement。`keep_brooks_baseline` 必须为空补丁；中性或证据不足不得改 baseline。
生产 Shadow 始终使用 PA-only baseline；materialized Joint 结果只属于研究 B 臂。

Validator 只判断事实与权限，不判断某个 Spring、Absorption 或 Exhaustion 解释是否专业上唯一正确。

正式 Role Validator 只把无歧义契约设为 hard error：未知 evidence ID、目录身份不一致、
结构化关系缺少相对量证据、越权改变 PA baseline、明确引用 K0，或 `K#` 超出所引 evidence/
冻结闭盘窗口。数字转抄、自由文本比较措辞、命名时段、参与者归因和 VPA 信息增量不足均为
可审计 warning，不触发重试或中断分析；这些 regex 发现不能升级成隐性交易 Gate。生产 VPA
最终 hard failure 只形成 warning；研究 Joint hard failure 只能让该 B 臂保留 PA-only，不得使
已验证 PA 分析失败。

唯一新增的自由文本 hard check 是可复算关系的明确反述：相邻窗口净推进同向/反向、带明确
K 范围的窗口方向标签、明确绑定 K 窗口的数值 high/low 区间、声称历史棒不存在任何已收盘后续棒，
以及 HTF 已引用 OHLC 点位相对当前 K1.close 的上/下方。它们不判断
Absorption、Exhaustion、Spring、Test、支撑阻力或磁力位是否专业成立。

显式 `sequence_claim` 的倒序是 hard error；两个 evidence 窗口重叠时，该关系无法证明先后，
程序只删除这一条 claim 并记录 normalization + warning，保留其余 VPA observation，避免一个
局部关系错误拖垮整个 Shadow 报告。

## 8. PA grounding 与高周期

- `ANALYSIS_FACT_CATALOG` 提供可复算的价格事实；`pa_evidence.fact_refs` 和 `theory_rule_ids` 使 PA 判断可追溯。
- `PA.BAR.K# close_zone` 只做单棒几何四分位报告；Prompt 仅展开 K1-K10 的详细几何，更长背景继续由完整 K 线表和 `PA.WINDOW` 提供，避免重复注入整窗同类事实。
- `PA.PAIR.K#.K#` 的比较方向固定为 newer bar 相对 older bar，字段使用 `newer_high_vs_older_high`、`newer_low_vs_older_low`、`newer_close_vs_older_close`。不得保留 `high_relation` 等缺少主客体的旧别名。
- PA 的 K 线阴阳、inside/outside、窗口数量、EMA 方向和程序差值必须与目录一致；精确阳阴棒数量与连续段只留在 `PA.WINDOW`，LLM 只引用 fact ID 后做定性解释。
- `bar_by_bar_summary[].reason`、signal reason、`gate_trace[].reason` 与 `htf_context` 是推断字段，不得手工复写 OHLC、EMA、ATR、比率、百分比、阳阴棒数量、价位数字或 `canonical_value` 的 key=value。模型引用 `PA.*` / `HTF.H#` 后只解释 Brooks 背景与时机。
- Brooks 的趋势、周期、Setup 和信号质量仍由 LLM 结合背景判断，程序不恢复旧 threshold router/normalizer。
- `general.higher_timeframe_enabled` 默认开启；未指定 override 时使用通用一档映射。
- HTF 只提供价格结构背景。当前周期独占方向、Gate、Router 和 Setup；冲突只能提高确认要求或风险披露。
- HTF 相关解释必须引用 `HTF.H#`，不得用 Brooks `H1/H2` 形态标签冒充高周期 K 线证据，也不得复写高周期数值。VPA 不跨周期比较 raw volume 或 RVOL。
- 自由文本中的普通 `K#`、结构化 `K#-K#` 范围与 `HTF.H#` 必须使用共享解析器。解析器需支持中文紧邻文本和常见横线，不得把 `PA.*` fact ID 内的 `K#` 当成普通 K 线引用。
- HTF 越界校验以实际注入的序号集合为准，同时检查单点与范围两个端点；`H0`、缺失序号和超出注入集合的序号均拒绝。
- HTF 文本把某个明确 `HTF.H#` OHLC 点位称作上方/下方目标、水平或磁力位时，空间方向必须
  相对当前周期 K1.close 成立；程序只核对价格在哪一侧，不决定该点位是否有结构意义。
- PA 极值声明和 VPA 比较声明只能使用同一语义子句内的 `K#`、`PA.*` 或 `evidence_id` 作为证据，不得从后续子句借用引用。VPA 逗号仅在两侧都构成独立的“引用 + 比较声明”时分句，避免将 `evidence_id: interpretation` 拆开。

Validator 的语义错误必须以真实 JSON 字段路径开头。它可以核对显式结构化引用或同一局部表达中的确定事实，但不得从自由文本中选择一个范围，再把其他分句的数字猜测性绑定到该范围；尤其禁止恢复自然语言阳阴棒计数复算器。

Normalizer 只兼容白名单内无歧义语法别名，例如 `waiting` → `pending_closed_bar`、`weakened_bull` → `weakens_bull`。每次兼容都必须写入 `normalization` 审计事件；调用仍可标记为 validated，但该 run 不得计为 `first_pass_clean`，也不得把语法兼容误算为 PA/VPA 语义错误。

重试时，审计记录保留原始失败回答、Schema 合法值和实际 retry feedback。PA Stage 1/2
继续使用带 immutable guard 的 sanitized draft；VPA/Joint 则把可解析的原始角色草稿放回
assistant turn，并要求只修复反馈列出的字段或引用、其余值逐字保持。修复结果仍以完整角色对象
为事务边界重新执行 Schema、引用、事实和权限校验；authority violation 不进入自由重写。
不得用 `{"__rewrite_required__": true}` 丢弃一份可解析草稿后重新随机生成整份高维报告。

## 9. 订单与执行安全

- Stage 1 Gate 为 `wait/unknown` 时程序短路 Stage 2 模型调用，生成一致的 no-order 结果。
- Stage 2 不得重新定义 Stage 1 的方向、周期或 Gate；出现反证时本轮等待下一根已收盘 K 线重新分析。
- Stage 1 可以让 Gate 通过但不定义当前 Setup；这表示允许 Stage 2 基于已冻结的市场框架、
  方向和 Brooks 路由独立完成 Setup、trigger、invalidation 与订单裁决。Stage 1 的
  `entry_setup` 只是框架提示；只有完整 Stage 2 计划通过校验后，程序才建立
  `PaSetupCandidate` 供 VPA 对照，并可在隔离研究中交给 Joint 裁决。
- entry、stop 与 TP1 必须各有结构依据；TP2 只有存在独立远端结构目标时才填写，否则为 null。
  Stop 先回答当前 Setup 在何处失效，信号棒极点不是通用答案。RR 是复算结果，不能通过扩大止损、
  把止损塞进噪音、缩小目标或反推胜率凑方程。
- §9.0P 计划型限价只校验做多 `entry < K1.close`、做空 `entry > K1.close`。程序不因 K1 历史影线触价猜测未来挂单失效。
- 未提供已验证 tick size 时，程序不猜测、不静默改价。
- `decision_confidence_threshold` 是模型之后的执行策略，只应用一次并写入审计 trace。

## 10. Records、数据库与复盘

JSON record 是完整审计源；SQLite `records/audit/analysis.sqlite3` 是可重建索引，当前 schema version 为 9。

索引保存：

- PA/VPA 结构化摘要和 `review_context_json`。
- `PaHypothesisSet`、`VpaEvidenceReport` 与明确 `RunOutcome`；`JointDecisionProposal` 仅存在于研究 record。
- 离线实验 manifest、冻结输入 hash、ABBA/BAAB 顺序，以及四臂的 assignment、处理接收、first-pass、持久化和失败状态。
- 每次调用的 arm、stage、attempt、request/response hash、latency、tokens 和 validator status。
- VPA observations、模型声明/程序自动绑定的 evidence、逐 PA hypothesis assessment，以及结构化
  `analysis_failures`；复盘不再依赖扫描 JSON prose。
- 每次失败或兼容归一化的原始响应、字段路径、合法枚举、实际重试提示和 normalization before/after。
- 人工真实结果 `analysis_outcomes`。
- 闭盘 OHLC 反事实结果 `counterfactual_outcomes`。

`analysis_outcomes` 只记录人工确认的执行结果。`counterfactual_outcomes` 只评估冻结计划，不代表真实 fill 或 live PnL。market order 缺真实 fill、同 K 入场与离场、同 K 同触 stop/target、突破跳空越价等情况必须保留为 unsupported/ambiguous。

每条 record 的反事实只回放该 record 的正式订单，并在派生索引中标记为 `arm=official`。A/B 对照通过四条独立 record 的 experiment arm
归组完成，禁止从一条生产 Shadow record 合成第二个“VPA arm”。

常用命令：

```powershell
python tools/review_analysis_audit.py rebuild --records-dir records/pending
python tools/review_analysis_audit.py list --limit 50
python tools/review_analysis_audit.py experiments --limit 50
python tools/review_analysis_audit.py summary --min-snapshots 50
python tools/review_analysis_audit.py counterfactual --min-snapshots 30 --cost-per-trade-r 0.05
python tools/review_analysis_audit.py quality --min-snapshots 20
python tools/run_pa_vpa_aabb.py records/pending/<source-record>.json
```

## 11. 严格质量门禁

`quality` 只从完成且 protocol-valid 的 A/A/B/B manifest 加载四条 record，并同时检查：

1. `first_pass_clean`：所有调用首轮通过，无 retry、normalization、fallback 或失败状态；warning 可接受并保留审计。
2. `pa_grounding`：存在事实引用与理论规则，且无 PA 语义校验错误。
3. `vpa_grounding`：observation 引用的 evidence 确实含有可用相对量比较器，存在真正量价信息增量，且无 VPA 语义错误或越权。仅有 `source=volume` 或 raw volume 不计为通过。
4. `aabb_protocol`：冻结身份、四臂分配、隔离客户端、处理边界和持久化内容均有效。
5. `cohort_consistency`：一次质量评估只能包含一个 Provider/settings/源码/Prompt cohort。
6. `repeatability`：A/A 与 B/B 的外部决策签名分歧率不超过预设上限。
7. `performance`：每次调用和整轮的 latency/token 数据完整并满足阈值。

默认性能阈值为：call p50 100 秒、call p95 150 秒、run p50 220 秒、run p95 300 秒、run token p50 180k、p95 250k、B/A latency ratio p95 2.2、B/A token ratio p95 1.35。门禁至少需要 20 个独立行情 Snapshot；repeatability 默认也需 20 个独立 Snapshot。重复实验不会增加样本数。样本不足返回 `insufficient_sample`，不是通过。

`quality` 默认选择最近一个完成且 protocol-valid 的 cohort；使用 `--cohort-id` 可显式复核旧 cohort。
`summary` 与 `counterfactual` 也支持该参数。跨 cohort 的默认汇总必须标记为 descriptive only，不能用于晋级。
本次 VPA core、证据渲染与 Validator 变更会改变 source/prompt digest；后续 A/A/B/B 必须建立
新 cohort，不得与修改前样本合并计算 VPA lift。

Gate 即使通过也固定返回 `ready_for_soft=false`。正式启用前还必须独立验收 matched counterfactual outcomes、交易频率稳定性，以及包含手续费和滑点的样本外表现。

## 12. 验证与演进

核心离线回归：

```powershell
python -B -m pytest tests/unit -q
python -B -m pytest tests/property -q
python -B -m pytest tests/integration -q  # 默认排除 live；联网数据源冒烟测试使用 -m live
python -B -m pytest tests/e2e -q
python -B -m compileall -q pa_agent tools
git diff --check
```

离线测试只能证明确定性链路、Schema、权限和统计实现。模型的 PA/VPA 专业判断必须用修复后新生成的 Shadow records 复核，并跨品种、周期、市场状态做样本外评估，禁止围绕单次 BTC、黄金或某一个时间周期调 Prompt。运行时语义规则不得包含品种名、某次 run ID、某个行情价位或仅为通过单条记录而放宽的阈值；新增事实规则至少要验证跨价格尺度不变性，并确认它约束的是可复算事实而非交易解释。

理论与数据语义参考：

- [TradingView Relative Volume at Time](https://www.tradingview.com/support/solutions/43000705489-relative-volume-at-time/)
- [TradingView Volume data notes](https://www.tradingview.com/support/solutions/43000502040-volume/)
- [StockCharts Wyckoff Method](https://chartschool.stockcharts.com/table-of-contents/market-analysis/wyckoff-analysis-articles/the-wyckoff-method-a-tutorial)
- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)

外部理论只用于定义概念和证据边界，不直接导出固定阈值、胜率或交易结论。

## 13. 多资产运行与性能边界

多资产不会改变 PA/VPA 权限链。每个品种先独立冻结主周期、HTF 与 VPA catalog，再把不可变输入
交给有界调度器。调度器不生成 Setup、不解释量价，也不因为队列压力修改交易结论。

同一品种/周期严格 FIFO 单飞；不同资产按 `monitoring.max_concurrency` 并行。每个任务创建独立
client、assembler、validator 与 orchestrator，禁止跨资产共享模型调用状态。任务以 source、venue、
symbol、timeframe、snapshot 和 cohort 去重，满载时明确返回拒绝原因，不用“保留最新”覆盖旧快照。

运行性能分成 queue latency 与 execution latency。前者反映容量和 Provider 配额，后者反映实际模型
及持久化耗时；两者与终态写入 `monitoring_jobs`，并用 `run_id` 关联完整分析记录。调度层成功只代表
输入被准确处理一次，不代表 PA/VPA 判断正确或策略盈利。
