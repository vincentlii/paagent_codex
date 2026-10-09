# Codex 单 Agent 联合分析：本地发布候选审查（2026-10-08）

## 结论与范围

当前分支是 **本地实现候选，不是 VPS 持续 Paper 上线许可**。本轮仅审查、补全
`codex_only`／`pa_vpa_joint` 的部署配置、调度与备份边界、看板投影；没有改变
Brooks Router、Validator、Trader Equation、Risk、订单物化器或 Direct 的正常配置路径。
测试只使用本地合成输入，没有调用真实 Codex、行情或交易账户，也没有修改 VPS。

单 Agent 正式候选固定 `gpt-6-luna/xhigh`；Stage 内子协作仍为研究模式且关闭。
既有两次真实同快照完整链约 10.9／17.9 分钟，另一个 Stage 1 约 437.9 秒；
样本不足以估计 p95，且已明显不能证明整链 p95≤300 秒、token p95≤250k。
本机 Codex CLI 0.158.0-alpha.2.1 显示 `Not logged in`，本轮不能补做真实质量／性能验收。
不得把旧模型／旧源码回执混入本轮 cohort。

## 本轮具体变更与验收

| 项目 | 本地结果 | 仍需验证 |
| --- | --- | --- |
| GPT-only | `codex_only` 预检与工厂不再强制 Direct key；Direct／auto 仍要求 key。新增显式联合配置和 systemd drop-in，旧 Direct 样例不变。 | 目标 Linux systemd 配置、账号与 CLI 权限。 |
| 联合运行身份 | Audit `analysis_runs.analysis_pipeline` 为可空新增列；新记录来自冻结 `AnalysisRecord`，旧记录保留 NULL。看板按 pipeline＋backend 分组，不把旧 Shadow 与 Joint 性能混算。 | 真实旧库备份后迁移及 VPS 只读看板。 |
| 调度审计 | 接受回执先于派发落库，审计失效拒绝新任务；完成审计失效呈失败，不把它伪装为成功。 | 异常断电、崩溃窗口和跨存储对账。 |
| Codex Worker | `.running` 尊重有效租约；超时且无结果才重排，已有结果不重复执行。 | 真正的 Linux worker kill／重启／结果恢复。 |
| 备份 | 三个 SQLite 在线备份、原始记录、经验、冻结 Job、角色接受标记及脱敏配置；manifest SHA-256 验证。SQLite 连接显式关闭，避免 Windows 临时目录句柄残留。 | 暂停状态恢复演练、跨存储一致性、冻结资源保留策略。 |
| 看板 | 显示当前 pipeline 与逐次 pipeline，失败类型、模型／强度／耗时／token、Paper pending／ambiguous；无 Direct key 禁止 UI 切回 Direct。 | VPS 访问、真实运行输出、质量与依据审阅页面。 |

本地定向检查：Audit／投影／Dashboard／调度／Headless／Codex spool／备份／VPS 资产
相关回归已通过；`ruff --select I,UP` 与 `git diff --check` 已核对；Runtime 资产审计
`ok=true`。这些检查只证明其覆盖的接口，不证明主观交易判断正确。完整测试命令和
当前失败项以执行当轮控制台为准，不能把本报告当作 CI 结果。

## 必须保留的 no-go

1. **崩溃恢复未闭合。** 调度索引先记录任务身份，但冻结 `AnalysisTask.payload` 仍只在
   Headless 内存。若进程在角色运行中或“分析记录已写、调度完成回执未写”窗口崩溃，
   不能可靠重建原任务或证明无重复模型调用。`run_on_startup=false` 不是持久任务恢复。
   本轮不以延长 timeout、租约或增加重试次数掩盖此缺口。需先定义持久 payload＋
   完成对账，或显式暂停并人工核对未终态任务，再做 kill/restart 故障注入。
2. **备份非全局事务。** 每个 SQLite 文件内部一致；记录文件、冻结 Job、接受标记与
   多个数据库之间没有原子快照。`verify_backup` 核对存在性与字节，但不证明业务对账。
   恢复前暂停服务、核对清单／三个 SQLite `quick_check`，并抽样关联任务／记录／Paper。
