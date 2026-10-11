---
lifecycle: active
capability: paper-observation-limited
verification_scope:
  - local
  - VPS-controlled-live
issue_status: monitoring
最后核验: 2026-10-12
核验提交: c20d04f41f06d344c9da1b7a76997ab1f722765c（运行代码、知识与 Runtime；后续发布证据提交仅更新文档）
权威来源:
  - docs/linux_headless_deployment.md
  - CONTEXT.md
  - docs/architecture/ADR-0015-runtime-recovery-and-frozen-execution-facts.md
  - docs/architecture/ADR-0023-vps-observability-and-control-plane.md
---

# 运行与排障

## 私有源码仓库与清理边界

独立私有仓库目标为 `vincentlii/paagent_codex`，目标发布分支为 `main`。
首次创建和上传前，须明确核对用户授权的账户、私有属性及历史载荷范围；
本地提交或已登录 GitHub 不能独自证明外部仓库已经创建或上传完成。
本地继续在现有 `codex-analysis-backend-v1` 工作树开发，使用独立 remote
`paagent_codex` 推送 `HEAD:main`；原 `origin` 保留为 PA_Agent 上游，
不使用 `--mirror`、`--all` 或上传其他实验分支。源码上传不代表 VPS 已更新。

清理前核对 tracked 引用和运行资源契约；宣传图片、一次性 probe 结果及
生成缓存可移除，运行代码、PA/VPA 原文、Runtime AGENTS/Skills、lock、
必要测试与正式审计证据保持完整。已提交文件可从清理前提交恢复；缓存
可再生成，虚拟环境保留在本地且不上传。不得顺手清理其他工作树或 Git 对象。

上传前分别扫描当前 tracked 文件与当前分支可达历史，不仅检查 `.gitignore`。
生产配置、API key、Codex auth、数据库和原始运行记录保持本地；保留的上游
历史曾包含日志，不能把当前目录已忽略当成历史已清理。2026-10-11 历史扫描
的 45 项命中逐项复核为请求 ID（44）和密钥遮罩测试的虚构 fixture（1），
不是实际认证密钥；不为消除误报修改测试或放宽扫描规则。

本次源码树由 697 减为 675 个 tracked 文件；212 个测试／helper／fixture
及运行代码、知识、Runtime 资源、部署文件和依赖锁均与清理前逐字节一致。
当前源码树扫描仅命中上述虚构 fixture；README 的本地链接与忽略规则通过。
未重跑模型、未修改 VPS，不能把源码整理当作新的运行或盈利验收。

## Provider 修复拒绝与 VPA 身份停止

修复派发前的 `invalid_request` 先比对 canonical 错误完整路径、Provider Schema
与原始 Draft，不先改 Prompt。嵌套 required 不应变成根字段；原 Draft 缺父对象时，
授权最小实际缺失容器，并在模型补字段后完整重验，不让赋值器自行扩权限。

VPA 的 `review_required` 若报告 hash 不一致，先区分 PA 价格 / 指标 hash 与
VPA OHLCV / 来源 hash，再用同一原 frozen frame 和既有完整 catalog verifier
核对同源身份、handoff、cutoff；不能把不同 hash 直接当错配或强制相等。
新 Job 交付显式 binding，旧 Job 保持原投影。失败不改写、不自动重跑。
诊断证据及验证边界见 [契约根因报告](../quality/v1_contract_failure_repair_20261011.md)。

## 调度完成但分析失败：错误传递

`JobCompletion.status=completed` 只表示 handler 已返回；分析成功仍看
`analysis_success`。失败 record.exception/run_outcome 已有原因时，调度审计
须保留该原因及可信 Worker failure reason；成功 sidecar 不升级为整链失败。
启动错误保留数值 errno，不记录原始 stderr、路径或认证细节。旧记录缺失的
原因保持未知，不倒填、不以重跑覆盖。先核对 TasksMax 线程拒绝与 OOM，再判断
CLI 异常退出；详见 [根因报告](../quality/v1_failure_root_cause_audit_20261010.md)。

## 白名单运行配置与会话保留

