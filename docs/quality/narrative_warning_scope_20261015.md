# 叙述校验作用域与阶段交接边界

本报告分开记录离线实施、发布前检查和随后用户授权的固定提交 VPS 发布。
当前运行版本以末节及 `docs/knowledge/current-status.md` 的发布记录为准。

## 长期原则

程序区分三种情况，不把自然语言匹配成功当成事实已核实：

| 情况 | 处置 |
| --- | --- |
| 不适用：没有当前检查所针对的明确陈述，或联合阶段合法使用 VPA | 不生成该项警告 |
| 无法可靠核对：周期、主语、比较器或窗口不唯一，事实不可用，或表达为否定/条件 | 在既有 warning 文本中标明 `unverified`，不宣称冲突 |
| 可复算矛盾：明确陈述与同源、同周期、已绑定对象的程序关系相反 | 保留非阻断叙述警告 |

这里的分类只针对原有叙述质量检查。Schema、引用身份、权限、明确 OHLC/几何、时序、
订单数学与执行安全的 hard checks 不变。非阻断警告继续写入既有审计，不改 Draft、不自动重试、
不增加模型审核、不向 Joint 新增警告载荷，也不自动解释为等待或拒单。

## 根因与修复

- **EMA 作用域**：原规则把整段文字中的方向及所有数字都当成 EMA 斜率，且只读取主周期。
  4h 背景因而可能与 1h EMA 比较，“4小时”也可能被当成 delta。现在只匹配直接 EMA 谓词，
  明确数字才比较；HTF 字段使用交付的 HTF frame，唯一周期标记匹配对应 frame。
  混合周期、缺少对应 frame、持续时间及未来条件标为无法核对，不猜测。
- **否定极值主语**：bar summary 的行标签不一定是句中谓词主语。现在明确的 K# 否定谓词
  优先绑定自己的主语；K#-K# 范围不再被当成单棒比较。歧义不推导成事实矛盾。
- **VPA 比较器**：原基线检查把单棒 `volume_vs_baseline_relation` 用于窗口，缺失字段也报冲突。
  现在单棒与 current/prior window 分开，只按唯一窗口主语读取相应 causal baseline 关系。
  匹配不跨标点或另一个比较器；否定及条件不当成肯定事实。多 selector、缺事实或缺主语
  标明无法核对，不要求模型为了校验器重写段落。
- **未来闭盘**：接受“观察下一根收盘”“等待下一根K线收盘”等正常表达；“收盘前”仍不能
  证明闭盘确认。原 K0/未闭盘约束保持，不增加执行 Gate。
- **联合阶段**：只有通过联合上游与 Schema 校验的显式路径才传入 Joint 上下文，免去旧 PA-only
  成交量措辞提示；普通 PA 默认仍执行原提示，不从输出中的 VPA 词语或字段反推角色权限。

有限语言匹配不是通用语义验证器。无警告不代表所有叙述正确；`unverified` 也不代表数据本身缺失、
模型必须补证据或交易应被拒绝。复杂专业判断仍由 LLM 负责。

## Prompt、Skills 与冻结语义

- 用户可见说明仍用简体中文，不再规定内部推理的语言。
- 模型提交建议 Draft；Host 按已授权模式校验和执行。模型没有凭据或直接下单权限。
- 信号棒极点外规则限定到相应的突破入场；计划型限价依 §9.0P，stop 依结构失效。
- `validated/accepted` 只表示指定协议、引用及确定性检查已通过，不表示专业解释已被逐句证明。
  Frozen Snapshot 事实不可改写；冻结 Stage 1 约束同一运行的权限，不使专业解释免于反证。
  Stage 2 可在 Router 内保留反证及合法等待，不重写上游、不扩大方向、Setup 或 Gate 权限。

Provider/canonical Schema、知识锚点和案例、mandatory 读取范围、Router、阶段数量与交易规则不变。
校验策略身份更新为 `structured-hard-scoped-prose-warning-v2`；Runtime 文本更新会改变新 Job 的资源摘要。
历史 Frozen Job、历史记录及原始失败不迁移或改写。发布时仍须按原流程排空 Host/Worker 队列、
核对恢复任务并协调 Runtime 版本，不能将本地修复视作已上线。

## 验证与边界

- 新增回归先复现原误报，再验证正确表述、可核对矛盾、无法绑定与不适用分支。
- Direct/Codex 显式 Joint 离线链均验证三个角色各一次；原 Draft、entry/stop/TP1 与风险说明不改写。
- 独立审阅者从 PA、VPA、量化架构三视角通过，并独立运行 165 项聚焦测试通过。
- 第一次完整 Headless 回归为 1910 通过、2 失败：失败是旧校验版本和旧“拆句”提示的固定预期；
  更新这两项预期后，相关 39 项测试通过。第二次为 1911 通过、1 失败：未改动的面板认证测试
  出现一次 Windows 回环连接 `WinError 10053`；随后整组面板 52 项通过，未据此修改认证逻辑。
- 最终完整 Headless unit/integration/property 回归 **1912 通过、7 项 live deselected**，耗时 94.88s；
  新增 29 项作用域回归。两项警告均是第三方 tvDatafeed 的无效转义弃用提示，与分析校验警告不同。
  全仓关键 Python 静态错误检查与 `git diff --check` 通过。未改动的 GUI/Qt 相关测试按 Headless 范围排除。
