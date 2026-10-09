---
name: pa-stage2-decision
description: Use when the Host selects price-only PA Stage 2 after Brooks routing, not joint Stage 2 or research arbitration.
---

# PA Stage 2

输出分工：`decision` 说明选中方案或等待的决定性依据与条件；`decision_trace`
只回答各路由节点的问题；信号/入场棒字段只描述实际触发链；置信度与胜率说明
分别解释可信程度与主观概率依据。保留独立反证，不在各字段重写同一完整论证。
取证以能改变候选资格、结构价格或等待条件的问题为准；已交付的确定性测量直接引用。
必要证据齐备后提交当前结论，不为修饰文案追加取证；证据不足的合法等待与执行失败分开。

目标是在已验证 Stage 1 artifact 与 Brooks Router manifest 内形成交易决策 Provider Draft。

1. 首先调用一次 `get_role_baseline`。定位基线末尾的 `stage2_handoff_receipt`：`stage2_handoff_receipt.read_plan_complete` 和 `stage2_handoff_receipt.terminal_resource` 确认本轮读取已结束，精确路径指向 Provider Contract、角色协议、Role Skill、validated Stage 1 与 Router；按路径读取实际值，再按 resources 顺序阅读其余材料。不要在最后一个模块结束处臆测后面还有未交付内容。把 cycle、direction、Gate 和允许的策略文件视为不可扩张边界。有冻结经验时完整阅读随包交付的 experience_entries，保持反锚定。`mandatory_baseline_v1` 是 fresh 全量基线；`mandatory_pa_delta_v1` 只可用于 Host 已验证的同 Snapshot PA resume，并必须给出 parent job 与继承清单。版本或机械身份不合由 Host fail-closed，模型不复审投影或继承授权。
2. 新基线的 `mandatory_modules` 全部是 `loading_policy=mandatory`；按 `stage2_handoff_receipt.mandatory_knowledge_sources` 分清本轮 `resources` 的完整原文与同 Snapshot Stage 1 已验证继承的完整原文，不要求继承模块再次出现在本轮资源中。按 ID 与 `load_order` 阅读本轮模块，Role Skill 原文另位于 `locations.role_skill`。digest 是 Host 的冻结校验身份，不要求你重新计算。此简短清单替代重复的章节目录，不减少授权知识。旧冻结 Job 若交付 `list_allowed_knowledge`，仍按原清单定位。这里只会包含 Brooks Router 当前授权的策略分支和成熟 Stage 2 基础规则，不得跳过、截断或自行扩张到未路由策略。
3. 按回执 `market_context_sources` 定位价格与 HTF：fresh 基线阅读本轮完整 price_frame 分块和全部冻结 HTF evidence；delta 仅在对应继承标记为 true 时使用本 PA 会话已读全量材料。`experience_entries` 标明冻结经验的数量与本轮路径，存在时完整阅读。无论何种交付都不能只看信号棒，必须保留背景、位置、结构和即时触发的全图关系。新增必读资源若由工具明确报错，不用部分内容继续判断或伪造 Draft；Host 根据审计 fail-closed，也不为形式完整而重复拉取已验证继承资源。
4. 先识别 Router 允许的真实候选。若 Job 允许 `stage2_challenge_v1` / `single_challenge_v1` 且候选的位置、结构失效或 TP1 障碍有实质争议，按 Runtime AGENTS 尽早委托一次：限定该候选的一个 PA 问题，给相关 PA/HTF 原值、连续区间、必要测量锚点与完整 Brooks section，不交上游全文、VPA 或订单 Draft。父同期比较其他候选与等待，不先完成全部比较再启动。纯等待、无真实候选或无争议不调用。不得重新分类 Stage 1、引入未路由 Setup 或降低门槛。
5. 对候选逐项调查：Background/Location 是否合适、signal/trigger 是否明确、entry 是否可执行、结构失效点是否真实、目标是否有独立结构依据。
6. 只按 ID/小区间复核必要的冻结价格；HTF 只能从 `evidence_index.htf_evidence_ids` 选择；Brooks 理论只读授权 section。历史案例只能对照，不能因表面相似覆盖当前图的独立判断。正式 PA Stage 2 不读取 VPA evidence。
7. 综合时收集已启动 child，按原值核对已读引用，仅重评受影响候选及依赖结论；失败不采用且保留审计，不冒充成功或市场 No Trade。比较交易、等待与拒绝当前尝试。证据支持且满足协议才输出 Trade；市场条件不满足或专业解释有矛盾时输出有依据的合法 No Trade，说明等待或失效条件。必要资源缺失、预算耗尽是 Host 运行故障，不是市场 No Trade；不得重写 Stage 1/Router 或返回模型停止对象。
8. Stop 回答 Setup 在何处失效；不得用固定 tick/ATR 或凑 RR 替代结构判断。TP2 没有独立依据时保持 null。
9. 不计算或修改 position size、Risk、Trader Equation、execution disposition，也不改变 Stage 1 方向和胜率语义。

对限价计划区分“未来价格触达条件”与“已经出现的信号”，不以尚未触价一概拒绝 Host 允许的被动限价单；也不将预测写成已发生事实。检查到 TP1 的结构障碍、目标空间和订单方向，不用凑 RR 改动结构 stop。

需要数值核对时用 `measure_price_window` 比较明确窗口；MM 使用 `project_price_leg` 的有符号 `origin + (end - start)`。锚点及投射是否适用由 Brooks 背景决定，工具返回价格不构成交易授权；引用源 K 线/fact，不引用测量 digest 充当 fact ID。

只输出原 PA Draft v2，经 Runtime 的 `result` 封套提交；不输出 `analysis_role_stop_v1`。概率是未经校准的主观先验，不因解释更长而上调；不计算或修改 position size、Host Trader Equation。禁止为了凑齐 schema 猜测价格、Setup 或经验结论。
