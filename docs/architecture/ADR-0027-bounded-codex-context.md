# ADR-0027：Codex 有界上下文与 Direct 隔离

## 状态

Accepted，2026-09-22。

## 决策

新发布的 Codex Stage 1 Job 使用 `bounded_context_v2`；Stage 2、VPA 与研究角色在各自
packet 策略通过独立非劣验收前继续使用 `semantic_parity_v1`。历史 Job 中冻结的
`semantic_parity_v1` 继续按原有完整必读顺序回放，不被新策略重解释。

`bounded_context_v2` 由 Host 生成一个确定性、digest-bound 的角色决策包：完整价格帧与
HTF 数据只交付一次；高价值 PA 事实形成 fact spine。当前实现将 Host 选定的全部
`loading_policy=mandatory` 章节原文嵌入 foundation；section index 只列未内嵌的
非 mandatory 章节，可从同一 Frozen Snapshot 精确读取。不得摘要、改写或删除源知识。

正常分析只允许：一次决策包、至多一次批量事实调查和一次批量知识调查。矛盾未消除时应
输出 unknown/transitioning、降低置信度或 typed stop，不得重复开放式调查。

### 2026-10-12：调查契约一致性修订

新发布的正常 Stage 1 Job 在原 `bounded_context_v2` 上冻结
`stage1_investigation_policy=stage1_investigation_v1`，Packet 同时交付策略版本、
attempts 配额口径和成功基线优先要求。Gateway 与 Worker 复用同一纯流程校验：
先成功读取一次 Packet，事实调查至多一次、知识调查至多一次、合计至多两次。
同类工具共用配额；失败尝试仍计数，denied 不能被后续成功 Draft 隐去。
预算耗尽保持 `incomplete`，不生成 No Trade 或 availability fallback。
MCP 在锁定 SDK 的公开 middleware 中、参数解析前校验声明的输入 Schema；直接调用
入口复用同一检查。参数失败进入 Gateway 的原预算和审计，保存空 request 与固定原因，
不保存对端原始值或解析器错误。成功预校验不计数，实际取证仅计一次。

无此字段的历史 Job 保留原校验语义，不重写 Packet、Skill、digest 或历史结果。
未知显式策略版本 fail closed；repair 和 research child 不消费父调查配额，仍受其
原权限、预算及必读契约约束。程序只检查读取流程，不判断市场解释或增加交易 Gate。
具体实现和本地验证范围见 [教材、Prompt 与调查契约修订](../quality/knowledge_prompt_investigation_20261012.md)。

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
`AGENTS.md` 或 Role Skills；这些 Codex 调查策略不改变 Direct 的 Schema、Validator
和交易主链。领域知识与 Prompt 契约仍由共享资源维护；显式联合 Stage 2 的指令须符合
ADR-0025，不能继承 PA-only 禁止使用 VPA 报告的措辞。

## 性能与验收

- 不以放大 timeout 作为性能修复。
- 记录 packet 字节/token、optional 调查次数及最后一次 Evidence 到最终输出的耗时。
- 本地真实验收前先通过 packet 完整性、旧 Job 回放、Direct 隔离和 Worker 有限调用测试。
- `max` 仅作为质量上界实验；生产候选需以同 Snapshot 的质量非劣和延迟门槛共同决定。
