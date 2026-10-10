# Linux VPS Headless 部署

本部署用于持续拉取 Binance USD-M 的 XAU 永续行情，自动运行 PA/VPA
分析并进行本地 paper trading。当前代码没有私有交易所 API，也不会提交真实订单。

## 1. 目录与前提

推荐使用 64 位 Ubuntu 22.04/24.04，并约定：

- 代码：`/opt/pa-agent`
- Miniconda：`/opt/miniconda3`
- Conda 环境：`pa-agent`
- 运行状态：`/var/lib/pa-agent`
- Dashboard 共享投影与控制：`/var/lib/pa-agent-control`
- 非代码配置：`/etc/pa-agent`
- Codex ChatGPT auth 与临时 Job：`/var/lib/pa-agent-codex`（独立 `pa-agent-codex` 用户）

先安装 Miniconda，再把已验收的代码版本放到 `/opt/pa-agent`。Miniconda 安装包和
校验值应从官方文档获取：<https://docs.anaconda.com/miniconda/>。

```bash
sudo git clone <YOUR_PA_AGENT_REPOSITORY> /opt/pa-agent
cd /opt/pa-agent
sudo bash deploy/install_vps.sh
```

安装脚本从已提交的 `uv.lock` 导出 `vps` profile 的精确依赖并安装到 Miniconda 环境，
随后创建专用用户、目录和 systemd unit。它不会启动服务，也不会覆盖已有
`settings.json`、环境文件或密钥。

## 2. 默认单资产 XAU 与白名单选择

首次安装会从 `config/settings.vps.example.json` 创建：

```text
/var/lib/pa-agent/config/settings.json
```

默认监控：

| Source | Symbol | 周期 | 高周期 |
|---|---|---:|---:|
| `binance_usdm` | `XAUUSDT` | `1h` | `4h` |
默认且唯一启用的资产是 `binance_usdm / BINANCE / XAUUSDT / 1h`；HTF 固定为
`4h`、60 根已收盘 K 线，全局分析并发为 1。新版可由看板显式勾选 BTCUSDT/ETHUSDT，
并选择并发 1/2/3；不自动启用新资产，不关闭 HTF/VPA，不支持任意品种或交易所。
原默认 `vpa_mode=shadow` 的正式结果仍为 PA-only；当前已获批全量 A 单 Agent
`pa_vpa_joint` 部署由显式 pipeline 控制，不能由品种选择切换权限。默认单笔风险为权益的 `0.5%`、组合风险上限 `2%`、最大杠杆 `3x`。
这些是 paper 研究参数，不代表收益承诺。

## 3. API Key 与模型

不要把 API Key 写入 JSON。用不会把密钥值留在 shell history 的方式创建 credential：

```bash
read -rsp 'PA Agent API key: ' PA_KEY; echo
printf %s "$PA_KEY" | sudo install -o root -g pa-agent -m 0640 /dev/stdin /etc/pa-agent/api_key
unset PA_KEY
```

再创建独立的 Dashboard 密码。它不能与 LLM API Key 共用：

```bash
read -rsp 'Dashboard password (16+ chars): ' DASH_KEY; echo
printf %s "$DASH_KEY" | sudo install -o root -g pa-agent-dashboard -m 0640 /dev/stdin /etc/pa-agent/dashboard_token
unset DASH_KEY
```

编辑 `/etc/pa-agent/pa-agent.env`：

```dotenv
PA_AGENT_MODEL=deepseek-v4-flash
PA_AGENT_BASE_URL=https://api.deepseek.com
```

`PA_AGENT_MODEL` 必须填写服务商实际开放的 API model id。套餐名称和 API model id
不一定相同，不应猜测填写。

### 3.1 可选 Codex Analysis Backend

仅使用 ChatGPT 套餐的联合分析 Paper 候选与下方旧 DeepSeek/Shadow 流程互斥，不能把
`codex_only` 误写成 `auto`：

- 新配置样例是 `config/settings.vps.codex-joint.example.json`，显式固定
  `analysis_pipeline_v1:pa_vpa_joint`、单一 XAUUSDT/1h、HTF 4h、VPA 和 Paper。
  旧 `settings.vps.example.json` 不变；安装器不会覆盖已有 `settings.json`。
  样例的 900 秒是完整调用的运维等待上限，不是性能合格线；正式质量晋级仍以
  `config/quality_contract.toml` 的完整运行 p95 300 秒及 250k token p95 为准。
  2026-10-09 用户仅对**受控持续 Paper 观察**豁免性能阻断，不豁免事实、权限、恢复、
  数据或 Paper 对账门禁；该试运行不能称为质量晋级或收益证明。
