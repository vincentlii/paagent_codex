# 阶段 6：交付时点与 latency-aware Paper 验收

日期：2026-09-09。基线：`2b20fa9`，分支 `codex-analysis-backend-v1`。
范围：本地代码及必要离线验收；不部署、不调用真实模型、不下单。
本文同时定义后续阶段须保留的执行语义，不能用旧 OHLC 结果替代新版交付后结果。

## 结论与计划逐项对应

| 计划要求 | 实现与验收依据 |
| --- | --- |
| 结构目标与限价可达性 | 保留唯一 materializer、Validator、结构止损定仓和 effective order 策略；既有 §9.0P 只检查限价相对 K1.close 的方向，不因历史影线触价否决。新版 Paper 多空入场限价经滑点后仍不劣于限价，不改模型目标或止损。 |
| 持久化后 available_at | Host 记录分析/响应/验证时点；PendingWriter 先写完整记录与审计索引，再记录可用时点并保存回执。失败不释放可执行结果。 |
| 下一主周期收盘过期 | expiry 为冻结最新闭盘开盘时刻加两个固定周期，不随分析延迟后移。接收、替换和交易事务激活时检查时间。 |
| 显式替代 | 有效同市场分析替代本 pipeline 的未成交计划；过期或失败分析不能替代，已开仓不取消。旧单有效窗口未观察完整时保留 ambiguous，不虚构零收益。 |
| latency-aware Paper | 新订单为 `paper_latency_v3`；旧 v1/v2 与研究账户保持原语义。激活前已结束的棒不能成交。 |
| 同棒不确定 | 柱内交付且整棒触价、柱内入场与退出同棒、有效窗口缺失均保留 ambiguous/censored，不猜柱内路径。 |
| TP2 非 Gate | 新执行计划只带 entry、结构 stop、TP1；TP2 留在原分析供参考，不进入新版 Broker 第二目标校验或执行。 |

## Host 时间与权限链

所有新增时点均为 UTC epoch milliseconds，不由模型填入。

1. `analysis_as_of_ms`：冻结数据边界，来自快照元数据。
2. `model_completed_at_ms`：最后一次有响应的调用账本时间，即 Host 收到响应后的观察值，不是 Provider 内部精确结束时刻。
3. `validated_at_ms`：完整结果通过既有处理、准备持久化时的观察值。
4. `result_available_at_ms`：完整记录及审计索引成功保存后的观察值；附带该时点的回执也须成功保存，调用者才可释放结果。
5. Paper `available_at_ms`：执行激活时点，不能等同记录可用时点。Coordinator 重检后，Broker 取得 SQLite 写锁再取时钟、检查 expiry；重启不得倒填此前时间。
6. `decision_expires_at_ms` / Paper `expires_at_ms`：冻结下一主周期收盘。迟到结果保留原 expiry 并拒绝执行。

缺失时点、时钟逆序、未知周期或日历月周期保持不可执行，不用文件 mtime、快照时间或模型自述补造。
旧记录不补时间；没有显式 pipeline 的旧配置保留原行为。角色停止、研究 soft/仲裁或 PA-only fallback 候选不能借新版名称进入联合账户。

## 执行与失败边界

- `pipeline-pa_only`、`pipeline-pa_shadow`、`pipeline-pa_vpa_joint` 与旧正式/研究账户隔离；报表沿用 simulation version 分层，不重标旧收益。
- 柱内激活时，整棒触价不能证明触价发生在激活后，必须标为不确定。完整有效窗口确认未触价则到期取消；闭盘缺失则不确定，不是确认未成交或 0R。
- Headless 在一轮行情观察后进行时钟到期检查；缓存命中、抓取失败及暂停期间也有检查入口，不重复模型或行情写入。进程停止期间不承诺即时状态更新，恢复后不能恢复过期资格。
- 到期是在轮询/暂停心跳时对账，不是交易所毫秒级定时器；数据迟到后不重写已 censored 的结果。更细粒度成交证据须另作版本验证，不能悄悄改变 v3。
- TP1 沿用既有成本/保护止损模拟；不增加动态退出、加仓、分批止盈或 LLM 持仓管理。限价保护指入场限价，不假设盘口排队或成交量足够。
- 柱内入场后同棒退出仍未知；开盘已成交时保留原保守止损优先规则。Paper 并非真实执行收益。

