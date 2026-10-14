---
name: vpa-evidence-analysis
description: Use when a frozen PA Agent job requests an independent VPA evidence report.
---

# VPA Evidence

输出分工：每条 observation 解释一个有独立意义的量价关系及局限；hypothesis assessment
说明这些观察如何支持或挑战 PA 假设；时机字段只回答当前入场尝试的适用性与确认程度。
不要把同一窗口换措辞复制为多条观察，也不要为凑标签重新解释整张图。保留真正独立的反证。
目录内数值与序列是程序事实，直接引用；推理集中于 Background、Location、Effort/Result
及已发生的后续响应。未来确认留在条件中，不能通过更长推理把未发生的证据变成确定结论。

目标是形成独立的量价证据草案，不替代 Brooks PA 结论。

调用下面的唯一入口后，先阅读随包交付角色协议的 `runtime_guidance`；调查限制和唯一输出/失败模式以本次冻结合同为准。扩展未交付时不采用其流程。
1. 首先调用一次 `get_role_baseline`，按 resources 顺序完整阅读其中 manifest、core_context 与 upstream_artifact，核对 Job identity、当前阶段协议、volume provenance、capability、data status、PA hypotheses 与 entry-attempt applicability。下列基线材料已在本次返回中，不逐项重复调用。
2. 新基线的 `mandatory_modules` 全部是 `loading_policy=mandatory`；按 ID 与 `load_order` 定位并阅读全部 PA 已选 VPA 模块原文。digest 是 Host 的冻结校验身份，不要求你重新计算。此简短清单替代重复的章节目录，不减少授权知识。不得固定截取前若干段，也不得用 Skill 摘要替代当前知识版本。
3. 阅读随包交付的全部 VPA evidence 分块，覆盖本次授权目录。这是原 Windows VPA Prompt 的量价信息基线，不能只抽取支持既有假设的证据。任一必读交付失败即停止，不用部分内容继续判断。
4. 按 Background → Location → Effort/Result → Leg/Wave Comparison → Acceptance/Follow-through 分析，最后才考虑标签。只有runtime_guidance.stage_challenge明确存在时按该研究扩展处理一个实质争议；否则不启动挑战。
5. 从完整目录中为每个 observation 选择精确 VPA evidence ID；不得猜测或枚举 index 外 ID。优先围绕 PA 已定义的结构区域及其实际交互窗口，分析量价接受、拒绝、回测与反证；用已有 observations 和 next_confirmation 表达，证据不足就保留未决，不强求每个位置都有结论，不创造新位置或订单价。必须同时寻找挑战 primary hypothesis 的证据，说明支持、挑战、中性或信息不足。比较必须有明确对象和同源因果基线。
   数据可用不等于存在诊断性量价增量。完整读取必读证据后，有可引用的诊断性证据时说明增量并评估反证；否则明确“未发现诊断性增量”，允许 `observations=[]`，按实际证据选择 `neutral` 或 `insufficient_data`，不强凑观察。未读证据或工具失败仍按第7项停止，不得以无增量或中性掩盖。
   一个解释句若涉及多个比较，应分别写明各自的 evidence ID、窗口/前棒及因果基线；新高/新低必须有冻结回看区间。引用多个 ID 不等于它们共同证明同一个未经界定的比较。
   同源比较必须保留两段结果的方向、幅度和相对强弱；“都缺乏确认”不能掩盖一段横走而另一段显著下跌。解释限制不等于消除这种不对称。
6. 标签不能替代 Evidence，也不能成为第二个 Setup Router。
7. raw/tick/proxy/unknown volume 按 provenance 限制解释，不能互换基线或反推真实参与者身份。已完整读到的数据确实不足时使用契约内 `insufficient_data`，不写不存在的 unavailable 关系枚举，也不以 neutral 代替缺失。工具或必读资源失败、预算耗尽返回 Runtime incomplete，不能伪造完整 VPA 报告。
8. 核对实际已读引用、provenance与时序；研究意见不能替代完整目录覆盖。分别评价 primary/alternative hypothesis、背景关系和当前 entry timing。若没有 PA entry attempt，保持其不适用状态，不编造 entry。解释每个事件前先核对该事件 evidence 的 `later_closed_bar_count` 与 `later_closed_bar_range`，逐项评价已发生的后续响应；最新棒没有后续数据，不代表更早事件也没有。下一根收盘所需确认只能是未来条件，不写成已发生证据。sequence claim 必须引用实际先后证据，不从窗口交叠推导先后。
9. 不创建或翻转 Brooks Setup、方向、订单、胜率、Gate 或正式 Trade/No Trade。

成交量窗口及比较基线由 Host 冻结，不能用价格工具创造新的 VPA evidence ID 或成交量分类。充分证据下的 support/challenge 是专业解释，不是程序测量。输出 VPA Draft，经 Runtime result 封套提交；上游身份矛盾为 review_required。
