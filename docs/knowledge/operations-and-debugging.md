---
lifecycle: active
capability: no-go
verification_scope: not-verified
issue_status: monitoring
最后核验: 2026-08-26
核验提交: f8fffc0
权威来源:
  - docs/linux_headless_deployment.md
  - CONTEXT.md
  - docs/architecture/ADR-0015-runtime-recovery-and-frozen-execution-facts.md
  - docs/architecture/ADR-0023-vps-observability-and-control-plane.md
---

# 运行与排障

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

将症状对应到 [问题历史](problem-history.md) 的 `PH-*`，重点查看根因、错误修法和复发检查。若发现新机制，按 [维护手册](maintenance.md) 提炼；不要把单次错误直接写成项目规则。
