---
name: pa-stage2-analysis
description: Analyze only PA Agent Stage 2 within validated Stage 1 and Brooks Router boundaries.
---

# PA Stage 2

目标是在已验证 Stage 1 artifact 与 Brooks Router manifest 内形成交易决策 Provider Draft。

1. 把 CORE_CONTEXT 中的 Stage 1 与 Router 视为不可扩张边界，不重新分类 Stage 1。
2. 按需调查候选 Setup、trigger、invalidation 和 reward/risk 所需的冻结价格证据。
3. 只读取 Router 已授权的 Brooks 知识，不引入新的交易体系或未路由 Setup。
4. Trade 必须满足 Provider Contract 的方向、价格与证据要求；否则输出合法 No Trade。
5. 不自行修改 Risk、Trader Equation、position size 或执行 disposition。
6. 不使用 VPA 证据改变正式 PA Stage 2 方向。

禁止为了凑齐 schema 猜测价格或 Setup；边界不足时选择 No Trade 并校准 confidence。
