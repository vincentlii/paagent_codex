# V1 运行失败根因审计与最小修复

日期：2026-10-10。范围：VPS Headless、Codex Worker、调度回执；不涉及 GUI。

## 结论与证据范围

已确认两个不同问题：Worker 的 `TasksMax=64` 无法承载当前三品种 CLI/MCP
线程需求；调度账本没有复制正常返回的失败分析原因，掩盖了故障类别。
资源容量修正已在上一轮发布；本轮修复失败原因传递，并保留进程创建错误的
数值 errno。没有修改 PA/VPA 知识、AGENTS/Skills/Prompt、模型、交易校验或重试。

历史缺少 stderr 的异常退出不能全部恢复唯一原因。容量试验成功不等于真实
三品种完整链通过；本轮不为排障额外调用模型，保留下一自然任务的验证边界。

## 一、原位只读清点

截至本轮发布前、2026-10-10 13:30 左右（Asia/Shanghai）：

- 调度账本 72 个任务：17 个成功、55 个失败；其中一个是更早的不可恢复任务。
- 原始分析记录 71 份：17 个成功、54 个失败。
- 当前 Binance 三品种看板口径的历史失败：XAU 27、BTC 14、ETH 13，共 54。
  更早的 Bitget XAU 失败不进入当前市场身份统计。
- 13:09 容量修正重启之后尚无新模型分析；Worker `pids.events:max=0`、
  `memory.events` 全为 0。不能把历史累计失败误认成扩容后的新增失败。

原始 54 份失败记录的类别互斥、合计如下：

| 类别 | 数量 | 可确认内容 |
| --- | ---: | --- |
| 旧 Direct Stage 2 空响应 | 2 | 2026-09-03/04，不是当前 Codex 模型质量样本 |
| 旧 Codex 冻结身份/发布拒绝 | 1 | 2026-09-04，保留旧协议证据 |
| Stage 1 unavailable | 10 | 其中 9 份明确为进程创建失败，旧记录未保留 errno |
| Stage 1 停止 | 6 | 5 个预算停止，1 个明确工具传输 resource temporarily unavailable |
| Stage 1 输出前拒绝 | 35 | 8 个旧记录无详细 Worker 原因；13 个 CLI exit 1；14 个 exit -6 |

本批失败没有 PA/VPA 专业校验拒绝作为根因的记录。此结论不能证明专业分析正确，
只说明不应为解决这些启动失败继续堆叠知识、Prompt 或 Validator 规则。

## 二、已确认的资源根因与修正