当前发布版本的 `AnalysisProfile.symbols` 仅允许 Binance XAUUSDT/BTCUSDT/ETHUSDT；
`max_concurrency` 仅 1/2/3，`decision_stance` 仅 conservative/balanced/aggressive。
默认仍单黄金与并发 1；服务 idle 后才 applied，旧 frozen identity 不重标。
VPS 并发调整须同步核对 `PA_AGENT_CODEX_MAX_WORKERS` 容量；调度设为 3 而 Worker
仍为 1 不会产生三任务并行。当前单黄金不会因此加速 Stage 1→VPA→联合 Stage 2。
用户已启用三品种。Worker 的 `TasksMax` 计入 CLI/MCP 线程；2026-10-10 实际
64 上限命中 pids 拒绝 23、OOM 0，隔离探针验证 256 可容纳 150 线程。
发布单元采用有界 256，保留内存 1 GB、CPU 200% 与权限隔离，不扩大模型并发。
先暂停新任务、确认 scheduler/inbox 排空、备份，再重启 Worker；单元/文档改动
不需要重装 Python 包或中断 Dashboard。历史退出 -6 不倒填唯一根因，后续自然
任务实际成功率与 pids/memory 计数分开核对，见交易总览发布报告。
非该原生 VPS 范围的旧监控保留原数据源，不得把不支持的品种请求误报已应用。
取消勾选后，检查 `analysis_enabled=false` 与 Paper pending/open：有正式活跃
订单仍轮询行情，无活跃订单不额外取数。暂停整体分析仍沿原暂停/expiry 语义。

Codex 长期记忆在发布 config 与每次 CLI override 中均关闭；存在磁盘 rollout
不等于会话被其他任务读取。仅同快照、已验证 Stage 1 首稿 UUID 可续接 Stage 2。
专属服务会话超过 14 天时，由空闲 Worker 每小时调用官方 delete 清理，每轮最多
8 个、每个 3 秒；inbox/running/active 或仍存 PA workspace 时保留。错误只记计数，
不阻断交易分析，不触碰 auth/records/Paper。不得手工删除 Codex SQLite 或整段
会话目录。VPS 已发布并在空闲时清理 1 个符合条件的专属会话、errors=0；三品种
负载与长期效果仍待 Paper 观察，见 [升级报告](../quality/v1_operations_upgrade_20261010.md)。

## V1 Headless / Fast 故障的核验顺序

1. 核对 desired/applied Fast、control/profile generation 与 `control_error`；控制
   往返只影响后续冻结任务，不能改旧 Job、登录、AGENTS 或知识。
2. 核对 Snapshot/cohort、Job Runtime identity 和 service tier；不能靠 UI 按钮
   与故障时间相邻就判定根因。新 joint pipeline 与旧默认 pipeline 的身份须分离。
3. 核对 Worker 启动时间、固定源码提交和安装包；服务运行时禁止覆盖安装。
   包更新后重启 Headless、Worker 和 Dashboard，不能只刷新网页。
   Windows 导出发布档案显式使用 `git -c core.autocrlf=false -c core.eol=lf archive`；
   中文路径完整提取，wheel 与 Git blob 逐文件核验，不把工作区换行混入 Runtime digest。
4. 从完整 record 的 Codex call/exception attempts 读取 `worker_error_type`、
   `worker_failure_reason`、typed wall latency。该 reason 是 Host 分类，不保存
   raw stderr、Prompt 或模型推理。旧记录缺字段时保持未知，不能倒填为已知根因。
5. timeout/正常 CLI 退出都需清理该次私有 Linux 进程组，避免遗留 MCP、管道和
   资源占用；不能按名字结束所有 Codex。TasksMax/MemoryMax 未命中不先扩限额。
6. 控制暂停、等待 idle、备份并验证、停止三个服务，再发布固定提交。重启后先
   确认 paused/无重复旧任务，再恢复原获批 Paper；不为排障盲目完整重跑模型。

