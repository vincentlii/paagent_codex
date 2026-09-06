---
name: vpa-evidence-analysis
description: Use when a frozen PA Agent job requests an independent VPA evidence report.
---

# VPA Evidence

目标是形成独立的量价证据草案，不替代 Brooks PA 结论。

1. 调用 `get_job_manifest` 核对 Job identity，再用 `get_market_core` 与 `get_upstream_artifact` 核对当前阶段协议、volume provenance、capability、data status、PA hypotheses 与 entry-attempt applicability。
2. 调用 `list_allowed_knowledge`，严格按 `load_order` 顺序用 `read_knowledge_module` 完整读取每个 `loading_policy=mandatory` 的 PA 已选 VPA 模块。不得固定截取前若干段，也不得用 Skill 摘要替代当前知识版本。
3. 按 `evidence_index.vpa_evidence_chunk_size` 分批，用 `get_vpa_evidence` 完整读取 `vpa_evidence_ids` 中的本次授权目录。这是原 Windows VPA Prompt 的量价信息基线，不能只抽取支持既有假设的证据。
4. 按 `Background → Location → Effort/Result → Leg/Wave Comparison → Acceptance/Follow-through` 调查；最后才考虑 Wyckoff 标签。
5. 从完整目录中为每个 observation 选择精确 VPA evidence ID；不得猜测或枚举 index 外 ID。必须同时寻找挑战 primary hypothesis 的证据，说明支持、挑战、中性或信息不足。比较必须有明确对象和同源因果基线。
6. 标签不能替代 Evidence，也不能成为第二个 Setup Router。
7. raw/tick/proxy/unknown volume 按 provenance 限制解释；不可用或比较器不足时返回 unavailable/neutral，不反推参与者意图。
8. 分别评价 primary/alternative hypothesis、背景关系、当前 entry timing 和下一根已收盘 K 线所需确认。
9. 不创建或翻转 Brooks Setup、方向、订单、胜率、Gate 或正式 Trade/No Trade。
