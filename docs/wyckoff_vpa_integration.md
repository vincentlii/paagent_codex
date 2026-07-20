# PA Agent × Wyckoff VPA 融合架构

本文是当前 PA/VPA 链路的长期事实来源。代码、测试与本文冲突时，先核对实际实现并同步修正文档，不保留旧实现作为兼容叙述。

## 1. 目标与边界

核心分工：

> 程序测量事实；Brooks PA 定义市场框架与 Setup；VPA 补充或挑战独立量价证据；LLM 裁决当前 Entry Attempt；程序验证输出与订单合法性。

本次融合解决的是事实可靠性、分析可审计性、VPA 信息增量和实验有效性，不直接证明盈利能力，也不以单次行情结果调整规则。

- VPA 不是第二套交易系统、第二个 Strategy Router 或机械 Gate。
- Brooks Router 始终是唯一策略路由，只读取已验证的 Stage 1 枚举与 pattern keys。
- 程序不机械识别 Spring、UT/UTAD、SOS/SOW、LPS/LPSY、Absorption 或 Exhaustion。
- `soft` 尚未启用正式决策合并，运行时固定降级为 `shadow`。
- VPA 关闭时，Prompt、测量、Schema 要求和历史 VPA 字段都不得进入原 PA 链路。

## 2. 职责分配

| 层 | 拥有的职责 | 不拥有的职责 |
|---|---|---|
| 数据与测量 | 闭盘快照、K 序号、OHLC 合法性、EMA/ATR、bar geometry、相对量、因果基线、来源语义、HTF 取数 | 趋势、Setup、Wyckoff 事件、信号质量 |
| LLM Stage 1 | Brooks 周期、方向、Always In、结构位置、Setup、Gate、竞争解释 | 改写程序事实、创建不存在的 evidence ID |
| Brooks Router | 根据已验证结构化 PA 字段选择策略文件 | 读取自由文本、VPA 标签或指标阈值猜策略 |
| LLM Stage 2 | 结合 PA、策略知识和 VPA 证据判断时机、订单与风险 | 绕过 Gate、伪造价格、修改冻结字段 |
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

Volume seasonality 使用数据层提供的、早于当前分析窗口的同源历史样本。时间桶先把 UTC epoch
转换到明确的 `timezone_basis`；时区无效或样本不足时必须标记 unavailable，不得用当前窗口自身
构造看似充分的季节性基线。

## 3. 运行模式

### `off`

- 执行原 Brooks 两阶段链路。
- 不构建 VPA evidence catalog。
- 不注入 VPA Prompt，不要求 `vpa_context` 或 `vpa_impact`。
- 历史记录中的 `vpa_*` / `wyckoff_*` 字段在上下文注入前被清除。

### `shadow` + `full_prompt_ab`，默认

同一冻结的当前周期快照、HTF 背景和 Provider 配置上运行两条独立链路：

1. A 臂：完整 Brooks Stage 1 + Stage 2，不含 VPA catalog。
2. B 臂：完整 Brooks+VPA Stage 1 + Stage 2，包含 VPA catalog。
3. 两臂分别使用隔离客户端，禁止 Provider fallback。
4. 程序外部比较实际结构化输出，不接受模型自述“未改变 baseline”。
5. A 臂是唯一正式 GUI、告警和订单结果；B 臂只保存到 `vpa_sidecar`。
6. 用户置信度执行阈值在两臂模型输出完成后各应用一次，避免污染模型层比较。

完整 B 臂 Stage 1 尚未产生 Brooks Context，因此只加载 VPA Core；B 臂 Stage 2 再根据 B 自己已验证的 Stage 1 加载相关 Context 模块。不得用 A 臂诊断替 B 臂预选 Stage 1 模块，否则会污染独立反事实。

### `shadow` + `marginal_sidecar`

这是边际诊断工具，不是真实 full-prompt A/B：

- A 臂先运行并冻结 Brooks Stage 1、Router、策略文件和初始 Stage 2 请求。
- VPA Stage 1 只输出 `vpa_context`；VPA Stage 2 只输出 `vpa_impact` 或必要决策差量。
- `keep_brooks_baseline` 必须逐字段保持完整 `decision`、`decision_trace` 和 `terminal`。
- 该设计用于研究“给定同一 Brooks 判断后，VPA 增加了什么”，不能进入完整 A/B、频率或绩效统计。