统计口径：task callback 不等于分析成功；legacy null/零且无覆盖证据的 Token、
Context、Evidence 和 latency 保持 unknown；首稿通过由 canonical classifier
判断，不因最终成功掩盖 repair/fallback/normalization。Host 延迟和调用累计分开。
开仓后的 v3 缺失中间 bar 记录 censored，不凭下一根触价补确定 PnL。
本轮证据和未闭合项见 [V1 体检报告](../quality/v1_health_audit_20261009.md)。

### 分析成功但 Paper 拒收：闭盘修订

先独立核对 `analysis_runs` / call ledger 与 `paper_decisions`；completed 不能代表
Paper 接收成功。发生 `conflicting closed bar for paper market identity and timestamp`
时，以同源 market key＋ts_open 比对账本和原始冻结棒，再按观察时间追踪记录；
必要时只读查询公开行情，不改旧 Snapshot。2026-10-09 已观察到刚闭盘约 2 秒与
约 17 秒的 close/volume/amount 不同，后者与后续记录及公开 API 一致。
时间闭盘不保证 Provider 数值已稳定；不能先归因 Fast 或模型。

按用户授权的 ADR-0012 小范围修订：volume/amount 可审计更新；OHLC 仅在所有账户
均未消费该棒执行路径时可更新。`paper_market_bar_revisions` 保存 before/after、
observed-at 与当前版本最后确认时间；已影响执行的价格冲突仍回滚整次观察，
不改收益、订单、旧拒收回执或原始快照。不使用价格容差，也不加固定等待时间。

Headless 观察传递冻结 as-of；完成／恢复回调使用原记录 as-of，而非模型完成时间。
旧记录晚到时不得回退已知新版本；相同版本再次观察也更新最后确认时间，重启后
保护仍生效。首个修订前已有旧行情的，审计只记录实际已知原值，不伪造旧观察时间。
发布必须先备份；本地实现及验收见上述报告第八节，VPS 发布及真实账本轮询证据见
第九节。已修复历史冲突不意味着允许改写旧 rejected 回执或绕过交付 expiry 重放订单。

## 核心顺序

遇到“分析未通过”、PA-only 降级或 VPS 异常时，按以下顺序执行：

1. **保留证据：**不要立即覆盖 record、清库、重复运行或改 Prompt。保存 task/run ID、时间、source/venue/symbol/timeframe、Snapshot hash、Provider/模型、requested/applied reasoning、日志和 UI 文案。
2. **识别终态：**读取结构化 RunOutcome、失败角色和错误码，不用“分析未通过”五个字定根因。
3. **检查输入身份：**确认已收盘 bars、主周期/HTF 同源、volume provenance、instrument spec 和 Snapshot/catalog hash。
4. **检查 Provider：**区分 API key、余额、401/429、timeout、5xx、截断 JSON、模型能力和请求参数。
5. **检查契约与事实：**只把 Schema、明确 OHLC、Evidence identity、权限和订单几何视为代码可修错误。
6. **检查持久化和线程：**确认成功 record 真正落盘、SQLite/磁盘可写、任务未重复、线程/进程终态一致。
7. **决定行动：**代码 bug 修根因；合理 LLM 专业偏差记录复盘；Provider 故障重试/换服务；数据不足保持 unavailable。

同一问题第三次验证仍失败，停止局部 patch，审查架构、数据流、状态管理、依赖和失败边界。

## RunOutcome 快速分类

| 状态 | 先检查 | 通常归属 |
| --- | --- | --- |
| `completed` | warning、执行资格、record 是否完整 | 正常完成 |
| `completed_with_warnings` | warning role、VPA/quality 细节 | 正式 PA 成功，附加质量问题 |
| `degraded_pa_only` | Joint prompt/role/validator | 隔离研究 Joint 失败并退回 PA baseline；不是生产 VPA Shadow 状态 |
| `unavailable_input` | bars、identity、HTF、volume、snapshot hash | 数据或配置 |
| `unavailable_provider` | HTTP code、余额、key、timeout、model availability | Provider/网络 |
| `invalid_model_output` | first draft、repair pointers、final validation | 模型契约或确定性事实；不自动等于模型交易判断差 |
| `cancelled` | cancel source、thread/process shutdown | 用户/生命周期 |
| `internal_error` | stack、persist state、task identity | 代码或环境内部错误 |

