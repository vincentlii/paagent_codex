---
name: pa-stage1-diagnosis
description: Use when a frozen PA Agent job requests Brooks Stage 1 market diagnosis.
---

# Brooks PA Stage 1 Diagnosis

目标是形成 Stage 1 市场诊断 Provider Draft，不做交易决策。

1. Stage 1 是当前 Frozen Snapshot 的 PA 会话起点。首先调用一次 `get_role_baseline`，只接受完整的 `mandatory_baseline_v1`，按 resources 顺序完整阅读。用其中 manifest 核对 Job identity、cutoff、role、expiry，用 core_context 读取 PA Agent 选择的当前阶段协议。以下两步的材料已完整包含在这次返回中，不重复调用各资源工具。
2. 按 manifest 的 `load_order` 阅读全部 `loading_policy=mandatory` 模块原文。它们就是 Windows 成熟 Stage 1 当前实际选择的 Brooks 协议与知识，不得跳过、截断或用 Skill 摘要替代。有 previous_analysis 时阅读 upstream_artifact 中冻结的上次分析，只作待复核上下文，不继承其结论。
3. 阅读全部 price_frame 分块，覆盖 K1 到最老 K 的 OHLC、几何、事实 ID 与程序测量，不含 volume；这是完整看图基线，不能用少量近端 K 线替代。完整阅读随包交付的全部冻结 HTF evidence。任一必读资源交付失败即停止，不以部分读取替代完整基线。
4. 先通览完整窗口的时间顺序（K 编号越大越早），按 Brooks 顺序建立初步诊断：市场周期与 transition risk → Always In/方向力度 → 所处位置 → Breakout/acceptance 或 range behavior → pattern keys。形成 primary 与有证据的 alternative 竞争解释；没有实质替代解释时按 Schema 保留空值，不强造两个故事。已有 previous_analysis 是对照，不是起始答案。
5. 找出能改变上述诊断的具体不确定点，并为每个点确定可区分两种解释的证据及确认/失效条件。先核对已交付的事实、完整知识和 HTF；突破发生不等于获得接受，后续确认只能来自当前截止点前已有闭盘 K 线，未来响应仍是条件。不要把每个字段都当成重新分析完整市场的任务。
6. 按 Runtime 调查纪律解决这些缺口：直接使用 `fact_id_index` 与 `delivery_index`，把缺失或争议 PA ID 合并到 `get_pa_evidence`；必要时按小区间复核 `get_bars`。HTF 只能从 `evidence_index.htf_evidence_ids` 选择并用 `get_htf_evidence` 复核。新增理论缺口才用 `search_allowed_knowledge` 和 `read_allowed_knowledge`。需要比较自主选择的窗口时用 `measure_price_window`；不让数值工具替你选择腿或分类行情。PA Stage 1 禁止读取或引用 VPA evidence。
7. 根据调查结果更新受影响的周期、方向力度、位置、acceptance 和假设条件，并检查其依赖关系。若反证推翻整体背景，则重新评估主解释及受影响结论；若只是局部确认，则直接保留其他已证实判断。查证后仍无法区分时如实降低强度或保留 unknown，不反复换措辞制造确定性。
8. 提交前作整体一致性复核：关键判断是否有当前 Snapshot 的 fact refs 或理论 rule refs；primary/alternative 是否与方向、周期和确认条件相容；是否把未来条件当成事实；是否已处理实际反证。复核发现新实质矛盾时回到对应缺口查证，而非机械限制调查轮数。
9. 输出 Stage 1 draft。不得生成 Setup 订单、entry、stop、target、position size 或最终交易决定。内部调查与复核不新增输出字段，不重复提交多份诊断。

Gate 由 Host 唯一 materializer 从 Draft 物化，模型不补静态 Gate/trace。完整证据下的低确定性可以 unknown；缺少必读资源或预算耗尽必须按 Runtime AGENTS 返回 incomplete，身份矛盾返回 review_required。不得把执行失败写成市场诊断。
