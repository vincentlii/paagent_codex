# Linux VPS Headless 部署

本部署用于持续拉取 Binance、OKX、Bitget 的 XAU 永续行情，自动运行 PA/VPA
分析并进行本地 paper trading。当前代码没有私有交易所 API，也不会提交真实订单。

## 1. 目录与前提

推荐使用 64 位 Ubuntu 22.04/24.04，并约定：

- 代码：`/opt/pa-agent`
- Miniconda：`/opt/miniconda3`
- Conda 环境：`pa-agent`
- 运行状态：`/var/lib/pa-agent`
- Dashboard 共享投影与控制：`/var/lib/pa-agent-control`
- 非代码配置：`/etc/pa-agent`

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

## 2. 配置三交易所 XAU

首次安装会从 `config/settings.vps.example.json` 创建：

```text
/var/lib/pa-agent/config/settings.json
```

默认监控：

| Source | Symbol | 周期 | 高周期 |
|---|---|---:|---:|
| `binance_usdm` | `XAUUSDT` | `1h` | `4h` |
| `okx_swap` | `XAU-USDT-SWAP` | `1h` | `4h` |
| `bitget_usdt_futures` | `XAUUSDT` | `1h` | `4h` |

默认 `vpa_mode=shadow`，正式 paper 账户仍只执行 PA authority；Joint 只允许离线
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
PA_AGENT_MODEL=deepseek-chat
PA_AGENT_BASE_URL=https://api.deepseek.com
```

`PA_AGENT_MODEL` 必须填写服务商实际开放的 API model id。套餐名称和 API model id
不一定相同，不应猜测填写。

## 4. 上线前四步验收

```bash
BIN=/opt/miniconda3/envs/pa-agent/bin
COMMON_ENV='PA_AGENT_HOME=/var/lib/pa-agent PA_AGENT_OPERATION_DIR=/var/lib/pa-agent-control PA_AGENT_PROMPT_DIR=/opt/pa-agent/prompt_engineering PA_AGENT_API_KEY_FILE=/etc/pa-agent/api_key'
```

1. 离线检查配置、Prompt、依赖和目录权限：

```bash
sudo -u pa-agent env $COMMON_ENV "$BIN/pa-agent-headless" --check
```

2. 只访问公开行情 API，不调用 LLM：

```bash
sudo -u pa-agent env $COMMON_ENV "$BIN/pa-agent-headless" --probe-data
```

三个结果都必须显示 `"success": true`，并核对 `resolved_symbol`、
`instrument_type`、`tick_size`、`quantity_step` 和 `contract_multiplier`。

3. 真正执行一轮三资产分析并写入 paper 账本：

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

四步都通过后再启动：

```bash
sudo systemctl enable --now pa-agent pa-agent-dashboard
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
- “恢复运行”：恢复轮询，若已有新闭合 K 线则按原去重规则分析。

看板不能改 Prompt、风险参数、资产列表或 Joint 权限，也不能直接发起模型分析、
真实下单或执行 shell。进程启停与重启仍使用 `systemctl`。这是有意的权限边界，
避免 Web 控制面改变交易语义。

Dashboard 以独立的 `pa-agent-dashboard` 用户运行，只能访问
`/var/lib/pa-agent-control`。它不能读取 `/var/lib/pa-agent`、分析数据库、Paper 数据库
或 LLM API Key；上述内容由可信 Headless 进程生成脱敏只读投影。

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

重新执行 `--check`、`--probe-data`、`--once` 后再启动。回滚时切回之前记录的 commit，
再次运行安装脚本和四步验收。不要删除 `/var/lib/pa-agent`，paper 与审计数据都在那里。

## 8. 运行边界

- Binance、OKX、Bitget 合约是否可用以启动时公开 metadata 为准。
- OKX quantity 是合约张数，`contract_multiplier` 来自 `ctVal * ctMult`；模拟盈亏、
  手续费、风险和名义价值均据此换算。
- Bitget K 线没有闭合标记，程序用交易所服务器时间和周期长度确认已收盘。
- VPA 不创建 Setup、不翻转 PA 方向、不修改主观胜率；其增量价值需由配对研究验证。
- 当前只有 paper execution。接入真实交易前必须另做私有 API、幂等订单、持仓同步、
  kill switch、时钟偏差、精度和异常恢复审计。
