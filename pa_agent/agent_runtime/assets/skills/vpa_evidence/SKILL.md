---
name: vpa-evidence-analysis
description: Use when a frozen PA Agent job requests an independent VPA evidence report.
---

# VPA Evidence

目标是形成独立的量价证据草案，不替代 Brooks PA 结论。

1. 首先调用一次 `get_role_baseline`，按 resources 顺序完整阅读其中 manifest、core_context 与 upstream_artifact，核对 Job identity、当前阶段协议、volume provenance、capability、data status、PA hypotheses 与 entry-attempt applicability。下列基线材料已在本次返回中，不逐项重复调用。
2. 按 manifest 的 `load_order` 阅读全部 `loading_policy=mandatory` PA 已选 VPA 模块原文。不得固定截取前若干段，也不得用 Skill 摘要替代当前知识版本。
3. 阅读随包交付的全部 VPA evidence 分块，覆盖本次授权目录。这是原 Windows VPA Prompt 的量价信息基线，不能只抽取支持既有假设的证据。任一必读交付失败即停止，不用部分内容继续判断。
4. 按 `Background → Location → Effort/Result → Leg/Wave Comparison → Acceptance/Follow-through` 调查；最后才考虑 Wyckoff 标签。
5. 从完整目录中为每个 observation 选择精确 VPA evidence ID；不得猜测或枚举 index 外 ID。必须同时寻找挑战 primary hypothesis 的证据，说明支持、挑战、中性或信息不足。比较必须有明确对象和同源因果基线。
6. 标签不能替代 Evidence，也不能成为第二个 Setup Router。
7. raw/tick/proxy/unknown volume 按 provenance 限制解释，不能互换基线或反推真实参与者身份。已完整读到的数据确实不足时使用契约内 `insufficient_data`，不写不存在的 unavailable 关系枚举，也不以 neutral 代替缺失。工具或必读资源失败、预算耗尽返回 Runtime incomplete，不能伪造完整 VPA 报告。
8. 分别评价 primary/alternative hypothesis、背景关系和当前 entry timing。若没有 PA entry attempt，保持其不适用状态，不编造 entry。只解释当前截止点前已有的 closed-bar 响应；下一根收盘所需确认只能是未来条件，不写成已发生证据。sequence claim 必须引用实际先后证据，不从窗口交叠推导先后。
9. 不创建或翻转 Brooks Setup、方向、订单、胜率、Gate 或正式 Trade/No Trade。

成交量窗口及比较基线由 Host 冻结，不能用价格工具创造新的 VPA evidence ID 或成交量分类。充分证据下的 support/challenge 是专业解释，不是程序测量。输出 VPA Draft，经 Runtime result 封套提交；上游身份矛盾为 review_required。