3. **性能与专业质量未晋级。** 旧完整链均超五分钟门槛，且尚无本轮相同设置的新样本。
   必须先恢复有额度的 CLI，并用新冻结行情得到完整 Stage 1→VPA→Joint Stage 2
   回执。分解模型推理、取证、队列与修复耗时；盲评 PA 结构、VPA 因果、等待与
   Trade/No Trade、结构止损、事实引用和重大幻觉。质量不降才考虑性能政策调整。
4. **Linux/VPS 尚未验收。** 本地资产审计不覆盖实际 UID/GID、`systemd`、网络、
   原生交易所数据、Dashboard loopback、备份 timer 或持续 Paper。不要启动服务。
5. **旧脏工作区未整批提交。** 既有多个阶段的修改和研究文件须保留原样；本轮不把
   它们扫进一个无法追踪的提交，也不清理用户资料。发布前需建立可审计 commit 和
   wheel／Linux 非 live 回归，再按 paused canary → Paper 的门禁推进。

## 三角色自审

- **软件架构师：** 保留单一主链与隔离 Worker；Direct 仍可用但 GPT-only 无凭据时不
  伪装可用。索引迁移可从原始 JSON 重建。最大阻断是任务 payload 与完成回执跨存储
  恢复，不能称 exactly-once；看板“运行时配置”与“历史运行身份”现已分开。
- **量化工程师：** 旧 pipeline 不补造身份；失败／unknown 不计零，Paper 与研究指标
  不混为真实收益。p95 需多独立快照、匹配 cohort；两次完整链只能作诊断，不能证明
  性能目标或概率校准。恢复也必须保持冻结 as-of 与 no-lookahead。
- **PA／VPA 交易员：** Stage 1 price-only、VPA 独立证据、联合 Stage 2 在 Router 范围内
  判断的权威边界未因运维改动变化。当前没有新盲评结果，不能宣称 Brooks Setup、
  Wyckoff VPA 因果或交易决策更好；市场不确定可等待，资源／身份故障必须停止。

## 下一轮最短路径

1. 先解决任务持久化／对账与暂停恢复，不动交易判断链；做一次中断故障注入和恢复演练。
2. 用新 cohort 做一轮 `gpt-6-luna/xhigh` 完整冻结行情链，收集逐角色 wall／token／
   取证／修复及原始 Draft，再做盲评；不以调高运维 timeout 代替达标。
3. 满足质量、性能、安全门禁后，在 Linux VPS **paused** 部署，验收权限、数据、
   Worker、看板、备份／恢复；先单次 Paper canary，再考虑持续调度。

## 2026-10-08 补充验收：重启恢复与真实冻结行情

本节覆盖上文“本轮没有真实调用”和“崩溃恢复未闭合”的历史状态，但**不改变上线
no-go**。变更尚在脏工作区、未提交；真实调用的 `start.json` 固定了当时源码指纹，
其后仅对两处自然语言误报警规则做了离线修正，不能把旧回执冒称为新源码的完整运行。

### 重启恢复边界

- `monitoring_frozen_tasks` 与接受回执同一 SQLite 事务保存完整冻结任务和 digest；
  Host 在模型工作前持久记录 `started_at_ms`。重启只自动恢复**从未开始**且身份可验证
  的任务。已开始的任务先核对唯一 JSON record、时间、Snapshot、Stage 1/2 和审计索引；
  无法证明完成的任务保持 in-doubt 并阻断该资产，不自动再调用模型。
- `AnalysisRecord.monitoring_task_id` 连接任务和结果。已开始后取消的任务仍保持 in-doubt，
  避免旧 Worker 可能继续运行时重新派发。Direct 正常分析链未改变。
- 两处真实进程 `os._exit` 故障注入、调度/Headless/联合链定向测试共 116 项通过；
  扩大至 CLI 与事实校验后的相关回归为 279 项通过。真实 XAU 冻结任务在新 SQLite
  实例中按 task ID、K 线、HTF 和 digest 重载成功；写入 start marker 后不再自动派发。
  含 NaN 的指标不能用 Python dataclass `==` 判定等价，本轮用规范序列化和冻结 hash
  校验。**真实 VPS 断电、跨库备份恢复与 Paper 对账仍未演练。**

### 单次真实模型链

隔离本地 ChatGPT 登录与项目 `.venv`（Python 3.11）运行；冻结 `XAUUSDT/1h` 主周期
100 根已收盘 K 线、4h HTF 60 根，Snapshot hash
`7d77a357ff90571c91f22f11204d20084addb242645fad12360a1694148b205d`。
执行 `gpt-6-luna/xhigh`、单 Agent、`pa_vpa_joint`，无 Paper 或真实订单写入。
回执保存在本地隔离诊断目录，不进入生产记录或研究四臂统计。

