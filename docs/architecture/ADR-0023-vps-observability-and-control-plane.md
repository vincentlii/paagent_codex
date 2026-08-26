# ADR-0023：VPS 可观测性与控制面

## 状态

Accepted

## 决策

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
Bot 与 Dashboard 仅通过 `pa-agent-control` 下两个单向权限目录交换信息：Bot 只能写
`status/`、Dashboard 只能写 `control/`，双方仅能读取另一方向。Dashboard 用户不属于
`pa-agent` 组，因此不能读取 Provider credential 或运行状态根目录。

## 理由

- 交易员：暂停不能改变冻结快照、PA/VPA 结论或 Paper 执行语义。
- Wyckoff/VPA：看板只展示已落盘的 VPA evidence，不派生新的量价标签。
- 量化与软件架构：状态投影可重放、损坏控制文件 fail closed、控制命令持久化且不跨线程
  操纵分析对象；Web 故障不会中断 Bot，Bot 故障会由心跳过期明确呈现。

## 非目标

- 不在线修改 Prompt、策略权限、风险参数或资产列表。
- 不从 Dashboard 发起一次行情分析或真实订单。
- 不把 loopback HTTP 服务直接暴露公网。
