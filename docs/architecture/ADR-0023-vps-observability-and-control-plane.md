# ADR-0023：VPS 可观测性与控制面

## 状态

Accepted

> `desired_backend` 的窄范围分析 Backend 切换由 ADR-0024 增补；本文禁止在线修改
> Prompt、策略、Risk、资产和进程权限的其他控制边界继续有效。

### 2026-10-10：白名单运行选择增补

用户授权在既有 `AnalysisProfile` 中增加 XAUUSDT/BTCUSDT/ETHUSDT 品种选择、
全局分析并发 1/2/3 和 conservative/balanced/aggressive 倾向。仅适用于 Binance
USD-M、1h/4h 的 V1 Headless 范围；默认仍单黄金、并发 1、balanced。
这替代初版禁止任何资产选择的限制，不允许任意交易所/品种/周期或风险编辑。
选择只在 idle boundary 生效：重建已排空的调度服务，保持同品种 FIFO、任务独立
客户端及冻结身份。停用品种分析不撤销订单，正式 Paper 活跃订单继续观察。
倾向复用 Stage 2 选择性规则，不改变 Stage 1、Router 或风险底线。旧 Profile
默认字段的指纹保持兼容，已有冻结任务不重标。证据与发布边界见
[V1 运行升级](../quality/v1_operations_upgrade_20261010.md)。

## 决策

### 2026-10-15：Demo 两项执行设置增补

用户明确授权在交易总览配置单笔订单总价值与限价有效期，默认500 USDT / 3小时。
Dashboard仅经既有鉴权、Origin/header、正文白名单和CAS修改Runtime control v5，
不直接读写执行库、凭据或发送交易所请求，也不修改启用开关、杠杆、账户和品种白名单。
金额为有限正Decimal（最多8位小数、不超过1e9），有效期为1–24整数小时；数量仍按目标
lot向下取整，不足最小数量或保证金则拒绝，不通过调价或加仓满足。

Host空闲边界确认生效，后续新任务冻结完整设置及代次，意图和执行账本保留自身值；
已有任务、挂单、持仓与Paper协议不变。TTL从分析K1收盘起算，到期撤未成交余量，
不延长决策新鲜度，不改变止损/TP1。请求保存与Host生效分别显示。
旧无`trade_execution_context_v1`任务的设置不属于模型身份、市场hash或cutover证书；
2026-10-25授权的新任务TTL分析身份改由[ADR-0028](ADR-0028-frozen-trade-execution-context.md)规定，
金额及cutover控制不变。旧control v1–v4默认迁入500/3h，保留Fast；
无参数的旧Intent仅省略新增optional字段参与摘要，不改变旧合法null字段摘要。
兼容只保证新Worker读旧数据，发布和回退必须协调Host/Worker版本。详见
[Demo验收](../quality/okx_demo_execution_acceptance_20261015.md)。

VPS Dashboard 是独立进程和独立 Unix 用户，不导入分析编排器，不持有 LLM 或交易所
凭据，也不直接调用 PA、VPA、Paper 或数据源对象。Headless 使用 SQLite `mode=ro`
读取审计索引和 Paper 账本，再通过原子 JSON 发布版本化、白名单化的完整运行投影；
Dashboard 只读取该投影，不能打开生产数据库或配置目录。

控制采用持久化的 desired-state 文件。Dashboard 只能写入 `running` 或 `paused`；
Headless 主循环主动读取并决定安全响应。`paused` 只阻止新行情快照进入队列，已有任务
继续完成，避免中断模型调用后留下含混记录。进程启动、停止、重启和发布继续由 systemd
负责，不从 HTTP 请求执行 shell 或提权操作。

控制请求与控制生效是两个状态。Dashboard 写入递增 generation 后先显示“处理中”；
Headless 到达不会再提交本轮新任务的安全边界后，在状态投影中回写
`applied_control_generation`。只有 generation 匹配且运行状态一致时，Dashboard 才显示
“已暂停”或“运行中”，避免把文件写入成功误报为 Bot 已经完成切换。