| 指标 | 本次实际回执 |
| --- | ---: |
| Stage 1 / VPA / Stage 2 | 148.3 / 90.8 / 84.7 秒 |
| Host 到记录持久化 | 324.5 秒 |
| 三角色总 Token（Provider 实报） | 116,657 / 64,100 / 369,004；合计 549,761 |
| Evidence 调用 / 总工具耗时 | 25 次 / 约 3.0 秒 |
| 结果 | `completed_with_warnings`，官方 `joint`，等待、不下单 |
| 截止前余量 | 约 477.9 秒 |

Stage 1 的 bullish broad-channel 假设保留 trading-range 替代；VPA 为可用数据但入场时机
证据不足；联合 Stage 2 没有虚构入场、止损、目标或胜率。这是**单次决策合理性观察**，
不是 Brooks/VPA 准确率、校准、交易增益或稳定性证明。模型/工具分解表明工具耗时仅约
3 秒，主要耗时在模型处理；Stage 1 唯一 Decision Packet 后至最终输出约 138 秒。
整链高于 300 秒目标，549,761 Token 高于 250k 目标，性能门禁未过。

本次原始 record 的两条警告经冻结数据核对为检测误报：K1 高点 4145.98 低于前窗
4147.00，模型说“尚未越过”而旧正向突破规则误判；VPA 的“下一根闭盘棒”明确指未来
已收盘棒。现规则按否定语义和闭盘同义词处理，原输出离线复核无这两条警告；原始
record 与旧回执不改写。定向回归 279 项通过。仍须在未来独立 Snapshot 复核模型质量。

前两次完整验收尝试在 Stage 1 启动后即因 required MCP 无法初始化而停止，保留原始
失败回执且用量覆盖未知，不能当作成功或性能样本。根因是隔离工作目录下 Python
曾导入另一个 editable 项目，以及系统 Anaconda Python 缺少已声明的 `mcp` 依赖；
Worker 现绑定 Host 同一包根目录，本地验收固定项目 `.venv`。另两份相同市场 K 线
的证据由 Python 3.11/3.13 分别计算出不同浮点事实目录 hash，跨解释器重放被拒绝；
冻结与执行必须固定同一发布环境，不得将其解释为行情变化。

### 三角色结论

- **软件架构师：** 接受—持久化—开始—结果对账边界已可在本地 fail-closed；仍需
  VPS kill/restart、SQLite/文件/Paper 跨存储核验，不能宣称 exactly-once。
- **量化工程师：** 同 Snapshot/同代码/同解释器的回执可追溯，失败不计零成本；
  只有一个真实样本，既不估 p95，也不证明收益增量。当前性能指标明确不达标。
- **PA/VPA 交易员：** 未把 VPA 强行当入场信号，等待且不伪造交易参数是合理边界；
  Stage 1 市场结构和联合判断仍需盲评与前向 Paper，多行情验证后才能讨论上线。

## 2026-10-09 修正后源码／新快照单次联合验收

