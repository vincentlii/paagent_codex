# ADR-0027：可选固定盈亏比退出策略

- 状态：Accepted for implementation，2026-10-21 用户授权完整实现。
- 范围：Headless、网页控制面与 OKX Demo；本地实现不等于线上启用或交易收益验证。

## 决策

`AnalysisProfile` 增加 `take_profit_mode=pa|fixed_rr` 与
`fixed_reward_risk_ratio=1|1.5|2|2.5`。默认仍为 PA；固定模式初值 1.5。
它改变模型评估的退出目标及概率事件，因此复用分析 Profile 的 CAS、idle apply、
冻结任务和 cohort，不能仅作为 Demo 执行末端的止盈替换。

PA 模式维持原结构 entry/stop/TP1 和可选研究 TP2。固定模式只将止盈价格的责任
交给程序：模型继续判断 Setup、入场、结构失效、路径障碍、交易/等待及主观胜率；
VPA 仍是 Evidence Analyst，不增加目标、方向、Setup 或概率权限。

固定模式 `Provider Draft` 不包含 `take_profit_price` / `take_profit_price_2`。
`pa_provider_contracts.materialize_stage2_draft` 是唯一物化器：

1. 从模型计划 Entry 与结构 SL 计算 `R=abs(Entry-SL)`；多单 `TP=Entry+kR`，空单 `TP=Entry-kR`。
2. 用冻结的真实目标交易所 tick 向入场方向取整，原 Entry/SL 不调整；TP2 为 null。
3. 用取整后的真实目标距离和模型概率运行原 Trader Equation、Validator、execution disposition。

模型概率明确针对“计划入场成交后，该固定目标先于结构止损达到”。它不是挂单成交率、
原结构目标胜率或盈亏平衡胜率，也不因选定倍数而被程序反填。模型仍可合法等待或
No Trade。固定模式不增加模型轮次、Router 或第二物化器。

## Tick、身份与执行

OKX Demo Worker 从已读取的合约快照投影 `price_rules=[{inst_id,tick}]`。
Host Dispatcher 只接受 environment/schema 正确、contracts coverage 为 true、
观测不超前且不超过 30 秒、映射唯一且数值合法的目标规则。这里不读取凭据，
不查询模型，也不从 OHLC 小数位猜真实 tick。

目标 tick 冻结到 Host `FrozenAnalysisPayload.take_profit_tick_size` 与 codec，并进入
cohort；市场 Snapshot hash 不变。Stage 2 的同一 policy 进入 Direct Prompt、Provider
Schema/digest、Codex digest-bound 角色协议、Validator、记录 `role_runtime` 和 Intent。
首次 Codex Prompt 仍仅身份、角色、索引；完整知识和其他冻结事实不减少。

缺 tick 时允许合法 No Trade，但可执行候选物化明确失败，不猜价格、不生成伪 No Trade、
不走 availability fallback 或无效修复。下单时再按当前 OKX 合约核对冻结 tick，发生
变更则拒绝该新请求，不重新计算价格。已有保护及退出预算继续使用原请求。

Intent 校验 TP 是否与自身 policy/Entry/SL 一致；Host 派发前核对 record policy 与
原冻结任务一致。流水显示该计划冻结模式、请求 RR 和实际价格距离 RR，不能使用当前
面板设置重标历史。固定倍数描述计划价格距离，实际成交偏差、费用及资金费可能使
实际净盈亏比不同。

## 兼容与回退

- 旧 Profile 缺字段明确解释为 PA/1.5，不继承当前全局模式。PA 默认通过统一身份
  投影省略新增字段，保留旧 profile fingerprint；历史 cohort 不回填。
- 旧 Host payload 缺 tick 保持无字段；旧 Intent 缺 policy 时摘要排除新增 null 字段，
  原 content digest、客户订单 ID 与原价保持。
- 设置只作用于未来新任务。已接受任务、挂单、持仓、过期时间和保护不因设置切换修改。
- 旧研究 Joint Arbiter 未获新策略授权，显式组合拒绝；研究默认 PA 不变。
- 不迁移数据库、不回填数据、不增加依赖。Host、Codex Worker、Dashboard、Demo Worker
  发布时须协调版本和排空模型队列；不能跨 Runtime 资产版本续接旧 Codex 会话。
- 任意新版 Intent（包括 PA 模式序列化的 `take_profit_policy:null`）产生后，旧 Demo Worker
  不支持其字段，不能直接让旧版本接管活动计划。旧摘要兼容不等于旧 reader 能读取新版存储。
  回退前先将新任务切回 PA 并按原恢复/排空流程处理，保留账本与保护，不能删除记录避开兼容。

## 验证与依据

验收见 [固定盈亏比验收](../quality/fixed_risk_reward_acceptance_20261021.md)。
三视角审查：PA 保留结构风险和交易判断；VPA 无新权限；程序只计算用户明确选定的
退出数学和冻结/执行安全。工程通过不证明固定模式优于 PA 模式。

外部行为依据：[OKX instruments 文档](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments)
的 `tickSz`，以及 [Python Decimal](https://docs.python.org/3/library/decimal.html)
的确定性十进制运算与定向取整。实现不硬编码某品种的当前 tick。
