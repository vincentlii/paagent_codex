---
lifecycle: active
capability: research-only
verification_scope: local
最后核验: 2026-10-09
核验提交: working-tree knowledge-delivery audit（未提交；仅本文标注范围）
权威来源:
  - docs/architecture/ADR-0005-offline-pa-vpa-aabb.md
  - docs/architecture/ADR-0021-continuous-paired-joint-research.md
  - docs/quality/acceptance.md
  - docs/architecture/ADR-0024-codex-analysis-backend.md
  - docs/quality/stage1_subagent_market_pilot_20260928.md
  - docs/quality/subagent_complementary_pilot_20260928.md
---

# 研究与评价

2026-10-09 分层知识交付对照在新冻结 XAU 快照、新代码上按 A1/B1/B2/A2
预注册并完成 4/4；盲评先封存再解盲。全量 A PA/VPA 为 93.0/92.5，
分层 B 为 89.25/92.5；B Stage 2 快 12.5 秒而全链慢 15.1 秒，PA
质量门槛未过。旧快照的结果方向相反，两轮各仅一张独立行情，均为本地
`research-only`；正式候选仍全量 A，不证明盈利或 p95。详见
[分层知识四臂研究](../quality/layered_stage2_knowledge_research_20261008.md)。

2026-09-28 补充：互补分工修复后的新四臂完整链 0/4，隔离 CLI 限流导致 B1/B2 无 Stage 1 Draft；A1 仅局部 Stage 1 可诊断，不能计算子 Agent 政策速度差、盲评非劣或 p95。四臂失败保留 ITT，额度恢复后必须新登记，旧 cohort 不重跑混合。详见[修复后真实 cohort](../quality/subagent_complementary_pilot_20260928.md)。下方是修复前旧 pilot 和当时未修复状态的历史记录。

2026-09-28 Stage 1 子协作 pilot：一个独立真实冻结 Snapshot，四份 Stage 1 Draft，
完整链 3 成功/1 缺失；B 政策平均比 A 慢 17.9%，只有 B1 成功收集 child。
四份局部 Stage 1 可独立评分，缺失整链必须 unscorable，不用局部 Draft 补造 receipt。
盲态评分先冻结再解盲；+1.3 分的政策均值差不能证明 child 质量增益或统计非劣。
预登记速度/完整性门禁不满足，暂停 VPA/Stage 2/组合付费扩展和生产晋级。
详见[三角色评价与证据](../quality/stage1_subagent_market_pilot_20260928.md)。

本轮同时确认 PA resume CLI usage 的会话累计口径：派生增量只扣同线程已验证父计数，
不扣独立 child；失败/未报告仍 unknown，原账本不改。程序普通 VPA auto-binding 的
normalization 标志与既有规则冲突，保留并披露，不据该标志夸大首稿失败。
以上是局部模型/执行诊断，不是 closed_trade/Paper 收益，也不能用于 p95、校准或生产晋级。

Codex Stage 内单问题子 Agent 另按 [逐阶段研究计划](../quality/stage_subagent_research_plan.md)
做 `off` 对照和 Stage 1、VPA、Stage 2 分别试验；模型固定
`gpt-6-luna/xhigh`，双方均使用 agentic 调查，只改变子协作政策。当前实现为
Host 独立子进程和有限只读入口，2026-09-27 本地真实合成传输已通过，生产默认关闭。
Host 的 start/collect、失败、父子实际读取与独立用量证明实际暴露；无派生为合法政策样本，
失败/未收集/start 拒绝保留 ITT，但不是干净 per-protocol。合成 canary 不是市场质量/时延增益。
以下旧 PA/VPA A/A/B/B 限制针对 Shadow 与研究 Joint patch；显式
`pa_vpa_joint` 的联合 Stage 2 按 ADR-0025 独立形成未校准主观先验，
不是修改 PA-only 胜率。