本次仅做本地隔离验收，没有部署 VPS、启动持续 Paper 或发送订单。使用
`scripts/freeze_local_policy_market.py` 从 Binance USD-M 冻结新的 XAUUSDT/1h 已收盘
100 根与同源 4h 60 根；`as_of_ms=1791476486366`，
`snapshot_hash=a32b6b37181924e7caadce91c3137218c8c4e8eab30806cd2edc9f71b0fcaf70`。
最近主周期及 HTF 收盘边界均为 `1791475200000`，早于冻结时刻；`DataQualityReport`
无 hard error 或 warning。源码指纹
`997045314ad1b3d0a53e8e8c8c724b60c57a517532b52fe36ce5112c9b16017d`，
Runtime 资产 digest `9a9b691fd4e84c6cd13d2293aa16f2c439c690b5f82f303cde5031adc2a7e043`。
冻结输入、`start.json`、逐角色 checkpoint、完整 Host record 与指标原件保留在
`D:\new_paagent\tmp\codex-joint-acceptance-20261009\`，不与旧同快照四臂拼接。

正式全量知识、单 Agent、`pa_vpa_joint`、`gpt-6-luna/xhigh` 三角色均首稿
`validated`：Stage 1 108.05 秒／111,835 Token，VPA 97.13 秒／64,790 Token，
联合 Stage 2 67.67 秒／333,710 Token；Host 全链 273.53 秒／Provider 实报
510,335 Token。三次传输、零 repair、零 fallback、零 Evidence denied；21 次证据访问
合计约 0.339 秒。Stage 1 的单次 Decision Packet 与 VPA／Stage 2 各自的完整
baseline assembly 均有成功审计。Host 结果为 `completed_with_warnings`、
`official_path=joint`、`wait`／`不下单`，没有入场、止损、目标或概率，也没有 Paper
写入。单次低于 300 秒不构成 p95 达标；总 Token 仍超过 250k 目标，主要开销在
模型侧累计上下文，非 Evidence 网关。

逐角色专业核对：Stage 1 将宽幅交易区间／中性作为主解释，保留宽通道偏多备选，
HTF 偏空仅作为背景风险；K28、K20、K6 的低点确实逐步抬高，K1 仍在前 20 棒
极值内。VPA 的 `vpa-w020` 当前／前窗净进展为 -0.033／-4.601 ATR，
`vpa-w040` 为 -3.264／-0.098 ATR；报告的方向和相对活动比较与冻结目录一致，
把 K2→K1 卖压与 K1 之后无已闭盘响应分开处理，未创建新的 Setup。联合 Stage 2
依据已验收 VPA 报告、Router 和 PA 主／备选判断选择等待，没有把 VPA 当作突破许可。
这是本快照的合理性核对，不是交易正确率、收益或校准证明。

质量保留项：VPA 首稿有 6 条 `professional_prose_warning`，集中于窗口比较的逐句
引用不够精确；其正文又使用英文，不符合本项目用户可见解释优先中文的要求。
Stage 2 有 1 条 `next_cycle_prediction` 的无歧义别名规范化，未触发 repair。
这些警告没有改变本次等待结论，但不能宣称零警告或专业质量完全晋级。
`meta.ai_provider` 显示 DeepSeek 配置快照、`requested_reasoning_effort=high` 是通用
请求字段；实际三次调用的 `provider_id=openai_codex`、`effective_model=gpt-6-luna`、
`effective_reasoning_effort=xhigh` 与冻结 Runtime 一致。审阅者应使用后者判定运行身份，
不把配置快照冒充实际 Provider。

缓存清理只删除 worktree 中 Git ignored 且可重建的 29 个 `__pycache__` 与一个
`.ruff_cache`，合计约 12.53 MB；删除后确认无残留。保留所有未提交源码、知识、
研究材料、旧失败回执和本次冻结／模型原件；没有发现能证明安全删除的其他冗余改动。

三角色结论：软件架构师确认完整基线、身份、角色隔离和回执贯通，但 VPS 恢复／
持续运行仍未验收；量化工程师确认新快照、闭盘、实际用量与失败分母，单样本不能估
p95，Token 门禁未过；PA/VPA 交易员认为本次保留反证与等待合理，但 VPA 引用表达
必须进一步改善并在独立行情复核。仍为本地候选，**不授权正式上线**。

## 2026-10-09 有界 Paper 发布准备（后续授权）

用户在上述验收后明确批准全量 A 部署至 VPS 持续 Paper，并豁免性能目标作为
该次观察发布的阻断条件；专业质量、收益、实盘权限门禁未放宽。已批准将保留的
工作区基线与必要发布改动提交，不再以旧 no-go 结论否定本次限定授权。

新增 Fast 控制只对下一份冻结 Codex 任务生效，CAS generation、idle 应用、
请求／实报身份和独立 cohort 均保留；不减少全量知识，不改变 xhigh，不承诺
GPT-6 Luna 的固定 1.5× 速度。原 Direct 不增加 service tier 参数。

本地非在线 unit/integration：1573 passed、7 live deselected；发布相关追加
定向回归 97 passed、备份边界 6 passed，JS 语法、相关 Ruff I/UP、资产审计和
wheel 构建通过。Windows MCP 故障根因是旧 Cursor SDK 全局替换 Popen，现将
argv 修正仅限制在 Cursor bridge，不再破坏 MCP 类型；不是削减知识或放松验证。

三角色自审：架构师确认配置冻结与用户隔离，要求 Linux 权限、重启及备份真实
验收；量化工程师仅接受前向 Paper 观察，不宣称 p95／收益达标；交易员保留
PA 主框架、VPA 证据层、HTF、反证及合法等待，不用 Fast 绕过任何专业门禁。
持续调度须等待目标 VPS 安装、数据探测、备份恢复与真实三角色联合链回执闭合。

## 2026-10-09 VPS 有界 Paper 发布验收

运行提交 `48eb9b156998379125d6ab9980761af72a4329fa`；传输 bundle SHA-256
`5e388f24bbe76f7356e0aa1683c3fa052504c2c3b66852e9dc1faf411854902d`。
原 `/opt/pa-agent` 原位替换，已安装包所有 Python 源码与提交比对无差异，Runtime
资产 digest 仍为 `9a9b691fd4e84c6cd13d2293aa16f2c439c690b5f82f303cde5031adc2a7e043`。
CLI 由旧 standalone 0.153.0 替换为固定 npm 0.162.0；ChatGPT 登录和
`gpt-6-luna/xhigh + Fast` 的 AGENTS/Skill canary 通过。前两个临时探针等待 stdin
而未获得模型结果，修正临时脚本后通过；正式 Worker 原已有 DEVNULL，不需补丁。

Linux 定向回归 59 项通过，涵盖任务 payload 持久化、进程退出后的恢复/阻断、
Dashboard 控制与备份。源码、数据库、凭据的负向权限检查通过；Codex 用户不能
写源码，不能读生产/纸交易数据库与 Direct 密钥，Dashboard 不能读这些数据或
Codex auth。公开 Binance 探测确认 XAUUSDT tradifi perpetual、真实交易所 volume、
1h＋4h 闭盘及合约规格；没有私有交易 API 或真实订单路径。

systemd 备份已实际成功；暂停后的备份
`/var/backups/pa-agent/pa-agent-20261009T023548Z` manifest 校验通过，隔离恢复的
analysis/monitoring/paper 三库 `quick_check=ok`。发布前 root-only 回滚归档保留在
`/var/backups/pa-agent-release-20261009/pre-release-state.tar.gz`；此归档含部署配置，
不应公开或复制至普通用户目录。日常备份模块仍剥离凭据，不复制 auth/API key。
旧 Bitget task `1d904c2d…071a10` 没有冻结输入或可关联记录，精确核对后登记
`legacy_unrecoverable` 失败，未盲目重跑或计为成功；原件与备份保留。

### 首次真实 VPS 联合链

Run ID `094945e6df204f8aaed7e55864fe1991`，task ID
`4bafbc8b3dfdf1838add8a2152167eff1c0e48eb3d2bab90c1f377e05dca597e`，Snapshot
`2a58d3c953e09069b5708a6a1424e9d906daab790ad71e7a7f37e4d7f14febfa`；
`analysis_as_of_ms=1791512480398`。三次冻结 Job 均绑定相同快照、全量 A、
`gpt-6-luna/xhigh` 与 `service_tier=fast`，Stage 内子协作关闭。

| 角色 | 调用耗时 | Provider Token | 验收 |
| --- | ---: | ---: | --- |
| Stage 1 | 149.53 秒 | 113785 | 首稿 validated，单次完整 Decision Packet |
| VPA | 100.09 秒 | 64501 | 首稿 validated，完整 baseline assembly 1 次 |
| 联合 Stage 2 | 124.34 秒 | 362596 | 首稿 validated，完整 baseline assembly 1 次 |

调用合计 373.96 秒，Host 到结果可用约 377.21 秒；总 540882 Token、26 条内部
Evidence 审计事件、Evidence 合计 3.51 秒（含首次 tokenizer 冷启动），零 denied、
repair、fallback。内部资源读取事件不等于 26 次模型往返；VPA/Stage 2 的 assembly
回执均确认无损合并交付，不能仅凭内部事件列表误判为模型逐个读取。

Host `completed_with_warnings`、`official_path=joint`、最终 wait/不下单。Paper
独立账户 `pipeline-pa_vpa_joint` 记一条 no_trade、零订单；旧 `pa-official` 账户
数据不混入联合链。任务 completion、唯一原始 record、analysis 索引、Paper
disposition 核对一致。暂停状态重启三服务后仍仅这一条任务/记录，没有重跑模型。
这不构成跨存储全局 exactly-once 或长期中断覆盖证明。

专业核对：K1 确实上破且收于前 20 棒高点上方，但无 K1 后已闭盘响应；较早 K55
高点仍在其上方。VPA w040 当前净进展为正、前窗为负，w008 当前向上进展较前窗
更强，与报告一致。Stage 2 未将背景量价支持当作接受或入场许可，保持等待。
探索性检查发现 K38/K25/K21/K10 并非逐个高低点单调上升；模型并未声称这一
严格序列，不能把该更强假设当硬校验或反向宣称结构已获证明。宽通道与趋势区间
仍是主观竞争解释，需后续独立行情与前向 Paper 检验。

保留 PA 引用范围和 VPA 逐句比较表达警告各 5 次，以及两条无歧义规范化；不宣称
零警告。通用请求/配置快照仍可能显示 DeepSeek/high 或旧 sidecar 的 off，实际
三角色 Provider、有效模型/effort、Joint path 与冻结身份才是实际执行依据。

### 三角色结论与运行状态

- 软件架构师：允许本次有界 Paper 观察。安装产物、权限、备份、真实回执与暂停
  重启闭合；Codex 无代码/数据库权限，Host 持有事实、Router、Validator 和 Risk。
- 量化工程师：仅接受 forward Paper。单张新快照不估 p95、不证明收益；本次
  超过原 300 秒/250k Token 目标，按用户明确豁免开放观察，不改为“性能达标”。
- PA/VPA 交易员：等待判断与未接受的上破证据相容，VPA 无独立下单权限；引用
  表达警告与周期细分不确定性仍需持续审阅，不能承诺科学上永远正确。

通过看板控制接口恢复 running（generation 13）；三项服务 enable，每日备份
timer enable，下一次新闭盘触发分析，不对启动时的旧闭盘立即重跑。Fast 请求与
应用为 true，不保证固定 1.5×；套餐用量/限流与模型可用性须前向观察。
三条旧孤立 Codex 手动会话已正常 SIGTERM 关闭，登录与历史文件保留。新服务
空闲内存约 Headless 62 MB/Worker 65 MB/Dashboard 25 MB，VPS 可用约 2.8 GB，
磁盘剩余约 22 GB；此为时点读数，不是长期资源上界。

Dashboard 仅监听 `127.0.0.1:8765`，本机安全 tunnel 已建立。已请求内置浏览器
打开 `http://127.0.0.1:8765`，但浏览器自动化因 Windows sandbox 初始化失败而
不能做本轮视觉验收；HTTP 控制/状态与健康读取已通过，未开放公网 8765。
原始结果只在 VPS 原位保留，不导出私有事实载荷；后续访问沿现有认证通道。

