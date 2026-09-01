---
name: pa-vpa-evidence
description: Produce independent VPA shadow evidence for one frozen PA Agent snapshot.
---

# VPA Evidence

目标是形成独立的量价证据草案，不替代 Brooks PA 结论。

1. 读取冻结 volume provenance、capabilities 与 VPA measurements。
2. 按需查询 effort/result、spread、close location、relative volume 与背景窗口证据。
3. 只使用 PA Agent allowlist 中的 VPA 知识，不把 Wyckoff event 名称升级为正式 authority。
4. 明确支持、挑战、中性或不可用，以及对应 fact refs 与不确定性。
5. 数据不可用或质量不足时返回 unavailable/neutral，不反推成交量。
6. 不创建 PA Setup、订单价格、方向或正式 Trade/No Trade。