### `soft`

当前仅保留配置兼容，实际执行记录为 `configured_mode=soft`、`mode=shadow`。代码中没有 dormant Soft merge。未来启用必须重新设计正式决策权限，并基于独立样本验收。

## 4. A/B 有效性

只有同时满足下列条件的 `full_prompt_ab` 才进入统计：

- 当前 `pair_identity_version` 匹配。
- A/B 使用相同冻结 Kline 与 HTF 输入。
- 两个客户端实例隔离，且不允许 fallback。
- A 的初始 Stage 1 不含 VPA catalog，B 含 VPA catalog。
- 两臂都禁用增量历史，B 不复用 A 的 Stage 2 前缀。
- 每次调用首轮通过，无 retry feedback、失败调用或非 `validated` 状态。
- A/B 都有完整 latency、token usage 和调用臂标识。
- VPA 没有改写 Brooks 所拥有的结构化语义。

`pair_input_hash` 由实验设计、counterfactual scope、原始 snapshot hash、catalog ID、Provider 指纹，以及 A/B 初始 Stage 1 请求哈希共同生成。`pair_id` 取该哈希前缀。这样 HTF、Prompt、Provider 或 VPA 注入变化不会被误合并为同一实验。旧 identity 记录保留用于审计，但不进入当前统计。

当前执行顺序固定为 A 后 B。它有利于复现，但可能存在 Provider 负载、缓存和顺序效应，因此关键快照要重复运行；结果报告必须披露重复运行的分歧，不能把一次随机输出当成 VPA 信息增量。

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
- 因果 RVOL、百分位和均值只使用当前 K 线之前的已收盘样本，绝不回看未来 bar。
- 因果基线只有在 `baseline_sample_count >= baseline_min_samples` 时可用；当前最低样本为 8。样本不足是 `unavailable`，不是低量。
- 单棒 volume 缺失或非正时标记 `volume_available=false`，不参与 volume 比较，也不能支持 `supports/challenges`。
- `source=volume` 只说明该 evidence 含有量字段，不等于存在 VPA 信息增量。至少一个因果基线、前棒、seasonality 或相邻序列比较关系必须为程序实际算出的 `above/equal/below`；只有 raw volume、比较器均为 `unavailable` 时必须按证据不足处理。
- `real`、`tick`、`proxy`、`unknown` 按数据源元数据解释。tick/proxy 只能做同源相对比较，unknown 不得声称真实成交量。
- `vpa-c` 只提供相邻三棒窗口的原始比较，不自动切分 wave 或生成信号。
- Seasonality 仅在数据层提供同品种、同来源、同时间桶的足量历史时可用。当前快照没有该历史时明确记录 `history_not_provided`。

Stage 1 必须逐字复制 `catalog_id`，只能引用目录中存在的 evidence ID。`observations[].bar_range` 由程序根据 evidence ID 并集生成，模型不得填写。放量、缩量、扩张、收缩等表述必须给出明确比较对象，并与程序关系一致。

## 7. VPA Schema 与权限

Stage 1 `vpa_context` 只保存：

- `catalog_id`
- `data_status`
- `summary`
- `observations`
- `background_relation`
- `entry_timing_relation`
- `next_confirmation`

Stage 2 `vpa_impact` 只保存：

- `background_relation`
- `entry_timing_relation`
- `evidence_used`
- `decision_effect`
- `reason`

VPA 开启时，运行时派生 Schema 必须把顶层 `vpa_context` / `vpa_impact` 同时设为 required 且类型为完整 object；不得用 `null` 代替。全量 Prompt 与 paired delta Prompt 必须使用同一字段和枚举契约。VPA 关闭时不派生该要求，也不向原 PA Prompt 注入字段。

`next_confirmation.state` 只允许 `pending_closed_bar` / `not_applicable`。前者的 trigger 必须明确等待下一根已收盘 K 线，并把 acceptance/rejection 锚定到具体 `K#` 高低点或已命名结构；若提及未来量能变化，还必须写明因果基线、前一根已收盘棒或其他程序可复算比较器。中文 `K线` / `K 线` 的空白差异只属于语法差异，不得改变语义判定。

