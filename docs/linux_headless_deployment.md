# Linux VPS Headless 部署

Headless 模式不加载 Qt，不显示图形界面，也不执行真实下单。它按资产轮询最新已收盘 K 线，每个新冻结快照自动运行 PA/VPA 分析，并写入 JSON 与 SQLite 审计。

## 1. Miniconda 安装

```bash
git clone https://github.com/rosemarycox5334-debug/PA_Agent.git /opt/pa-agent
cd /opt/pa-agent
conda create -n pa-agent python=3.11 -y
conda activate pa-agent
pip install -e .
```

Linux 使用 TradingView；MT5 Python 包和桌面 GUI 不属于 Headless 依赖。需要在本机运行 GUI 时安装 `pip install -e ".[gui]"`。

## 2. 状态目录与配置

```bash
sudo useradd --system --home /var/lib/pa-agent --shell /usr/sbin/nologin pa-agent
sudo install -d -o pa-agent -g pa-agent -m 0700 /var/lib/pa-agent
sudo install -d -o pa-agent -g pa-agent -m 0700 /var/lib/pa-agent/config
sudo cp /opt/pa-agent/config/settings.example.json /var/lib/pa-agent/config/settings.json
sudo chown pa-agent:pa-agent /var/lib/pa-agent/config/settings.json
sudo chmod 0600 /var/lib/pa-agent/config/settings.json
```

编辑 `settings.json`：

- `monitoring.enabled=true`
- 在 `monitoring.assets` 中配置每个 `source/exchange/symbol/timeframe`
- `prompt.vpa_mode=off|shadow|soft`
- 根据 API 网关配置 `provider.model` 与 `provider.base_url`
- 不要把明文 API Key 写进配置

运行状态遵循显式 `PA_AGENT_HOME` 边界，概念与 [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/latest/) 的资源/状态分离一致；本项目保留显式目录以方便 systemd 权限控制。

## 3. API Key

推荐 systemd credential：

```bash
sudo install -d -m 0700 /etc/pa-agent
sudo sh -c 'umask 077; printf %s "YOUR_API_KEY" > /etc/pa-agent/api_key'
```

也可使用 `PA_AGENT_API_KEY_FILE` 或仅用于临时会话的 `PA_AGENT_API_KEY`。运行时 Key 不会回写 JSON。`LoadCredential=` 行为见 [systemd.exec](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)。

## 4. 部署前检查

```bash
export PA_AGENT_HOME=/var/lib/pa-agent
export PA_AGENT_API_KEY_FILE=/etc/pa-agent/api_key
/opt/miniconda3/envs/pa-agent/bin/pa-agent-headless --check
/opt/miniconda3/envs/pa-agent/bin/pa-agent-headless --once
```

`--check` 只验证配置、Prompt、数据源能力和写权限，不访问网络或调用模型。`--once` 会真实拉取行情并分析一次，退出码非零表示任一最新快照未成功完成。

## 5. systemd

根据实际 Conda 路径编辑 `deploy/systemd/pa-agent.service`，再安装：

```bash
sudo cp /opt/pa-agent/deploy/systemd/pa-agent.service /etc/systemd/system/
sudo cp /opt/pa-agent/deploy/systemd/pa-agent.env.example /etc/pa-agent/pa-agent.env
sudo systemctl daemon-reload
sudo systemctl enable --now pa-agent
sudo systemctl status pa-agent
journalctl -u pa-agent -f
```

服务使用 `StateDirectory=pa-agent` 创建并授权状态目录，同时启用 `Restart=on-failure`、启动频率限制、只读系统目录和唯一可写状态目录。相关语义见 [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html) 与 [systemd.exec](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)。

## 6. 数据与复盘

- 完整记录：`$PA_AGENT_HOME/records/pending/*.json`
- 分析数据库：`$PA_AGENT_HOME/records/audit/analysis.sqlite3`
- 调度数据库：`$PA_AGENT_HOME/records/audit/monitoring.sqlite3`
- 日志：`$PA_AGENT_HOME/logs/pa_agent.log`

`monitoring_jobs.analysis_success=1` 仅表示分析链路成功且记录已持久化，不代表交易盈利。真实收益、人工标签和 OHLC 反事实复盘继续分库存储。

## 7. 停止与升级

```bash
sudo systemctl stop pa-agent
cd /opt/pa-agent
git pull --ff-only
conda activate pa-agent
pip install -e .
pa-agent-headless --check
sudo systemctl start pa-agent
```

先停止服务再升级，避免运行中 Prompt/源码变化造成 cohort 混用。`SIGTERM` 会停止新轮询、取消队列并断开数据源；若外部依赖迟迟不返回，systemd 的 `TimeoutStopSec` 是最终边界。
