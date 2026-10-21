# 可选固定盈亏比模式本地验收（2026-10-21）

## 范围与状态

用户授权实现 PA / fixed_rr 两种退出模式及 1、1.5、2、2.5 倍选择；桌面 Qt GUI
不在范围，网页控制面仍在范围。设计依据为
[ADR-0027](../architecture/ADR-0027-selectable-fixed-risk-reward.md)。

本轮没有 commit、推送、VPS 发布或线上配置变更，没有调用真实模型、提交模拟盘订单
或修改历史。默认仍为 PA；固定模式初始倍数为 1.5。

## 行为与权限

- 固定模式由模型确定入场、结构止损、Setup、等待/交易及针对固定目标的未校准主观概率；
  Draft 不输出 TP1/TP2。唯一 materializer 根据 Entry、SL、倍数及真实冻结 tick 计算 TP1，
  向入场方向取整，再运行原 Trader Equation、Validator 与执行处置。
- Stage 1、Brooks Router 和独立 VPA 权限不变；不增加模型调用阶段，也不减少 mandatory 知识。
  Direct、Codex、repair 与冷回退共享同一策略和 Schema 身份。
- 模式/倍数通过既有 Profile CAS 和 idle apply 生效，只作用于新任务。真实 tick 来自 Demo
  Worker 现有合约投影，需新鲜且完整；冻结到任务和 cohort，市场 Snapshot hash 不变。
- 无 tick 时拒绝可执行候选，保留合法 No Trade。当前合约 tick 与冻结值不符时拒绝新下单，
  不偷偷改价。记录与 Intent 绑定原策略，流水显示逐计划模式、请求倍数与实际价格 RR。
- 旧 Profile、冻结任务及 Intent 保留 PA 语义与原摘要。已有挂单、持仓和保护不因切换重算。
  无数据库 migration、历史回填或新增依赖。

## 验证

| 检查 | 结果 |
| --- | --- |
| 固定策略及冻结任务聚焦回归 | 31 passed（固定策略 23 项，冻结任务 8 项） |
| 完整非 live Headless 回归 | 2009 passed / 1 本机连接中断 / 7 live deselected；相关登录组独立复测 9 passed，详见回执 |
| 关键静态错误检查 | `ruff check pa_agent tools tests --select E9,F63,F7,F82 --no-cache` 通过 |
| 差异空白检查 | `git -c core.safecrlf=false diff --check` 通过 |
| 离线 wheel | 构建通过；319 个包内成员与源码逐字节一致 |
| 安装资源 smoke | 从解包后的 wheel 导入 Headless/Dashboard，加载 Runtime Skills 和固定目标 Schema 通过；未加载 Qt |
| 网页本地验收 | 主面板/交易页自动化覆盖，独立本地 mock 页面手动切换和保存通过；未修改线上面板 |

聚焦覆盖多空四档、tick 取整、实际目标进入方程、拒绝模型 TP、缺 tick、旧指纹与 Intent
摘要、不同策略 cohort、codec、Direct/Codex Joint 和普通 PA/Shadow、repair、冷回退、
HTTP CAS、旧 Host 防护、Headless idle apply、Demo 原价保护及 tick 变更拒绝。

Headless 回归覆盖 `tests/unit tests/integration tests/property -m 'not live'`，排除以下
用户明确不要求的桌面 GUI 文件：`test_chart_fit_view.py`、
`test_chart_widget_no_lines_when_not_trading.py`、`test_chart_skip_redraw.py`、
`test_debug_widget_masks_key.py`、`test_decision_panel.py`、`test_future_trend_panel.py`、
`test_gui_worker_lifecycle.py`、`test_token_indicator_thresholds.py`、
`test_next_bar_prediction.py`、`test_switch_mid_analysis.py`、`test_next_bar_prediction_perf.py`。
使用独立仓库内 `--basetemp`、`-p no:cacheprovider`、`-o addopts=''` 和已缓存 tokenizer 词表；
测试进程获本机 socket 权限以运行 HTTP mock，不访问真实交易所或模型。

完整回归曾暴露旧冻结任务测试的时间依赖：新建任务的摘要合法包含创建/过期时间，
跨秒生成不同任务不能假定摘要相同。测试现用显式固定 clock 验证确定性，并对同一已发布
任务核验读回与篡改检测；生产时间与摘要算法未改。早期失败保留，不冒充最终通过。

## 三视角审查与边界

一名独立审阅者完成 PA、Wyckoff VPA 和量化软件三个视角复核，未发现确定阻断。
PA 的结构止损与交易判断保留；VPA 未获得新交易权限；程序新增的责任限于用户指定的
目标数学、冻结身份与执行安全。审阅过程中发现的 Direct No Trade 输出契约矛盾已修正。

离线通过不证明策略更赚钱，也不证明真实模型必然正确理解新协议。本轮没有新的真实
模型链或 OKX 下单验收。固定倍数是计划价格距离；实际成交、手续费和资金费会改变净 RR。
网页截图仅为本地 mock 验收画面。

未来发布需协调 Host、Codex Worker、Dashboard、Demo Worker 版本并排空模型队列；
不得跨 Runtime 资产版本续接会话。存在任意新版活动 Intent（含 PA 模式的策略 null 字段）时，
旧 Worker 不能直接接管；旧摘要兼容不代表旧 reader 可以读取新版存储。
回退应遵循 ADR-0027 的排空/恢复边界。

## 回归回执与产物

最终完整回归：`2009 passed, 1 failed, 7 deselected, 2 warnings in 132.28s`。
失败项是既有 `test_browser_login_rejects_invalid_or_cross_site_requests` 的 control header
拒绝用例，`HTTPConnection.getresponse()` 遇到 Windows `ConnectionAbortedError: WinError 10053`。
该路径在读取 body 前按原安全逻辑返回 403，本轮未修改。相同登录拒绝组原输入独立复核
`9 passed in 5.34s`。此处未确认具体系统连接中断原因，不把复测写成整次全量零失败，
也未为掩盖它放松安全断言或添加自动重试。本轮功能测试、冻结任务及共享辅助函数调用
在最终完整回归均通过。两条 warning 来自既有 tvDatafeed 的 invalid escape sequence。

wheel SHA256：`56bac78f4a4503934f8e40ec3401fde41eedac622ccfd6a63fd6565c1cc35419`。

本地保留产物位于忽略目录 `docs/changelog/backups/20261021-fixed-rr/`：构建/回归日志、
wheel 与 `local-fixed-rr.png`。临时测试目录、预览服务、一次性辅助脚本与缓存已清理。
原有 ICT 相关四项工作区改动保持不动。

## 发布前复核

2026-10-21 用户进一步授权 commit 并发布。固定策略、冻结任务、阶段权限、Dashboard
及交易页重新聚焦回归 `100 passed in 37.86s`；关键静态检查与差异检查通过。
独立只读审查未发现确定代码阻断，并补充了新版 PA/null Intent 的回退边界。
此前全量中的连接失败及其独立复核保留原口径，不改写成全量零失败。

VPS 原源码为 `21d7db4`，四服务 active、NRestarts=0。发布前保存原配置并暂停调度
55→56；Host queued/running/unfinished=0，Codex inbox 空，无恢复阻塞。
旧版 Demo 此时已为 `blocked/reconciliation_gap`，原因投影为 `fills=false`，其他覆盖为真，
活动计划/持仓/外部单为0。这是安装前状态，不能归因本次固定盈亏比改动。
发布过程保留执行安全拒绝；发布后状态与固定提交回执另行追加。