`decision_effect` 只允许 `keep_brooks_baseline`、`require_confirmation`、`prefer_retest`、`lower_confidence`、`reject_current_attempt`。数据可用且 Stage 2 实际执行时，必须引用至少一项 Stage 1 已验证 evidence；任何非 baseline effect 还必须引用至少一项含真实相对量比较器的 evidence。无证据时只能明确 `neutral` 或 `insufficient_data`。

VPA 不得：

- 修改 `cycle_position`、`alternative_cycle_position`、`direction`、`gate_result`。
- 修改 `diagnosis_confidence`、market phase、spike/climax、support/resistance、patterns 或结构化 bar semantics。
- 改变 Brooks Router 策略文件。
- 从 no-order baseline 创建交易，或将可执行订单翻转为反向订单。
- 修改 `estimated_win_rate` 或 Stage 2 `diagnosis_confidence`。
- 仅依赖 PA 已有价格结构，伪装成独立 VPA 支持或冲突。

VPA 可以在 B 臂中影响现有 `trade_confidence`、`watch_points`、`risk_assessment`、entry timing 和 trigger requirement。Shadow 的正式结果仍使用 A 臂。

Validator 只判断事实与权限，不判断某个 Spring、Absorption 或 Exhaustion 解释是否专业上唯一正确。

## 8. PA grounding 与高周期

- `ANALYSIS_FACT_CATALOG` 提供可复算的价格事实；`pa_evidence.fact_refs` 和 `theory_rule_ids` 使 PA 判断可追溯。
- `PA.BAR.K# close_zone` 只做单棒几何四分位报告；Prompt 仅展开 K1-K8 的详细 `PA.BAR`，更长背景继续由完整 K 线表和 `PA.WINDOW` 提供，避免重复注入 100 行同类几何事实。
- `PA.PAIR.K#.K#` 的比较方向固定为 newer bar 相对 older bar，字段使用 `newer_high_vs_older_high`、`newer_low_vs_older_low`、`newer_close_vs_older_close`。不得保留 `high_relation` 等缺少主客体的旧别名。
- PA 的 K 线阴阳、inside/outside、窗口数量、EMA 方向和程序差值必须与目录一致；精确阳阴棒数量与连续段只留在 `PA.WINDOW`，LLM 只引用 fact ID 后做定性解释。
- `bar_by_bar_summary[].reason`、signal reason、`gate_trace[].reason` 与 `htf_context` 是推断字段，不得手工复写 OHLC、EMA、ATR、比率、百分比、阳阴棒数量、价位数字或 `canonical_value` 的 key=value。模型引用 `PA.*` / `HTF.H#` 后只解释 Brooks 背景与时机。
- Brooks 的趋势、周期、Setup 和信号质量仍由 LLM 结合背景判断，程序不恢复旧 threshold router/normalizer。
- `general.higher_timeframe_enabled` 默认开启；未指定 override 时使用通用一档映射。
- HTF 只提供价格结构背景。当前周期独占方向、Gate、Router 和 Setup；冲突只能提高确认要求或风险披露。
- HTF 相关解释必须引用 `HTF.H#`，不得用 Brooks `H1/H2` 形态标签冒充高周期 K 线证据，也不得复写高周期数值。VPA 不跨周期比较 raw volume 或 RVOL。
- 自由文本中的普通 `K#`、结构化 `K#-K#` 范围与 `HTF.H#` 必须使用共享解析器。解析器需支持中文紧邻文本和常见横线，不得把 `PA.*` fact ID 内的 `K#` 当成普通 K 线引用。
- HTF 越界校验以实际注入的序号集合为准，同时检查单点与范围两个端点；`H0`、缺失序号和超出注入集合的序号均拒绝。
- PA 极值声明和 VPA 比较声明只能使用同一语义子句内的 `K#`、`PA.*` 或 `evidence_id` 作为证据，不得从后续子句借用引用。VPA 逗号仅在两侧都构成独立的“引用 + 比较声明”时分句，避免将 `evidence_id: interpretation` 拆开。

Validator 的语义错误必须以真实 JSON 字段路径开头。它可以核对显式结构化引用或同一局部表达中的确定事实，但不得从自由文本中选择一个范围，再把其他分句的数字猜测性绑定到该范围；尤其禁止恢复自然语言阳阴棒计数复算器。