## Windows GUI

启动入口：

```powershell
python -m pa_agent.main
```

常见问题：

| 症状 | 检查 | 正确处理 |
| --- | --- | --- |
| `No module named pyqtgraph` | 当前 Python/Conda 环境、包安装位置 | 激活安装项目依赖的同一环境；不要为 Headless 强装 Qt |
| `pa_agent.gui` 不能直接执行 | 使用了包目录作为 module | 使用 `python -m pa_agent.main` 或安装后的 `pa-agent` |
| 分析按钮后失败 | GUI validation detail、RunOutcome、records/audit | 按失败角色排查，不只看弹窗 |
| 关闭窗口崩溃 | 是否仍有分析/刷新线程、是否调用 terminate/deleteLater | 协作取消并等待真实 `QThread.finished()` |
| 行情与分析 K 线不同 | Snapshot 是否冻结、刷新是否在分析期间继续 | 分析以冻结 Snapshot 为准，UI 图表可稍后刷新 |

## Linux Headless / VPS

安装和 systemd 命令以 [Linux 部署文档](../linux_headless_deployment.md) 为准。推荐验收顺序：

```bash
pa-agent-headless --check
pa-agent-headless --probe-data
pa-agent-headless --once
pa-agent-paper-report
```

- `--check` 验证配置、目录、资源和凭据可读性，不调用行情/模型。
- `--probe-data` 验证真实行情 identity、HTF 和 instrument spec，不调用模型。
- `--once` 完成一次冻结分析、持久化和 Paper 流程。
- 常驻前验证 systemd 用户、ownership、重启、日志、磁盘和时间同步。

Headless 启动失败时检查：

1. `PA_AGENT_HOME`、Prompt/resource 目录和配置文件是否由正确 Unix 用户读写。
2. wheel/lock 是否包含 Prompt 知识和所选数据源依赖，且未加载 Qt。
3. API key file 权限是否只允许 Headless 用户读取。
4. operation directory 是否允许 Headless 发布 status、Dashboard 读 status/写 control。
5. SQLite 是否 busy、磁盘是否只读/已满，成功是否被错误投影。

## Dashboard

- 默认只监听 loopback，通过 SSH tunnel 访问。
- 使用独立 Dashboard token，不复用 Provider API key。
- 网页密码登录替代 Basic Auth 弹窗；会话 8 小时有效，退出／服务重启后失效。
  内置浏览器显示“无法访问”时，先核对真实错误码：`ERR_INVALID_AUTH_CREDENTIALS`
  可能是认证 challenge 失败，不能误判为隧道断开。用未登录首页、`/healthz` 和认证
  `/api/status` 分别定位页面、服务与授权；密码不放 URL，不为排障开放公网端口。
  具体安全边界见 [ADR-0023](../architecture/ADR-0023-vps-observability-and-control-plane.md)。
- Dashboard 用户不能读取 `PA_AGENT_HOME`、内部数据库、records、Paper ledger 或配置。
- 写入 `paused` 后先看 control `generation`，再等待 runtime `applied_control_generation` 和 `service_state=paused`。
- Profile 或 Backend 的 `202` 只是 desired state；必须等待 desired/applied generation、fingerprint
  和 Backend 一致后才能 `Run Once`。单次任务完成后仍保持 paused。
- Recent Runs 必须同时核对 requested/effective model、effort、repair、latency、terminal outcome；
  `auto` 成功不能替代 `codex_only` smoke。
- 暂停不终止进行中的任务；停止/重启进程使用 systemd，不在 Dashboard 中模拟。
- 控制文件损坏时 Headless fail-closed 暂停新任务，并发布 `control_error`。

## Provider 与模型

