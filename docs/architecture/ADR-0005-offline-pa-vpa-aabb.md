# ADR-0005：PA/VPA 增量只用离线 A/A/B/B 估计

- 状态：Accepted
- 日期：2026-07-20

## 背景

生产 Shadow/Soft 的 Prompt context 已经不同，单次运行中保存的 baseline 与 joint 结果不是两个
独立反事实。让模型声明“保持 baseline”，或从一条 record 合成两个订单，会混入注意力变化、
共享上下文和模型随机性，也会在失败样本被过滤时形成幸存者偏差。

## 决策

VPA 信息增量使用 `pa_vpa_aabb_v1` 离线协议：同一冻结输入运行 A1/A2（VPA off）与 B1/B2
（VPA soft）。顺序由冻结输入 hash 确定为 ABBA 或 BAAB；每个 arm 使用新客户端，禁用 fallback
和增量上下文。身份分为三层：`market_snapshot_hash` 只标识主周期/HTF 的完整已收盘行情；
`cohort_id` 标识 protocol、Provider/settings 与实际分析源码/Prompt 资产版本；
`frozen_input_hash` 再绑定行情、VPA catalog 与 cohort，作为单次四臂实验的不可变输入身份。

实验器在任何模型调用前登记四个 assignment，随后记录处理接收、first-pass、run ID、record
path、持久化与失败状态。四条 record 由程序外部提取决策签名；模型生成的 comparison prose 不
参与协议判定。warning 可进入 first-pass，retry、normalization、fallback 和 hard failure 不可。

统计同时报告 ITT 与 per-protocol。ITT 保留未交付和降级 arm，衡量实际系统效应；per-protocol
只比较协议完整且确实收到指定处理的样本。A/A 与 B/B 分歧用于估计模型随机波动。生产 record
只回放自己的正式订单，实验归组由独立 manifest/arm ledger 完成。

统计抽样单位是独立 `market_snapshot_hash`，不是 arm、重复运行或 `frozen_input_hash`。同一行情的
A1/A2、B1/B2，以及同一行情在不同分析版本下的重跑均不得增加独立样本数。质量门禁只能在单一
`cohort_id` 内计算；默认选择最近一个已完成且协议有效的 cohort，跨 cohort 汇总仅供描述。

## 后果

- 单次 Shadow/Soft 不再被误报为 A/B，也不能直接证明 VPA 增益。
- 失败 arm 不会从样本中静默消失；运行可靠性与策略效应可分开查看。
- 一次实验需要四次完整模型分析，成本高于生产分析，因此只能离线或抽样执行。
- Soft 启用仍需跨 Snapshot 的频率、反事实结果、成本后样本外表现与运行 SLO，质量门禁不自动晋级。

## 验证

- 每个 manifest 必须恰有 A1/A2/B1/B2 四个 assignment。
- A 臂不得含 VPA 对象或调用；B 臂必须使用同一冻结 catalog。
- 四臂 PA Stage 1 初始 request hash 必须相同，客户端 token 与对象身份必须不同。
- 持久化 record 必须可重算同一个 `frozen_input_hash`。
- 同一行情跨分析版本必须保持相同 `market_snapshot_hash`、产生不同 `cohort_id`，且不得重复计样。
- 质量与结果报告必须区分 ITT、per-protocol、A/A 分歧和 B/B 分歧。