Normalizer 只兼容白名单内无歧义语法别名，例如 `waiting` → `pending_closed_bar`、`weakened_bull` → `weakens_bull`。每次兼容都必须写入 `normalization` 审计事件；调用仍可标记为 validated，但该 run 不得计为 `first_pass_clean`，也不得把语法兼容误算为 PA/VPA 语义错误。

重试时，审计记录保留原始失败回答、Schema 合法值和实际 retry feedback，但发送给模型的修正草稿会把失败叶子替换为 `__REWRITE_REQUIRED__`；VPA 全局契约错误以 `vpa_context` / `vpa_impact` 为事务边界整体重写。重试几何摘要只提供方向、`close_zone` 与包含关系，不重新注入 ratio/position 数字。若错误要求新增 `PA.WINDOW`、`PA.PAIR` 等证据，`pa_evidence.fact_refs` 必须被明确列为联动可编辑字段。不得把旧错误句或模型核对过程重新注入正式输出。

## 9. 订单与执行安全

- Stage 1 Gate 为 `wait/unknown` 时程序短路 Stage 2 模型调用，生成一致的 no-order 结果。
- Stage 2 不得重新定义 Stage 1 的方向、周期或 Gate；出现反证时本轮等待下一根已收盘 K 线重新分析。
- entry、stop、TP1、TP2 必须各有结构依据。RR 是复算结果，不能通过扩大止损、把止损塞进噪音或缩小目标凑方程。
- §9.0P 计划型限价只校验做多 `entry < K1.close`、做空 `entry > K1.close`。程序不因 K1 历史影线触价猜测未来挂单失效。
- 未提供已验证 tick size 时，程序不猜测、不静默改价。
- `decision_confidence_threshold` 是模型之后的执行策略，只应用一次并写入审计 trace。

## 10. Records、数据库与复盘

JSON record 是完整审计源；SQLite `records/audit/analysis.sqlite3` 是可重建索引，当前 schema version 为 7。

索引保存：

- PA/VPA 结构化摘要和 `review_context_json`。
- `pair_id`、identity version、protocol、first-pass、authority 与 comparison。
- 每次调用的 arm、stage、attempt、request/response hash、latency、tokens 和 validator status。
- 每次失败或兼容归一化的原始响应、字段路径、合法枚举、实际重试提示和 normalization before/after。
- 人工真实结果 `analysis_outcomes`。
- 闭盘 OHLC 反事实结果 `counterfactual_outcomes`。

`analysis_outcomes` 只记录人工确认的执行结果。`counterfactual_outcomes` 只评估冻结计划，不代表真实 fill 或 live PnL。market order 缺真实 fill、同 K 入场与离场、同 K 同触 stop/target、突破跳空越价等情况必须保留为 unsupported/ambiguous。

常用命令：

```powershell
python tools/review_analysis_audit.py rebuild --records-dir records/pending
python tools/review_analysis_audit.py list --limit 50
python tools/review_analysis_audit.py summary --min-snapshots 50
python tools/review_analysis_audit.py counterfactual --min-snapshots 30
python tools/review_analysis_audit.py quality --records-dir records/pending --min-runs 20
```

## 11. 严格质量门禁

`quality` 只接纳完成的 full-prompt records，并同时检查：

1. `first_pass_clean`：所有调用首轮通过，无 retry 或失败状态。
2. `pa_grounding`：存在事实引用与理论规则，且无 PA 语义校验错误。
3. `vpa_grounding`：observation 引用的 evidence 确实含有可用相对量比较器，存在真正量价信息增量，且无 VPA 语义错误或越权。仅有 `source=volume` 或 raw volume 不计为通过。
4. `ab_protocol`：pair identity、隔离客户端、冻结输入和 Prompt 差异均有效。
5. `performance`：每次调用和整轮的 latency/token 数据完整并满足阈值。

默认性能阈值为：call p50 100 秒、call p95 150 秒、run p50 220 秒、run p95 300 秒、run token p50 180k、p95 250k、B/A latency ratio p95 2.2。门禁至少需要 20 个完成样本；样本不足返回 `insufficient_sample`，不是通过。

Gate 即使通过也固定返回 `ready_for_soft=false`。正式启用前还必须独立验收 matched counterfactual outcomes、交易频率稳定性，以及包含手续费和滑点的样本外表现。

## 12. 验证与演进

核心离线回归：

```powershell
python -B -m pytest tests/unit -q
python -B -m pytest tests/property -q
python -B -m pytest tests/integration -q
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
