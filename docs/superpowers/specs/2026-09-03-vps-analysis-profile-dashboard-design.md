# VPS Analysis Profile 与 Codex Runtime 验证设计

## 状态

已确认，2026-09-03。

本设计补全现有 Linux Headless、Dashboard 与 Codex Analysis Backend 的部署控制面。它不改变
Brooks Router、Validator、Risk、Trader Equation、Paper 或交易权限。

## 目标

用户只通过 VPS Dashboard 即可查看和安全选择下一轮分析使用的 Backend、模型与思考强度，
并能分别触发一次 DeepSeek Direct 与 Codex Agent 验证。初版固定单数据源、单品种，同时把
VPA Shadow 与 HTF 作为标准分析组成。

## 初版固定市场配置

- 数据源：Binance USD-M。
- 品种：`XAUUSDT`。
- 主周期：`1h`。
- 高周期：启用，固定为 `4h`，读取 60 根已收盘 K 线。
- VPA：`shadow`；正式 authority 仍为 PA Stage 2。
- 资产数：1。
- 全局并发：1。
- 不启动真实交易。

Dashboard 展示这些字段，但初版不允许修改。后续扩展资产与周期必须另立设计和实验 cohort。

## Analysis Profile

新增不含凭据的 `AnalysisProfile`，与运行/Backend desired state 一起进入版本化控制文件。Profile
至少包含：

- `deepseek_model`：`deepseek-v4-flash | deepseek-v4-pro`；
- `deepseek_thinking`：布尔值；
- `deepseek_reasoning_effort`：项目 Direct Client 支持的强度；
- `codex_model`：服务端 Codex model catalog 中的值；
- `codex_reasoning_effort`：所选模型允许的强度；
- 固定资产、VPA、HTF 的只读投影；
- `generation`、`updated_at_ms`、`actor` 与内容 fingerprint。

Profile 只能由 Dashboard 提交白名单字段。API Key、Base URL、Prompt、AGENTS、Skills、Knowledge、
Validator、Router、Risk、Paper 与任何文件路径均不得进入请求或控制文件。

## 模型目录

### DeepSeek Direct

初版模型目录为：

- `deepseek-v4-flash`
- `deepseek-v4-pro`

它们是 DeepSeek 官方当前 OpenAI-compatible Chat Completions model ID。旧的 `deepseek-chat` 与
`deepseek-reasoner` 不再出现在 Dashboard 新配置中。

### Codex Agent

初版受支持目录为：

- `gpt-5.6-sol`
- `gpt-5.6-terra`
- `gpt-5.6-luna`
- `gpt-5.5`
- `gpt-5.4`
- `gpt-5.4-mini`

模型目录由服务端发布，Dashboard 不接受任意 model 字符串。每个条目声明自己的 reasoning
allowlist；界面在切换模型时同步收窄可选强度。Codex 模型是否对当前 ChatGPT 账号实际可用，
由部署 preflight 与真实 smoke test 确认；失败必须显示原模型和明确错误，不得静默换模型。

## 安全应用流程

1. Dashboard 读取当前 desired Profile、Headless 已应用 generation 与 capability catalog。
2. 用户编辑模型、Thinking 和 reasoning，界面显示 current 与 pending 差异。
3. Dashboard POST 只携带白名单字段、控制头和当前 generation。
4. 服务端执行类型、枚举、模型/强度组合与 optimistic concurrency 校验。
5. 校验通过后原子写入下一 generation；密钥与配置目录保持不可见。
6. Headless 只在没有 in-flight 或 queued task 的安全边界应用 Profile。
7. 已冻结任务保持原模型、reasoning、Backend、Prompt、AGENTS、Skills 与 Snapshot identity。
8. 应用失败时 fail closed 为 paused，并保留旧 applied Profile 与可诊断错误。

Dashboard 用户仍只写 operation control directory。它不能写 `/etc/pa-agent`、生产 settings、Codex
home、spool job、数据库或源码。

## Runtime 应用

DeepSeek Profile 由 Headless 在安全边界重建 Direct Client/Orchestrator 所需的 settings 副本；
不得修改含 API Key 的持久 settings。运行记录同时保存 requested/effective model、thinking、reasoning
和 Profile fingerprint。

Codex Profile 由 PA Agent 冻结进每个 `FrozenAgentJob`。Worker 以 Job 中的 model/reasoning 执行，
但必须再次验证它们属于本地发布的 capability catalog；Job 值和 Worker 允许值不一致时 fail closed。
Worker 不从 Dashboard 读取命令，也不重启 systemd 来切换模型。

Profile 更新只影响后续 Snapshot。Headless 不允许用新 Profile 接续旧 Snapshot 的 Stage 2、VPA 或
repair。

## Codex Runtime 资产与权限

部署前必须验证：

