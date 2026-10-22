# 运行可靠性与 VPA 语义修复研究

研究日期：2026-10-21；本地实施及复查：2026-10-22。当前为**已本地实施，未提交/发布，模型质量未验收**。
第1–17节保留原研究时点的事实与设计；第18–19节记录实施与复查，不能把旧“尚未实现”当作当前状态。

本轮只读审查源码、历史测试与主任务回传的脱敏运行证据，执行不调用模型的本地契约重放，
并核对 Brooks / Wyckoff 第一手资料。以下建议不是已经生效的交易规则，也不授权重跑生产分析、
修改历史记录或生成订单。其他运行问题由本文后半部分统一补充。

## 1. VPA 问题的结论

**值得修复的是阶段契约混义：前置 VPA 尚无具体订单，却被要求填写当前订单的时机关系；
程序再统一写成 `insufficient_data`，下游模型容易把“不适用”读成“当前不能交易”。**

推荐给新冻结任务增加明确的前置 VPA 作用域：只输出可回答的背景、位置、量价比较、反证和
独立后续条件；不再要求评价不存在的 Entry Attempt。是否存在 Entry Attempt 是 Host 已知的
阶段事实，应与 `data_status` 分开表达。联合 Stage 2 仍唯一决定 Setup、Entry、结构 SL、
对应退出协议下的目标与交易价值；不增加模型阶段，不提前伪造 PA-only 订单。

不能把本次所有 `wait` 判为错误。当前显式 Joint 没有“VPA timing 不足就强制 wait”的程序 Gate，
历史上同样 `setup_candidates=[]`、`entry_timing_relation=insufficient_data` 也能产生合法交易。
修复目标是减少无效信息和错误前提，不能通过提高交易率验收。

## 2. 已证实的源码链路

| 环节 | 当前行为 | 权威来源 |
| --- | --- | --- |
| 当前显式链路 | Stage 1 → Validator → Router → 前置 VPA → 联合 Stage 2；没有先生成 PA baseline 订单 | [ADR-0025](../architecture/ADR-0025-joint-stage2-pipeline.md)、`pa_agent/orchestrator/two_stage.py::_run_predecision_vpa` |
| PA handoff | 前置 VPA 固定调用 `compile_pa_handoff(record.stage1_diagnosis, None, frame)` | `two_stage.py`，当前约第1139行 |
| 候选身份 | `_candidate(...)` 只从已有 Stage 2 的合法可执行订单生成；只有 Stage 1 时 `setup_candidates=[]`，即使 Gate=proceed | [pa_handoff.py](../../pa_agent/ai/pa_handoff.py) 的 `_candidate`、`compile_pa_handoff`；[ADR-0008](../architecture/ADR-0008-canonical-record-and-validation-boundaries.md) 第4项 |
| 适用性 | `_entry_attempt_context` 将空 candidate ID 集合投影为 `applicable=false`、`reason=no_pa_setup_candidate` | [prompt_assembler.py](../../pa_agent/ai/prompt_assembler.py) 当前约第815行 |
| 输出形状 | `VpaEvidenceReportDraft` 和 canonical report 都要求 `entry_timing_relation`；Relation 只有 supports / neutral / challenges / insufficient_data | [role_contracts.py](../../pa_agent/ai/role_contracts.py) |
| 物化 | 没有候选时，`validate_vpa_evidence` 强制把 timing 改成 `insufficient_data`，记录 normalization；不改背景关系 | [role_validator.py](../../pa_agent/ai/role_validator.py) 当前约第764–780行 |
| 下游交付 | 联合 Stage 2 的 baseline receipt 校验完整 VPA report，明确要求其中存在 `entry_timing_relation` | [evidence.py](../../pa_agent/agent_runtime/evidence.py) 当前约第351–365行 |
| 下游决策 | 显式 Joint Validator 校验报告身份、引用、Provider Draft，并复用唯一 PA 校验与物化器；没有读取 timing 枚举来强制 wait | [json_validator.py](../../pa_agent/ai/json_validator.py) 的 `validate_joint_stage2`、[pa_provider_contracts.py](../../pa_agent/ai/pa_provider_contracts.py) 的 `joint_stage2_reference_issues` |

“没有具体订单候选”不是“没有结构位置”或“没有可研究的 Setup 假设”。Stage 1 已提供周期、
方向、主/竞争假设、支撑阻力和引用窗口，这些足够让 VPA 检查相关位置的已发生量价行为。
Stage 2 才负责判断这些材料能否构成正式可执行方案。程序不能为让 timing 适用而从 Stage 1
自动补出 Entry、Stop、Setup 或可执行 candidate。

需要区分旧研究代码：[role_validator.py](../../pa_agent/ai/role_validator.py) 的
`validate_joint_decision` 存在“neutral/insufficient timing 必须保持 PA baseline”的检查，
但它属于已有 PA 订单之后的旧研究 Arbiter，不是当前 `validate_joint_stage2`。不能因为搜索到
该检查，就断言当前 Joint 有相同程序 Gate，也不能为本次修复删除其历史实验约束。

### 2.1 无模型调用的确定性重放

本轮使用既有测试 fixture `VALID_STAGE1`、`make_frame`、`_vpa_payload`，调用真实 handoff、
适用性投影和 VPA Validator。结果：

```json
{
  "gate": "proceed",
  "candidate_count": 0,
  "entry_attempt": {
    "applicable": false,
    "candidate_ids": [],
    "reason": "no_pa_setup_candidate"
  },
  "catalog_data_status": "limited",
  "validator_ok": true,
  "draft_timing": "challenges",
  "canonical_timing": "insufficient_data",
  "canonical_background": "challenges",
  "normalization_reason": "no_pa_setup_candidate"
}
```

这里 timing 由“有没有订单”决定，并非由该 fixture 的市场证据决定；数据质量与背景挑战仍
保持各自状态。这个重放证明字段混义，不证明 fixture 代表真实市场或 VPA 解释正确。

再调用现有 `tests/unit/test_joint_pipeline_runtime.py::run_joint` 的 Direct/Codex 全 mock 链，
两者均按 `stage1 → vpa_evidence → stage2` 各一次，`candidate_count=0`、timing 为
`insufficient_data`，但接受可执行 `突破单`、`exception=null`。因此现行契约并未机械阻断交易。
该测试只有预制模型响应，不证明真实模型理解；一次本地 tokenizer 获取不可达后使用既有估算
fallback，本轮不据此报告 Token 或性能改善。

### 2.2 自然运行观察与因果边界

下表来自主任务对 VPS 的只读核验回传，本子任务未登录 VPS、未读取凭据。正式复查可按
`run_id` 返回对应原始 record 和调用审计，不以本文转述替代原始证据。

| 证据 | 实际观察 | 能与不能说明什么 |
| --- | --- | --- |
| 2026-10-21 12–21点所取36条 record | 30条 completed 的 Stage 2 `decision_intent=wait`；这30条前置候选为空、VPA timing 均为 insufficient_data；另6条为运行失败 | 值得检查等待理由，不能将30条都认定漏单；失败与 No Trade 分开 |
| BTC 20点，`ddaa03a5fb0c4efdb273da05eb7405da` | 理由一方面提到回撤支撑处设想限价、支撑测试尚不可靠，另一方面引用“VPA…没有足够的入场时机证据” | 无订单时机占位进入了实际取舍；支撑不可靠仍可能是独立合理等待理由 |
| BTC 21点，`94e014253990403f8829fd805a7a80e1` | 理由包含 K1 不是合格信号棒、深位置需新的结构与失效判断；因素中写“VPA背景中性，入场时机insufficient_data” | §9.0P 路径与 timing 语义需盲评；不能只凭一句话否定全部 PA 理由 |
| XAU 19:05，`522b3b59c306403c855e9f0f0da50673` | “报告没有给出入场时机确认”与阻力测试/拒绝证据不足共同进入等待理由 | 需要拆开“当前协议没问得到”与“位置反证确实未解决” |
| 10月19日 BTC trade，`0c700e755b3142d797d5bf7794f92314`；另有 `26334e12fb1448289f0f5285173eb65a`、`6be80005c52a4038aedea1b150b1e144` | 同样空前置候选、timing insufficient_data，仍有模型 trade 限价 | 直接反驳“这个枚举必然导致程序不交易” |
| 10月20日旧 PA 模式成功62条 | 同样全部 wait | 不能将近期全 wait 单独归因固定RR模式 |

当前实证支持：**字段的阶段语义不清，并已进入部分真实等待叙述，具有需要修复的偏置风险**。
尚未证实：移除字段会改变多少交易；哪些现有 wait 不合理；改后成交率、胜率、收益或模型质量
会改善多少。仅比较生产前后交易次数不能识别因果贡献，行情、主观随机性和退出模式都会混杂。