| 证据 | 含义 | 行动 |
| --- | --- | --- |
| 401/403 | key、权限或 endpoint 错误 | 检查实际 Provider/profile，不修改交易 Prompt |
| 402/余额不足提示 | 上游账户/路由计费问题 | 核对本次请求实际使用的 base URL、model、key；余额充足不代表请求走了预期 Provider |
| 429 | 限速或并发上限 | 使用有界退避、降低并发；不把它记为模型输出无效 |
| timeout/5xx | 临时服务故障 | 保留 task ID 有界重试，避免重新冻结不同 Snapshot |
| 截断/非 JSON | Provider 交付问题 | 记录 first-pass，按契约允许一次定向修复或失败 |
| requested low 但 applied high | Provider 不支持映射或请求方言不匹配 | 分别记录 requested/applied；比较 cohort 时按 applied 隔离 |
| 专业判断偏差 | 合法结构、事实无矛盾，但 PA/VPA 解释可争议 | 交易复盘/换更强模型/重复研究，不新增硬 validator |

## Codex Analysis Worker

- 每个 Frozen Snapshot 必须出现新的 PA 会话；只有该 Snapshot 的 PA Stage 1 attempt 1 可由 Stage 2
  以冻结 parent job/thread identity `resume`。VPA、Joint、repair 必须是新的 ephemeral task；任何
  跨 Snapshot、Stage 1 repair 后续接或身份不一致都应在模型启动前失败。
- CLI 必须为 read-only、approval `never`，并禁用 shell、Web、image、用户 config；唯一工具为
  `required=true` 的 `pa_evidence` stdio MCP。
- Codex CLI 0.153.0 尚不接受文档中的 `tools.view_image` strict-config key。当前通过不传
  `--image`、workspace 无图片、AGENTS 禁止 image、仅注册 Evidence MCP 保持等价边界；不要盲目加入
  目标 CLI 不接受的 key。`codex exec` subprocess 必须使用 `stdin=DEVNULL`。
- production 保留 workspace `AGENTS.md` 与 Role Skill discovery，不传 `--ignore-rules`；上线前必须以
  `runtime_audit --live-canary` 的随机 sentinel 验证两者确实生效。
- OpenAI Codex profile 使用 ChatGPT account auth；`auth.json` 按密码管理，不设置隐式
  `OPENAI_API_KEY`/`CODEX_API_KEY` fallback。显式 DeepSeek Codex profile 只读取隔离 worker 的
  `DEEPSEEK_API_KEY` credential，不改变会话隔离、Evidence 或 Host authority。
- `PA_AGENT_CODEX_ENABLED` 默认关闭；启用前 model/effort 必须来自发布 catalog，并确认 Codex binary、Prompt 目录、
  `$PA_AGENT_HOME/codex/{home,jobs,runtime,outbox}` 权限通过离线 `--check`。
- systemd 以独立 `pa-agent-codex` 用户运行 spool worker。Headless 只写 Job/inbox、读取 result；
  Codex 只读 Job，只写独立 home/runtime/outbox/inbox/result，不能访问 `/var/lib/pa-agent` 或
  systemd credential。真实 VPS 上必须对源码、生产 DB、Paper ledger 和 credential 做负向读取/写入测试。
- `job_digest`、resource digest、output schema、Evidence audit 或 role 不一致均按不可接受结果处理，
  不摄取 outbox、不写 canonical record。
- `runtime_asset_digest`、Codex binary digest、model/effort 与 Profile fingerprint 必须进入 Frozen Job；
  未知 model/effort、已安装资源与源码不一致或 runtime identity drift 都 fail closed。
- 排障先区分 CLI/auth/rate-limit/timeout/MCP startup 等 availability failure，与 schema、事实、
  authority、专业判断等 analysis failure；只有前一类可进入后续定义的 cold fallback。