- 独立 Worker 环境样例是 `deploy/systemd/pa-agent-codex-joint.env.example`，固定
  `gpt-6-luna/xhigh`。先确认目标 CLI/账号实际支持该组合；不降强度或换模型冒充同一 cohort。
- 新部署必须先备份原配置与控制状态，保持 `paused`，再将 Codex-only 样例作为本次
  `settings.json`。通过 `RuntimeControlStore` 设 `desired_backend=codex_only`，并将
  `AnalysisProfile(codex_model="gpt-6-luna", codex_reasoning_effort="xhigh")`
  设为目标 profile；确认 generation 已在 Headless idle 边界应用。
- 安装器只创建 paused 控制状态，不会自动切换 Backend/模型。确认服务已暂停后，以
  控制文件所有者执行一次原子控制更新；此处不接触 Codex 登录信息或 API key：

  ```bash
  sudo -u pa-agent-dashboard /opt/miniconda3/envs/pa-agent/bin/python -c '
  from pathlib import Path
  from pa_agent.operations.analysis_profile import AnalysisProfile
  from pa_agent.operations.runtime_state import RuntimeControlStore
  store = RuntimeControlStore(Path("/var/lib/pa-agent-control/control/control.json"))
  state = store.read()
  if state.error or state.desired_state != "paused":
      raise SystemExit("control must be valid and paused")
  state = store.set_desired_backend("codex_only", actor="codex_joint_bootstrap")
  profile = AnalysisProfile.model_validate({**state.analysis_profile.model_dump(mode="json"), "codex_model": "gpt-6-luna", "codex_reasoning_effort": "xhigh"})
  store.set_analysis_profile(profile, expected_generation=state.generation, actor="codex_joint_bootstrap")
  '
  ```
- 不创建 DeepSeek key 时，需将 `deploy/systemd/pa-agent-codex-only.conf` 作为
  `pa-agent.service.d/codex-only.conf` 安装并执行 `systemctl daemon-reload`。此 drop-in
  只清除主服务强制的 Direct `LoadCredential`；Dashboard token 和 Codex 登录凭据仍须独立保护。
  不要在 Direct/auto 部署使用它。`--check` 会按 desired backend 校验相应凭据与 Worker 配置；
  无 Direct key 的运行时禁止切换 Direct/auto。
- 恢复旧版时先暂停并备份当前配置/控制文件，再恢复旧配置、Direct credential 和原
  `LoadCredential`，等待 idle 生效后才恢复调度。不得把联合 Paper 账本重标为旧 `pa-official`。

这只是发布资产与离线配置契约。持续 Paper 必须先通过目标 VPS 的权限、重启恢复、
备份与单次 Paper canary。性能不达标时只可按上文限定为受控观察，并保留独立 cohort。