- Wheel 构建成功，解包后的 Headless 导入不含 Qt；Prompt 与 13 项 Runtime 资源审计通过。
  本机 `python -m build --no-isolation` 的依赖预检查因独立 `wheel` 包缺失而停止，随后使用
  已安装 setuptools 内置的 `bdist_wheel` 构建，未安装依赖或修改锁文件。
- 未进行新模型质量、延迟、Token、利润或自然运行验收，不据此宣称提升；GUI/Linux CI 未在本轮执行。

旧代码与清理前文本备份位于忽略目录 `docs/changelog/backups/20261015-warning-scope-before/source.zip`。
本次测试/构建临时目录在最终验收后清理，四份用户既有 ICT 改动保持原字节；备份目录保留验收摘要。

## 发布前最终检查

2026-10-15 用户明确授权最终检查、commit 与 VPS 发布。复核未发现新增确定 bug；同一独立
审阅者确认 PA/VPA/量化架构边界可进入发布验收。新鲜 Headless 回归为 **1912 passed /
7 deselected，105.92s**，全仓关键静态检查及差异检查通过。VPS 发布前四服务正常，
Host 队列为空，Demo 对账 ready、无活跃订单或持仓；这些是发布前状态，不是新版本上线证据。

## 发布检查发现的 Demo 备份连接缺陷

原版本备份服务返回 success，但真正核对 manifest 时发现 `execution.sqlite3-wal/-shm` 已不存在。
`with sqlite3.connect(...)` 只控制事务，不关闭连接；原实现取文件清单时目标 WAL/SHM 仍存在，
函数返回或进程退出后关闭连接才 checkpoint/清理，导致 manifest 失效。失败备份原样保留。

修复仅用 `contextlib.closing` 显式关闭源和目标连接，全部关闭后再生成 manifest；不手动删除
sidecar、不改备份格式、WorkerLock、源 `mode=ro` 或交易逻辑。新增同进程 GC、跨进程退出后
逐成员存在/哈希核验、目标 quick_check、源/目标完整 iterdump 一致测试；原实现两项先红，
修复后 23 项 Demo 聚焦回归通过。发现问题后未覆盖安装，先恢复旧四服务并保持 Host paused48，
以继续对账保护；后续须用修复版本生成可核验备份再安装，不能靠重写旧 manifest 消除失败。

同一独立审阅者三角色复审通过，独立备份测试4项通过。包含修复的完整Headless回归为
**1914 passed / 7 deselected，91.43s**；关键静态及差异检查通过，无真实模型调用。

## 2026-10-15 固定发布验收

- 本轮优化提交6de6b47、备份修复c0637ba均已推送私有main。VPS原HEAD为f9848f4（此前代码
  与fe9574f相同），通过已验证增量Git bundle离线构建新旧wheel，固定安装
  `c0637bab00b3df6de0328c2712d15d261eaf4804`；pyproject/uv.lock/部署单元无变化，no-deps安装。
- 暂停47→48并确认Host queued/running/unfinished全0、Codex inbox/running及子进程为空，
  无恢复阻塞。Demo对账新鲜完整、无活动订单/持仓及未决请求后才停四服务。
- 首次旧官方Demo备份`demo-1792062762558`因失效sidecar manifest核验失败，原样保留；
  此时没有安装，先恢复旧服务并保持暂停。修复后在同一WorkerLock下生成候选备份
  `releases/warning-c0637ba/demo-backup/demo-1792063145707`，全部11成员hash与完整数据库
  逻辑一致；Host新备份为`pa-agent-20261015T111905Z`，manifest及数据库quick_check通过。
- 停服安装前后4库逐内容逻辑摘要、17385个原文件hash、47项非项目依赖完全一致；
  无migration、回填、历史记录改写、配置或认证替换。315个已安装包成员与固定源码一致，
  pip check、13项Runtime资源及POSIX权限、9项runuser隔离检查通过。
- Linux wheel SHA256为`173e99f33eef2dc68987dabea37503c857b2a062363ac3561771b7a33e50eda1`；
  新Runtime asset为`401d952ea8cc91cd15ef785b7897234755a1711a91d96f861ed2689757ea6247`。
  安装后的官方Demo备份服务实际生成`demo-1792063285236`，11成员均存在/hash一致、
  无临时WAL/SHM成员且quick_check通过，未将候选helper冒称官方服务验收。
- 先启动Demo/Codex/Dashboard，再启动Host并核对paused状态：新Runtime/健康auth正确，
  原Profile4/Fast/Run Once5与Demo配置保持；HTTP health/status/trading为200、未认证401，
  交易面板从既有`trading`封套核验selected_backend=okx_demo。核验脚本曾误读顶层字段，
  按既有JSON合同修正后通过，无产品改动。
- 恢复原running为generation49后再次确认四服务active/running、Result=success、NRestarts=0，
  Demo ready/完整覆盖、无恢复阻塞、源工作区干净。未重做cutover、补发旧任务、触发Run Once
  或人工模型/测试订单；重启后的调度内存计数归零不代表账本删除。

发布证据、新旧wheel、有效备份与原失败备份保留在VPS的`/var/backups/pa-agent`及
`/var/backups/pa-agent-demo`；本地原文、增量包和脱敏收据在既有ignored备份目录。
完整Linux/GUI CI、新版本自然模型链质量/Token及交易收益尚未验收，发布不证明这些改善。
后续验收文档提交仅同步CONTEXT及本报告等说明，代码/安装包版本仍为c0637ba，不重启服务。