## 数据变更与回退

`paper_orders` 仅新增 nullable `available_at_ms`、`expires_at_ms`；旧行维持 null，版本和收益不重标。
本次只在临时数据库验证增量迁移，没有打开或迁移用户历史/VPS 数据库。

部署前须暂停相关写入，一致性备份完整 Paper SQLite、records 和 audit，不能单独复制活动 WAL 数据库主文件。
回退代码时同时停用显式新版 pipeline，并保留新版账本为只读证据；旧 Broker 不理解 v3 时间语义，不能继续处理含 v3 活跃订单的账本。
必要时从部署前备份恢复独立旧版实例，不删除新版记录。两个新增列无需逆向 DROP。
未验证 Linux 断电持久性、跨数据库原子事务或生产恢复；沿用既有文件原子替换与失败检查，不作更强保证。

## 三角色自审

这是同一实现者的三个视角，不是三位独立专家或 subagent 审计。

| 视角 | 判断与边界 |
| --- | --- |
| 软件架构师 | 接入原持久化与执行入口，没有第二条交易主链；时间由 Host 所有。回执失败、晚到入单和重启幂等有定向覆盖。跨存储崩溃恢复和租约属于阶段 9/11，不宣称 exactly-once。 |
| 量化工程师 | 不以快照时刻冒充交付时刻；入场限价不被滑点改为更差成交；缺失和柱内不确定不当零；v1/v2/v3 分层。主周期 OHLC 会产生较多 censored 样本，不能放宽规则粉饰收益。 |
| PA / Wyckoff VPA 交易员 | 保留 Brooks Router、结构止损、TP1 和等待；VPA 不增加第二套 Setup/Gate。过期是订单时效而非主观行情否决，没有精简知识或 Skills。专业增益仍需阶段 7 独立评价。 |

结论：通过阶段 6 的本地实现验收，不等于策略盈利、真实模型效果或 VPS 上线验收。

## 必要验证

15 个直接相关测试文件，188 项通过（6.90 秒）：

```text
test_headless_monitor.py              test_pipeline_paper_coordinator.py
test_analysis_delivery.py             test_latency_paper.py
test_execution_plans.py               test_paper_execution.py
test_paper_execution_coordinator.py   test_paper_reporting.py
test_joint_pipeline_runtime.py        test_pa_vpa_joint_orchestrator.py
test_pa_vpa_aabb_protocol.py           test_higher_timeframe.py
test_analysis_audit_store.py          test_pending_writer_sanitize.py
test_pending_writer_no_plaintext_key.py
```

使用项目 Python，pytest `-o addopts= -q -p no:cacheprovider` 和本次独立临时目录。
新增覆盖包括持久化/回执失败、多空限价、柱内市价、到期、缺失闭盘、事务激活、替换不确定性、旧表增量迁移、账户隔离、重启去重和角色停止。
关键 Ruff `E9,F63,F7,F82` 与 `git diff --check` 通过；没有全套、live 或收益测试。
新增文件格式化后再次运行其中 24 项核心边界检查，全部通过；这是上述 188 项的子集，不重复计数。

下一步是阶段 7 冻结证据盲评与独立实验协议，本阶段不提前开展。

## 2026-10-13 后续授权修订

本报告前文的固定下一收盘 expiry 是原 v3 验收证据。新 1h Headless 显式 pipeline 冻结 `limit_3bar_v1`：接收/激活资格仍到下一收盘，限价 pending 独立到第三个后续收盘；其他订单、旧 Frozen Job 和研究账户不迁移。扩展限价使用 v4，新增 nullable 新鲜度字段，不回填旧行。缺棒、柱内顺序未知仍 censored；有效新分析可提前替代，失败不续期。新版本验收、备份/回退边界及发布结果见 [运行修订报告](v1_runtime_failure_paper_audit_20261012.md)。
