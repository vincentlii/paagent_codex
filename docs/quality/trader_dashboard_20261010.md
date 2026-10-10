# V1 交易总览：口径、边界与验收

日期：2026-10-10。基线 `0f61d1b`，分支 `codex-analysis-backend-v1`。
用户选择第一种“交易总览”原型；初次本地实现未 commit、未发布、未调用模型或行情。
用户随后明确授权提交并发布；发布证据在完成后追加，原统计及权限边界不变。

## 页面与数据流

新增登录保护页面 `/trading` 和只读 API `/api/trading`。原运行控制页面只增加导航。
交易页展示账户已结算余额、期间净收益、参考毛浮盈亏、结构风险，三品种分析与交易
对比、累计已实现净收益曲线、订单流水和最近分析复盘。支持全历史及滚动 7/30 天；
品种筛选作用于订单和最近分析表，账户与收益曲线保持三品种合计。

数据流沿用 ADR-0023：Headless 使用只读 SQLite，`operations/trading.py` 生成投影，
既有 30 秒 cache 发布原子 status JSON；Dashboard 只读取此 JSON，经 allowlist 后返回。
Dashboard 仍无生产 DB 权限。独立 API 避免原控制页下载交易详情。无新 dependency、
schema、migration、模型调用、交易按钮或策略规则。

SQL 连接 `mode=ro` / `query_only`，busy timeout 0.25 秒、每连接 SQL progress deadline
1 秒；超时返回对应区域不可用，不影响分析终态或执行。此为 SQL 预算，不是整页实时
SLA。订单每品种/状态最多 10 笔，任务最近 30 条，曲线最多 180 个有平仓的 UTC 日期；
全历史聚合不受列表截断影响。长复盘字段展示最多 2400 字符，原始 record 不改写。
页面每 30 秒读取，HTTP 请求 8 秒可中止，失败保留最后数据并提示；切换筛选不掩盖失联。

## 统计契约

| 内容 | 口径 |
| --- | --- |
| 分析总次数 | 当前 Binance USD-M / BINANCE / XAU、BTC、ETH / 1h 的唯一已受理 `monitoring_jobs.task_id`。重试、duplicate 与 `submission_count` 不增加计数；可含历史其他 Backend/协议，页面明确说明。 |
| 完成 / 失败 | 完成要求调度 completed 且 analysis_success=1；失败来自最新非取消失败回执，取消和未有终态分开。不能等同模型判断正确。 |
| 模型意图 | 仅成功、完整身份匹配的 `pa_vpa_joint` 索引，候选与模型 No Trade 分开。无索引/身份不匹配保持未知覆盖，不猜协议。 |
| Paper 处置 | 正式联合账户和 arm 的 paper_decisions，通过 task_id 关联当前市场任务；submitted/no_trade/rejected 分开。尚无处置不冒充接单。 |
| 订单与收益范围 | `pipeline-pa_vpa_joint`、arm=`pa_vpa_joint`、`paper_latency_v3`、binance_usdm/BINANCE、1h、三品种；产品类型允许 Provider 实际记录的 `perpetual` 和黄金 `tradifi_perpetual`，不改写身份。研究、其他账户和版本不混入。 |
| 已记入场 | 已有 fill_price 与 fill_bar_ts 的 open/closed/ambiguous；不确定入场未有 fill 不计。已记入场不代表退出路径已知。 |
| 平仓净收益 | 只用 status=closed 且有限 realized_pnl；该值已扣双边 fees，不能再次减 fees。缺失只算 coverage，不计零。 |
| 胜率 | 净收益 >0 的已知平仓笔数 / 已知平仓笔数；零收益单列 breakeven。pending/open/cancelled/ambiguous 不进分母，零样本为未知。 |
| 期间 | 分析/Paper 处置按 task 创建时间；接单与其入场按计划激活时间；平仓净收益按 exit_bar_ts。当前挂单/持仓/不确定总数不被期间隐藏。不是同 cohort 转化率。 |
| 收益曲线 | 已知净收益按退出 K 线的 UTC 日聚合并累计，同日不虚构柱内顺序。180 点截断不丢累计基准；只代表所选期间已实现净收益，不是 MTM 曲线。 |
| 账户余额 / 风险 | 整个联合账户的已结算余额及当前 pending/open 结构风险；范围外订单存在时明确披露。不是“当前版本净值”。ambiguous 不可判定风险不能凭空补造。 |
| 参考毛浮盈亏 | 同完整市场身份、入场以后已确认收盘价，且订单 evaluated_through_ts 已推进到该棒。价格缺失/陈旧超过两个周期/回放落后保持 null；组合不发布部分估值合计。无持仓时为 0。 |

