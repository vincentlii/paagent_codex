---
name: pa-stage1-analysis
description: Diagnose only PA Agent Stage 1 Brooks market context from a frozen snapshot.
---

# PA Stage 1

目标是形成 Stage 1 市场诊断 Provider Draft，不做交易决策。

1. 先读取 CORE_CONTEXT 中的 Stage 1 协议、当前 Context 与已有核心事实。
2. 仅在需要澄清结构时按 fact ID 或 K 序号调用 `pa_evidence`；优先小范围查询。
3. 如需理论定义，只读取 allowlist 中 PA Agent 已选择的 Brooks 知识。
4. 区分程序事实、Brooks 解释和不确定性；不得把 VPA 量价证据带入 PA Stage 1。
5. 完成 market cycle、always-in、location、pattern keys、evidence refs 与 Gate 所需字段。
6. 不生成 entry、stop、target、position size 或最终 Trade/No Trade。

冲突时以 Frozen facts 和 Provider Contract 为准；证据不足时保守表达，不补造结构。