新版 `analysis_study_v1` 的本地实现与限制见 [阶段 7 报告](../quality/phase7_research_acceptance.md)。
它区分 Backend、联合和调查三个问题；fixed/agentic 已接入 Frozen Job/Gateway/Worker，
Host 复用成熟编排并验证完整冻结资源、模型/推理身份和持久交付。默认入口只预检。
本地实现不等于真实因果增益已证明；仍需前向采样和盲评。旧协议语义保持不变。
2026-09-13 更新：Windows Luna/max 两批工程试跑均失败，接入层根因与修复见
[真实实验补充](../quality/phase7_luna_pilot_20260913.md)。2026-09-14：用户已授权，pilot-03 真实读取成功但无完整分析，2 失败/2 缺失；已修复 typed stop 身份误报。本地不再冗余重跑，持续模拟盘留给 VPS。不得把失败、CLI 退出成功或测试通过写成分析成功。

## 三层验收

| 层级 | 核心问题 | 证据 |
| --- | --- | --- |
| 工程正确性 | 是否零事实矛盾、零越权、可恢复、可重放、无非法订单？ | 单元/集成/回放/发布测试与 audit |
| 分析质量 | PA/VPA 是否引用正确证据、遵守理论、给出可复核假设？ | 人工三角色评分、盲审、重复运行稳定性 |
| 交易增益 | Joint 相对 PA-only 是否改善成本后 EV、回撤、MAE/MFE 或时机？ | 预注册、配对、样本外、Paper forward 研究 |

上一层通过不能替代下一层。Prompt 更完整、分析更像专家或测试全部通过，都不能直接证明盈利。

## 模式与用途

| 模式 | 正式 authority | 研究含义 |
| --- | --- | --- |
| `off` | PA Stage 2 | PA-only 兼容和泄漏基线 |
| `shadow` | PA Stage 2 | 收集独立 VPA evidence；不是 A/B |
| 正式 `soft` 请求 | PA Stage 2 | effective mode 收敛为 `shadow` |
| A/A/B/B A 臂 | PA-only | A1/A2 测 PA-only 组内随机性 |
| A/A/B/B B 臂 | Joint research result | B1/B2 测 Joint 组内随机性 |

VPA-enabled 模型自称“没有改变 baseline”不是反事实证据。Prompt Context 已改变注意力，必须用同一冻结 Snapshot 独立运行 A 和 B。

## Analysis Backend A/A/B/B

`analysis_backend_aabb_v2` 与 PA/VPA treatment A/A/B/B 是两个不同问题：A1/A2 使用当前 DeepSeek
Direct，B1/B2 使用 Codex Agent；四臂都强制使用同一个 Frozen Snapshot、可用 HTF frame、VPA
catalog 与生产 `shadow` authority，其他 Domain code、Router、Validator、Risk 和知识版本保持一致。
该协议只回答完整 Backend system value；由于模型家族和执行方式同时变化，不得把差异单独归因为
Agentic investigation。实验 arm 关闭 runtime fallback，Codex availability failure 保留在 ITT。

实现由独立 [`backend_aabb.py`](../../pa_agent/experiments/backend_aabb.py) 冻结 Backend pair、共享设置、
四角色 contract、HTF/VPA identity 与 source/runtime digest。`pa-agent-backend-aabb <record>` 是
单记录离线入口。四臂在调用前写入现有实验 ledger；成功 record 仍写入唯一
`AnalysisRecord v5`，失败 arm 保持 assigned/unpersisted。Backend 汇总按 `(cohort_id,
market_snapshot_hash)` 去重，现有 PA/VPA frequency/counterfactual 查询则显式限定 PA/VPA protocol，
两类实验不得混算。executor 必须回传实际构造 Backend 的脱敏 runtime fingerprint 并与冻结 cohort
核对；同一 cohort/Snapshot 的重复 assignment 视为冲突并阻止验收，不能用后续成功覆盖先前失败。
20 Snapshot 仅代表工程 repeatability 可审查，不代表交易晋级。