命令与认证行为以 [OpenAI Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)、
[Codex authentication](https://learn.chatgpt.com/docs/auth) 和
[Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) 为准。

## 数据与 VPA 降级

**先检查程序事实：**bars 是否已收盘、volume 是否 available、真实零与缺失零、proxy/tick volume、RVOL baseline coverage、seasonality 是否严格因果。

**再检查 VPA 输出：**Evidence ID、bar range、sequence chronology、是否复写错误 OHLC、是否越权创建 Setup/方向/价格/胜率。

VPA 输出 neutral/insufficient 是合法结果。VPA role 失败时正式 PA 应保留并记录 `completed_with_warnings`；若整次 PA 也失败，继续检查是否错误地把 VPA warning 升成 hard reject。

## Records、audit 与 SQLite

排查至少关联以下身份：`run_id`、task ID、source/venue/symbol/timeframe、Snapshot hash、Prompt/contract digest、provider/model/settings、cohort。

- JSON record 是分析内容主证据；audit 保存调用、校验、warning 和资源身份。
- SQLite projection 用于检索、调度和报告，不应覆盖原始 record。
- 只有完整持久化的成功分析进入去重历史。
- `analysis_outcomes` 与 `counterfactual_outcomes` 分离；Paper v1/v2 不混合。
- 修改前复制原始失败证据到本次审计范围，修复后清理临时回放文件，不删除长期 record。

## 修复还是接受

**必须修复的代码问题：**

- 程序事实与冻结 OHLCV 矛盾；
- PA/VPA/Joint 权限越界；
- Schema、identity、hash、订单数学或持久化错误；
- 合法模型措辞被自由文本规则误拒；
- 失败被记成功、任务重复、线程/进程崩溃；
- requested/applied、desired/applied 等状态被混写。

**可接受但应复盘的 LLM 偏差：**

- 在相同事实下选择了另一种合理市场假设；
- Setup 或 Wyckoff 标签有专业分歧但证据引用正确；
- 对未来走势判断错误；
- neutral/insufficient 或选择等待下一根已收盘 K 线。

**不能忽略：**模型捏造 OHLC/volume、引用不存在 Evidence、VPA 创建订单、错误价格几何或结构化字段互相矛盾。这些需要契约/事实层处理，不靠换模型掩盖。

## 复发路径

检查 packet 完整性时，将 foundation_exact_sections 与 manifest 中每个 mandatory 模块的
全部原文逐章节比较，不能仅看 packet 工具调用成功。不得在 packet 内另设文件名白名单
绕过 Host 知识选择；manifest availability 也不能冒充 Stage 2 的完整阅读继承凭证。
packet 的 `section_index` 只列未内嵌原文的可查 section；必读 section 的 ID、标题、
alias、digest 与原文都在 `foundation_exact_sections`，索引为空不等于知识缺失。
Stage 2 的 `stage2_handoff_receipt` 给出 Provider Contract、validated Stage 1、Router
和联合 VPA 报告的实际资源路径；先按路径核对值，不能凭模型的“未找到”措辞
断言 Frozen Job 缺件。回执证明 Host 交付，不证明模型确实理解了长内容。
新 Stage 2/VPA 基线用 `mandatory_modules` 的 ID、digest、顺序核对后续完整
模块原文；省略重复的 `list_allowed_knowledge` 不表示未加载知识。旧冻结 Job
仍要求原目录操作。检查 Worker 审计时按 Job 冻结的交付策略判断，不能把两版
基线的调用数量直接当作知识覆盖差异。
新 Stage 2 回执的 `mandatory_knowledge_sources` 将完整必读集合逐项分为本轮
原文交付与同快照 PA 会话继承；`market_context_sources` 对价格/HTF 做同样区分，
`experience_entries` 给出冻结数量及本轮路径。`pa_hypotheses_lineage` 证明它是
validated Stage 1 的 Host 派生投影，不是同形副本，其 `source_digest` 不等于
Stage 1 artifact 摘要并非冲突。模型声称缺件时先核对回执实际路径与值，再查
是否发生上下文理解错误。任何真实模型验收运行期间不得修改 Prompt、知识、
AGENTS 或 Skill；StudyHost 的域指纹漂移会使整轮研究样本无效。
若 Stage 2 再称长基线“被截断”，先分别查 MCP `truncated`、模型会话中
实际可见的工具 JSON 是否可解析并含末项资源与 `snapshot_identity`，以及
末尾 `stage2_handoff_receipt.read_plan_complete`/`terminal_resource`。
不能仅凭模型措辞或较少的本轮资源数推断传输失败；也不能仅凭 Host 回执
断言模型已读懂知识或形成了正确交易判断。
联合 Stage 2 的 `evidence_index.vpa_evidence_ids` 若出现在旧冻结 Job 中且为空，
只说明该 PA 角色无权直接读取原始 VPA catalog，不说明已验收报告的引用无效。
新 Job 在 `stage2_handoff_receipt.accepted_vpa_report.citable_evidence_ids`
单列报告可引用集合，最终仍由 Host 对照冻结 catalog；PA 角色不获得原始
VPA 读取工具。PA 续接 delta 不提供只统计本轮 resources 的 `delivery_index`，
因为 Stage 1 packet 已读值须从同一会话继承，不能错标成“仅索引”。

Codex 性能排障先核对 Frozen Job 的实际入口和 agents_text：packet 的 role_skill/price_frame
不在旧 resources 的 get_market_core/get_price_frame 路径中。读取协议说明不能要求模型寻找
另一种交付布局；修正规则只证明入口一致，不证明模型时延下降。累计输入、推理用量、工具
处理时间与 Provider 等待分开看；额度失败不作为性能样本。当前证据与契约去重边界见
[运行链路审计](../quality/runtime_chain_audit_20260914.md)。

Stage 1 调查拒绝先看冻结 `stage1_investigation_policy` 与 Evidence audit 的 operation、
status、reason。显式 `stage1_investigation_v1` 必须先成功交付完整 Packet；事实与知识
各最多一次，失败尝试也占配额，窗口测量/投影共用事实配额。Gateway 在读取前拒绝，
Worker 独立复核，不能因最终 Draft 有效而忽略 denied。预算失败是 incomplete，其他
权限/流程拒绝是输出契约失败，均不是 availability fallback，也不是市场 No Trade。
未知策略在 CLI/MCP 启动前拒绝；历史无标记 Job、child、repair 按各自冻结契约检查。
不改旧 Job 或重跑历史失败来消除审计。见
[知识/Prompt/调查修订](../quality/knowledge_prompt_investigation_20261012.md)。

Runtime AGENTS/Skills 改动会改变冻结的 asset digest；更新前须同时确认 Host 的
queued/running/unfinished 为 0、无 in-flight 或 recovery-blocked asset，Worker inbox
无 pending JSON 或 `.running`。旧 Job 与 PA 会话保留原身份，不迁移或跨版本续接。
若部署只改项目包且依赖锁、systemd 和配置未变，可在停止三服务后离线重装完整
`--no-deps` wheel，并核对全部依赖、installed/source 字节、Runtime 权限及原配置。
若这些前提不成立，按 [部署文档](../linux_headless_deployment.md) 执行完整安装流程。
启动后先核验暂停状态、Worker 新身份和认证、接口健康，再恢复原获批调度。
回退必须恢复对应版本的 Python、知识与 Runtime，不单独降级 Worker。

VPA 后续闭盘 hard error 要核对否定句的**主语**，不能把同句所有 `K#` 当成
“没有更晚收线”的对象。2026-09-24 两次真实首稿都说最新 K1 无后续闭盘，
K2 仅是相对活动比较或前棒；旧 Validator 因逐个扫描同句 K 标签误拒并多耗
一次模型修复。新规则只硬拒明确主语或明确并列事件；遇到不明确的自然语言
仍须回到冻结目录和原文检查，不得因离线重验通过就宣称新的真实运行已完成。
版本化 `analysis_pipeline=pa_vpa_joint` 的实际链路需查 `analysis_pipeline`、
VPA report 和 Stage 2 `joint_analysis`；旧 `requested/effective_vpa_mode=off`
只表示未启用旧 Shadow/Soft 分支，不能单凭该字段判定未使用 VPA。

将症状对应到 [问题历史](problem-history.md) 的 `PH-*`，重点查看根因、错误修法和复发检查。若发现新机制，按 [维护手册](maintenance.md) 提炼；不要把单次错误直接写成项目规则。

## 冻结任务重启与本地 CLI/MCP 解释器

发布迁移时，旧版可能存在 accepted 但没有冻结 payload／完成回执的历史任务。
不得填造 payload、改成成功或重新请求模型；先暂停并备份，按精确 task ID 核对
原始记录与 ledger，明确不可恢复时人工登记失败并保留原因及原件。2026-10-09
VPS 的一条旧 Bitget 任务按此处理，未改变新 Binance 任务语义。

非交互验收脚本与正式 Worker 都必须向 CLI 提供 `stdin=DEVNULL`；否则 SSH
管道可能使 CLI 等待额外输入，不能把这种等待归因为模型推理或账号故障。
发布时除了 Runtime 资产 digest，还须比对完整已安装 Python 包与固定提交，
记录 CLI 版本；npm 入口 JS 的 digest 不能独自证明底层 native binary 版本。
升级 CLI 必须重新 canary，即使入口摘要未变。完整私有行情、记录及 auth 不导出
到扩大访问范围的临时目录；质量检查在原位执行，只输出检查结果与必要回执。

Codex Headless 接受任务时，冻结 payload 与调度回执同事务落库；`started_at_ms`
必须先于模型调用持久化。重启时，未开始且 digest/身份正确的任务可自动排队；
已开始任务只能用唯一、完整、已审计的原始 JSON record 对账，不能因为缺少调度
完成回执就重跑模型。未证实完成时标记 in-doubt，阻断同资产并人工核对；取消
已开始任务也按此处理。跨 SQLite、记录文件与 Paper 的整体一致性仍需停机核验。

`Codex execution unavailable` 不能直接归因于账号或额度。先用隔离 `CODEX_HOME`
检查 `codex login status`；若普通最小 CLI 调用成功而 required `pa_evidence`
无法初始化，从隔离工作目录用 **Host 同一 Python 解释器**检查 `pa_agent` 实际
导入位置和 `import mcp`。MCP 子进程现由 Host 显式绑定同一 `pa_agent` 包根目录；
但解释器仍须安装 `pyproject.toml` 指定的依赖。2026-10-08 本机系统 Anaconda
Python 曾导入旧 editable 项目且缺少 `mcp`，项目 `.venv` 正常；两个失败回执均
未取得模型 Draft，用量保持 unknown，不应反复启动完整市场链试探。

冻结证据中的 PA 指标 hash 涉及底层浮点值：同一 K 线文件跨 Python 3.11/3.13
可能产生不同 hash。研究/验收须用同一发布解释器冻结、重载和执行，先做无模型
`input_from_evidence` 预检；hash 不符时禁止改写原证据或跳过校验。

## Stage 内协作排障

先读[研究计划](../quality/stage_subagent_research_plan.md)。`pa_challenger.toml` 是 Host 规格，
不安装为 native CLI agent。新 Job 必须冻结 `host_stage_challenge_v1` 和预算，Worker 必须显式
开启研究；原生 spawn 继续关闭。`check_host_stage_challenge.py` 默认仅预检，`--execute`
才消耗真实模型额度。合成 canary 与行情 A/B 的结果分开。

调查失败先查 `collaboration.status/start_rejections/failure_code`、scope digest、actor、
delivered_value_ids 与 evidence_events。child 只会读索引中的资源，不能把未交付原值的目录 ID
当依据；测量回执额外携带源 bar，VPA 回执额外携带成交量来源/解释上限，均在字节预算内。
父 mandatory 必须单独验收，child 读取不能抵扣。PA Stage 2 续接时复用绑定文件路径，但换为
新 Job 和新 capability；Stage 1 旧 token 不能跨阶段使用。

父进程失败也查 `*.challenge.json` sidecar 和失败 record 的 call_attempts。没有合法正文时
response_hash 必须为空，已观察父子计数不能丢；`usage_observed_without_response` 区分这类
消耗，metrics coverage 保留 unknown。总耗时看父 Stage wall，不能把重叠 child wall 相加。
该工具权限设计不等于 OS 对恶意同用户进程的隔离，Linux 仍需独立用户及 service cgroup 验收。