行情 close 的收盘边界由 ts_open+1h 确定，展示价格时点；它不是网络实时报价，
不含未来退出费和资金费。fill/exit_bar_ts 是模拟所在 K 线，不是交易所实际成交时刻。
Paper 未实现资金费、真实盘口撮合、容量约束与清算，不能声称等同实盘收益。

## 专业复盘与三角色核对

复盘从已有 `review_context_json.decision_review / vpa_evidence` 抽取，保留 PA
背景、方向、Gate、交易理由、入场/失效、风险/观察点以及 VPA 摘要、关系、事实引用、
认识状态与后续确认。模型文本使用 textContent。未沿用旧 `shadow_only_no_effect`，
未计算 VPA 贡献率，未把模型主观先验当实测胜率，未让网页生成新专业判断。

| 角色 | 核对结论 |
| --- | --- |
| PA 交易员 | No Trade 不是失败；候选、执行、入场、平仓分开；不新增 Setup、距离或风险 Gate。通过。 |
| Wyckoff VPA 交易员 | 展示 canonical 证据、关系及确认条件，引用不被换成自动交易规则，不推算因果贡献。通过。 |
| 量化软件架构师 | 唯一任务、完整身份、版本/账户隔离、closed-only、coverage、只读与有界刷新均保留；回放落后不估值。通过。 |

上述为实现者三视角核对，附一名只读协作者源码审阅，不等同三名独立专家审计。
协作者发现的“已入场不确定订单漏计”“回放落后误估浮盈亏”“复盘身份关联不足”
均已补回归并修正，只改变展示，不增加模型/执行校验。

## 验收与剩余边界

新增离线用例覆盖任务重试去重、全历史超 200 条计数、候选与处置分离、研究/版本/市场
隔离、未知平仓覆盖、手续费不双扣、期间时间口径、缺失/陈旧/未收盘价格、订单回放落后、
错误索引身份、历史订单独立复盘、DB 不变与缺失时不建文件、Host 状态发布、认证与
只读路由、白名单/有界 API、HTML 目标唯一、JS 语法/渲染/空态/复盘及失联保持。

验收命令使用项目 Python，`python -B -m pytest -m 'not live' -o addopts= -p no:cacheprovider`，
测试数据库仅位于本次独立临时目录。相关 Headless、状态、Paper、联合链、HTF 与审计
回归，以及关键 Ruff 与 diff 检查结果在完成后记录于下方。不运行 GUI 或付费实验。

完成结果：

- 新页面/统计与运行控制、联合编排、联合运行、HTF 的定向离线回归：83 passed。
- 最终完整 Dashboard 模块回归：52 passed，覆盖既有登录/写入边界及新增只读路由。
- 此前 Headless、状态投影、Paper reporting/coordinator、运行路径与 import 边界等
  九个相关模块回归：130 passed。以上集合有重叠，不相加为独立测试总数。
- 授权发布后的新鲜定向核验：交易投影、页面逻辑、备份与部署资产共 23 passed。
- 新文件 Ruff 全项、既有受改 Python 文件关键错误项及 `git diff --check` 通过。
  对照基线 AST，`do_POST` 未改变，原控制写入及认证规则没有被交易页面重写。

扩大回归期间，两次既有非法 POST 拒绝用例出现 Windows `WinError 10053` 连接中断；
定向复跑与最终完整 Dashboard 回归均通过，基线同类十项检查也通过。未定位间歇
中断的唯一原因，不能据此宣称不存在 HTTP 环境问题；未为让测试通过修改认证规则。
发布到 Linux 后仍应核对实际登录和只读页面可达性，不需要额外模型分析。

初次本地验收未验证：VPS 实际发布、生产历史库数据覆盖、浏览器像素验收与长期运行负载。
Node 的轻量 DOM 用例验证 JS 行为，不冒充真实浏览器端到端或视觉验收。
新文件应随正常包构建自动包含；发布必须同时更新 Host 与 Dashboard，保留账本和控制
配置，不重跑分析、不重置账户。仅更新 Dashboard 会收到“Host 尚未发布交易数据”。