## 3. 三角色的判断依据

### 3.1 PA 交易员

计划型限价与信号棒触发是不同的交易路径。项目教材
[二元决策 §9.0/§9.0P](../../prompt_engineering/二元决策.txt) 已明确：没有合格信号棒时，仍须
评估背景驱动限价；合格的结构计划可以没有当前入场棒；§9.7 的未触发状态不能单独否决这种
计划。它要求真实背景、合法方向、结构位置、失效依据和合理交易数学，绝不要求无条件挂单。

Brooks 官方问答说明，不同市场背景可采用不同进场方式；区间中的限价交易需要背景，而强
趋势中的同类“支撑”并不当然适合反向限价。这里只据此确认“订单类型不能统一要求同一种
当前确认”，不导入文章中的加仓、双边交易或教学概率，项目自身权限仍更窄。
来源：[Ask Al: Trading range days price action](https://www.brookstradingcourse.com/ask-al/trading-range-days-price-action/)。

PA 视角的修复应让 Stage 2 先辨别当前是“已有信号、计划未来触价、还是确实没有有效 Setup”，
再使用独立 VPA 证据。不应要求先有一个订单，让 VPA 确认后才允许产生该订单，这会形成循环
前提。也不应因为格式不适用就强行下单，结构不足时仍然 wait。

### 3.2 Wyckoff VPA 交易员

VPA 可以在最终订单出现前，评价 PA 已指定区域的 Effort/Result、相关波段比较、测试中的供需
变化以及实际接受/拒绝；这些是位置证据，不等于批准某个未定义的精确 Entry。事件必须有背景
和足够已发生响应；缺少未来确认就保持未决，不能把未来条件当事实。

Wyckoff Analytics 对供需、Effort/Result、ST/Test、SOS/LPS 的说明支持这种上下文分析；其
示意图并不把每种事件都规定为所有行情的必需条件。将这些材料用于本项目“前置位置证据”的
安排，是架构推论，不是该来源验证过的 LLM 效果或收益结论。
来源：[Wyckoff Method](https://www.wyckoffanalytics.com/wyckoff-method/)。

`neutral` 表示已评估但没有明显方向性增量；`insufficient_data` 表示所讨论问题的证据不足；
`not_applicable` 表示这个问题当前没有对象。三者不能互换。“不需要订单时机字段”也不意味着
忽略可信反证。某次支撑测试中持续强供给、明显下行跟随，仍可挑战 PA 假设并使 Stage 2 等待。

### 3.3 量化与软件架构师

当前把三个维度挤入一个关系枚举：阶段适用性、数据/比较器覆盖、专业证据关系。这增加下游
误读的机会，也使统计中的 timing insufficient_data 无法直接解释。

Host 已知“Stage 2 还没执行”，可准确物化作用域和不适用状态；是否支持/挑战、结构能否交易
仍由模型决定。此设计符合 [ADR-0001](../architecture/ADR-0001-analysis-authority.md)、
[ADR-0008](../architecture/ADR-0008-canonical-record-and-validation-boundaries.md)、
[ADR-0018](../architecture/ADR-0018-evidence-context-and-provider-drafts.md) 和
[Codex Analysis North Star](../knowledge/codex-analysis-north-star.md)。

需要控制兼容成本：旧 `off/shadow` 及研究 Arbiter 在 PA Stage 2 后评价真实订单，仍有当前 timing
语义；不能全局删字段，也不能把已冻结历史 Job 改成新语义。修复应在一个明确版本入口分派，
复用现有验证、物化和记录链，不复制第二套 VPA 或订单实现。

## 4. 方案比较与最小推荐

| 方案 | 做法 | 收益 | 局限与风险 | 判断 |
| --- | --- | --- | --- | --- |
| A：只增加 applicability/说明 | 保留前置 report 的 timing=insufficient_data，在 Joint 旁加 not_applicable 和禁止误读说明 | 开发量较小，可改善显示 | 同一包仍同时写“不适用”和“数据不足”；模型仍须消解两种表述；现有 Prompt/Skill 已有类似说明 | 不作为根治，最多是旧记录只读展示兼容 |
| B：按作用域交付 VPA | 新前置协议不要求/交付订单 timing；Host 物化前置作用域和 applicability，原证据完整保留；旧后置协议不变 | 从问题和数据形状上消除无效问题，无新增模型调用；不用凭空补订单 | 需要同一版本贯通 Schema、Validator、Direct/Codex 投影、repair、record 与读者 | **推荐，属于最小完整修复** |
| C：先出候选或追加后置 VPA | Stage 2A 先生成完整候选，VPA 判断 timing，Stage 2B 再定案；或订单后再跑 VPA | 可以针对精确 Entry/SL 做量价评议 | 新增串行调用、预算和失败边界；容易循环修价；Stage 1 提前造可执行候选还会扩大权限 | 本次不选；未来只有证据证明精确订单级 VPA 的增益覆盖成本时另立研究 |

不推荐“允许模型把 insufficient_data 改成 neutral/supports”或取消安全检查来消除字面上的不足。
那只是把缺失伪装成认可，既污染证据，也限制模型诚实表达不确定性。

现行说明已包括：`_VPA_ROLE_PROTOCOL` 的不适用说明、PromptAssembler 的 Joint 输出规则、
`joint_stage2/SKILL.md` 第8项、`vpa_evidence/SKILL.md` 第8项。因此单纯在末尾再加一句禁止，
没有解决输入对象混义，还会继续增加重复条款。

## 5. 推荐方案的完整语义

### 5.1 三个问题各自归位

| 问题 | 所有者 | 新前置协议的表达 | 不允许的推导 |
| --- | --- | --- | --- |
| 这份 VPA 是否正在评估一个已存在订单的时机？ | Host，依据真实阶段与已验证 handoff | 明确前置 scope；当前订单时机 `not_applicable`，原因是订单尚未形成 | 不能推导市场没有机会，不能反向生成候选 |
| 冻结数据、provenance 和比较器是否可用？ | Host 测量与 catalog | 保留现有 available / limited / unavailable、逐证据覆盖及限制 | limited 不等于所有证据失效；无基线不等于低量 |
| 已发生量价证据怎样影响背景、位置或竞争解释？ | 独立 VPA 模型 | 保留 observations、hypothesis_assessments、background_relation、next_confirmation | 无诊断增量不强制支持；真实不足不填 neutral；不越权给订单 |

建议新协议名字表达阶段，例如 `predecision_vpa_scope_v2`；具体命名在实施 ADR 确认，本文不
提前新增全局能力开关。新任务 canonical 层用明确版本区分前置报告与旧后置报告，不通过缺字段
猜版本。前置模型 Draft 移除 `entry_timing_relation`，其 Provider Schema 不再要求也不授权填写
该字段；当前专指订单时机的 `finding_kind=entry_timing` 同样不作为前置必答分类，已有背景、
effort/result、sequence、acceptance/rejection 足够表达已发生证据。历史事件的时序分析仍保留。

Host 写入报告作用域和“不适用”的确定性原因，不由模型复述。联合输入使用新作用域的完整
已验收报告，**不同时携带旧 timing=insufficient_data 占位**。这些是新任务的正式契约，不是在
旧报告上静默删字段或重标。旧记录的原 report 保持原字节；展示可以派生“不适用（前置报告）”，
并注明是显示解释，不能将原枚举改成 neutral。

不需要增加强制 `diagnostic_increment=true/false`、每个位置强制支持、VPA分数或新交易 Gate。
“完整读取后未发现诊断性增量”可由既有 summary、空 observations 和实际关系表达；比较器的
availability 继续逐证据保留，不用增加一套自动判定诊断价值的指标。

### 5.2 Stage 2 如何使用，而不是再等一次 VPA 批准

Stage 2 在同一调用中完成两个逻辑问题，无需引入新的 API 阶段：

1. 依据冻结 Stage 1/Router、完整 PA/HTF 证据，比较当前真实候选与等待；信号触发方案按对应
   信号路径，背景驱动限价按 §9.0P，不因“没有当前信号棒”跳过后者。
2. 把已验收 VPA 中与所讨论位置真正相关的支持、挑战和条件纳入选择。保留 VPA 反证，但可以
   明确说明其无诊断增量，或某项量价条件与本次计划无关。最终 Entry/结构 SL/交易意图仍由
   Stage 2 判断，程序只核对事实、权限、合法几何、真实 tick 与订单数学。

`next_confirmation` 仍是 VPA 区分竞争解释的后续观察，不是所有 PA Setup 的审批 Gate。
若 Stage 2 认为该确认对它当前选中的交易论点确实必需，则应等待并说明“哪项未决证据、影响
哪个结构、满足什么条件才改变决定”；若已有 PA 结构充分，不能仅因一般性的未来观察就
把所有可计划订单改成等下一根。这个判断保持 LLM 所有，不能用关键词正则替它裁决。

“依据可明确仅为价格”与“运行回退到 PA-only pipeline”不同：

- VPA 正常完成、数据确实 limited/unavailable 或无诊断增量时，Joint 可按原授权说明其局限，
  使用充分 PA 依据决定 trade、wait 或 reject；不要求 VPA 必须改变交易。
- VPA 工具、必读交付、Schema、身份或预算失败时，当前显式链仍停止，不可伪造中性报告，也
  不可把半途失败换名为合法 wait。旧 Shadow 的失败保留 PA-only 规则不变。

固定 RR 模式依 [ADR-0027](../architecture/ADR-0027-selectable-fixed-risk-reward.md)：LLM 仍须
评价实际入场、结构 SL 和相应目标路径；不能把“允许 PA 依据决策”理解为不评价固定目标障碍，
更不能移动止损或上调胜率来凑 1:1.5。原 PA 目标模式亦保持独立结构目标要求。

### 5.3 应继续 wait 的反例

| 情形 | 为什么等待仍合理 |
| --- | --- |
| PA Gate 未通过、方向权限不明、Setup 不在唯一 Router 范围 | VPA 不能替 PA 创造权限 |
| 价格在区间/通道中部，缺少有依据的可执行结构位 | 删除 N/A 字段不创造位置价值 |
| 支撑仅是一个数字，尚无足以支撑当前交易论点的结构依据或明确失效点 | 结构论证仍应由 Stage 2 完成，不能因为想挂单就虚构支持 |
| 当前论点是突破/反转确认，但已收盘信号、跟随或关键拒绝尚未成立 | 对这个特定论点，确认是实质条件；不等于所有限价都必须等确认 |
| 量价证据确实显示关键支撑持续受供给压制、有效下行跟随，PA反证尚无法化解 | 属于真实挑战，不能被 not_applicable 元数据抹掉 |
| 合理结构 SL 过远、目标前有重要障碍，当前主观先验不足以支持交易者方程 | 不为成交率收紧止损、扩大目标或编高概率 |
| 需利用尚未发生的市场信息才能确定 entry/失效依据 | 未来信息不能在本轮当成事实；可记录明确观察条件 |

反面也要测试：在 Gate/方向/位置/结构风险均合理、符合 §9.0P 的样本里，不得单凭“前置报告
没有订单时机评估”或“当前入场棒未触发”认定整个计划不成立。这里只要求比较路径完整，
并不预设最终一定 trade。

### 5.4 相邻 PA 语义也须一起厘清，但不能机械放宽

**第一处歧义：§9.0P 的“价格靠近支撑/阻力/边界/EMA”没有明确指 K1.close 还是拟议 Entry。**
同一节同时说明等待未来回撤/反弹、允许无当前入场棒，并只要求 Entry 位于 K1.close 的合法一侧。
如果把“当前已经站在边界”当成所有未来限价计划的必要条件，就容易反复出现“当前不在支撑，
未来到支撑时再分析”，使一个可在当前结构中完整定义的计划永远无法预先挂出。

但也不能直接改成“只要远处有支撑就挂单”。应把两类位置分别表达：

- **K1.close 的当前位置**：用于当前市场背景、结构是否仍有效、从当前到 Entry 的路径及价格
  距离/潜在等待的专业判断；当前较远可以成为具体风险，不能在程序中添加任意距离 Gate。
- **拟议 Entry 的位置**：必须锚定本轮已存在、可引用且与方向/Router相容的结构；不能是等
  未来形成的新支撑、未来更小信号棒或新 Wyckoff 测试。未来触价只负责触发当前已完整定义的
  计划，不负责补出现在尚不存在的 Setup 或结构 SL。

建议新版本将“计划位置必须有当前结构依据”作为清晰问题，再让 Stage 2 判断当前位置、路径
与背景是否使这个计划有价值。若模型认为必须先观察触价后的新结构或闭盘确认，当前应 wait；
不能一面要求触价后重新分析，一面提交自动触价成交的被动限价单。既有 `entry_rule` 文本不是
可执行的“先确认后挂单”条件，参见 [ADR-0017](../architecture/ADR-0017-deterministic-facts-and-execution-semantics.md)。

历史 `0c700e755b3142d797d5bf7794f92314` 在当前低位仍提供上方未来反弹限价，说明系统曾接受
两种位置不重合的计划；它不证明任何远端挂价都合理。当前“靠近”的文本确有可核实的歧义，
但把解释稳定为 Entry锚点、取消模型自行理解的当前临近前提，**可能改变策略选择性**，不能
仅称“无行为影响的文字修正”。须新知识/协议版本、新cohort，正反例与匹配回放独立验证。
不新增 fixed-distance、ATR距离、TTL自动放宽或程序推荐价位。

**第二处歧义：已收盘信号、入场触发与后续跟随是三个时点。**

| 状态 | 当前应说明的事实与决策边界 |
| --- | --- |
| 已有合格已收盘信号，计划的未来触发价尚未触发 | 尚无入场棒是 pending；在既有许可下可以形成计划，不必先虚构未来入场棒或其跟随 |
| 已发生突破/入场尝试，需要判断其有效性 | 只检查截止快照时真实已有的后续棒；未发生的跟随保持未知；如所选Setup确需确认则wait |
| 当前 K1 不适合当信号，但更早已收盘信号可能仍有效 | 应核对原信号是否失效、是否已错过/触发及当前背景，不能仅因“最新K1不漂亮”把信号替换成下一根；是否仍可用由LLM判断 |
| 没有可用信号，也没有合格 §9.0P 计划 | 明确等待什么未来事实；不要求每次都找出更早信号凑交易 |

当前代码已有这种区分：[json_validator.py::_check_signal_chain](../../pa_agent/ai/json_validator.py)
允许限价/突破计划的 `entry_bar={bar:null,strength:not_triggered,freshness:pending,
follow_through:null,still_valid:null}`；真实市价入场仍要求具体入场棒及 `follow_through=true`，
本建议不放宽它。本轮无模型重放使用既有 fixture，K2已收盘信号+突破单pending的 signal-chain
检查为空错误；计划限价 Entry=2035、K1.close=2043 的同类检查亦为空错误。它只证明机械契约
允许未触发方案，不证明这些测试价格代表当前可交易机会。

教材“当前K1低质量信号棒处理”末句直接要求当前轮次 wait，与 §9.0=否仍须评估 §9.0P 的
总路径容易相互争夺解释；§9.5/§9.7 的“无跟随偏等待”也须按具体路径限定。实施时应在原
知识位置给出一次明确作用域：**所选交易论点确实需要新信号，且其他已授权计划不成立时**，
才按这个等待分支处理；不能在每轮末尾简单重置为“等下一根更好”。保持知识锚点/案例完整，
不把真正需要跟随的Setup改成无需跟随，不新增信号年龄硬阈值。

这里能确认的是状态与措辞冲突风险，无法仅凭文字断定近期每次wait都因此发生。新Prompt即使
只做语义澄清，也会改变模型分支权重，仍须版本化并纳入同快照匹配/盲评；不能用“只是澄清”
绕过策略行为验证。评审的目标是避免错误前提，同时保留需要等信号、位置未成立、已失效等反例。

## 6. 实施范围与复杂度控制（未开始实施）

顺序应先确定一个版本化语义，再让所有消费者共用它，避免只在 UI 或 Skill 局部修饰：

1. 明确前置/后置作用域、Host字段与模型字段、旧记录读法；更新 ADR-0025 的新任务增补，
   不重写旧 ADR 的历史研究身份。
2. 在现有 `role_contracts.py` / `role_validator.py` 内按已知版本分派，复用全部事实引用、
   序列、provenance、authority与唯一物化逻辑。不能宽松接受未知 scope，也不复制新 Validator。
3. `two_stage.py` 的前置调用冻结 scope；PromptAssembler、Provider Schema、repair保护路径、
   Codex publisher、FrozenEvidenceGateway 与 mandatory receipt 从同一契约派生。Direct/Codex
   获得同义内容，保留全部原授权知识和证据，不能只在一个 Backend 隐藏旧字段。
4. 新前置报告通过 canonical record、审计、质量检查与只读 Dashboard；复盘显示“不适用”
   与真正数据不足。旧报表不得因新字段缺失而把历史成功记录判坏或补零。
5. 删除本次版本下相互重复或已不适用的 timing 指令，不在旧句子上叠加新例外。教材知识保持
   Background/Location/Effort-Result 和反证；阶段字段只由角色协议解释，不复制到每个知识模块。
6. 完成非 live 回归及冻结兼容验收，再进行经用户授权的模型研究；不拿本地通过当上线证明。

涉及的直接消费者还包括 `pa_agent/orchestrator/role_call.py::_STABLE_ROLE_PATHS`、
`pa_agent/records/quality_gate.py`、`records/projection.py`、`records/audit_store.py`、
`operations/trading.py`。需审查它们是否假定 timing 永远存在；保留旧协议条件，不做全仓字段
替换。新增设计无需数据库 migration；若实施中发现必须变更持久化结构，另行说明迁移、回退
和验证，不能在研究文档中预先视为已授权。

发布仍需按 [ADR-0022](../architecture/ADR-0022-runtime-resource-and-release-reproducibility.md)
和 [ADR-0024](../architecture/ADR-0024-codex-analysis-backend.md) 排空 Host/Worker，核对恢复
阻塞、固定 Runtime/Prompt/Schema 身份；旧 Frozen Job 不在线迁移或跨版本续接。新的 Runtime
摘要和 cohort 必须记录；若已有新版本 record，应使用能读新旧契约的兼容构建回退，不能直接
让只识别旧 report 的安装包接管。

## 7. 如何验证，避免以“多下单了”自证成功

### 7.1 不调用模型的确定性验收

- 为当前歧义建立最小回归：新前置 report 在 Gate=proceed/候选为空时，模型 Schema不要求
  timing，Joint输入不含旧 insufficient_data 占位，Host作用域明确为不适用；但所有背景、位置、
  量价 evidence ID、next_confirmation、provenance 与原始冻结身份完整保留。
- 验证 complete+neutral、complete+insufficient、limited、unavailable、强支持、强挑战均能按
  各自真实语义通过或停止，不从“不适用”生成支持，不从资源失败生成 neutral。
- 用 mock 响应证明合法 §9.0P 计划可以通过，也证明缺结构、逆方向、非法引用、未来事实、非法
  Entry/SL/TP、方程不通过仍不能执行。配对保留同一数据下的合法 wait；测试不要求多交易。
- 补齐两种位置与三种时点的反例：当前不在边缘但Entry锚点当前已定义；远端位置需未来造结构；
  已收盘信号+未触发入场；关键跟随尚未发生且确为Setup前提；旧信号仍有效/已失效各一组。
  程序只校验状态、引用、时序和合法价格，位置有效性与是否应wait由盲评检查，不写规则替模型判断。
- 真实旧 Frozen Job/历史 record 按原 Schema、Skill 与 report 重放；旧 off 不泄漏 VPA，旧
  Shadow 正式 PA 结果不变，旧 A/A/B/B Arbiter 的 timing/baseline 权限不变。
- Direct/Codex、repair、停止、HTF、PA/固定RR、records/audit、web投影与 wheel资源全链回归；
  新旧契约、未知版本、工具失败都应有明确结果。三角色审查实际字段与样本，不只看测试绿灯。

### 7.2 先做盲评，再考虑新的有界模型对照

先冻结复查样本，保留本次 wait、历史同样 timing 的 trade、明确应 wait、明确无 VPA 增量、
真实量价强反证、数据不足及 §9.0P 正反例，不按未来盈亏或最终是否成交筛样本。评审者只看当时
冻结 1h/4h、原始知识与证据、候选理由，不看未来价格；分别标注：

1. 等待依据是实质结构/反证、协议不适用被误读、还是无法判定；允许多原因同时存在。
2. 是否正确区分信号触发与计划型限价；是否真正比较了已授权方案与等待；是否保留关键反证。
3. Entry/结构 SL/目标路径是否有当时证据；是否把 VPA 数据局限写成不存在的支持或必需审批。

不要求评审者为每张图给唯一“正确订单”；专业分歧保留并报告一致度，事实/权限错误单独核验。
历史文本盲评只能定位错误前提，不能证明新协议会使真实模型改善。

后续若获准调用模型，可预注册两层实验，均使用固定旧快照、不接订单、不改生产：

| 层次 | 设计 | 可识别的结论 |
| --- | --- | --- |
| 输入语义消融 | 在同一已验证 Stage 1/Router/完整 VPA观察下，对照旧 timing 占位交付与新的显式前置作用域；A1/A2为旧交付、B1/B2为新交付，四个独立 Stage 2 客户端，ABBA/BAAB顺序 | 缩小为“交付语义”对 Stage 2 的影响，A/A与B/B衡量随机性；不是VPA增益实验，也不是整链性能证明 |
| 完整候选协议比较 | 旧完整 Joint 链与新完整 Joint 链各重复两次；同冻结主/HTF/catalog、相同模型/effort、同退出模式与tick、全新独立会话；预登记全部四臂，控制其他版本与设置 | 新方案的端到端可靠性、专业质量、调用成本与耗时；不能将Stage 1自身随机波动全归因VPA字段 |

这两种新比较须有独立版本化研究身份、assignment与cohort，**不能直接冒用**当前
`pa_vpa_aabb_v2`。该既有协议的 A=off、B=旧研究soft，是原 PA/VPA增量协议，不能把 B 偷换成
新版 Joint 后仍称原实验通过。若未来要重新声称 VPA 对 PA 的净收益，应另按既有授权研究协议
和相应晋级程序验证，不能由本次旧/新交付对照代替。

所有真实模型调用必须之后单独获用户授权；本轮未执行上述四臂。实验不重跑洗掉失败，不过滤
无交易、repair、missing 或超时，不共享会话；旧/新输出、知识、scope、Schema、Runtime均冻结。
结果同时报告 ITT、per-protocol、A/A、B/B分歧；相同 `market_snapshot_hash` 不重复计独立样本。

### 7.3 接受标准与收益边界

工程接受标准是可验证的不变量：权限/事实/未来信息错误和静默历史改写为0，缺必读不得接受，
新前置输入不再带伪装成数据不足的 N/A字段；原 PA、Shadow、研究和订单数学回归不退化。
质量接受标准是盲评中错误前提减少、关键反证/合法等待保留，预注册非劣界限与失败处理，不在
看到结果后改标准。不以 trade比例、成交量、单次盈利或输出更肯定作为通过条件。

小规模 matched replay 只能作为语义冒烟，不能证明收益。需要收益结论时按
[quality_contract.toml](../../config/quality_contract.toml) 当前至少300个独立快照等原有门槛，
同一cohort、预注册样本外范围，重叠前向窗口合并episode；每条record只回放自己的正式订单。
No Order/未触发按既有ITT口径，pending/ambiguous为censored，missing保留并阻止验收；OHLC不能
判定的路径不补零，不把反事实当模拟盘实成交。净收益、成本、尾部风险和覆盖分别报告。

## 8. 本段研究的未验证边界

- 已核对源码的结构性混义、旧/新链路权限差别，并完成无模型契约重放；不是新实现验收。
- 已有生产片段表明 timing 占位进入部分等待理由，但其因果贡献、所有等待是否合理仍未验证。
- 没有因本研究更改模型、Prompt、Skills、Schema、交易规则、配置、历史记录或VPS。
- 没有证据保证方案能增加合理交易、降低亏损或提高收益；其原理收益是让角色只回答当前
  可回答的问题，消除错误前提，并保持“事实和思考决策分开”。
- §9.0P 中 neutral / Always-In 的已有表述，以及更多跨教材规则是否存在独立冲突，不能由本次
  timing 结论一并判定；如需调整方向权限，必须另行按专业证据和授权讨论。

## 9. 其余问题的证据与处置边界

以下为主任务的本地源码核验、VPS原位只读查询和仅GET的OKX Demo核验。不包含下单、撤单、
模型调用、服务重启、配置修改、账本修复或游标重置。所有数字是有时间范围的观测，不是长期SLA。

### 9.1 运行失败不能与模型等待混算

截至北京时间2026-10-21 21:48，12–21点所取36条分析record中，30条完成且原始模型意图均为wait；
6条失败：12点BTC/ETH为本地熔断拦截，16点ETH Stage 1、17点BTC VPA、21点XAU Stage 2为CLI
exit 1，18点BTC VPA为180秒预算停止。36是运行记录数，可能含同一市场任务的不同尝试，不是36个独立行情样本。

三次exit 1分别约8.9/27.9/5.1秒，可信分类仅为`Codex failed without an availability signal`。
它们不能据此归因为JSON错误、网络、额度或模型能力。Worker的PID拒绝与OOM计数均为0，服务未重启；
这些证据也不保证其他外部故障不存在。专属CLI日志库为空，Worker未保存原始stderr，不能倒推出丢失诊断。

后续只读扩展中，33条完成记录的整链耗时中位数约350.2秒、描述性P95约451.6秒、最大469.2秒；
成功VPA调用约60–163秒。该成功子集排除了超时，不能用它证明180秒已足够，也不能把各阶段P95相加当整链P95。
失败VPA的部分元数据在`exception.attempts`中存在，在`call_attempts`中却可能缺失；须核对下述观测传递路径。

## 10. OKX成交同步：恢复历史衔接，并修复空闲期水位

### 10.1 已确认根因，不再只是猜测

[okx_demo.py::_pages/snapshot](../../pa_agent/execution/okx_demo.py)使用`/api/v5/trade/fills`，且只有找到
上次`billId`才认可增量覆盖；没有新成交时，[demo_store.py::apply_snapshot](../../pa_agent/execution/demo_store.py)
不更新成交游标。只要这个成交ID滚出接口保留窗口，后续空响应永远不能找到它，`fills=false`就持续存在。

OKX官方区分最近三天的`fills`与最近三个月的`fills-history`，后者需要`instType`；分页`after`向更旧记录
推进，不能把任意空页或一次HTTP成功当成所需历史区间已完整。来源：
[成交接口](https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-transaction-details-last-3-days)、
[历史成交](https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-transaction-details-last-3-months)、
[官方分页说明](https://www.okx.com/docs-v5/trick_en/#pagination)。

本次由原执行用户在原私有credential上下文中仅发GET，凭据未输出或复制；账本仅以`mode=ro`读取：

| 核验 | 结果 |
| --- | --- |
| 当前成交游标年龄 | 约93小时，超过三天 |
| Demo最近成交接口 | 成功返回0条，找不到旧游标 |
| 同账户Demo历史成交接口 | 可用，返回29条；旧游标位于第1条，没有更近的前缀成交 |
| 本地成交与历史重叠 | 本地18条全部能精确匹配，无缺失 |
| 重叠关键字段 | ordId/billId/side/fillPx/fillSz/fee/feeCcy/fillTime逐字段核对，0冲突 |

本地无模型探针也复现：同样空窗口，无游标时coverage=true，有旧游标时coverage=false。
所以本次阻断来自“最后一次成交ID”被当成“最近一次完整同步”的证明；不是发现了未解释的新成交。
历史接口中的额外旧记录不等于本地漏记本轮策略成交，不能把它们自动补成正式策略交易或改动账本起算点。

### 10.2 推荐实现

1. 将“最后成交身份”与“已完整观察到的时间边界”分开。保留原fill/bill cursor，在现有metadata中记录
   各流水的覆盖水位、查询范围和恢复状态。覆盖指明账户、流水类型、过滤条件、固定from/to及交易所时间，
   只证明该有界区间的API可见覆盖，不证明未来永无晚到事件。完整空响应也能推进到本次固定to，
   不能直接写成本机now或伪造新的成交ID。
2. 正常轮询查询已覆盖边界之后的有界区间，保留边界重叠和晚到事件的复核；只在区间位于接口保留期内、
   分页完整且身份/过滤一致时确认覆盖。已有有效近期观察水位后，不再强求93小时前的最后成交ID必须出现在
   近期响应中，否则空闲期问题仍会复发。时间过滤采用接口对应的`ts`语义；真实成交展示仍用`fillTime`，二者不混用。
   活跃订单仍经原精确订单查询、成交与净仓核对，不能仅靠时间水位认定无迟到成交或无敞口。
3. 超过近期窗口、旧水位未知或未找到锚点时，转同账户历史接口接回已存锚点/证明所需区间覆盖。本次旧游标
   可直接接回；不是删除cursor、忽略差异或重新初始化账户。账单`bills`也应按其自身保留窗口采用同样原则，
   不能把fill的窗口或排序字段直接套给bills。
4. 每轮仍有请求/页数预算。恢复进度绑定固定from/to、账户、过滤条件、原anchor和下一页after，
   页数耗尽时保留该进度和明确的`backfill_pending`，下一轮继续同一区间，不能每轮从首页重新卡住。
   整个区间完整后只原子推进到原固定to；恢复期间新出现的头部事件须再补齐到新快照范围，不能把旧页完成当作
   已追平当前时间。恢复使用现有metadata/events，不另建常驻进程。
5. 按已有账户绑定和`(instId, tradeId)`去重，重叠事件复核不可变成交字段。相同身份不同价格/数量/费用不能被
   `INSERT OR IGNORE`悄悄掩盖，应保留冲突审计并阻断新增入场；已有可信历史不覆盖。
6. 无法从历史接口恢复、账户不匹配、时间倒退、事件冲突或超过可取历史时，保持缺口并列出缺失范围；
   活跃计划继续按既有精确订单观察和保护预算管理，不能因为全账户历史未齐就放弃现有保护。
7. 只有账户、订单、algo、持仓、成交、账单、合约覆盖均满足原条件，且活动计划与净仓对账一致时才恢复ready。
   旧rejected/no_trade/过期Intent不补发；恢复后只处理正常授权且仍新鲜的新分析。

### 10.3 验收、数据风险与回退

覆盖空闲超过3天/7天、完整空区间、固定区间恢复期间新增头部成交、晚到事件、断网跨窗口、分页超过单轮预算、重复边界、相同时间多成交、部分成交、
相同ID冲突、账户错配和历史不可恢复。以固定HTTP fixture验收，不靠反复真实下单制造数据。
VPS先一致备份账本和元数据，再按原身份只读对账；修复后的自然轮询应从blocked恢复ready，原18条及原PnL不变。
本次实际GET已验证历史接口对该Demo账户可用，仍不能保证未来所有账户/接口状态相同。

风险是新增覆盖水位或补入缺失流水会改变执行器对完整性的判断。写入前须有恢复区间、原锚点和哈希审计，
并把事件写入与水位提交放在同一事务。无须迁移现有表；若实现发现需要schema迁移，先补正式迁移设计。
回退只能使用理解新增水位的兼容reader；旧reader会再次因旧cursor阻断，不能把阻断当成数据坏了而清库。
若恢复后已有新单，不能直接回滚到旧账本快照抹掉它们，须按活动计划兼容流程恢复。

## 11. 复盘：按订单绑定历史分析，不依赖最近30次

### 根因与实际影响

[trading_page.py](../../pa_agent/trading_page.py)的Demo复盘只在最近分析数组按run_id查找；
[trading.py::_analysis](../../pa_agent/operations/trading.py)只选最新30项，而Demo流水允许150个计划。
Demo订单自身无review字段，[safe_demo_projection/safe_composed_projection](../../pa_agent/operations/demo_trading.py)
还会清洗未在白名单中的字段。Paper旧订单已有独立补查，Demo没有。当前5笔Demo订单都不在最近30项中，
且投影无review；5份原分析的PA理由和VPA摘要均仍在库里。原页面脚本内存复现与此一致。

### 推荐实现与验收

- 由Host按最多150个当前展示订单批量读取分析索引，复用已有只读预算与`_review`投影。Dashboard仍只读JSON。
- 从已验证DemoIntent投影最少的源身份与分析绑定，核对run_id、monitoring task、pipeline、源品种/venue/provider、
  timeframe及对应冻结身份；不能只把OKX合约字符串替换成Binance符号就认定属于同一分析。
  可核对的摘要必须比较同一种身份，不能混比PA价格hash与市场/成交量hash。
- 在Host最终组合投影中绑定review，再贯通两次allowlist；页面读取订单自身review。复盘缺失/索引失联/身份不符时
  显示明确状态和原因，不能用最近一次同品种分析顶替，也不能生成新复盘内容。
- 保持最近分析30项和订单150项，保留原文本长度上限、textContent防注入及读超时。无需新HTTP端点、新数据库或模型调用。
- 回归必须点击“滚出30项后的Demo旧订单”，并覆盖三品种映射、旧v1/v2、来源错配、两轮JSON清洗、数据库不可用、
  长文本/恶意HTML与交易事实独立显示。真实VPS对照5份原文，不改原记录、订单状态和收益。

## 12. CLI异常：保留可行动的诊断，严格限制恢复权限

### 根因边界

已知三次exit 1的具体外部原因无法从现有证据恢复；这部分不能声称“已经找到并彻底修复”。
已确认的本地缺陷是观测不足和兜底分类不够准确：
[codex_worker.py::_classify_failed_process](../../pa_agent/agent_runtime/codex_worker.py)把未知CLI退出归入OutputError，
[codex_analysis_backend.py::_worker_failure](../../pa_agent/ai/codex_analysis_backend.py)再映射invalid_request。
因此没有可用Draft的进程失败也会显示为“输出被拒绝”，并非已证实模型违反Schema。

[OpenAI官方非交互文档](https://learn.chatgpt.com/docs/non-interactive-mode)提供JSONL的turn/error事件；
流中也有推理和工具内容，所以不能直接保存整个stdout来补日志。本方案保留既有CLI隔离，不为排障更换Backend或模型。

### 推荐实现

1. 在Worker边界产生一份版本化、限长的机器诊断，贯通Spool→Backend→exception/call_attempts→审计→面板。
   记录来源、失败phase、exit code、可信terminal code/HTTP状态、request/job/thread绑定、超时来源、已知usage覆盖。
   区分进程未启动、MCP初始化失败、取证失败、Provider错误、预算停止、输出解析和业务Validator拒绝。
2. 只读取可信进程事件/明确系统信号，不让agent文本、tool正文或无关启动warning冒充故障authority。
   只保存白名单代码、数字状态、安全模板；未知正文仅保存指纹和分类，不保存原始stderr、认证细节、Prompt或隐藏推理。
3. 未知CLI退出使用明确的`execution_unknown`诊断，不能继续暗示JSON/Schema错误；它不自动变成availability，
   不触发Direct fallback、不伪造wait、未知用量不计实际0。具体落到现有failure kind或新增子类，须以最小贯通改动实现。
4. VPA失败台账也复制已有可信attempt元数据，不只挑带`collaboration`的attempt。当前生产subagent=off时，
   `two_stage.py::_run_predecision_vpa`的这条筛选会使exception中已有的延迟/usage在call_attempts摘要丢失；
   不能把观测缺失当0耗时或0token。
5. 显示“限流”“熔断等待/拦截”“VPA预算停止”“CLI异常，原因未确定”“契约拒绝”等具体原因，保留历史原值。
   验证failure kind/retryable贯通，避免外层仅凭粗状态重新授予重试。仅已确认的可用性错误走既有单一Host退避入口，
   不在CLI、Spool、阶段和调度器各叠一层重试；incomplete、专业/权限/Schema错误不自动重跑。

还须修正同类的重试权限传递风险：Stage 1/2捕获ModelCallFailure后常写粗粒度`unavailable_provider`，
而[analysis_runner.py](../../pa_agent/monitoring/analysis_runner.py)据该粗状态推导retryable，可能覆盖
`record.exception.retryable=false`。这可由源码证明存在权限丢失路径，不代表本次三次exit 1均已被自动重跑。
推荐以可信类型化失败为准，显式不可重试优先；持久化失败走既有artifact恢复，不重新分析。
用同一record构造invalid_request/role_stopped/真正availability/仅落盘失败的反例验收，避免仅修展示仍多次调用。

### 验收

固定事件fixture覆盖未知退出、结构化429、401/403、5xx、终态与warning冲突、MCP启动失败、无usage、
输出不可解析、Schema拒绝及超时；预埋假凭据、URL、JWT、工具文本与推理，证明诊断不泄露且不被注入改分类。
用本次三份原失败record验证旧读者显示“诊断不足”，不倒填新原因。下一自然同类故障必须能绑定到明确诊断，
若仍未知则保持未解决，不能仅因近期暂未复发宣称已根治历史外部故障。

## 13. 超时：先校准VPA局部预算，保持整链有界

当前默认Stage 1上限300秒、VPA180秒、Stage 2上限300秒，整链600秒；
[publisher.py::_budget_for](../../pa_agent/agent_runtime/publisher.py)、Backend剩余预算、Spool deadline、
Worker进程timeout和lease/grace必须相互一致，不能只延长某一层。

推荐第一候选：**新任务VPA由180秒增至240秒，整链先保持600秒**；旧Frozen Job按原预算执行。
这是针对已确认局部超时的有界余量，不是证明240秒最优或保证本次失败重跑会成功。
成功整链当前最大约469秒，先不普遍扩大所有阶段或降低xhigh、减少完整知识；新scope去掉无效问题后再测实际耗时。

执行timeout取各层剩余预算/原任务有效期中的最早边界；预算耗尽仍为incomplete，不转市场wait，也不自动再跑。
记录队列、取证、模型、阶段与整链耗时，保留censored超时；继续观察是否转移为Stage 2/整链超时。
若600秒成为可复现瓶颈，再以完整新分布和新鲜度要求评估720秒等候选，不事先连续加预算掩盖问题。
延长分析预算不延长原订单3h TTL、不改Entry/SL/TP或补发过期建议。

验收使用假时钟和可控进程覆盖180/240/整链边界、排队延迟、取消、进程组回收、租约过期、迟到结果及一次接收。
Publisher/Backend/Worker/Spool联测后，按实际新任务而不是重跑旧失败核验。质量指标、Token与未知覆盖同步报告。

## 14. 熔断恢复：等待探针结果，不丢掉同轮另外两品种

12点BTC/ETH在调用模型之前被拒绝。生产factory共享一个持久breaker；冷却到期只放行XAU探针，其余立即返回
`Codex circuit is open`。单探针保护合理，立即终止同轮其他任务则损失了可恢复的分析机会。
标准Half-Open防止恢复时请求洪峰，参考[Microsoft Circuit Breaker](https://learn.microsoft.com/en-gb/azure/architecture/patterns/circuit-breaker)。

推荐在既有breaker/Backend入口增加**有界、可取消的准入等待**，不增加探针模型调用或第二个调度器：

- 一个真实新任务取得探针资格；同轮其他已冻结任务在原并发/队列上限内等待状态通知。
- 探针成功后解除等待、继续各自原Snapshot；不是重跑已经失败的市场分析，也不换新快照或借用黄金结论。
- 等待与后续调用共用明确整链deadline；计时必须在准入等待之前初始化，不能沿用当前`allow()`之后才设起点
  的位置而让等待不计预算。超出原有效期/预算、取消或探针失败时给出非决策终态，不虚构wait。
- 探针资格必须在成功、异常、取消和超时路径释放；有代次/所属调用绑定，不能由旧响应清除较新的熔断状态。
- 冷却尚未到期且剩余预算不足时明确延后/拦截，不保持无界等待，不清空历史breaker强行重试。
- 记录admission_wait与实际调用分开，模型调用计数不能因等待增加；按任务而非多次提交重复计成功率。

保持同品种FIFO、三品种并发3、全局pending有界及旧失败历史；控制paused仍按原约定阻止新任务并排空已受理任务，
不因此撤旧单或自动取消已运行分析。恢复策略由Host单点拥有；
参考[AWS重试上限指导](https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_mitigate_interaction_failure_limit_retries.html)，
避免分层重试放大负载。测试覆盖三请求并发、probe成功/失败/取消、重启、旧代次迟到响应、控制暂停和快照失效。

## 15. 成套实施顺序、发布与回退

本轮只提出方案。下一轮实施须以用户最终确认范围为准，不能把本文当成已经发布或已经获得模型/下单实验许可。

| 顺序 | 实施组 | 可独立验收的结果 |
| --- | --- | --- |
| 1 | 错误诊断贯通、历史复盘读取、OKX覆盖水位与历史恢复 | 先恢复可观察性和执行就绪；不改变PA/VPA判断规则 |
| 2 | 有界熔断准入、VPA240秒候选及统一deadline | 真实调用与未调用区分，不重复执行、不丢同轮可恢复任务 |
| 3 | 前置VPA作用域、新旧报告契约、PA计划/信号时点澄清 | 新输入消除伪数据不足，合法等待和真实反证保留 |
| 4 | 固定样本盲评与经授权的旧/新契约对照，再做自然运行验收 | 以错误前提、权限/事实、可审计性、失败与成本验收，不以多开单验收 |

前三组可分别开发受控模块，阶段独立验证；本次研究不自动commit。
第1/2组工程修复可先按完整验收发布。第3组新模型语义必须先完成第7节预注册的匹配回放/盲评，
再确认是否启用；可以使用不同固定提交分批发布，不能把“整包版本协调”理解为未经质量验证同时启用新契约。
每次发布自身都要求Host/Worker/Dashboard兼容版本一致，不允许混版本交接。
保持当前三品种、500 USDT、3h、PA/fixed_rr切换、1:1.5及原主观先验/交易数学，不附带引入ICT或新执行类型。

发布前须备份受影响源码、Runtime/Schema/知识及控制状态；Host暂停并排空Codex及恢复任务，
Demo Worker在账户/活跃保护安全边界一致备份。先验证备份manifest与数据库，再协调Host、Codex、Demo、Dashboard版本。
回退构建必须能读新旧VPA报告、覆盖水位与Intent；不能只回退一个服务，也不能靠删历史或取消用户旧单解决兼容。

最终离线范围：全部非live Headless测试，PA、VPA off、生产Shadow、旧研究A/A/B/B、显式Joint、HTF、
订单数学、records/audit、运行时/Spool、Demo独立计划、网页投影/交互、打包资源及无Qt smoke；静态错误与diff检查。
新功能先红后绿，原自然失败不改写。分页/复盘/诊断修复不需要额外真实下测试单。

VPS验收：四服务身份/权限/资源一致；5份历史复盘可读；真实覆盖全部完成并无重复成交/费用；
原计划与保护保持；不补发旧建议。再观察至少连续三个自然整点的三品种任务，逐条记录调用、失败、等待/交易与执行处置；
若自然无故障，也须保留离线故障注入证据，不能以“没再发生”证明故障处理正确。
这一步是后续验收计划，本轮没有创建定时监控、调用模型或切换运行配置。

## 16. 三角色汇总与不确定性

| 角色 | 支持的设计 | 必须避免的错误 |
| --- | --- | --- |
| PA交易员 | 根据计划入场处评价Location，信号方案与未来限价分开；结构Entry/SL和是否交易仍由Stage 2决定 | 为提高交易率凭空补候选、改止损/胜率；删除真实PA反证 |
| Wyckoff VPA交易员 | 前置评价可观察的背景/位置证据，允许支持、挑战、未决或无诊断增量；真正数据不足保留 | 把not_applicable当insufficient_data或支持；把所有next_confirmation当审批条件 |
| 量化/架构 | 唯一契约入口、Frozen身份、精确订单复盘、可证明区间覆盖、单点有界恢复与可用诊断 | 第二套物化器、重复调用、隐性Gate、原始敏感日志、清游标/重写历史来报成功 |

三视角由主任务与一名协作者交叉核查，不等同三名独立专家审计。
已证实本地机制可修复；外部服务不可能保证永不失败，三次历史未知CLI退出也无法凭空恢复根因。
语义修复具有明确逻辑依据，但是否改善实际分析须按第7节验证；不能保证增加合理交易或改善收益。

## 17. 本轮研究核验

一名协作者完成三角色交叉审查，主任务已补严历史覆盖的固定区间、晚到事件与新头部追平，
以及熔断计时起点、显式不可重试优先和工程/语义启用分离。最终PA、VPA、量化架构方案审查均通过；
该结论只评价设计，不代表功能已实现、模型质量通过或允许发布。

文档全部本地链接存在、代码围栏成对、无待补占位；`git diff --check`通过。
本次实际改动仅研究报告和知识库导航/状态，未改生产代码、测试、Prompt/Skills/Schema、配置或线上账本。
原有ICT相关四项改动保持；未commit、推送或部署。研究中只使用无模型契约探针和只读现场核验，
未运行新实现的全量测试，不把研究验证冒充实现验收。


## 18. 2026-10-22 用户批准后的本地实施

本节描述用户“按照你的计划完整修复”后的实现；第1–17节保留此前研究时点的证据与未实施状态。
本轮未commit、推送、发布或更改VPS；没有新增真实模型调用、补发旧交易或删除业务历史。
此前ICT四项变更保留，AGENTS只另行修正本任务的前置/后置timing作用域，ICT内容不覆盖。

### 实际修复

| 问题 | 实现与边界 |
| --- | --- |
| OKX空闲后永久缺口 | `DemoEventSync`区分最后事件与完整覆盖时间；账户/过滤/时间绑定，近期/历史区间、最多四页、固定after续接，历史完成后继续追平新头部。完整空响应可以推进覆盖，不伪造新成交ID。 |
| 历史事件冲突 | 同身份不可变字段冲突保留旧行、写一次审计并持久阻断新入场；等价数值表示不报冲突。流水写入与水位原子提交，旧metadata可恢复，无表migration。 |
| 故障期间保护 | 当前订单、algo和净仓继续观察；元数据损坏或错误账户/stream读法保留原件、拒绝新入场，同时允许精确订单成交的保护。保护数量、Entry/SL/TP与用户独立计划规则不变。 |
| Demo旧订单复盘 | Host按最多150个订单的原task/run、source/venue/symbol/type/timeframe/pipeline/snapshot批量只读关联；30秒缓存只缓存review，交易事实仍使用新投影。两轮allowlist和页面点击使用订单自身review，缺失/身份不符有明确提示。 |
| CLI误分类与漏诊断 | 增加非availability的未知执行异常；仅投影固定phase/category/exit/HTTP/errno/预算来源，Spool核验请求/Job/Runtime/acceptance绑定。忽略agent/tool正文及任意错误原文，不保存stderr/隐藏推理。普通模型role stop不推断为超时。 |
| 错误重试权限 | Handler以可信failure kind和明确retryable为准；Schema、权限、专业停止和未知进程错误不借粗状态重获自动重试。VPA失败台账保留最后attempt，不要求有child collaboration；未知用量不冒充实际0。 |
| 熔断与预算 | 一次真实新任务作半开探针，其余有界等待Condition通知；取消、deadline和代次保护避免漏释放/旧成功清新故障。准入与发布耗时均计入原600秒，超预算不启动worker；model_calls与等待分开。新VPA首稿240秒，repair及旧研究arbiter180秒。 |
| 前置VPA | `predecision_vpa_v2`独立Provider Schema不含订单timing字段，Host写不适用；同一Validator/冻结身份分派，Direct/Codex/receipt/repair/审计/读者贯通。缺scope按旧契约，未知scope拒绝；真实背景不足、支持/反证和独立后续条件保留。 |
| PA知识作用域 | 保留§9锚点与案例，区分拟议entry已有结构、现价位置、已收盘信号、未来触价及后续跟随；无合格信号或计划仍可等待，不强凑候选或取消结构要求。 |

### 备份与恢复

修改前662个成员已逐项SHA256核验，备份在`docs/changelog/backups/20261021-runtime-remediation/`。
`source-before.zip` SHA256为`492ecdcba086b78cb5b5966d377db7d933b4b782ebf31ae91e46f5d854093e82`。
这里只备份并修改本地源码，没有迁移或回填VPS数据库。后续发布仍须一致备份现库并按第15节排空/协调。
新覆盖metadata或新报告产生后须用兼容reader回退，不重置cursor、不用旧数据库抹掉后续成交。

### 三角色交叉核查

本轮只用一名协作者，分别实现独立模块并交叉审阅父任务改动；不等同三名独立专家。
PA审查确认结构、方向、Router与合法wait保留；VPA审查确认不适用不变成支持/不足，真实反证仍可挑战；
架构审查确认单一物化器、同源Frozen身份、历史区间完整性、可靠失败权限与有界恢复。
复核发现的两项底层问题——普通role stop误报预算、发布耗时未扣剩余deadline——已修并补回归。
新scope的派生视图明确为前置/联合取证，不再误称旧Shadow；不宣称VPA造成了决策改变。

### 验证范围与上线边界

聚焦回归包括旧PA/off/Shadow、研究A/A/B/B、新Joint、新旧report/未知scope、Direct/Codex/repair、
HTF/订单数学、records/audit、三品种历史复盘点击、OKX HTTP/固定分页/重启/冲突/部分成交保护。
完整全量结果、静态检查和打包核验由下方最终验收记录固定；不把早期聚焦数量累加为独立测试数。
旧历史CLI exit1的具体外因仍不可恢复；本地观测与失败处理缺陷已修，不能保证外部服务永不失败。
新语义的真实模型匹配回放/盲评尚未执行，交易质量/收益未知，按第7/15节另行验收和启用。


#### 最终本地验收记录

- 全部非live：**2039 passed，7 deselected**，111.88秒；无失败/错误/跳过。
  两条warning来自既有可选`tvDatafeed`依赖的Python转义弃用提示，未修改该依赖。
- 使用现有Hypothesis/AnyIO测试插件，在允许本机回环通信的环境运行；禁用本地残留Qt插件。
  原EMA慢生成提示的固定种子复核5项通过；完整复验无该失败，不降低属性测试标准。
  全量初次暴露的3条旧预期已按授权的新未知异常/VPA240行为更新；额外证明未知异常不自动重试/fallback。
- 命令：`python -B -m pytest -p _hypothesis_pytestplugin -p anyio.pytest_plugin -m "not live" -q -o addopts= -p no:cacheprovider`。
  环境`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`，tokenizer使用已有本地cache；临时目录不属于项目产物。
- `ruff --select E9,F63,F7,F82 --no-cache`、`git diff --check`通过。
- 使用已有锁定构建缓存离线生成wheel；269个源码/Prompt/Runtime资源逐项与工作区字节相同，
  从解包安装位置import Headless、读取PA/VPA教材与新Schema/Skills，未导入Qt或pyqtgraph。
  wheel SHA256：`ef4aac9a4752b60609d8b37f3b14325fe4830de970a590a2435a4c713747f10b`。
  Runtime asset：`70d7dbf8239f4f18a54f7de0209f946a1d59e91a406271cc8fb98c35edc62d3b`。
- 源码备份662成员重新逐项SHA256通过；既有README/研究导航ICT变更字节保持，ICT评估原文未改动。
- 本轮pytest临时目录、构建解包、构建缓存、egg-info和过程日志清理；保留源码备份及精简验收凭证。

结论仅为**本地工程修复与非live验收完成**。本轮无Git提交、VPS发布、真实模型对照或新模拟单。
新语义仍须按第7/15节匹配回放/盲评后决定生产启用，不承诺交易频率或收益提高。

## 19. 2026-10-22 可维护性与“永远等下一根”复查

第18节工程验收不代表文字契约已无遗漏。本次复查确认仍有同族矛盾，按原所有者替换，
不通过继续加禁令、宽松校验或提高交易率来掩盖。只使用一名协作者进行 PA/VPA/架构交叉复核。

### 根因与成套修正

| 残留 | 原处修正与理由 |
| --- | --- |
| §9.0P 无信号却允许 `quality=weak` | 改为实际 Provider Schema 允许的 `invalid`；不放松 Schema，不因格式冲突触发无谓 repair。 |
| 中部/靠近边界的对象不明 | §9.0P 否分支与 §11 指向拟议 entry；当前位置、距离和路径仍由 LLM 判断，不允许未来虚构结构或任意远价。 |
| §9.5/9.7 把入场后跟随当挂单前提 | 区分已触发、尚未触发及 Setup 仍待闭盘成立的路径；较早有效信号不因 K1 质量差自动失效。`9.7=不适用` 使用已有 trace 枚举，不扩 Schema。 |
| 相邻教材仍要求先有入场/确认棒再提交 | 同步替换信号字典、逐棒检查单、上下跌通道策略、突破输出要求和 gap_bar 原句；确认实际事件与提交待触价订单分开，特定 Setup 的确认要求保留。 |
| `trade` 加文字条件误当程序会等待确认 | 唯一公共 Stage 2 contract 明确 trade=当前可提交；`entry_rule` 是解释，执行器不判断未来闭盘。依赖未来闭盘或未形成结构的资格当前仍为 wait。Direct/Codex 共用，不增加文字关键词 Gate。 |
| 新旧 VPA 平行渲染与重复完整 Schema | 共用一个输出形状，只按 scope 增减 timing；完整 Schema 留在传输与 Host。补回共同必读 core 的 signed-deviation 幅度原则，不复制整套旧协议。 |

上述替换不授予新 Setup 或反向交易权限，不修改 Router、结构止损、Trader Equation、固定目标职责、
风险尺度或真实 tick 检查。普通突破、尖峰第一棒、高潮、逆势、2BR/MTR 等已有产品限制保留。
只允许已有有效资格的订单等待未来触价；不能把“未确认”写成“已确认”，也不能要求未来成交后的跟随
先发生才允许挂单。合法 wait 的完整 Joint 回归继续通过，不把中性/不足强改为支持。

### 对持续不下单的作用与边界

本次去掉两类错误前提：不存在订单却评价它的 timing，以及待触价订单却必须先出现入场/确认棒。
它们在逻辑上会造成无谓等待或格式失败；消除矛盾使模型能够使用原本允许的 pending 路径。
但前置 VPA 不足从未是强制 wait 的程序 Gate，真实结构、方向、信号或交易者方程不合格仍应不交易。
实际模型是否减少不合理等待、入场是否更合理或收益是否改善，仍需同快照匹配回放与盲评；未新调模型。

### 复杂度和离线性能

没有增加模型角色/阶段、持久化表、依赖、枚举或策略配置。固定 mock Joint 样例前后均为
Stage 1、VPA、Stage 2 三次调用；等待/恢复预算仍有界。Direct 新旧 VPA 合并渲染，删除重复完整 Schema；
旧 Frozen/Shadow/研究 reader 因仍有调用者保留，作用域由冻结版本决定，不进入新前置路径。

同一离线样例的 `cl100k_base` 静态估算如下。它们不代表 Provider 实际计费 token、缓存、
完整多轮上下文或模型延迟；Codex 列只计算一次初始必读交付文本，不等于初次 Prompt 或总用量。

| 测量 | 原优化前源码备份 | 本次最终源码 | 变化 |
| --- | ---: | ---: | ---: |
| Direct 三阶段文本估算 token | 152,842 | 153,748 | +0.593% |
| 其中 Direct VPA 文本估算 token | 8,496 | 8,152 | -4.05% |
| Codex 三角色初始必读交付文本估算 token | 162,400 | 163,545 | +0.705% |

假回复全链 7 次暖态本地中位耗时约 103ms/95ms，只能说明未观察到明显本地 CPU 开销，
不能据此宣称提速。旧订单复盘批读 11 次：5笔中位约1.7ms，150笔中位约11.9ms、最高18.3ms；
150笔示例复盘 JSON 约1.43MB，30秒缓存仅缓存复盘，交易事实仍更新；这些内容不进入模型输入。
该样例未发现明显工程性能回退，真实模型与生产负载性能尚未验收。

### 清理、备份与兼容

复查前630成员 SHA256逐项核验，`source-before-review.zip` SHA256：
`b0e176a269c6847f8e3adab25e4b728c2ad0ab72a02340c1286c9288c7bc8d46`。
备份及精简凭证位于 `docs/changelog/backups/20261022-maintainability-review/`；原662成员备份继续保留。

旧状态页按既有维护规则归档至[历史状态](../knowledge/archive/README.md)，当前页808行缩为60行；
North Star 移除已被后续发布替代的当前状态描述，改为引用统一状态页。研究报告首部标明第1–17节历史作用域，
移除已退役 GUI 的验收条款；维护手册明确原句替换、公共契约和有调用者的历史兼容边界。
这些归档不进入 Runtime/模型资源；不删除教材案例、原章节ID、历史分析、交易记录或收益。
ICT 原有导航和评估内容保留，未纳入本轮实现。完整静态 Schema 与资源身份校验未压缩或旁路。

### 最终复查验收

- 最终全量非live：**2052 passed，7 deselected**，105.50秒；两条既有可选 `tvDatafeed` 转义弃用 warning。
  使用与第18节一致的 Hypothesis/AnyIO 插件和现有 tokenizer cache；没有连接真实模型、行情或交易所。
- 新回归通过真实 Provider Schema 与完整 Joint 物化/Validator 链，覆盖限价/突破 pending、K1/K2 信号、
  PA/fixedRR、`9.7=不适用`、合法 wait、新旧 VPA 输出形状；不只检查某句 Prompt 是否存在。
- 静态关键错误、`git diff --check`、本次文档相对链接检查通过；8个改动教材的原知识锚点完整保留。
- 离线 wheel 中269个源码/Prompt/Runtime成员与最终工作区逐项字节一致；从安装位置读取新 Schema、
  PA/VPA教材及 Skills 并 import Headless，无 Qt/pyqtgraph。wheel SHA256：
  `ad20cdc7d3cf59053dcb93d0511f65c4408c3c3f84f90cc28d91d02f0a344a74`。
  Runtime asset 仍为 `70d7dbf8239f4f18a54f7de0209f946a1d59e91a406271cc8fb98c35edc62d3b`；
  教材改动另由 Frozen manifest/资源摘要冻结，不能只用 Runtime asset 代表教材身份。
- 静态测量使用旧源码备份与最终源码各自的 `tests.unit.test_joint_pipeline_runtime.run_joint`，
  Provider 全部 mock；Codex 通过实际 Publisher/Gateway/MCP 构造必读交付，不启动真实 CLI/模型。
- 复查临时源码解包、测量/pytest目录、wheel解包和 build/egg-info清理；保留备份及精简验收凭证。

本轮仅本地复查修复：未commit、推送、发布VPS或调用新真实模型。工程缺陷的修复已回归；
模型效果、生产恢复及交易收益仍分别待验证，不承诺消除所有失败或让每次分析都下单。

## 20. 用户授权提交与有限 Demo 发布

2026-10-22，用户在知悉第19节“本地2052项通过、真实模型效果与线上恢复未验收”后，明确要求
“commit并发布”。按这项最新授权，将全部已工程验收的修复部署到既有有界 OKX Demo；
第15节研究时建议的先质量对照再启用，不作为本次用户明确授权有限运行的额外阻断。
这不是盲评/收益通过或质量晋级，也不新增真实模型研究、补发旧交易或改变执行配置。

提交前新鲜聚焦回归106项通过（7.99秒）；一名独立审阅者未发现新旧Frozen、覆盖metadata、
历史复盘或发布协调的确定技术阻断。发布须暂停排空，备份并验证四库和Demo独占状态，
协调Host/Codex/Demo/Dashboard安装，核对Runtime与Prompt资源、权限及原配置后恢复。
正式版本、备份、现场恢复与验收结果在发布完成后补充，不能把此授权段落当作已经上线。
