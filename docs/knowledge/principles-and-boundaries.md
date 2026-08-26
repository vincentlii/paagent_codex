---
lifecycle: active
capability: not-applicable
verification_scope: not-verified
最后核验: 2026-08-26
核验提交: f8fffc0
权威来源:
  - AGENTS.md
  - CONTEXT.md
  - docs/architecture/ADR-0001-analysis-authority.md
  - docs/architecture/ADR-0003-pa-vpa-joint-roles.md
---

# 原则与边界

本页是非规范性索引。规则文本以 [AGENTS.md](../../AGENTS.md)、[CONTEXT.md](../../CONTEXT.md)、相关 ADR 和专项规范为准；本页不能独立新增强制规则。

## PA 交易员视角

| 编号 | 一句话摘要 | 适用模块 | 权威来源 |
| --- | --- | --- | --- |
| `PA-01` | Brooks PA 定义市场框架、周期、Always-In、方向和 Setup。 | Stage 1/2 | [CONTEXT](../../CONTEXT.md)、[ADR-0001](../architecture/ADR-0001-analysis-authority.md) |
| `PA-02` | Brooks Router 是唯一 Strategy Router，只消费已验证枚举和 pattern keys。 | Router/Prompt | [AGENTS](../../AGENTS.md)、[ADR-0004](../architecture/ADR-0004-context-sliced-prompts.md) |
| `PA-03` | Stage 1 的 Gate 是框架级短路；Stage 2 独立决定是否存在当前 Entry Attempt。 | Stage 1/2 | [CONTEXT](../../CONTEXT.md) |
| `PA-04` | entry、结构止损、TP1、可选 TP2 与当前时机由 LLM 结合 Setup 判断。 | Stage 2 | [ADR-0020](../architecture/ADR-0020-contextual-risk-probability-and-vpa-seasonality.md)、[质量契约](../quality/acceptance.md) |
| `PA-05` | stop 是当前 Setup 的结构失效保护，不由固定 ATR、跳数或 RR 阈值机械决定。 | Stage 2/Execution | [CONTEXT](../../CONTEXT.md)、[质量契约](../quality/acceptance.md) |
| `PA-06` | `estimated_win_rate` 是未校准 PA 主观先验，可进入 Trader Equation，但不是历史胜率。 | Stage 2/Research | [ADR-0020](../architecture/ADR-0020-contextual-risk-probability-and-vpa-seasonality.md) |
| `PA-07` | 程序可以验证明确价格事实和订单数学，不能修正 PA 专业结论来让样本“通过”。 | Validator | [ADR-0002](../architecture/ADR-0002-provider-validation-boundary.md)、[ADR-0017](../architecture/ADR-0017-deterministic-facts-and-execution-semantics.md) |
| `PA-08` | 保护止损触达与分析假设失效是不同事件，Paper 回放不能混称。 | Stage 2/Paper | [CONTEXT](../../CONTEXT.md)、[ADR-0019](../architecture/ADR-0019-immutable-candidate-and-paper-ohlc-v2.md) |

## Wyckoff VPA 交易员视角

| 编号 | 一句话摘要 | 适用模块 | 权威来源 |
| --- | --- | --- | --- |
| `VPA-01` | PA defines，VPA informs，LLM arbitrates；VPA 不是 Brooks Setup 的审批器。 | VPA/Joint | [ADR-0003](../architecture/ADR-0003-pa-vpa-joint-roles.md)、[VPA 集成规范](../wyckoff_vpa_integration.md) |
| `VPA-02` | 分析顺序是 Background → Location → Effort/Result → Leg/Wave → acceptance/follow-through。 | VPA Prompt | [VPA core](../../prompt_engineering/wyckoff_vpa/core.txt) |
| `VPA-03` | Evidence first、hypothesis plural、label last；先描述量价事实和后续验证，再谨慎命名事件。 | VPA Prompt | [VPA 集成规范](../wyckoff_vpa_integration.md) |
| `VPA-04` | VPA 解释多根 K 线与波段关系，单根 bar 只作为序列中的证据。 | VPA Evidence | [VPA core](../../prompt_engineering/wyckoff_vpa/core.txt)、[趋势/回调](../../prompt_engineering/wyckoff_vpa/trend_pullback.txt) |
| `VPA-05` | proxy/tick volume 只作同源相对比较并降权，缺量时保持 insufficient_data。 | Volume/VPA | [ADR-0020](../architecture/ADR-0020-contextual-risk-probability-and-vpa-seasonality.md) |
| `VPA-06` | seasonality 是严格因果、数据能力驱动的测量上下文，不是 no-demand/no-supply Gate。 | Measurements | [ADR-0020](../architecture/ADR-0020-contextual-risk-probability-and-vpa-seasonality.md) |
| `VPA-07` | 事件模块按 PA Context 注入；程序选择知识，不命名 Spring、UTAD、SOS/SOW 或 Absorption。 | Prompt/Profile | [ADR-0004](../architecture/ADR-0004-context-sliced-prompts.md)、[VPA 集成规范](../wyckoff_vpa_integration.md) |
| `VPA-08` | VPA 不创建 Setup、不翻转方向、不改 Gate、订单价格或 `estimated_win_rate`。 | Validator/Shadow | [AGENTS](../../AGENTS.md)、[ADR-0009](../architecture/ADR-0009-production-shadow-research-joint.md) |
| `VPA-09` | 没有 PA Entry Attempt 时，VPA 可保留背景观察，但 timing 关系只能是信息不足。 | VPA/Handoff | [CONTEXT](../../CONTEXT.md) |
| `VPA-10` | VPA 真正的研究价值是独立量价信息、时机和风险校准，不是复述 PA 价格结构。 | VPA/Research | [VPA 集成规范](../wyckoff_vpa_integration.md)、[ADR-0021](../architecture/ADR-0021-continuous-paired-joint-research.md) |

