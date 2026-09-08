---
name: pa-vpa-stage2-decision
description: Use when the Host explicitly selects joint_stage2_agent_protocol_v1 with validated Stage 1, Brooks Router and an accepted VPA report; not PA-only Stage 2 or research arbitration.
---

# PA/VPA Joint Stage 2

在同一 Frozen Snapshot 中形成联合 Draft `pa_vpa_stage2_draft_v1`。不是先造 PA 订单再让 VPA 改单，也不是 research-only 仲裁。本 Skill 不授权启用 pipeline。

1. 调用 `get_job_manifest`、`get_market_core`、`get_upstream_artifact`；核对角色协议、Snapshot、validated Stage 1、Brooks Router、PA hypotheses 和已验收 VPA 报告。缺少必要交接返回 incomplete；身份或不可变上游矛盾返回 review_required，不重跑 Stage 1、不另造基线。
2. `list_allowed_knowledge` 后按 `load_order` 用 `read_knowledge_module` 完整读取全部 `loading_policy=mandatory`。Brooks 是唯一 Setup 框架，VPA 只提供辅助证据。不能用 Skill 替代原知识、案例和反证。
3. 用 `get_price_frame` 每块最多 100 根读取完整价格窗口，按 K 大到小理解时间顺序；`get_htf_evidence` 读取全部冻结 HTF ID。有冻结经验时 `get_experience_entries` 完整读一次，经验只对照、不覆盖当前事实。
4. 在 Router 内比较有意义的方案与等待，最多三项，不强凑数量。逐项说明背景、位置、即时信号或未来触价条件、结构失效点、目标障碍和到 TP1 的空间；不得改变 Stage 1、路由、新增 VPA 专属 Setup 或翻转交易方向。
5. 阅读完整 accepted VPA report，分别处理背景/位置支持与即时 entry timing 支持。高量不自动等于吸收，低量不自动等于无需求；解释依已选知识、provenance、相对基线和后续已闭盘响应。只引用报告实际可引用的 ID，不直读原始 VPA catalog、不重做 VPA 角色。
6. 按争议查询精确 PA/HTF evidence 或知识 section，必要时 `measure_price_window` / `project_price_leg` 核对锚点算术。工具不选腿、不推荐目标；测量 digest 不属于 Draft fact_refs。
7. 合成一个最终决策：VPA 可影响等待/入场倾向、候选选择和主观先验，不得越过 Brooks。完整的“数据不可用”报告允许明确仅价格依据；VPA 任务失败或缺报告必须 incomplete，不能当 neutral。不可变上游无法兼容时 review_required；专业分歧不靠关键词新增硬 Gate。
8. `joint_analysis` 必填 support、challenge、selection_vs_wait、entry_reachability、target_barriers、invalidation、vpa_evidence_refs；引用来自被验收的 VPA 报告。等待不编概率；交易概率是未校准联合主观先验。结构 stop 不为 RR 移动；TP2 无独立依据为 null，不影响 TP1 资格。
9. 用 Runtime result 封套提交 Draft 或 analysis_role_stop_v1；不计算 position size、执行结果或修改 Risk / Trader Equation。最终校验与交易权限只属于 Host。
