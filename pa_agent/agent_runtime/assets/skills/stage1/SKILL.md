---
name: pa-stage1-diagnosis
description: Use when a frozen PA Agent job requests Brooks Stage 1 market diagnosis.
---

# Brooks PA Stage 1 Diagnosis

目标是形成 Stage 1 市场诊断 Provider Draft，不做交易决策。

1. 调用 `get_job_manifest` 核对 Job identity、cutoff、role 与 expiry；调用 `get_market_core` 读取 PA Agent 选择的当前阶段协议与最小核心 Context。
2. 调用 `list_allowed_knowledge`，严格按 `load_order` 顺序用 `read_knowledge_module` 完整读取每个 `loading_policy=mandatory` 的模块。它们就是 Windows 成熟 Stage 1 当前实际选择的 Brooks 协议与知识，不得跳过、截断或用 Skill 摘要替代。若 `evidence_index.upstream_artifact_keys` 含 `previous_analysis`，再调用 `get_upstream_artifact` 读取冻结的上次分析，只把它当待复核上下文，不继承其结论。
3. 按 `evidence_index.bar_count` 与 `price_frame_chunk_size`，用 `get_price_frame` 从 K1 到最老 K 分块完整读取一次当前价格窗口。该帧只含 OHLC 与程序测量，不含 volume；这是完整看图基线，不能用少量近端 K 线替代。若 `htf_evidence_ids` 非空，必须用一次或多次 `get_htf_evidence` 完整读取这些 ID。
4. 先通览完整窗口的时间顺序（K 编号越大越早），建立 primary 与有证据的 alternative 竞争解释；没有实质替代解释时按 Schema 保留空值，不强造两个故事。记录能区分二者的确认和失效条件，而不是先选方向再找证据。
5. 只对争议点调用 `list_fact_ids` 后按 ID 使用 `get_pa_evidence`，必要时按小区间复核 `get_bars`；HTF 只能从 `evidence_index.htf_evidence_ids` 选择并用 `get_htf_evidence` 读取。完成 mandatory 基线后，新增理论缺口才用 `search_allowed_knowledge` 和 `read_allowed_knowledge`。PA Stage 1 禁止读取或引用 VPA evidence。
6. 按 Brooks 顺序判断：市场周期与 transition risk → Always In/方向力度 → 所处位置 → Breakout/acceptance 或 range behavior → pattern keys。突破发生不等于获得接受；后续确认只能来自当前截止点前已有闭盘 K 线。需要比较自主选择的窗口时用 `measure_price_window`；不让数值工具替你选择腿或分类行情。
7. 检查每个关键判断是否有当前 Snapshot 的 fact refs 或理论 rule refs；无法 grounding 时降低诊断强度或保留 unknown。
8. 输出 Stage 1 draft。不得生成 Setup 订单、entry、stop、target、position size 或最终交易决定。

Gate 由 Host 唯一 materializer 从 Draft 物化，模型不补静态 Gate/trace。完整证据下的低确定性可以 unknown；缺少必读资源或预算耗尽必须按 Runtime AGENTS 返回 incomplete，身份矛盾返回 review_required。不得把执行失败写成市场诊断。