Dashboard 默认只监听 `127.0.0.1`，使用独立 systemd credential 进行 HTTP Basic
认证，通过 SSH tunnel 访问。控制请求还必须携带自定义 header，并校验浏览器 Origin。
Dashboard token 与 Provider API Key 彼此隔离。

### 2026-10-09：浏览器表单登录增补

内置浏览器对 Basic Auth challenge 返回 `ERR_INVALID_AUTH_CREDENTIALS`，而同一
隧道和密码的 HTTP 验证成功。用户批准以网页密码登录替代浏览器认证弹窗。
未登录首页仅交付无私有数据的登录页；`POST /api/login` 校验独立 Dashboard token
后签发随机 256-bit、进程内保存的会话，固定 8 小时有效。最多 32 个会话；到期、
重新登录替换、退出或服务重启后失效。密码不写入 URL、HTML、日志或 Web Storage。

Cookie 为 host-only、`Path=/; HttpOnly; SameSite=Strict`。此服务限定 loopback HTTP
加 SSH tunnel，因此未使用依赖 HTTPS 的 `Secure` 属性；不得据此直接公开服务。
所有请求校验 loopback Host；登录、退出和控制 POST 都保留自定义 header、严格同源
Origin、4096 字节正文上限。10 次错误密码后限制登录 60 秒，无额外依赖或数据库。
显式 Basic Authorization 继续供原诊断脚本使用，但未授权响应不再发布
`WWW-Authenticate`，API 返回 JSON 401，页面自动回到登录页。

部署只重启 Dashboard，不中断 Host 或 Codex Worker。参考
[MDN Set-Cookie](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie)
与 [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)。
Bot 与 Dashboard 仅通过 `pa-agent-control` 下两个单向权限目录交换信息：Bot 只能写
`status/`、Dashboard 只能写 `control/`，双方仅能读取另一方向。Dashboard 用户不属于
`pa-agent` 组，因此不能读取 Provider credential 或运行状态根目录。

### 2026-10-10：只读交易总览增补

`GET /trading` 与 `GET /api/trading` 复用已有登录、Host 校验和安全响应头。
Headless 在既有 30 秒 review cache 中只读分析索引、调度账本与 Paper 账本，发布
`trading.schema_version=1`；网页不读取 DB、配置、原始 Prompt，也不发起模型或执行请求。
原 `/api/status` 不附加交易大块，避免运行控制页面反复下载复盘内容。

交易统计固定隔离正式 `pipeline-pa_vpa_joint` / `pa_vpa_joint`、Binance USD-M
`perpetual` 或 `tradifi_perpetual` / 1h 与 `paper_latency_v3`；黄金产品类型沿用
Provider 元数据原值，订单复盘仍按实际类型精确关联，不合并身份。
整账户已结算余额和结构风险与范围内净收益分开。
调度任务计数不等同当前 Codex 联合样本数；候选与 No Trade 只来自成功且身份匹配的
联合分析。净收益、胜率和曲线仅使用已知平仓结果，未知路径不计零。

复盘复述现有 canonical PA/VPA 内容，不生成新判断。各品种每状态 10 笔订单、
最近 30 个任务和曲线最近 180 个有平仓日期均有界，聚合计数不受列表截断影响。
SQL 使用 `mode=ro` 与有界读取；不增加 schema、依赖或数据库权限。
详细统计口径、验收与发布边界见 [交易总览说明](../quality/trader_dashboard_20261010.md)。

## 理由

- 交易员：暂停不能改变冻结快照、PA/VPA 结论或 Paper 执行语义。
- Wyckoff/VPA：看板只展示已落盘的 VPA evidence，不派生新的量价标签。
- 量化与软件架构：状态投影可重放、损坏控制文件 fail closed、控制命令持久化且不跨线程
  操纵分析对象；Web 故障不会中断 Bot，Bot 故障会由心跳过期明确呈现。

## 非目标

- 不在线修改 Prompt、策略权限、风险参数或任意资产列表；仅允许上述白名单选择。
- 不从 Dashboard 发起一次行情分析或真实订单。
- 不把 loopback HTTP 服务直接暴露公网。
