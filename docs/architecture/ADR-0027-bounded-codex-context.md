# ADR-0027：Codex 有界上下文与 Direct 隔离

## 状态

Accepted，2026-09-22。

## 决策

新发布的 Codex Job 使用 `bounded_context_v2`。历史 Job 中冻结的
`semantic_parity_v1` 继续按原有完整必读顺序回放，不被新策略重解释。

`bounded_context_v2` 由 Host 生成一个确定性、digest-bound 的角色决策包：完整价格帧与
HTF 数据只交付一次；高价值 PA 事实形成 fact spine；知识以少量原文核心 section 加完整
section index 交付，其余原文仍可从同一 Frozen Snapshot 精确读取。不得摘要、改写或删除
源知识。

正常分析只允许：一次决策包、至多一次批量事实调查和一次批量知识调查。矛盾未消除时应
输出 unknown/transitioning、降低置信度或 typed stop，不得重复开放式调查。

## 所有权边界

| 内容 | 唯一所有者 |
|---|---|
| 实时数据、cutoff、事实计算 | PA Agent Host |
| Brooks Router、Validator、Risk、Trader Equation | PA Agent Host |
| 输出形状 | Frozen output schema |
| Agent 权限与安全边界 | Runtime `AGENTS.md` |
| 角色有限状态机 | 对应 Role Skill |
| 动态市场协议与冻结身份 | decision packet |
| Brooks/VPA 原文 | Frozen knowledge sections |

Direct Backend 不经过 Publisher、FrozenEvidenceGateway、decision packet、Runtime
`AGENTS.md` 或 Role Skills；其 messages、schema、Validator 和交易主链保持不变。

## 性能与验收

- 不以放大 timeout 作为性能修复。
- 记录 packet 字节/token、optional 调查次数及最后一次 Evidence 到最终输出的耗时。
- 本地真实验收前先通过 packet 完整性、旧 Job 回放、Direct 隔离和 Worker 有限调用测试。
- `max` 仅作为质量上界实验；生产候选需以同 Snapshot 的质量非劣和延迟门槛共同决定。
