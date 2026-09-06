---
name: pa-stage2-decision
description: Use when a frozen PA Agent job requests Stage 2 analysis after Brooks routing.
---

# PA Stage 2

目标是在已验证 Stage 1 artifact 与 Brooks Router manifest 内形成交易决策 Provider Draft。

1. 调用 `get_job_manifest` 核对 Job identity；调用 `get_market_core` 读取当前阶段协议；再调用 `get_upstream_artifact` 读取 validated Stage 1 与 Router manifest，把 cycle、direction、Gate 和允许的策略文件视为不可扩张边界。若 `experience_entry_count>0`，必须调用一次 `get_experience_entries` 完整读取 PA Agent 已选择的有界历史案例，并保持反锚定。
2. 调用 `list_allowed_knowledge`，严格按 `load_order` 顺序用 `read_knowledge_module` 完整读取每个 `loading_policy=mandatory` 的模块。这里只会包含 Brooks Router 当前授权的策略分支和成熟 Stage 2 基础规则；不得跳过、截断或自行扩张到未路由策略。
3. 按 `evidence_index.bar_count` 与 `price_frame_chunk_size`，用 `get_price_frame` 从 K1 到最老 K 分块完整读取一次当前价格窗口。不能只看信号棒；必须保留背景、位置、结构和即时触发的全图关系。若 `htf_evidence_ids` 非空，必须用一次或多次 `get_htf_evidence` 完整读取这些 ID。
4. 比较 Router 允许范围内的候选 Setup 与 No Trade。不得重新分类 Stage 1、引入未路由 Setup 或为了交易而降低门槛。
5. 对候选逐项调查：Background/Location 是否合适、signal/trigger 是否明确、entry 是否可执行、结构失效点是否真实、目标是否有独立结构依据。
6. 只按 ID/小区间复核必要的冻结价格；HTF 只能从 `evidence_index.htf_evidence_ids` 选择；Brooks 理论只读授权 section。历史案例只能对照，不能因表面相似覆盖当前图的独立判断。正式 PA Stage 2 不读取 VPA evidence。
7. 仅在完整证据链成立时输出 Trade。否则输出合法 No Trade，并说明等待的下一项价格行为证据。
8. Stop 回答 Setup 在何处失效；不得用固定 tick/ATR 或凑 RR 替代结构判断。TP2 没有独立依据时保持 null。
9. 不计算或修改 position size、Risk、Trader Equation、execution disposition，也不改变 Stage 1 方向和胜率语义。

禁止为了凑齐 schema 猜测价格或 Setup；边界不足时选择 No Trade 并校准 confidence。