## 量化与软件架构视角

| 编号 | 一句话摘要 | 适用模块 | 权威来源 |
| --- | --- | --- | --- |
| `ARCH-01` | 可确定、可复算、无歧义的交给程序；依赖背景、位置和多种合理解释的交给 LLM。 | 全链路 | [ADR-0017](../architecture/ADR-0017-deterministic-facts-and-execution-semantics.md) |
| `ARCH-02` | 程序构建 Evidence Graph 和多种假设上下文，不用强标签锚定 LLM。 | Evidence/Prompt | [ADR-0018](../architecture/ADR-0018-evidence-context-and-provider-drafts.md) |
| `ARCH-03` | Provider Draft 是模型唯一输出契约；单一 materializer 生成程序字段。 | Provider/Validator | [AGENTS](../../AGENTS.md)、[ADR-0018](../architecture/ADR-0018-evidence-context-and-provider-drafts.md) |
| `ARCH-04` | normalizer 只做容器、白名单和无语义记号规范化，不能补结论、改价或创造节点。 | Normalizer | [AGENTS](../../AGENTS.md)、[ADR-0017](../architecture/ADR-0017-deterministic-facts-and-execution-semantics.md) |
| `ARCH-05` | 硬拒绝仅保护结构契约、明确事实、证据身份、权限和订单安全；专业多解进入 warning。 | Validation | [ADR-0002](../architecture/ADR-0002-provider-validation-boundary.md)、[质量契约](../quality/acceptance.md) |
| `ARCH-06` | `compile_pa_handoff(...)` 是 PA 到 VPA/Joint 的唯一入口，只复制已验证语义。 | Handoff | [CONTEXT](../../CONTEXT.md) |
| `ARCH-07` | `off` 必须实现 VPA Prompt、Schema、测量和历史字段零泄漏。 | Config/Prompt | [AGENTS](../../AGENTS.md)、[质量契约](../quality/acceptance.md) |
| `ARCH-08` | Stage 2 candidate 不可变；执行资格只写入独立 `execution_disposition`。 | Stage 2/Execution | [ADR-0019](../architecture/ADR-0019-immutable-candidate-and-paper-ohlc-v2.md) |
| `ARCH-09` | Snapshot、HTF、Prompt/contract digest、Provider/settings 和 cohort 必须绑定，防止身份漂移。 | Data/Runtime/Research | [ADR-0008](../architecture/ADR-0008-canonical-record-and-validation-boundaries.md)、[ADR-0022](../architecture/ADR-0022-runtime-resource-and-release-reproducibility.md) |
| `ARCH-10` | 正式 Shadow 不是 A/B；只有同 Snapshot 的独立 A1/A2/B1/B2 才能估计增量和随机性。 | Research | [ADR-0005](../architecture/ADR-0005-offline-pa-vpa-aabb.md)、[ADR-0021](../architecture/ADR-0021-continuous-paired-joint-research.md) |
| `ARCH-11` | 单样本修复不能成为通用规则；先提炼跨样本机制并防止样本坍塌。 | Debug/Research | [PA Agent guardrails](../../.agents/skills/paagent-change-guardrails/SKILL.md) |
| `ARCH-12` | 同一问题第三次验证仍失败时停止叠 patch，审查架构、数据流、状态和失败边界。 | Debug | [AGENTS](../../AGENTS.md) |
| `ARCH-13` | 多资产同资产 FIFO 单飞、全局并发有界、每任务独立 client/orchestrator。 | Monitoring | [ADR-0006](../architecture/ADR-0006-bounded-multi-asset-runtime.md) |
| `ARCH-14` | Dashboard 只消费脱敏投影和写 desired state；systemd 拥有进程生命周期。 | VPS | [ADR-0023](../architecture/ADR-0023-vps-observability-and-control-plane.md) |

## 三角色修改检查

每项修改在实施前回答：

1. **PA 交易员：** 是否保留了 Brooks 的背景、位置、Setup、结构失效和时机判断？
2. **VPA 交易员：** 是否真的增加独立量价信息，而非把事件标签或阈值写进代码？
3. **量化/软件架构师：** 该事实能否复算、失败能否归类、状态能否审计、规则是否跨样本成立？
4. **最终裁决：** 该改动是否改善长期架构，而不是只让当前 record 通过？

遇到冲突时先回到权威来源；需要新增强制约束时先修改相应 ADR/`CONTEXT.md`/`AGENTS.md`，再更新本索引。

继续阅读：[分析链路](analysis-pipeline.md) · [问题历史](problem-history.md) · [研究与评价](research-and-evaluation.md)
