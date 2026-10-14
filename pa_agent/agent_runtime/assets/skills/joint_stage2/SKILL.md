---
name: pa-vpa-stage2-decision
description: Use when the Host explicitly selects joint_stage2_agent_protocol_v1 with validated Stage 1, Brooks Router and an accepted VPA report; not PA-only Stage 2 or research arbitration.
---

# PA/VPA Joint Stage 2

输出分工：`joint_analysis` 解释 PA 与已验收 VPA 报告的支持、挑战及选择/等待取舍；
`decision` 提交最终方案及行动条件；`decision_trace` 仅回答对应路由节点的问题。
同一理由不必在三处完整复述，但不得省略改变交易决定的反证、目标障碍或失效依据。
上游市场诊断与 VPA 报告已通过各自校验，使用其结论及限定条件，不重新执行两个角色。
只调查能改变当前候选、价格锚点或等待条件的缺口；完成所需比较后输出当前决策。

在同一 Frozen Snapshot 中形成联合 Draft `pa_vpa_stage2_draft_v1`。不是先造 PA 订单再让 VPA 改单，也不是 research-only 仲裁。本 Skill 不授权启用 pipeline。

调用下面的唯一入口后，先阅读随包交付角色协议的 `runtime_guidance`；调查限制和唯一输出/失败模式以本次冻结合同为准。扩展未交付时不采用其流程。
1. 首先调用一次 `get_role_baseline`。定位基线末尾的 `stage2_handoff_receipt`：`stage2_handoff_receipt.read_plan_complete` 和 `stage2_handoff_receipt.terminal_resource` 确认本轮读取已结束；回执给出 Provider Contract、角色协议、Role Skill、validated Stage 1、Brooks Router、PA hypotheses 和已验收 VPA 报告在 `resources` 中的精确路径。按路径读取实际值，再按顺序阅读其余 resources，不把最后一个模块文本结束臆测成截断。`mandatory_pa_delta_v1` 只表示 price-only PA 材料可从同 Snapshot Stage 1 会话继承；VPA 会话本身始终隔离，只有本次冻结且已验收的结构化报告进入 Stage 2。回执或工具若实际失败，不伪造 Draft，由 Host 根据审计 fail-closed；不重跑 Stage 1、不另造基线。
   新冻结 Job 的 `pa_hypotheses_lineage` 由 Host 复算确认：`pa_hypotheses` 是 Stage 1 派生投影，其 `source_digest` 是投影摘要，不应与 Stage 1 artifact 字段或摘要逐项相等。模型不复审快照身份、投影和 Router 的机械交接；实际市场证据与上游专业解释有冲突时，在 Router 内保留反证，必要时合法等待。
2. 新基线的 `mandatory_modules` 全部是 `loading_policy=mandatory`；按 `stage2_handoff_receipt.mandatory_knowledge_sources` 核对每个 ID 的来源：`current_baseline` 按精确路径及 `load_order` 阅读完整原文，`verified_stage1_continuation` 使用同 Snapshot Stage 1 已读且 Host 校验的完整原文，不要求在本轮 resources 再次出现。Role Skill 原文位于回执的 `locations.role_skill`，不属于 knowledge module 数量。digest 是 Host 的冻结校验身份，不要求你重新计算。此简短清单替代重复的章节目录，不减少授权知识。Brooks 是唯一 Setup 框架，VPA 只提供辅助证据。不能用 Skill 替代原知识、案例和反证。
   只有 `runtime_guidance.layered_knowledge` 明确存在时，才按所交付的隔离研究协议读取可选章节；正式任务仍完整必读。
3. 按回执 `market_context_sources` 定位价格与 HTF：fresh 基线阅读本轮完整 price_frame 与全部冻结 HTF；delta 只在 Host 对应继承标记为 true 时使用 Stage 1 已读全量材料。回执 `experience_entries` 标明冻结经验数量和本轮路径，存在时完整阅读，经验只对照、不覆盖当前事实。始终按 K 大到小理解时间顺序；新增必读交付失败时不使用部分证据判断，由 Host fail-closed。
4. 在冻结Stage 1/Router内识别真实候选，比较最多三项有意义方案与等待，不强凑数量；分别说明背景、位置、即时信号或未来触价、结构失效和TP1障碍。读取完整竞争假设；review 不独立授予 Setup，复核知识不授予反向下单权限，最终候选依冻结primary与既有PA执行契约，不改方向/Gate。只有runtime_guidance.stage_challenge明确存在时按该研究扩展处理实质争议，否则不启动挑战。
5. 阅读完整 accepted VPA report，分别处理背景/位置支持与即时 entry timing 支持。高量不自动等于吸收，低量不自动等于无需求；解释依已选知识、provenance、相对基线和后续已闭盘响应。`stage2_handoff_receipt.accepted_vpa_report.citable_evidence_ids` 是报告引用集合，供 `joint_analysis.vpa_evidence_refs` 使用；它不是原始 catalog 的读取权限。PA 角色的 `evidence_index` 不列原始 VPA ID 是正常隔离，不代表已验收报告无效。只引用报告集合内的 ID，不直读原始 VPA catalog、不重做 VPA 角色；最终 catalog 一致性仍由 Host 校验。
6. 按争议查询精确 PA/HTF evidence 或知识 section，必要时 `measure_price_window` / `project_price_leg` 核对锚点算术。工具不选腿、不推荐目标；测量 digest 不属于 Draft fact_refs。
7. 综合已读证据与研究扩展允许的意见，保留反证。合成最终决策：VPA 可影响等待/入场倾向、候选选择和主观先验，不越过 Brooks。完整“数据不可用”报告允许明确仅价格依据；VPA 任务失败或缺报告由 Host fail-closed，不能当 neutral。证据完整但 PA/VPA 专业解释分歧未解决时，给出有依据的合法等待/No Trade，不靠关键词新增硬 Gate，不把资源失败伪装成市场判断。
8. `joint_analysis` 必填 support、challenge、selection_vs_wait、entry_reachability、target_barriers、invalidation、vpa_evidence_refs；引用来自被验收的 VPA 报告。在 entry_reachability / selection_vs_wait 中说明已存位置证据是否影响入场锚点或等待条件；无相关证据不强求影响。前置 VPA 无 Entry Attempt 的 timing 不适用，不等于背景/位置数据不可用，不要求重跑 VPA 或先造 PA-only 订单。等待不编概率；交易概率是未校准联合主观先验。结构 stop 不为 RR 移动；TP2 无独立依据为 null，不影响 TP1 资格。
9. 用 Runtime `result` 封套只提交 Draft，不输出 `analysis_role_stop_v1`；不计算 position size、执行结果或修改 Risk / Trader Equation。最终校验与交易权限只属于 Host。
