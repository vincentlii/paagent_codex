# PA Agent Codex

基于已优化的 [PA_Agent](https://github.com/rosemarycox5334-debug/PA_Agent) 增加受约束的 Codex Analysis Backend。当前运行方向是 **Linux VPS Headless + 网页看板 + Paper 模拟观察**；原 Direct Backend 与 Windows 桌面入口继续保留。

Brooks PA 是主市场框架，Wyckoff VPA 是独立证据层。Codex 负责理解上下文、调查冻结证据和专业判断；Host 程序拥有行情身份、时间边界、Router、校验、风险、订单数学及持久化权限。

## 分析链路

正式 Codex 全量单 Agent 联合链：

```text
Frozen Snapshot（主周期 + HTF）
→ PA Stage 1 市场诊断
→ Validator → Brooks Router
→ 独立 VPA Evidence Analyst
→ PA + VPA 联合 Stage 2 交易决策
→ Validator / Risk / Trader Equation
→ 审计记录与 Paper
```

- 完整知识和阶段协议由 Host 选择，经只读 Evidence MCP 交付；模型可在同一冻结快照内按需调查证据，不能获取未来行情、改代码或绕过交易边界。
- 不同快照使用独立会话；同快照的 PA Stage 1 首稿验证通过后才允许受控续接 Stage 2。VPA 与修复任务隔离，不开启长期记忆。
- Runtime `AGENTS.md`、角色 Skills 与规则按发布版本冻结。分层知识及 Stage 内子 Agent 仍属于隔离研究，不自动进入正式链。
- 当前 Binance Headless Profile 支持 XAUUSDT、BTCUSDT、ETHUSDT，有界并发及保守／平衡／激进倾向；默认配置与 VPS 实际启用状态须分别核对。
- 看板用于控制与复盘；模型意图、Paper 接收、实际入场及收益分别统计。不连接交易账户自动下单，Paper 不等于真实成交或盈利证明。

## 安装与部署

Python 3.11+，依赖版本以 `uv.lock` 为准。Linux Headless 不安装 Qt：

```bash
uv sync --frozen --extra vps
uv run --frozen --extra vps pa-agent-headless --check
```

上述检查需要先按部署文档配置 `PA_AGENT_HOME`、数据源及隔离 Worker。Codex CLI 安装、账户认证、权限隔离、systemd 服务、看板登录与 SSH tunnel 均见 [Linux Headless 部署文档](docs/linux_headless_deployment.md)，不要直接把样例视为已配置环境。

| 入口 | 用途 |
| --- | --- |
| [联合链配置样例](config/settings.vps.codex-joint.example.json) | 显式选择 Codex 联合 pipeline |
| [Worker 环境样例](deploy/systemd/pa-agent-codex-joint.env.example) | Codex 模型、思考强度及服务配置 |
| [配置说明](config/README.md) | 配置字段和本地凭据管理 |
| [当前状态](docs/knowledge/current-status.md) | 实际验收范围、VPS 发布状态及未验证项 |
| [Codex Analysis North Star](docs/knowledge/codex-analysis-north-star.md) | 长期架构和不可越过的边界 |
| [知识库入口](docs/knowledge/README.md) | 架构、知识调用、研究及排障索引 |

原桌面版安装 `.[desktop]` 后使用 `python -m pa_agent.main`，说明见 [桌面使用文档](PA_Agent使用文档.md)。Direct 与 Codex 不共享隐式认证；旧 off/shadow 与新版联合链不是同一协议。

## 仓库内容与维护

| 路径 | 内容 |
| --- | --- |
| `pa_agent/` | 分析、Codex Runtime、Headless、Dashboard、执行与审计代码 |
| `prompt_engineering/` | Brooks / VPA 原文知识、阶段协议及 Schema |
| `tests/` | 保留的回归测试、属性测试、集成测试与 fixture |
| `deploy/` | Linux 安装与 systemd 服务文件 |
| `docs/` | 升级计划、ADR、验收证据与长期知识库 |
| `tools/` | 有长期用途的诊断、验收与研究工具 |

修改前读取 [AGENTS.md](AGENTS.md) 和项目变更规范。CI 使用锁定依赖执行非 live 回归与资源检查；真实模型／行情实验必须显式授权，不因上传仓库而重跑。

当前源码树不包含本地 API key、Codex 登录凭据、生产配置、运行数据库、分析记录、虚拟环境或缓存。清理仅移除已确认无运行依赖的宣传素材、一次性结果及再生成缓存，不删减知识、Runtime Skills 或必要测试。源码历史保留；只发布当前分支，不镜像其他工作树和实验分支。上游历史曾包含日志，上传前须单独复核，方法见 [运行与排障](docs/knowledge/operations-and-debugging.md)。

**免责声明：**仅供学习与研究，不构成投资建议，不保证盈利。

保留原项目来源及 [GNU Affero General Public License v3.0 (AGPL-3.0)](LICENSE)。