### 发布前真实库修正

首次暂停发布核对发现 XAU 的 `instrument_type` 来自 Provider `contractType`，实际为
`tradifi_perpetual`，不是合成 fixture 使用的 `perpetual`；初始过滤漏计黄金联合意图。
只读统计允许这两种已知永续产品类型，保留实际类型，订单复盘进一步精确比较类型。
未知类型、其他来源/市场/版本仍排除，不重标旧记录、不改交易安全或订单数学。
新增两种类型及跨类型拒绝关联回归；投影、页面、Host/cache 的新鲜回归 69 passed。
三角色核对通过：PA/VPA 只补展示，量化侧修正产品范围而不合并事实身份。

跨平台打包须避免本机 `core.autocrlf=true` 使 `git archive` 交付 CRLF：从固定提交使用
`git -c core.autocrlf=false -c core.eol=lf archive`，wheel 内源码/资产逐项比对 Git blob，
再与 VPS checkout/installed 比对。不能用“文本看起来相同”冒充字节身份一致。
Windows tar 对中文文件名曾有提取错误；改用 ZIP，必须同时验证资源数量及字节，
不能仅校验已成功提取的子集。以上只修正发布流程，不引入应用规则或依赖。

### VPS 发布验收及并发容量修正

运行代码 `94e43f0` 已发布；`811c0b1` 为交易页初始提交。暂停、排空、停止三项
服务后安装固定 wheel，再重启并恢复原 Profile；备份
`/var/backups/pa-agent/pa-agent-20261010T045057Z` 校验通过。生产三库 quick_check
均为 ok，暂停期间表计数与备份一致；原记录、账户和配置未重置。
262 个代码/Runtime 文件与 38 个 Prompt 文件在 Git、wheel、VPS checkout 和
installed 逐字节一致；48 个依赖一致性、Runtime 资产及隔离权限检查通过。
认证 `/trading`、`/api/trading` 与 `/healthz` 为 200，未认证私有 API 为 401；
三品种唯一任务计数与独立 SQLite 查询一致，真实黄金产品身份与联合分析已显示。
未额外 Run Once；实际浏览器像素和交易盈利能力仍不属于本次验收。

恢复后自然定时任务暴露原有 Worker 容量问题：`TasksMax=64`、`pids.events max=23`，
BTC/ETH CLI 退出 -6，内存峰值约 243 MB、memory OOM 计数为 0。同类失败在发布前
也存在，不能归因只读页面或 Fast。用户授权有界容量修复；不修改模型、协议、知识、
Skills、Prompt、线程池并发数量或数据库。旧失败保留，不重跑洗掉。

独立同 VPS cgroup 线程探针（5 秒硬上限、无 Codex/行情调用）：64 上限下 150 个
请求仅创建 63 个工作线程，报 `can't start new thread`，pids 拒绝 1、OOM 0；
256 上限下全部 150 个通过，pids 拒绝 0、OOM 0。部署资产回归先确认旧值失败，
再锁定三路线程余量及 1 GB/200% 原资源边界。Worker 单元改为 `TasksMax=256`，
不是无限放开。Linux PID 控制器计入 TID，依据
[Linux cgroup v2 PID 文档](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html#pid)。

这里复现并修复的是已确认的线程容量不足，不是完整模型调用；尚不能断言全部
历史 -6 只有这一原因。真实三品种后续自然任务必须核对实际分析成功、逐阶段回执、
耗时及资源峰值，调度 completed 回调本身不构成分析成功证明。
三视角自审：PA 交易员确认订单与判断边界不变；VPA 交易员确认知识与证据交付不变；
量化软件架构师确认容量有限、OOM/CPU/权限隔离不放宽、idle 重启不丢任务。

参考：只读连接遵循 [SQLite URI](https://www.sqlite.org/uri.html)；
无新增图表依赖，使用 [SVG polyline](https://developer.mozilla.org/en-US/docs/Web/SVG/Reference/Element/polyline)；
HTTP 取消使用 [AbortController](https://developer.mozilla.org/en-US/docs/Web/API/AbortController)。
