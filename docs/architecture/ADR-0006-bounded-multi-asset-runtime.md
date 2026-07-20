# ADR-0006：有界多资产调度与 GUI 线程生命周期

- 状态：Accepted
- 日期：2026-07-20

## 背景

单资产 GUI 允许复用一个 client，但多资产自动监控若直接并发复用可变 client、assembler 或
orchestrator，会产生上下文污染、重复提交与跨资产身份漂移。无界线程和无界队列还会把 Provider
限流放大成内存、延迟和重试风暴。Qt 侧若在 `QThread.isRunning()` 仍为 true 时丢失最后一个强引用
或销毁父窗口，可能直接导致进程崩溃。

## 决策

多资产运行采用三层边界：

1. 数据层先冻结每个资产的 frame、HTF context、来源身份、`snapshot_hash` 与 `cohort_id`。
2. `MultiAssetAnalysisScheduler` 只负责确定性任务身份、有界排队、同资产 FIFO 单飞、全局并发、
   去重、取消和时延测量，不解释行情。
3. `IndependentAnalysisHandler` 在模型调用前再次核对 symbol/timeframe；生产 factory 为每个任务
   创建独立 client、experience reader、assembler、validator 与 orchestrator。持久化 writer 可共享，
   但每条 record 继续由唯一 `run_id` 标识。

任务身份绑定 source、venue、symbol、timeframe、冻结行情 hash 与分析 cohort。相同任务在 active
集合或有界历史中再次提交时明确返回 `duplicate`；队列满时返回明确拒绝状态，禁止静默覆盖旧任务。
一个资产任何时刻最多执行一次，其他资产可在全局上限内并行。

`monitoring_jobs` 保存每个 task 的提交状态、次数、终态、`run_id`、错误类别、queue latency 与
execution latency。分析 JSON 与 `analysis_runs` 仍是交易复盘主记录；调度表只回答运行层问题。

GUI 不再用分散的 zombie 列表。所有取消中的 QThread 交给 `QThreadLifecycleManager` 保持强引用；
业务结果使用独立信号，Qt 原生 `finished()` 只表示线程真实退出。关闭窗口时先请求取消并等待，若
线程仍在阻塞调用中则忽略本次 close event，轮询到全部退出后再销毁窗口，禁止调用 `terminate()`。

## 取舍

- 新建 client 会增加少量连接建立成本，但消除了共享可变 Provider 状态；并发上限用于控制成本。
- 同资产串行可能增加该资产等待时间，但保证顺序、增量上下文和记录身份不乱序。
- 关闭时遇到不可立即取消的网络调用，窗口会延迟退出；这比销毁运行中的线程更安全，Provider
  timeout 仍必须有上界。
- 调度成功不代表交易策略有效；它只证明每个冻结 Snapshot 被准确、可审计地处理一次。

## 验证

- 20 个不同资产在配置并发上限下全部完成，task/run 身份唯一，SQLite 恰有 20 条终态记录。
- 同一资产任务严格 FIFO，任意时刻 active 数为 1；重复与队列满均返回显式状态。
- queue latency 与 execution latency 分开测量；非阻塞 shutdown 可在后续调用中等待完成。
- 每个生产任务得到不同 client、assembler 与 settings 副本。
- 1000 次真实 QThread 启动、取消与回收后无运行线程被删除、无残留强引用、无 Qt 崩溃。

## 依据

- Python `ThreadPoolExecutor` 的 `shutdown(wait, cancel_futures)` 生命周期：
  https://docs.python.org/3/library/concurrent.futures.html
- Qt 明确禁止销毁仍在运行的 `QThread`，并将 `requestInterruption()` 定义为协作式请求：
  https://doc.qt.io/qt-6/qthread.html