Codex 默认关闭。先按 [OpenAI Codex 认证文档](https://learn.chatgpt.com/docs/auth.md)安装当前
Codex CLI，再以独立的 `pa-agent-codex` 服务用户完成 headless device auth：

```bash
sudo -u pa-agent-codex env \
  HOME=/var/lib/pa-agent-codex \
  CODEX_HOME=/var/lib/pa-agent-codex/home \
  /usr/local/bin/codex login --device-auth

sudo -u pa-agent-codex env \
  HOME=/var/lib/pa-agent-codex \
  CODEX_HOME=/var/lib/pa-agent-codex/home \
  /usr/local/bin/codex login status
```

device auth 需要先在个人 ChatGPT security settings 或 workspace permissions 中允许；浏览器中
完成一次性代码授权。`auth.json` 含可刷新 access token，按密码处理，不复制进仓库、日志或
Dashboard。若正式环境支持 Enterprise access token 或 workload identity，应另行评审并优先使用
短期身份；本版本不会静默改用 OpenAI API Key。

认证成功后编辑 `/etc/pa-agent/pa-agent-codex.env`：

```dotenv
PA_AGENT_CODEX_ENABLED=1
PA_AGENT_CODEX_MODEL=<CLI实际可用的model-id>
PA_AGENT_CODEX_REASONING=xhigh
PA_AGENT_CODEX_BINARY=/usr/local/bin/codex
PA_AGENT_CODEX_STATE_DIR=/var/lib/pa-agent-codex
PA_AGENT_CODEX_MAX_WORKERS=1
# ChatGPT Codex 模型留空；仅 deepseek-flash 工程 Backend 填只含该模型的只读 catalog。
PA_AGENT_CODEX_MODEL_CATALOG=
```

上述为默认单资产示例。看板并发选 2/3 时，同步将非密配置
`PA_AGENT_CODEX_MAX_WORKERS` 设为至少对应容量，并在 Host/Worker 排空、暂停后重启。
2026-10-10 用户已在看板启用 XAUUSDT/BTCUSDT/ETHUSDT，当前并发与 Worker 容量为 3。
Worker 的 `TasksMax=256` 有界容纳三路 CLI/MCP 进程和线程；该限制计入线程，
不能把三个分析任务等同于三个系统进程。保留 `MemoryMax=1G`、`CPUQuota=200%`，
不关闭权限隔离。容量故障先核对 cgroup 的 `pids.events`、`memory.events`；
只有线程上限明确命中时才调整，不因 Provider 失败盲目扩容。三模型真实峰值及
恢复效果仍由后续定时 Paper 验证，详见交易总览发布报告。

如果只做 ChatGPT Codex/Luna 验收，`/etc/pa-agent/pa-agent-codex-secret.env` 保持安装器创建的
空文件。只有选择 `deepseek-flash` Codex 工程 Backend 时，才用与 Direct API credential
独立的 key 写入该 Worker-only 文件；不写入共享非密配置：

```bash
read -rsp 'DeepSeek Codex API key: ' DS_CODEX_KEY; echo
printf 'DEEPSEEK_API_KEY=%s\n' "$DS_CODEX_KEY" | \
  sudo install -o root -g root -m 0600 /dev/stdin \
  /etc/pa-agent/pa-agent-codex-secret.env
unset DS_CODEX_KEY
```

systemd 只将该文件注入 `pa-agent-codex-worker`；Headless、Dashboard、源码、Job 和审计日志均不读取它。

不要猜测 model id；使用部署时 CLI 实际返回且实验 cohort 已冻结的值。`--check` 会在启用时验证
binary executable digest、model、reasoning、Runtime AGENTS/Skills 版本和隔离 spool 目录；这些
值共同进入 task cohort。检查不会联网或替你登录。首次应先保持 Dashboard
`deepseek_only`，完成 `codex_only` 单次 dry run 后才允许 `auto`。

## 4. 上线前四步验收

```bash
BIN=/opt/miniconda3/envs/pa-agent/bin
COMMON_ENV='PA_AGENT_HOME=/var/lib/pa-agent PA_AGENT_OPERATION_DIR=/var/lib/pa-agent-control PA_AGENT_PROMPT_DIR=/opt/pa-agent/prompt_engineering PA_AGENT_API_KEY_FILE=/etc/pa-agent/api_key'
```

1. 离线检查配置、Prompt、依赖和 Runtime 资产：

```bash
sudo -u pa-agent env $COMMON_ENV "$BIN/pa-agent-headless" --check
```

若已启用 Codex，`COMMON_ENV` 还需包含 `/etc/pa-agent/pa-agent-codex.env` 中的 Codex 变量；直接
使用 `systemctl start pa-agent` 时 systemd 会自动加载。手工验收可先执行：

```bash
set -a
. /etc/pa-agent/pa-agent-codex.env
set +a
sudo -u pa-agent env $COMMON_ENV \
  PA_AGENT_CODEX_ENABLED="$PA_AGENT_CODEX_ENABLED" \
  PA_AGENT_CODEX_MODEL="$PA_AGENT_CODEX_MODEL" \
  PA_AGENT_CODEX_REASONING="$PA_AGENT_CODEX_REASONING" \
  PA_AGENT_CODEX_BINARY="$PA_AGENT_CODEX_BINARY" \
  PA_AGENT_CODEX_STATE_DIR="$PA_AGENT_CODEX_STATE_DIR" \
  PA_AGENT_CODEX_MODEL_CATALOG="$PA_AGENT_CODEX_MODEL_CATALOG" \
  "$BIN/pa-agent-headless" --check

sudo -u pa-agent-codex env \
  PA_AGENT_CODEX_STATE_DIR="$PA_AGENT_CODEX_STATE_DIR" \
  PA_AGENT_CODEX_MODEL="$PA_AGENT_CODEX_MODEL" \
  PA_AGENT_CODEX_REASONING="$PA_AGENT_CODEX_REASONING" \
  PA_AGENT_CODEX_BINARY="$PA_AGENT_CODEX_BINARY" \
  PA_AGENT_CODEX_MODEL_CATALOG="$PA_AGENT_CODEX_MODEL_CATALOG" \
  "$BIN/pa-agent-codex-worker" --check
```

上述 Worker 手工检查适用于 ChatGPT Codex。`deepseek-flash` 的凭据由 systemd 秘密环境注入，
应用 `sudo systemctl start pa-agent-codex-worker` 触发同一 `ExecStartPre --check`，不在命令行展开 key。

安装器已执行静态 Runtime 审计，并比较源码与当前安装包中的 `AGENTS.md`、`config.toml`、rules、
四个 Role Skill 和 pointer repair Skill。
也可单独输出 machine-readable JSON；任何 `ok=false` 都阻止部署：

```bash
"$BIN/python" -m pa_agent.agent_runtime.runtime_audit \
  --source-assets /opt/pa-agent/pa_agent/agent_runtime/assets
```

创建 credential 且 Headless 首次 `--check` 建好私有 DB 目录后，以 root 只检查 metadata；该命令
不读取 credential 或数据库内容：

```bash
sudo "$BIN/python" -m pa_agent.agent_runtime.runtime_audit --check-permissions
```

权限契约是：源码只读；Job 对 Codex 只读；`home/runtime/outbox` 只允许 Codex 写；Provider
credential、生产 records/database 与 Paper ledger 对 Codex 无读写权限。

当前 CLI 使用服务专属、发布固定且 digest 校验的 `$CODEX_HOME/config.toml` 与 rules；production
与 canary 都不传 `--ignore-user-config` 或 `--ignore-rules`，避免绕过自定义安全策略或隐藏 workspace
`AGENTS.md`/Skill。
`tools.view_image` 已出现在 OpenAI config reference，但 Codex CLI 0.153.0 仍会在
`--strict-config` 下把它判为未知字段。因此当前 runtime 不传该无效 override；同时不传
`--image`、不物化图片、只开放 allowlisted Evidence MCP，并由 AGENTS 禁止 image 工具。CLI 升级后
若要启用该 config key，必须先在目标 VPS 复跑 strict-config canary。所有 subprocess 的 stdin 固定为
`DEVNULL`，避免 `codex exec` 在 systemd 或 SSH 环境等待额外输入。
workspace `AGENTS.md` 和 Role Skill 是否可发现仍必须由真实账号 canary 验证。canary 的随机 sentinel 只存在于
workspace Role Skill，prompt 与 schema 不含 sentinel；因此必须同时发现 workspace `AGENTS.md`
和 Skill 才能通过：

```bash
sudo -u pa-agent-codex env \
  HOME=/var/lib/pa-agent-codex \
  CODEX_HOME=/var/lib/pa-agent-codex/home \
  "$BIN/python" -m pa_agent.agent_runtime.runtime_audit \
  --live-canary \
  --codex-binary "$PA_AGENT_CODEX_BINARY" \
  --model "$PA_AGENT_CODEX_MODEL" \
  --reasoning-effort "$PA_AGENT_CODEX_REASONING"
```

升级 Codex CLI 或修改会话续接后，还应在不调用付费模型的本地 Responses 合成服务上验证
Stage 1 → Stage 2 `resume`。该检查要求同一 thread、完整 AGENTS、只限冻结工具，且 Stage 2
实际读到新 Job 的 `mandatory_pa_delta_v1`，而非 Stage 1 旧 baseline：

```bash
sudo -u pa-agent-codex env \
  HOME=/var/lib/pa-agent-codex \
  CODEX_HOME=/var/lib/pa-agent-codex/home \
  "$BIN/python" /opt/pa-agent/scripts/check_codex_delivery.py \
  --model-catalog /var/lib/pa-agent-codex/home/models_cache.json \
  --codex-binary "$PA_AGENT_CODEX_BINARY" \
  --model "$PA_AGENT_CODEX_MODEL" \
  --session-continuation
```

`models_cache.json` 不存在时不得猜测生成；先用目标 CLI 完成登录/模型列表刷新。该检查不读取
ChatGPT 会话凭据、不访问外部模型 endpoint；只使用 catalog 描述和 localhost 合成响应。

canary 返回非零、sentinel 不匹配或 Skill 未观察到时必须保持 Headless paused，禁止启动部署。

2. 只访问公开行情 API，不调用 LLM：

```bash
sudo -u pa-agent env $COMMON_ENV "$BIN/pa-agent-headless" --probe-data
```

唯一结果必须显示 `"success": true`，并核对 `resolved_symbol`、
`instrument_type`、`tick_size`、`quantity_step` 和 `contract_multiplier`。

3. 真正执行一轮单资产分析并写入 paper 账本：

```bash
sudo -u pa-agent env $COMMON_ENV "$BIN/pa-agent-headless" --once
```

4. 查看 paper 账户、订单和研究 disposition：

```bash
sudo -u pa-agent env PA_AGENT_HOME=/var/lib/pa-agent "$BIN/pa-agent-paper-report"
```

报告中的 A1/A2/B1/B2 仅是同一市场快照的重复 arm；统计单位始终是配对的
`market_snapshot`，不能把四个 arm 当作四个独立样本。

## 5. 启动 systemd

四步与 Codex live canary 都通过后再启动。安装器本身不会 enable 或启动任何服务；在用户审阅
DeepSeek/Codex smoke 前，Headless 必须保持 paused：

```bash
sudo systemctl enable --now pa-agent-codex-worker pa-agent pa-agent-dashboard
sudo systemctl status pa-agent-codex-worker
sudo systemctl status pa-agent
sudo systemctl status pa-agent-dashboard
sudo journalctl -u pa-agent -f
```

主服务验收后另行启用每日一致性备份；安装器不会自动启用 timer：

```bash
sudo systemctl start pa-agent-backup.service
sudo systemctl enable --now pa-agent-backup.timer
sudo systemctl list-timers pa-agent-backup.timer
```

备份位于 `/var/backups/pa-agent`，默认保留 14 份，包含三个 SQLite online backup、原始
`records/pending`、`experience`、Codex 冻结 Job 与角色接受标记，以及敏感字段剥离后的 `settings.json` 与
控制状态。备份目录按私有数据保护；不主动复制 API key、Dashboard token 或 Codex auth，
恢复后需单独重新配置凭据。恢复前必须先停止三项运行服务，并在隔离目录运行
`python -m pa_agent.operations.backup --verify <备份目录>` 核对 manifest 的 SHA-256，
再对三个数据库运行 `PRAGMA quick_check`。确认后按 manifest 路径恢复并校验 ownership；
不得覆盖运行中的 SQLite，也不得把恢复的冻结 Job 当成可自动续跑任务。各 SQLite
文件分别在线一致，文件树与多个数据库之间尚无全局事务；正式恢复前需在暂停状态
核对任务、记录和接受标记，不能仅凭 manifest 校验宣称跨存储一致。

`systemd-tmpfiles` 清理超过 7 天的孤立 Job/inbox/results/outbox，接受幂等 marker 单独保留 30 天；
应用文件日志每日轮转并保留 14 份，systemd journal 仍由宿主机 journald 策略管理。

新版 Worker 只在无 active/inbox/running dispatch 的空闲时段，每小时检查专属 Codex
会话保留；超过 14 天且 PA workspace 无恢复引用的会话由官方 UUID delete 清理，
每轮最多 8 个、每个最多 3 秒。旧 workspace 也仅在空闲且过期后清理，不在分析
启动时扫描删除。memories 保持关闭；auth、正式 records、账本和冻结恢复任务不删。
首次发布不会为验证清理而删除近期会话。详见 [运行升级](quality/v1_operations_upgrade_20261010.md)。

服务启动前只运行 `--check`。`--probe-data` 不放入自动重启链路，避免交易所短暂断线
导致 systemd 重启风暴。已接受任务的冻结 payload 与接收回执同事务持久化；重启只
自动恢复未开始且身份可验证的任务。已开始任务必须先凭唯一完整 record 对账，
否则保持 in-doubt 并阻断该资产，绝不盲目重跑模型。跨 SQLite／文件／Paper 仍无全局
事务，不能宣称端到端 exactly-once；持续 Paper 启动前须做目标 VPS 中断与对账验收。

## 6. Web 控制看板

Dashboard 只监听 VPS 的 `127.0.0.1:8765`，不要直接开放公网端口。在本机建立
SSH tunnel：

```bash
ssh -L 8765:127.0.0.1:8765 <VPS_USER>@<VPS_HOST>
```

随后打开 <http://127.0.0.1:8765>，在网页输入
`/etc/pa-agent/dashboard_token` 中设置的独立密码，无需用户名或浏览器认证弹窗。
登录会话固定 8 小时有效；退出或 Dashboard 重启后重新登录。Cookie 只保存随机
会话 ID，不保存密码。HTTP 仅用于本机 loopback＋SSH tunnel，不得公开 8765。
显式 Basic Authorization 仍供诊断脚本使用，API 未授权返回 JSON 401。

看板提供：

- Bot 心跳、并发队列、资产连接和最近分析状态；
- 最近 PA 决策、VPA evidence 摘要和 Paper 账户；
- “暂停分析”：不再接收新快照，正在运行的分析安全完成；
- DeepSeek `Flash/Pro`、Thinking 与 reasoning effort；
- Codex 发布目录中的 model 与该 model 支持的 reasoning effort；
- Binance XAUUSDT/BTCUSDT/ETHUSDT 勾选及分析并发 1/2/3；同品种串行，不同品种
  可受控并行。取消勾选只停新分析，已有正式 Paper 活跃订单继续接收行情观察；
- conservative/balanced/aggressive 三档 Stage 2 选择性；不改变 Stage 1、结构价格、
  数学或风险底线。默认 balanced；不提供强制交易档位；
- Codex Fast mode 的 desired/applied 开关；仅在安全空闲边界影响新冻结 Job，
  与 Standard 分属不同 cohort。需在目标账号和 CLI 验证支持；`gpt-6-luna` 不保证
  固定 1.5× 完成速度，Fast 可能更快消耗套餐额度；看板仍应以实测 latency/usage 判断。
- `deepseek_only / codex_only / auto` Backend 请求；
- 只在 paused、idle、Profile/Backend 已 applied 时可用的单次 `Run Once`。

受控 smoke 完成后，离线 Backend 对照使用已有、可重放且含 HTF 与 VPA catalog 的生产 Shadow
record：`pa-agent-backend-aabb <record.json>`。该命令运行 `analysis_backend_aabb_v2` 的四个独立
臂，不启用 provider/backend fallback，也不进入正式调度或真实执行。

首次受控 smoke 的精确顺序：

1. 点击 `Pause`，等待 Service、健康状态均为 `paused`，Running/Queue 为 `0 / 0`。
2. 选择 `DeepSeek Direct`，选择 `DeepSeek V4 Flash`、Thinking enabled、目标 effort，点击
   `Apply Profile`。
3. 等待 Desired/Applied generation 一致、Profile State 为 `APPLIED`，再点击 `Run Once`。
4. 在 Recent Runs 核对 `Backend`、Requested/Effective Model、Effort、Latency、Repairs、Outcome
   和 Status；同时核对本次 record 的 Snapshot hash、HTF 与 VPA Shadow。
5. 保持 paused，选择 `Codex Only`，选择已通过 live canary 的 Codex model/effort，等待 Backend
   与 Profile 均 applied 后再点击一次 `Run Once`。
6. 任一 Codex auth/runtime/Schema/事实/权限失败时保持 paused，切回 `DeepSeek Direct` 并等待
   applied；不要把 `auto` fallback 的成功误记为 Codex 成功。

Profile/Backend 的 HTTP `202` 只表示 desired state 已持久化，不表示已生效。已冻结 Job 永远保持原
model/effort/profile fingerprint，不因后续选择而换标。每个 `Run Once` generation 最多接受一个任务，
任务结束后仍保持 paused。

看板不能改 Prompt、风险参数、白名单以外的资产或 Joint 权限；`Run Once` 仍最多接收一项当前启用品种的完整
PA 主链分析，不能绕过 Snapshot、Validator、Router、Risk，也不能真实下单或执行 shell。进程启停与重启仍使用 `systemctl`。这是有意的权限边界，
避免 Web 控制面改变交易语义。

Dashboard 以独立的 `pa-agent-dashboard` 用户运行，只能访问
`/var/lib/pa-agent-control`。它不能读取 `/var/lib/pa-agent`、分析数据库、Paper 数据库
或 LLM API Key；上述内容由可信 Headless 进程生成脱敏只读投影。

Codex CLI 由独立 `pa-agent-codex` worker 执行。Headless 只能向共享 spool 发布 digest-bound Job
与读取结果；Codex 用户只能只读 Job，不能遍历 `/var/lib/pa-agent`、生产数据库、Paper ledger 或
systemd API credential。systemd 将 `/opt/pa-agent` 和 Python environment 设为只读，只允许
`/var/lib/pa-agent-codex/{home,runtime,outbox,inbox,results}` 写入。不同 Snapshot 使用独立 PA
session；Stage 1 attempt 1 不加 `--ephemeral`，仅在同 Snapshot Stage 2 通过冻结 identity 执行
`codex exec resume`，随后清理专用 workspace；VPA、Joint、repair 继续 `--ephemeral`。所有调用保持
read-only sandbox、`approval_policy="never"`，关闭 shell/Web，并只启用 required Frozen Evidence
MCP。Codex 输出先写 outbox，再由 PA Validator、Router、Risk 和 canonical writer 接管；
Codex 本身不直接改代码、配置、Risk、Paper 或订单。

## 7. 更新与回滚

更新前记录固定 commit、暂停调度并等待 Host/Worker idle，执行和验证账本备份。
必须停止三个加载项目包的服务，再更新源码或安装包；安装器发现 active 服务即
拒绝执行，防止运行进程混用新旧模块。单纯更新 pip 包或只重启 Dashboard 不够：

```bash
cd /opt/pa-agent
git rev-parse HEAD
sudo systemctl stop pa-agent.service pa-agent-codex-worker.service pa-agent-dashboard.service
sudo git fetch --all --tags
sudo git switch <VERIFIED_BRANCH_OR_TAG>
sudo bash deploy/install_vps.sh
```

重新执行 `--check` 和 installed/source Runtime audit，并按变更风险决定是否需要数据
probe/live canary；付费模型验收必须单独授权，不因重启默认触发。启动三项服务后先
确认暂停状态、desired/applied 一致及无旧任务重复，再恢复原已获批调度。保留 auth、
配置、失败回执、Frozen Job 和 `/var/lib/pa-agent`；不要重写旧记录或账本。

回滚仍先暂停和备份、等待 idle、停止三服务，再安装原固定提交并核对身份。GPT-only
部署不可盲选 Direct（它可能没有 API key）；选择可用的旧版本与明确配置，不把联合
Paper 账本改成旧 PA 账户。恢复数据必须另行验证，不能与普通代码回滚混为一谈。

## 8. 运行边界

备份服务保持输入只读，但 SQLite WAL 读取可能需要创建 `-wal`/`-shm` 辅助文件；
systemd 仅对 `records/audit` 与 `trade_records` 开放这一文件系统写入边界。
不能对运行中的数据库使用 `immutable=1` 来绕过权限，否则可能遗漏 WAL 内容。
发布前必须实际执行备份服务，并在隔离目录核对三份 SQLite 的 `quick_check` 与
manifest；旧数据、冻结回执与登录凭据不得随版本替换删除。

- Binance USD-M `XAUUSDT` 合约是否可用以启动时公开 metadata 为准；必须核对 tick size、
  quantity step 与 contract multiplier，不能从 symbol 名称猜测。
- XAUUSDT 是 Binance 黄金永续而非全球现货黄金交易量。参考时段按最新已确认
  收盘边界冻结，纽约时区包含 DST；官方 2026-09-15 规则之前保持历史时段未知，
  未接权威假日日历时不猜测公共假日。不据该背景增加 Gate 或重标旧快照。
- VPA 不创建 Setup、不翻转 PA 方向、不修改主观胜率；其增量价值需由配对研究验证。
- 当前只有 paper execution。接入真实交易前必须另做私有 API、幂等订单、持仓同步、
  kill switch、时钟偏差、精度和异常恢复审计。
- ChatGPT auth、rate limit、模型可用性和 Codex CLI flag 仍须在目标 VPS 实测；本地静态检查不等于
  真实账号或长期运行已验证。
