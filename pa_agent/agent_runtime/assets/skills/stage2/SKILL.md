---
name: pa-stage2-decision
description: Use when the Host selects price-only PA Stage 2 after Brooks routing, not joint Stage 2 or research arbitration.
---

# PA Stage 2

目标是在已验证 Stage 1 artifact 与 Brooks Router manifest 内形成交易决策 Provider Draft。

1. 首先调用一次 `get_role_baseline`，按 resources 顺序完整阅读其中 manifest、核心阶段协议、validated Stage 1 与 Router manifest，把 cycle、direction、Gate 和允许的策略文件视为不可扩张边界。有冻结经验时完整阅读随包交付的 experience_entries，保持反锚定。`mandatory_baseline_v1` 是 fresh 全量基线；`mandatory_pa_delta_v1` 只可用于 Host 已验证的同 Snapshot PA resume，并必须给出 parent job 与继承清单。其他版本或身份矛盾立即 review_required。
2. fresh 基线按 `load_order` 阅读全部 `loading_policy=mandatory` 模块原文；delta 基线完整阅读所有未继承模块，并继续使用本会话 Stage 1 已读且 digest 相同的继承模块。这里只会包含 Brooks Router 当前授权的策略分支和成熟 Stage 2 基础规则，不得跳过、截断或自行扩张到未路由策略。
3. fresh 基线阅读完整 price_frame 分块和全部冻结 HTF evidence；delta 仅在对应继承标记为 true 时使用本 PA 会话已读全量材料。无论何种交付都不能只看信号棒，必须保留背景、位置、结构和即时触发的全图关系。任一新增必读资源交付失败即停止，不用部分内容继续判断，也不为形式完整而重复拉取已验证继承资源。
4. 比较 Router 允许范围内的候选 Setup 与 No Trade。不得重新分类 Stage 1、引入未路由 Setup 或为了交易而降低门槛。
5. 对候选逐项调查：Background/Location 是否合适、signal/trigger 是否明确、entry 是否可执行、结构失效点是否真实、目标是否有独立结构依据。
6. 只按 ID/小区间复核必要的冻结价格；HTF 只能从 `evidence_index.htf_evidence_ids` 选择；Brooks 理论只读授权 section。历史案例只能对照，不能因表面相似覆盖当前图的独立判断。正式 PA Stage 2 不读取 VPA evidence。
7. 完整读完授权证据后比较交易、等待与拒绝当前尝试。证据支持且满足当前协议才输出 Trade；市场条件不满足时输出合法 No Trade，并说明等待或失效条件。必要资源缺失、预算耗尽是 incomplete，不是 No Trade；必须重写 Stage 1/Router 才能成立时是 review_required，不自动重跑。
8. Stop 回答 Setup 在何处失效；不得用固定 tick/ATR 或凑 RR 替代结构判断。TP2 没有独立依据时保持 null。
9. 不计算或修改 position size、Risk、Trader Equation、execution disposition，也不改变 Stage 1 方向和胜率语义。

对限价计划区分“未来价格触达条件”与“已经出现的信号”，不以尚未触价一概拒绝 Host 允许的被动限价单；也不将预测写成已发生事实。检查到 TP1 的结构障碍、目标空间和订单方向，不用凑 RR 改动结构 stop。

需要数值核对时用 `measure_price_window` 比较明确窗口；MM 使用 `project_price_leg` 的有符号 `origin + (end - start)`。锚点及投射是否适用由 Brooks 背景决定，工具返回价格不构成交易授权；引用源 K 线/fact，不引用测量 digest 充当 fact ID。

输出原 PA Draft v2，经 Runtime 的 result 封套提交。概率是未经校准的主观先验，不因解释更长而上调；不计算或修改 position size、Host Trader Equation。禁止为了凑齐 schema 猜测价格、Setup 或经验结论。
