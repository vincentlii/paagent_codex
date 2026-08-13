# ADR-0021：持续配对 Joint 研究运行时

- 状态：Accepted
- 日期：2026-08-13

## 背景

一次性 `run_pa_vpa_aabb.py` 已能对同一冻结行情执行 A1/A2/B1/B2，但无法独立承担长期抽样：
多进程可能重复消费同一快照，进程中断后没有租约恢复，Paper 落盘失败可能诱发四次模型调用重跑，
而把四臂调用直接塞入生产 Headless 又会扩大生产故障域和 API 成本。

持续实验还必须避免幸存者偏差。抽样条件只能依赖生产 PA 已完成、正式结果为 PA-only、冻结行情与
VPA catalog 可重放；不能要求生产 Shadow 的 VPA 角色先成功，`completed_with_warnings` 仍可成为样本。

## 决策

新增独立 `pa-agent-aabb-monitor` 研究进程。它只扫描已落盘的生产 Shadow record，按 record 时间从旧到新
处理，并显式要求 `--once` 或 `--forever`。该进程不进入生产分析调度器，也不改变任何生产正式结果。

每个 `(pipeline_id, cohort_id, market_snapshot_hash)` 使用 SQLite 原子租约：

- `running`：一个 worker 独占该快照；
- `retry_wait`：仅 Provider、I/O 或 Paper 暂时故障可有限重试；
- `completed`：四臂 protocol 有效，且启用 Paper 时四个隔离账户已写入；
- `terminal_failed`：协议无效、冻结输入损坏、权限/合约错误或重试耗尽。

已有完成且 protocol-valid 的同 cohort 实验不得再次调用模型。若四臂已完成而研究 Paper 暂时失败，
服务从 manifest 指向的四条 record 重放并只重试 Paper。重放必须重新运行 `validate_aabb_records(...)`，
缺失或漂移的 artifact 不得被当成成功。

四臂仍使用同一 `FrozenExperimentInput`、ABBA/BAAB、四个独立 client，A 臂为 PA off，B 臂为研究 soft。
Paper 账户按 `research-{cohort}-{arm}` 隔离；按时间顺序观察后续闭盘 K 线。生产 `pa-official` 账户、
生产 Shadow 与 Joint 研究账户互不写入。

## 权限与交易含义

- 程序只拥有快照身份、租约、重试、artifact 完整性、统计去重和 Paper 订单数学。
- PA、VPA 与 Joint 的专业判断链路不变；持续运行器不生成趋势、Setup、Wyckoff 标签或 Gate。
- VPA 失败臂保留在 ITT coverage，不因失败从样本中消失。
- 连续抽样提高证据数量，不代表 VPA 已提高胜率或盈利；晋级仍按预注册样本、成本后结果和质量门禁。

## 验证

- 并发 claim 只有一个成功；租约过期可恢复；退避与最大尝试次数可复算。
- 同一 cohort/market snapshot 的手工或持续完成实验不重复执行。
- Paper 暂时失败只重放 artifact，不重跑模型。
- 研究 CLI 导入不加载 Qt，可在 Linux Headless 环境运行。
- 生产 off/shadow、原 A/A/B/B protocol、实验统计和四账户隔离回归保持通过。
