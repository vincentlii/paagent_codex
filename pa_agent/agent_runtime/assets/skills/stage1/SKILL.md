---
name: pa-stage1-diagnosis
description: Use when a frozen PA Agent job requests Brooks Stage 1 market diagnosis.
---

# Brooks PA Stage 1 Diagnosis

目标是形成 Stage 1 市场诊断 Provider Draft，不做交易决策。不得生成 Setup 订单。

1. `LOAD`：只调用一次 `get_stage1_decision_packet`。核对 packet identity、cutoff、role、digest；完整阅读其中 100 根 price-only frame、全部 HTF、PA fact spine、角色协议、原文 foundation sections 与完整知识 section index。交付失败立即 incomplete，身份矛盾立即 review_required。
2. `DIAGNOSE`：只形成一次初步诊断。有 previous_analysis 时只作待复核上下文，不继承其结论。
3. `GAP`：最多保留两个会改变 cycle、direction、location、acceptance 或假设排序的未决问题。每个问题必须明确缺少的证据及不同结果会改变哪个结论；其他细节不触发调查。
4. 先通览完整窗口的时间顺序（K 编号越大越早），按 Brooks 顺序建立初步诊断：市场周期与 transition risk → Always In/方向力度 → 所处位置 → Breakout/acceptance 或 range behavior → pattern keys。形成 primary 与有证据的 alternative 竞争解释；没有实质替代解释时按 Schema 保留空值，不强造两个故事。已有 previous_analysis 是对照，不是起始答案。
5. 找出能改变上述诊断的具体不确定点，并为每个点确定可区分两种解释的证据及确认/失效条件。先核对已交付的事实、完整知识和 HTF；突破发生不等于获得接受，后续确认只能来自当前截止点前已有闭盘 K 线，未来响应仍是条件。不要把每个字段都当成重新分析完整市场的任务。
6. `INVESTIGATE`：最多一次批量事实调用和一次批量知识调用，总计不超过两次 optional calls。事实缺值用精确 ID 批量 `get_pa_evidence`；理论缺口用 index 中精确 section IDs 批量 `read_allowed_knowledge`。不得重新读取整幅价格帧、列举全部 facts 或开放式搜索。fixed 组跳过此状态。PA Stage 1 禁止读取或引用 VPA evidence。
7. `RESOLVE`：只更新受新证据影响的判断。冲突仍无法消除时使用 unknown/transitioning、降低强度或保留 alternative；不得重开已完成状态、循环换措辞或为了提高置信度追加调查。
8. `CHECK`：只做一次整体一致性检查；发现不能在剩余 envelope 内解决的矛盾时降低确定性或停止，不返回调查状态。
9. `FINAL`：最后一次工具返回后的下一条响应必须是符合 Host Schema 的单个 JSON object 或合法 typed stop。不得生成 Setup、订单、entry、stop、target、position size 或最终交易决定，不输出调查过程。

Gate 由 Host 唯一 materializer 从 Draft 物化，模型不补静态 Gate/trace。完整证据下的低确定性可以 unknown；缺少必读资源或预算耗尽必须按 Runtime AGENTS 返回 incomplete，身份矛盾返回 review_required。不得把执行失败写成市场诊断。

历史 `semantic_parity_v1` Job 的 `loading_policy=mandatory` 仍由其冻结 Skill 与 Worker 原样验收；本 Skill 的有限 packet 协议只适用于 `bounded_context_v2`。
