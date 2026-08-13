# ADR-0019：不可变候选决策与 Paper OHLC v2

- 状态：Accepted
- 日期：2026-08-13

## 背景

Stage 2 同时承载 LLM 的 PA 判断与程序执行安全。旧实现遇到 Trader Equation、TP2
几何或置信度门槛未通过时，会把原始 `decision`、`decision_trace` 和 `terminal` 改写为
不下单。这既丢失了模型候选，也让复盘无法区分“PA 没有信号”和“程序拒绝执行”。

旧 Paper OHLC 回放还可能在挂单于柱内成交后，使用同一根 K 线较早出现的高低点作为
止盈或止损，产生不可证实的收益。修正后的结果不得与旧模拟统计混合。

## 决策

1. 已验证的 Stage 2 `decision`、`decision_trace` 与 `terminal` 是不可变分析候选。
2. 程序新增顶层 `execution_disposition`，集中记录候选订单类型、实际可执行订单类型、
   状态、原因码及可复算检查。Trader Equation、订单几何与配置置信度只改变执行资格。
3. 所有执行消费者必须读取 `effective_order_type`；候选与 disposition 不匹配时直接拒绝，
   禁止从 reasoning 推断或绕过执行策略。
4. Paper 新订单使用 `paper_ohlc_v2`。挂单若在柱内触发，而同柱又触及止损或 TP1，
   因 OHLC 不含事件顺序，订单终态为 `ambiguous_entry_bar`，不计盈亏。
5. 市价单或开盘跳空即成交的挂单，其入场发生在该柱 open；之后的极值可用于退出。
   同柱同时触及止损与目标时采用保守的止损优先。已经跨柱持仓同样如此。
6. 旧数据库订单标记为 `paper_ohlc_v1`。报告按 `simulation_version` 分组，ambiguous
   单独计数，v1/v2 不得合并用于策略结论。
7. TP2 若存在，必须严格位于 TP1 的盈利方向之外。Paper 基线仍在 TP1 全量退出；TP2
   只保留为研究字段，在独立验证前不参与收益。
8. `first_pass_clean` 只度量 Provider 首轮结构质量。程序 execution warning 不算模型失败；
   retry、normalization、fallback、未验证调用和运行失败分别记录原因码。
9. 发布阈值只从 `config/quality_contract.toml` 加载。CLI 可显式覆盖，但不得维护第二套默认值。
10. Headless `--once` 的等待上界按已接受任务数与并发波次数计算，避免把多资产排队误报为超时。

## 后果

- 交易员可以同时看到原始 PA 候选和未执行原因。
- 执行安全保持确定性，并且不再伪造一条“LLM 选择不下单”的历史。
- Paper 收益不再利用不可观测的同柱路径，旧结果仍可审计但不会污染新口径。
- 质量门禁与运行超时都有单一、可解释的定义。

## 验证

- 候选在 Trader Equation、TP2 或置信度抑制后保持逐字段不变。
- 官方与研究 Paper 均拒绝 `execution_disposition.status=suppressed` 的候选。
- 覆盖柱内挂单歧义、开盘成交、跨柱同触止损/目标及 v1/v2 报告分组。
- execution warning 保持 `first_pass_clean`，normalization/retry/fallback 不保持。
- 多资产一次性等待按 `ceil(accepted/max_concurrency)` 个波次计算。
