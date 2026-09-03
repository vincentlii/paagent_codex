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

## 2. 配置固定单资产 XAU

首次安装会从 `config/settings.vps.example.json` 创建：

```text
/var/lib/pa-agent/config/settings.json
```

默认监控：

| Source | Symbol | 周期 | 高周期 |
|---|---|---:|---:|
| `binance_usdm` | `XAUUSDT` | `1h` | `4h` |
默认且唯一启用的资产是 `binance_usdm / BINANCE / XAUUSDT / 1h`；HTF 固定为
`4h`、60 根已收盘 K 线，全局分析并发为 1。不得追加第二个 enabled asset，也不得关闭 HTF 或
VPA。默认 `vpa_mode=shadow`，正式 paper 账户仍只执行 PA authority；Joint 只允许离线
A/A/B/B 研究。默认单笔风险为权益的 `0.5%`、组合风险上限 `2%`、最大杠杆 `3x`。
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
PA_AGENT_CODEX_REASONING=high
PA_AGENT_CODEX_BINARY=/usr/local/bin/codex
PA_AGENT_CODEX_STATE_DIR=/var/lib/pa-agent-codex
PA_AGENT_CODEX_MAX_WORKERS=1
```

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
  "$BIN/pa-agent-headless" --check

sudo -u pa-agent-codex env \
  PA_AGENT_CODEX_STATE_DIR="$PA_AGENT_CODEX_STATE_DIR" \
  PA_AGENT_CODEX_MODEL="$PA_AGENT_CODEX_MODEL" \
  PA_AGENT_CODEX_REASONING="$PA_AGENT_CODEX_REASONING" \
  PA_AGENT_CODEX_BINARY="$PA_AGENT_CODEX_BINARY" \
  "$BIN/pa-agent-codex-worker" --check
```

安装器已执行静态 Runtime 审计，并比较源码与当前安装包中的 `AGENTS.md` 和四个 Role Skill。
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

当前 CLI 使用 `--ignore-user-config` 隔离 `$CODEX_HOME/config.toml`，但 production 与 canary
命令都不传 `--ignore-rules`，避免 CLI 版本语义变化时连 workspace rules 一并屏蔽。
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

服务启动前只运行 `--check`。`--probe-data` 不放入自动重启链路，避免交易所短暂断线
导致 systemd 重启风暴。运行时失败按有界退避重试，同一闭合快照不会重复分析。

## 6. Web 控制看板

Dashboard 只监听 VPS 的 `127.0.0.1:8765`，不要直接开放公网端口。在本机建立
SSH tunnel：

```bash
ssh -L 8765:127.0.0.1:8765 <VPS_USER>@<VPS_HOST>
```

随后打开 <http://127.0.0.1:8765>。用户名可填 `admin`，密码是
`/etc/pa-agent/dashboard_token` 中设置的独立密码。

看板提供：

- Bot 心跳、并发队列、资产连接和最近分析状态；
- 最近 PA 决策、VPA evidence 摘要和 Paper 账户；
- “暂停分析”：不再接收新快照，正在运行的分析安全完成；
- DeepSeek `Flash/Pro`、Thinking 与 reasoning effort；
- Codex 发布目录中的 model 与该 model 支持的 reasoning effort；
- `deepseek_only / codex_only / auto` Backend 请求；
- 只在 paused、idle、Profile/Backend 已 applied 时可用的单次 `Run Once`。

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

看板不能改 Prompt、风险参数、资产列表或 Joint 权限；`Run Once` 只能请求固定单资产的一次完整
PA 主链分析，不能绕过 Snapshot、Validator、Router、Risk，也不能真实下单或执行 shell。进程启停与重启仍使用 `systemctl`。这是有意的权限边界，
避免 Web 控制面改变交易语义。

Dashboard 以独立的 `pa-agent-dashboard` 用户运行，只能访问
`/var/lib/pa-agent-control`。它不能读取 `/var/lib/pa-agent`、分析数据库、Paper 数据库
或 LLM API Key；上述内容由可信 Headless 进程生成脱敏只读投影。

Codex CLI 由独立 `pa-agent-codex` worker 执行。Headless 只能向共享 spool 发布 digest-bound Job
与读取结果；Codex 用户只能只读 Job，不能遍历 `/var/lib/pa-agent`、生产数据库、Paper ledger 或
systemd API credential。systemd 将 `/opt/pa-agent` 和 Python environment 设为只读，只允许
`/var/lib/pa-agent-codex/{home,runtime,outbox,inbox,results}` 写入；CLI 每个 role 使用 `--ephemeral`、
`--sandbox read-only`、`--ask-for-approval never`，关闭 shell/Web，并只启用 required Frozen
Evidence MCP。Codex 输出先写 outbox，再由 PA Validator、Router、Risk 和 canonical writer 接管；
Codex 本身不直接改代码、配置、Risk、Paper 或订单。

## 7. 更新与回滚

更新前先记录当前 commit，并停止服务：

```bash
cd /opt/pa-agent
git rev-parse HEAD
sudo systemctl stop pa-agent
sudo git fetch --all --tags
sudo git switch <VERIFIED_BRANCH_OR_TAG>
sudo bash deploy/install_vps.sh
```

重新执行 `--check`、`--probe-data`、Runtime audit 与 live canary 后再启动。回滚时先在看板选择
`DeepSeek Direct` 并保持 paused；若版本级回滚仍有必要，再切回之前记录的 commit，
再次运行安装脚本和四步验收。不要删除 `/var/lib/pa-agent`，paper 与审计数据都在那里。

## 8. 运行边界

- Binance USD-M `XAUUSDT` 合约是否可用以启动时公开 metadata 为准；必须核对 tick size、
  quantity step 与 contract multiplier，不能从 symbol 名称猜测。
- VPA 不创建 Setup、不翻转 PA 方向、不修改主观胜率；其增量价值需由配对研究验证。
- 当前只有 paper execution。接入真实交易前必须另做私有 API、幂等订单、持仓同步、
  kill switch、时钟偏差、精度和异常恢复审计。
- ChatGPT auth、rate limit、模型可用性和 Codex CLI flag 仍须在目标 VPS 实测；本地静态检查不等于
  真实账号或长期运行已验证。