### Analysis Backend 晋级门禁

Codex Backend 不因“Prompt 更短”或 20 个工程样本而替换 Direct。至少依次满足：

1. 独立 Unix 用户、Frozen Job、runtime identity、故障注入、重启恢复与 20 个独立 Snapshot
   repeatability 全部通过；缺失 arm 和重复 assignment 计入失败覆盖。
2. Brooks/VPA 专家在不知道 Backend 的前提下，按事实准确性、Context、Setup、证据充分性、
   Stage 1/2 连贯性和 confidence calibration 盲评；解释文风不计质量增益。
3. 至少 300 个独立 Snapshot、100 个明确匹配 outcome；最终样本量按预注册效应量和功效调整。
4. 同时报告 hallucination/事实错误、Trade/No Trade 分歧、VPA relation/evidence-ID 一致性、
   first-pass/schema、总 token、core-context bytes、Evidence retrieval、p50/p95 latency、coverage、
   ITT 与 per-protocol。
5. 扣除成本和滑点后的样本外结果、MAE/MFE、回撤或 calibration 有稳定增量，且触发频率变化可解释。
6. 先进入有限资产/周期的 Paper canary；任何 authority、identity、事实或延迟门禁越界立即回到
   `deepseek_only`。真实订单仍需独立 ADR，不由本门禁授权。

由于 Codex 与 Direct 的模型家族和执行方式同时变化，该门禁只能支持“完整 Backend system
是否更好”，不能声称已识别纯 Agentic reasoning 的因果效应。

当前 `backend_review` 工具生成不含 Backend/runtime identity 的 reviewer packet，并单独保存
unblinding key；评分冻结后才能解盲。固定质量权重为市场 Context 35%、Brooks Setup 30%、Evidence
可追溯性 20%、Stage 1/2 连贯性 15%，VPA 仍单独评价。Backend outcome report 只联结已有
closed-OHLC counterfactual ledger，始终返回 `ready_for_promotion=false`；它不能替代前向 Paper，也不能
把完整 Backend 差异归因于 Agentic investigation。

## A/A/B/B 协议

1. 模型调用前登记 A1、A2、B1、B2，不能按成功结果补登记。
2. 四臂使用同一 `market_snapshot_hash`、数据身份和冻结输入。
3. 四个独立客户端避免会话、retry 或 provider state 污染。
4. 使用 ABBA/BAAB 平衡顺序效应。
5. `cohort_id` 隔离 Provider、模型设置、代码、Prompt 和 contract 版本。
6. A1/A2 测 PA-only 随机性；B1/B2 测 Joint 随机性；配对 A-B 才估计 VPA/Joint 增量。
7. 重跑、重复臂和同行情跨版本复核不增加独立 Snapshot 样本数。
8. 持续研究进程只消费可重放的正式 Shadow record，不进入生产 Headless 同步链路。

## 研究输出

至少同时报告：

- 独立 Snapshot 数、完整四臂 coverage、缺失 arm 和 censored 数；
- A/A 与 B/B 的方向、订单、entry type、trade confidence 和关键语义分歧；
- A-B 对 `cycle_position`、`direction`、`gate_result`、订单、entry timing 的配对变化；
- token、latency、first-pass clean、retry、normalization 和 fallback；
- episode-level ITT、per-protocol、成本、滑点、MAE/MFE、回撤和触发频率；
- 按 source、venue、symbol、timeframe、市场 regime 和 cohort 的异质性，但不据小样本追阈值。

## ITT、Per-protocol 与截尾

**ITT 是收益主结论：**

- `no_order` 和 `entry_not_triggered` 计 `0R`；
- pending、同柱路径无法确定、历史滚出窗口保持 censored；
- 重叠前向窗口先合并为 episode，避免把同一行情重复计权；
- 缺失 arm 保留在 coverage 并阻止验收，不能只分析成功样本。

