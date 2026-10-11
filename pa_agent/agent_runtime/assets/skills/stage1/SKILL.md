---
name: pa-stage1-diagnosis
description: Use when a frozen PA Agent job requests Brooks Stage 1 market diagnosis.
---

# Brooks PA Stage 1 Diagnosis

目标是形成 Stage 1 市场诊断 Provider Draft，不做交易决策。不得生成 Setup 订单。
禁止读取或引用 VPA evidence；PA Stage 1 保持 price-only。

1. 调用一次 `get_stage1_decision_packet`，核对可直接比较的 identity、cutoff 与 role；packet digest 已由 Host/Gateway 验证，不自行重算。完整阅读冻结窗口、全部 HTF、PA fact spine、bar_measurements、角色协议、foundation 原文；`section_index` 只列尚未内嵌原文的 section，不重读已交付的 foundation。窗口长度以 packet 为准；表格 columns 与 rows 逐列对应，保留了真实 fact ID、几何和指标。交付失败为 incomplete，身份矛盾为 review_required。
2. 基线已完整读取后，若 Job 允许 `stage1_challenge_v1` / `single_challenge_v1` 且存在一个影响诊断的具体事件争议，立即按 Runtime AGENTS 委托一次，而不是完成整份诊断后再启动。限定为该事件的边界、尺度、随后闭盘响应；给出边界源棒、事件及连续后续、相关完整 Brooks section，必要时给对应 HTF。child 不判断整份 cycle 或起草替代 Stage 1。父同期建立整体周期、过渡风险、Always In、HTF/位置、其他 patterns 及字段一致性；不要重复 child 的逐棒调查。K 编号越大越早；长期背景与近期方向分别评价，previous_analysis 只作待复核对照。没有实质争议就不启动。
3. 仅针对能改变上述结论的证据缺口取证，最多两个问题。先使用已交付测量；缺值按精确 ID 批量 `get_pa_evidence`，尚未交付的窗口或投影测量可用 `measure_price_window` / `project_price_leg`，三者共用一次 fact 配额。理论缺口直接按 packet 索引 section IDs 批量 `read_allowed_knowledge`，不先搜索。fact 与 knowledge 最多各一次、合计两次；`investigation_envelope.policy_version=stage1_investigation_v1` 时按调用尝试计数，失败也占配额，且必须先成功读取 packet。预算耗尽返回 incomplete，取证拒绝不得以成功 Draft 掩盖；child 与 pointer repair 仍受各自独立的范围和预算约束。fixed 模式不做可选调查或启动挑战。不得重复整图、开放搜索、读取 VPA 或自行重算已有事实。
4. 综合时收集 child（如已启动），按本 Job 原值核对已读引用，仅重评受影响的判断及依赖字段；失败不采用且保留审计，不改变父停止语义。形成当前最有支持的诊断与条件。保留实质竞争解释和反证；突破发生不等于接受，旧边界的回收不等于近期边界的回收。尚未发生的确认与市场模糊性可以保留，不必靠延长推理消除。检查实质一致性与字段引用后提交 JSON；不输出调查流水账。剩余资源不足按停止协议处理，不伪装成低确定性的有效分析。

Gate 由 Host 唯一 materializer 从 Draft 物化，模型不补静态 Gate/trace。完整证据下的低确定性可以 unknown；缺少必读资源或预算耗尽必须按 Runtime AGENTS 返回 incomplete，身份矛盾返回 review_required。不得把执行失败写成市场诊断。

## 输出职责

- `market_hypotheses` 承载完整的主/替代解释、支持与反证、确认和失效条件；这是诊断论证的主体。
  `confirmation_condition` 描述会加强本假设、区别于竞争解释的未来闭盘证据；`disconfirmation_condition` 描述会削弱/推翻本假设的证据。若两者描述同一种推进，明确各自的比较边界；仍不能区分时保留不确定性，不把竞争解释的确认写成本假设的确认。
- `gate_trace` 仅回答当前节点的问题，给出该问题的关键依据，不逐节点重复完整诊断；节点问题由冻结决策协议确定。
- `bar_by_bar_summary` 描述 K5 到 K1 各自对背景的新增影响；`bar_analysis` 汇总当前信号与跟随状态，不另建一套背景解释。
- 单棒文字若称新高/新低、突破或跟随，写明相较的冻结 K 区间或具体前棒，并与相应 PA.PAIR/PA.WINDOW/PA.COMPARE 事实一致；不要把未界定范围的“本轮新低”当成可复核结论。
- `key_signals` 摘取决定主判断的事实关系；`risk_warning` 写尚未解决的风险；置信度说明写证据强弱与不确定来源。各字段保留其必要信息，不用重复长文充实格式，也不以简短为由省略关键反证。
- 假设及 Gate 的结构化 fact 引用只选真实 `PA.*` ID。`HTF.H#` 在 `htf_context` 表达高周期背景；不能加 `PA.` 前缀，也不能把 HTF ID 填入仅允许 PA 的数组。引用目录并不代表其中所有 ID 都能填入任意字段。
- 用户可见解释使用简体中文；专业枚举、字段名和真实引用 ID 保持原样。价格与指标留在专用字段，文字重点解释事实的市场含义。

历史 `semantic_parity_v1` Job 的 `loading_policy=mandatory` 仍由其冻结 Skill 与 Worker 原样验收；本 Skill 的有限 packet 协议只适用于 `bounded_context_v2`。