- 独立 `pa-agent-codex` Unix 用户和独立 `CODEX_HOME`；
- ChatGPT device auth 文件 owner/mode；
- 源码、生产数据库、DeepSeek credential、Paper ledger 对 Codex 用户不可读写；
- Job 只读，outbox/runtime 为唯一允许写路径；
- 每个角色使用独立 ephemeral Codex task；
- Web、Shell、外部 App、Plugin、自主安装 Skill 与用户配置全部禁用；
- `AGENTS.md` 明确 PA authority、no-lookahead、同一 Frozen Snapshot、禁止下单/改代码；
- Stage 1、Stage 2、VPA、Joint 四个 Role Skill 存在且角色权限不越界；
- Runtime AGENTS/Skills 内容和 digest 写入 Job/cohort identity；
- Evidence MCP 只暴露当前 Job、role allowlist、call/byte budget 内的只读事实。

部署检查同时验证 wheel/installed package 中资产与 Git 工作树资产 digest 相同，防止只在源码存在、
VPS 安装包缺失。

## Dashboard 交互

界面采用高密度、克制的交易运维控制台，而不是通用后台模板。顶部显示连接状态、当前 Backend、
暂停/恢复和“单次分析”。配置区并排显示：

- 固定市场范围：Binance USD-M / XAUUSDT / 1h / HTF 4h / VPA Shadow；
- DeepSeek 卡：Flash/Pro、Thinking、Reasoning；
- Codex 卡：Model、Reasoning、Runtime asset digest、认证/Worker 状态；
- Current、Pending、Applied generation 与差异；
- 应用按钮仅在表单合法且没有并发更新时可用。

状态区继续显示资产、最近分析、Paper 与运行异常，并为每次记录补充 Backend、模型、reasoning、
总延迟、修复次数和 outcome。前端不渲染模型 reasoning text，不读取数据库或密钥。

“单次分析”只在服务暂停且配置已应用时可请求一个新 Snapshot。重复点击、已有任务、Profile 尚未
applied 或服务不健康时必须拒绝。单次请求不是定时服务开关，完成后保持 paused。

## API 与错误契约

- `GET /api/status`：返回脱敏运行状态、control、current/applied Profile 与 capabilities。
- `POST /api/control`：保留 pause/resume/set_backend。
- `POST /api/profile`：带 expected generation 提交白名单 Profile。
- `POST /api/run-once`：请求一次受控分析，不携带自由 Prompt 或市场字段。

所有写请求继续要求 Basic Auth、同源检查和 `X-PA-Agent-Control: 1`。请求体保留严格字节上限。
冲突返回 409，非法组合返回 400，运行边界不允许返回 409，内部状态异常返回 503。错误正文只包含
枚举 code 与安全说明。

## 验证与验收

### 自动化

- Profile schema、模型/强度 allowlist、fingerprint 与原子写入。
- expected generation 并发冲突。
- Dashboard 无凭据投影、CSRF/Origin、请求大小和非法字段拒绝。
- Profile 仅在 idle boundary applied；in-flight task 身份不变。
- Direct Client 使用 requested/effective DeepSeek model/reasoning。
- Codex Job/Worker 使用并审计 requested/effective model/reasoning。
- 不支持模型、AGENTS/Skills digest 漂移和权限越界均 fail closed。
- run-once 去重、完成后保持 paused。
- VPA Shadow 与 HTF 4h 在 Direct/Codex 两个 Backend 均存在。
- 原 PA、Router、Validator、Risk、Paper 与 backend A/A/B/B 回归通过。

### VPS 验收

1. 运行 installer/preflight，验证 systemd、权限、Codex auth、AGENTS/Skills digest 和数据源。
2. Dashboard 通过 SSH tunnel 打开并显示唯一资产与固定 VPA/HTF。
3. 选择 DeepSeek Flash + agreed reasoning，应用后运行一次。
4. 选择 Codex model + agreed reasoning，应用后运行一次新的 Frozen Snapshot。
5. 比较 outcome、Stage 1/2/VPA 合规、修复次数、Evidence calls、Token 与 latency。
6. 可选再运行 DeepSeek Pro；不得把不同 Snapshot 的单次收益作为模型优劣结论。
7. 用户验收前保持长期定时服务 paused。

## ADR 修订

ADR-0024 第 7 节由“Dashboard 不得修改模型、reasoning、资产、VPA”修订为：Dashboard 可提交
本设计定义的、无凭据、白名单、版本化 Analysis Profile；只能在 Headless idle boundary 应用，
且所有 requested/effective identity 进入 Frozen Job 和审计。资产、周期、VPA 和 HTF 初版仍固定，
其余交易边界继续禁止修改。

## 非目标

- 多资产、多数据源动态编辑。
- VPA off 或 HTF off 的生产快捷开关。
- 自由输入 Codex/DeepSeek model ID。
- Dashboard 编辑 Prompt、AGENTS、Skills 或 Knowledge。
- 自动选择“最好”模型或按一次结果自动晋级。
- Codex fallback、Joint 或真实交易权限的扩大。