上一自然三品种任务命中 Worker cgroup `TasksMax=64`，pids 拒绝累计 23，OOM 0；
BTC/ETH 异常退出，XAU 联合链完成约 345 秒。PID controller 计入线程，不能只
按三个 CLI 进程估算容量。官方说明见
[Linux cgroup v2 PID controller](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html#pid)。

此前获批的无模型隔离对照：同一 150 线程需求在 64 上限只能创建 63 个线程并
命中 pids 拒绝，在 256 上限全部创建成功。修正提交 `37d397f` 已发布：
`TasksMax=256`，保持内存 1 GB、CPU 200%、模型并发 3、权限隔离和私有进程组清理。

进程创建失败、工具 resource unavailable 和异常退出与该资源不足一致；但
`exit 1/-6` 本身不是唯一资源证明。没有保存当时 stderr/core 的历史记录不得
倒填为已证实的同一根因，也不把未知异常自动改成 availability/fallback。

## 三、本轮代码修复

### 1. 不再丢失封装的分析失败原因

`IndependentAnalysisHandler` 正常返回失败的 `AnalysisRunResult` 时，调度回调
仍是 `completed`，这是正确的执行终态，不代表市场分析成功。旧审计仅复制
`JobCompletion.error_type/error_message`，这两个字段只在 handler 抛异常时存在。
因此 49 条 incomplete 与 5 条 unavailable_provider 调度回执均显示原因空白，
而原始 record 已有错误。

修复 `SchedulerAuditStore.record_completion`：仅在回调 completed 且
`analysis_success=false` 时，补录已有 record.exception 或 run_outcome.reason；
已有调度异常优先，追加最近一次可信 Worker failure reason，并沿原 2000 字符
上限保存。成功分析的 sidecar warning 不升级成整体失败。

不改 DB schema、不回填历史记录、不改 success/去重/恢复/重试语义。

### 2. 进程启动失败保留 errno

`CodexCliWorker` 原先把所有 `OSError` 都变成相同的启动失败文案。现在保留数值
errno，不保存原始错误文本、路径、stderr 或认证细节。异常类型仍为
`CodexWorkerUnavailable`，不新增重试。旧错误没有 errno 时保持未知，不推算补写。

## 四、验收与三角色自审

按排障护栏先建立失败用例：旧账本原因为空、旧启动错误缺少 errno。修复后
定向回归覆盖失败原因、成功 sidecar、调度异常优先、可信 Worker 原因、无
exception 的合法 role stop、CLI、Spool、Backend、调度恢复与部署单元。
最终 89 项定向回归通过（5.95 秒），关键 Ruff 静态检查及 `git diff --check` 通过。
发布身份在下方登记。

- 软件架构师：只修信息传递和有界资源容量，区分 callback 与 domain outcome，
  不加新协议、数据库表、自动重跑或模型链路分叉。
- 量化工程师：失败/缺失/未知保留，历史样本不改写；错误不是成功或 No Trade；
  不把预算/额度/资源失败当分析质量或速度样本。
- 交易员：完整 PA、VPA、HTF、Router、结构失效和订单数学保持原样；资源修复
  不替代专业质量验收，Paper 数据仍用于持续观察而非保证盈利。

## 五、后续自然运行判定

按新的运行提交与完成时间区分历史/新增失败。核对三品种三角色回执、耗时、
`pids.events`、`memory.events`、任务唯一性及 Paper 处置。
若仍失败，以新的可信 Worker 类别和 errno 区分启动、认证/额度、预算与输出问题；
不要直接扩大所有限额或放松交易校验。没有历史日志的 exit 1/-6 仍列未知。

本轮没有改变重试规则。联合链 incomplete/review_required 不自动重跑；旧
PA-only 的状态驱动 retryable 与 canonical 细分类别存在兼容审阅事项，不能在
本轮为观测修复直接开启已开始任务重跑。

## 六、发布回执

用户明确授权提交发布后，运行提交 `aa2d08c667b2d21e3d3ab90caf0997d5093092fe`
已从规范 LF 的 Git archive 构建 wheel，并快进 VPS 源码、停止三服务后重装。
备份 `/var/backups/pa-agent/pa-agent-20261010T053726Z` manifest 验证无错误；
暂停 generation 30 已 applied 且 scheduler/Spool 排空后才停服安装。

发布核验：

- wheel 与 VPS 安装的 272 个源码/Runtime/Prompt/fixture 文件对 Git blob 字节一致。
- 48 项依赖一致；Runtime 权限审计无问题，资产 digest 仍为
  `b991054d11f21bfd80b87f266cde7d24def02209ed0383cfd260109d2573e6c3`。
- 13:38 重启三个服务，ExecStartPre/ExecMainStatus=0、NRestarts=0、全部 active。
- 恢复 generation 31 running 且已 applied；Profile generation 4、指纹、设置 hash、
  三品种、并发 3、Fast true、`codex_only`、`gpt-6-luna/xhigh` 均保持不变。
- 71 原始记录哈希未变；monitoring/analysis/Paper 三库 quick_check=ok。
- `/healthz` running/200；私有 status/trading 接口 200，未认证 status 401。
- Worker 保持 TasksMax=256、MemoryMax=1 GB、CPU 200%；pids max/OOM 全 0，
  这是重启后空闲观测，不是三个 CLI 峰值或完整分析验收。

未额外调用模型或触发 Run Once，历史错误不改写。下一自然三品种完整链、长期
稳定、p95 和盈利仍未验证。发布后文档证据提交不改变已核验的运行 Python 身份。

回退时先暂停并排空；恢复固定旧包及服务后再恢复原控制，不能运行中覆盖文件。
本轮未迁移 schema；备份用于事故恢复，不能为回退代码顺便回滚正常新增 Paper 数据。