**Per-protocol 只诊断交付质量：**它回答“模型和 Paper 都成功时发生什么”，不能取代 ITT，否则会筛掉 no-trade、Provider 失败或 Joint 失败造成的真实系统成本。

## Paper 证据边界

- `analysis_outcomes` 只存人工核验的真实结果。
- `counterfactual_outcomes` 只存闭盘 OHLC 对计划的反事实回放。
- Paper v1/v2 按 simulation version 分开统计。
- 同柱触发后同时触及 stop/target、突破成交顺序不确定等情况保持 ambiguous/censored。
- 开盘已成交的订单可按保守 stop-first 规则回放，但必须记录协议。
- Paper PnL 不等于真实成交 PnL；真实部署仍需成本、滑点、流动性和拒单模型。

## Joint 晋级门禁

任何正式晋级提案至少满足 [质量契约](../quality/acceptance.md)：

1. 至少 300 个独立 Snapshot 和 100 个有明确结果的匹配样本。
2. 最终样本量按预注册效应量、方差和统计功效重新计算。
3. 工程硬不变量全部通过，A/A 与 B/B 随机性处于可接受范围。
4. 样本外、扣除成本后的 ITT 提供稳定增量，不只在单资产/周期/模型版本成立。
5. 触发频率下降超过 15% 时，必须由成本后 EV、MAE 或回撤改善支持。
6. 不允许 VPA 修改 `estimated_win_rate`、创建 Setup、翻转方向或进入正式账户。
7. 失败或中性结果同样保留，不调整 Prompt 到迎合当前样本。

晋级不是一次性“开关”。即使通过研究门禁，也需要有限流量 Paper canary、可回退、cohort 监控和明确停止条件。

## 当前能得出的结论

- 软件已实现 PA-only + VPA Shadow 与隔离 A/A/B/B/Joint 研究基础。
- VPA 可以提供与价格结构不同的成交量、努力/结果和 follow-through 证据维度。
- 程序可以消除可复算事实矛盾、权限越界和订单数学错误。
- 2026-08-13 审计记录了当时的全量非 live 工程证据。
- 单资产 Dashboard 的 DeepSeek/Codex `Run Once` 可以证明部署、认证、数据、协议和持久化链是否可运行；
  它是 operational smoke，不是模型对照实验。

## 当前不能得出的结论

- [联合 Stage 2 分层知识研究](../quality/layered_stage2_knowledge_research_20261008.md)已完成一个冻结快照的预注册四臂盲评：B 臂通过探索性专业质量筛查，但 Stage 2 和全链均值更慢，Provider 总 Token 更多；不晋级正式链。一个独立快照不能证明分层知识的统计非劣、泛化或交易增益。正式单 Agent 链保持全量必读。

- 不能声称 VPA 或 Joint 已提高胜率、盈利、EV 或出入场精度。
- 不能声称真实 VPS、systemd 权限、Provider/行情和长时间 Paper 已验证。
- 不能由单次模型表现判断某个 Prompt、thinking 强度或事件标签更优。
- 不能比较两个不同闭盘 Snapshot 的单次收益后声称 Codex 优于或劣于 DeepSeek；正式结论仍需同一
  Frozen Snapshot 的预注册 `analysis_backend_aabb_v1`。
- 不能把教材频率、盈亏平衡胜率或 LLM confidence 当成统计校准概率。
- 不能保证减少幻觉会自动提高盈利；市场判断仍有不可消除的不确定性。

## 防止样本坍塌

- 预注册问题、独立单位、成功/失败标准和决策后果。
- 先按根因分类，再修改架构；不为单条 record 添加阈值或文字特判。
- 保留 alternative hypotheses 和 neutral/insufficient，不强迫每次产生事件标签。
- 冻结 benchmark 与 holdout，Prompt 迭代不能反复查看并优化同一结果集。
- 报告分布、分层和置信区间，不只报告平均收益或“通过率”。

继续阅读：[当前状态](current-status.md) · [问题历史](problem-history.md) · [质量契约](../quality/acceptance.md)
