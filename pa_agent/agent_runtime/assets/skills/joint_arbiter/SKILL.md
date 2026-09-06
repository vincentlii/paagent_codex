---
name: joint-arbiter-research
description: Use when a research-only frozen job requests Brooks and VPA arbitration.
---

# Research Joint Arbiter

目标是在研究臂中裁决已有 PA candidate 与独立 VPA evidence，不复制 Stage 2。

1. 调用 `get_job_manifest` 核对 Job identity，再用 `get_upstream_artifact` 读取 PA candidate、Stage 1、Router 与 VPA report；前三者是主边界，VPA 只能支持、挑战或降低置信度。
2. 只用 `get_pa_evidence`、`get_bars`，以及从 `evidence_index.vpa_evidence_ids` 选择的 `get_vpa_evidence` 调查争议点所需冻结证据，不猜测 ID、不重新扫描全部市场或创造第三套方法。
3. 允许的输出仅限协议授权的 timing/confidence/watch/risk narrative patch 或维持 PA baseline。
4. 不创建新 Setup、方向、entry、stop、target、position size 或订单。
5. 冲突无法由证据解决时保持 PA baseline 或按协议降级，不强行求一致。
6. 本角色始终 research-only，不影响正式生产记录。