## 2026-10-09 看板登录兼容修复

内置浏览器三次访问首页均进入错误页，应用日志的实际错误码为
`ERR_INVALID_AUTH_CREDENTIALS`。Windows 不经代理的健康检查和显式认证均为
HTTP 200，因此不是服务、隧道或密码失效。经用户批准改为网页密码登录＋随机
内存会话，细则见 ADR-0023 增补；保留显式 Basic 诊断兼容，不再返回认证 challenge。

最终 Windows 看板／控制面 59 项、Linux 看板 44 项通过；Ruff I/UP/F、两页
JavaScript 语法及 diff 检查通过。VPS 实际首页、错误密码、原密码登录、会话
状态读取、跨站写入拒绝、退出后拒绝均通过；本机原 SSH 隧道未登录首页为 200
且包含登录表单。会话过期、伪造 Cookie、服务重启失效及 Unicode 输入由回归覆盖。
wheel SHA-256 为 `0f3067e8c8214c2dc4535f80827e45dba75053700a21aed231c41413ec8ada01`。

部署在已暂停且队列空闲后执行，仅重启 Dashboard。未修改模型、知识、AGENTS、
Skills、PA/VPA、Router、Validator、Risk 或 Paper；没有重新运行付费实验。
原看板模块在 `/var/backups/pa-agent-dashboard-login-20261009` 保留私有回滚副本，
没有改密钥、数据库或开放公网端口。浏览器自动控制仍受本机 sandbox 初始化故障
影响，HTTP 验收不冒充视觉验收，需用户刷新当前页面确认实际显示。

三角色自审：PA 交易员确认交易与暂停语义不变；VPA 交易员确认无量价／方向权限
变化；量化软件架构师确认认证与业务隔离、会话有界及 fail-closed 授权回归通过。
