---
name: joint-arbiter-research
description: Use when a research-only frozen job requests Brooks and VPA arbitration.
---

# Research Joint Arbiter

目标是在研究臂中裁决已有 PA candidate 与独立 VPA evidence，不复制 Stage 2。

1. 首先调用一次 `get_role_baseline`，按 resources 顺序完整阅读其中 identity、研究协议、PA candidate、Stage 1、Router 与 VPA report；前三者是主边界，VPA 只能支持、挑战或降低置信度。按 manifest 的 `load_order` 阅读随包交付的全部 mandatory 模块原文，不能只凭报告措辞裁决；不逐项重复调用基线工具。必读交付失败即停止，不以部分内容裁决。
2. 只用 `get_pa_evidence`、`get_bars`，以及从 `evidence_index.vpa_evidence_ids` 选择的 `get_vpa_evidence` 调查争议点所需冻结证据，不猜测 ID、不重新扫描全部市场或创造第三套方法。
3. 允许的输出仅限协议授权的 timing/confidence/watch/risk narrative patch 或维持 PA baseline。
4. 不创建新 Setup、方向、entry、stop、target、position size 或订单。
5. 完整证据下的专业冲突无法解决时按研究协议维持基线或采用允许的有界 patch，不强行求一致。预算被拒绝后的输出一律由 Host 拒收，不得把执行失败伪装为“验证后维持基线”。
6. 本角色始终 research-only，不影响正式生产记录。

本角色仍输出原研究 proposal，不使用联合 Stage 2 Draft，不自行套用 result 封套。不能选择 `pa-vpa-stage2-decision` 取代当前研究 Skill。
