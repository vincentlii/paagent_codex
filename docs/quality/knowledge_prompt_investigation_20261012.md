# 教材、Prompt 与 Stage 1 调查契约修订

## 范围与证据边界

本地候选基于 `codex-analysis-backend-v1` 的 `1403ff2`，工作开始时无未提交变更。
实施范围是教材适用条件、角色输出契约、Stage 1 调查流程；不涉及 GUI、部署、数据库
migration、历史结果修订、VPA 概率校准或性能优化。以下实现与验证属于本地未提交版本，
不改变 `current-status.md` 中已发布 VPS 的身份、Profile 或前向 Paper 状态。

## 1：教材的周期、单位与参照

**根因：**二次入场教材把 5m 场景的“两小时 / 24 根”写成一般失效时间；磁力位教材
混淆从 signal 极点计数与从 entry 计数，并将 E-mini 点数/tick 示例扩散到通用检查单。
跨周期、跨品种使用会改变例子的真实含义，可能锚定模型而非帮助结构判断。

**修订：**保留原文知识章节 ID、案例和反证；注明连续无缺棒的 5m 示例与真实周期
条件。二次入场仍依据调整、原逻辑和结构响应复核，市场失效与 Host 订单 expiry 分开。
E-mini S&P 500 示例明确 tick=0.25 index points，bullish signal high 为 S、假设 entry
为 E=S+t、target 为 E+G。5/9/17/41t 从 S 计数对应 entry 后 4/8/16/40t；41t
标明算术延伸。区分触价与成交，多头示例有空头镜像，跨品种使用真实 tick 与结构。

**来源：**原教材语境，Brooks 的
[Subtle Support and Resistance](https://www.brookstradingcourse.com/wp-content/uploads/2016/11/161117-Al-Brooks-TradersEXPO-Subtle-Support-and-Resistance.pdf)
和 [术语表](https://www.brookstradingcourse.com/price-action-trading-terms-glossary/)，以及
[CME E-mini FAQ](https://www.cmegroup.com/trading/equity-index/eminifaq.html)。
固定时长、点数、tick 数不作为跨资产 Gate、结构止损或成交保证。

涉及 `文件15`、`文件17`、`文件22`、`逐棒分析检查单` 与 `_reference/abbrev_glossary.md`。
未新增指标、交易阈值或知识摘要。

## 3：Provider Draft 与 PA/Joint 角色一致

**根因：**PA 通用事实规则要求模型填 `pa_evidence.fact_refs`，但它是 Host 物化字段，
Provider Draft 不允许。联合 Stage 2 的部分最终规则仍使用普通 PA Draft 名称与先验；
Direct 联合请求的 system 继承 PA-only 禁止成交量措辞，Codex persona 投影与其不一致。

**修订：**事实通过 Draft 已允许的 supporting/challenging/gate 引用字段声明，Host
继续唯一物化 canonical facts。Direct 与 Codex 联合阶段共用角色投影及输出规则，
明确 `pa_vpa_stage2_draft_v1`、已验收 VPA 报告、`joint_analysis` 和联合未校准主观先验。
不能机械加减 PA-only 先验，也不能让 VPA 独立定义概率或改写上游语义。
Direct 仍提交裸 JSON，Codex 仍使用原结果封套。

普通 PA 的系统前缀、输出规则与尾提醒保持原行为；VPA off 和 Shadow 的正式角色仍为
price-only。已有冻结 Job 读取自己的 Prompt/Skill/知识，不随当前资源重新投影。
没有修改 Draft Schema、Router、materializer、Validator、Risk 或 Trader Equation。

**取舍：**显式 Direct Joint 的 system 与 Stage 1 system 不同，因此不保证原 system
prefix 缓存命中；正确角色优先，实际缓存、延迟和用量需要另行测量。自定义 Joint
persona 沿用既有必需原文缺失时 fail-closed 的契约，不静默回退 PA-only 角色。

## 2：Skill 调查规则成为可审计流程

**根因：**Stage 1 Skill 已要求先读完整基线、事实与知识各一次，但运行时只检查总量，
不能阻止两次同类调查。工具参数失败还可能发生在 Gateway 之前，必须在 MCP 边界保留
审计，不能靠最终 Draft 的有效性证明取证合法。

**修订：**新正常 Stage 1 冻结
`stage1_investigation_policy=stage1_investigation_v1`，沿用 `bounded_context_v2`；
Packet 交付版本、attempts 口径、完整基线优先与配额。单一纯流程校验器同时用于
Gateway 读取前检查与 Worker 独立复核：成功 Packet 恰好一次、事实最多一次、知识
最多一次、可选调查合计最多两次。`get_pa_evidence`、窗口测量、价格投影共用事实配额；
知识按 Packet 索引直接批量读取，不开放搜索。所有尝试计数，denied 不得被成功 Draft
掩盖。预算停止保留 incomplete，其他流程/权限失败保留输出契约失败，均不得触发
availability fallback 或被记录为市场 No Trade。

MCP 使用锁定的官方 `mcp==2.1.1` 公开 middleware，在 SDK 参数解析之前检查既有
工具输入 Schema；直接调用入口复用此检查。输入错误也进入 Gateway 原预算和 durable
audit，仅保存空 request 与固定错误文字，不保存对端原始值。成功预校验不计数，
实际读取仍只计一次。没有增加依赖或修改旧角色的 SDK 参数处理。

全部 Host 选择的 mandatory 章节原文仍在 foundation，不消费可选调查配额。
child 的只读 scope、预算与独立 audit、pointer repair 的原限制保持不变。
未知策略版本在 CLI/MCP 启动前拒绝；历史无标记的 bounded / semantic parity Job
保持原规则，不重写 Skill、Packet、digest、原结果或失败记录。
架构与验收分别见 [ADR-0027](../architecture/ADR-0027-bounded-codex-context.md)
与 [质量契约第 21 项](acceptance.md)。

## 验证与三角色审查

教材修订通过 86 项原有 Prompt、知识、pattern 与 Router 测试，核对章节 ID 集合、
完整章节重组、正式路由知识选择及多空示例算术。角色契约先确认 PA 两项、Joint 四项
失败，再修复；139 项相关回归通过。新增测试直接核验实际 Direct standalone/prefix、
联合编排 RoleRequest、Publisher→MCP 的完整知识与 Schema，以及源资源变化后旧
冻结 baseline 的字节不变。

调查回归核验合法 fact+knowledge、同类重复读前拒绝、独立 Worker 拒绝、失败尝试、
baseline 顺序、固定调查、未知版本启动前拒绝。历史 Job 经 Builder/Store 重新发布并
校验完整 digest；真实本地 MCP→Host loopback→child 只读取证与父配额隔离通过。
fake CLI 返回成功 Draft 但 audit 有 denied 时不产生成功 outbox，并保留 usage。
参数失败同时覆盖直接 handle 与真实 SDK stdio；缺少必需字段、错误类型、额外参数
不逃逸 attempts 计数，重新正确调用同类工具仍被拒绝，Worker 不接受该 audit。

PA 与 VPA 审查确认背景、结构与 Router 所有权保持，联合报告权限与主观先验未扩张；
软件交叉审查核对最终交付、冻结兼容和所有失败入口。程序新增检查只拥有读取流程，
不读取或裁决模型市场结论。

### 最终本地验收

环境为 Windows、项目 `.venv` Python 3.11、锁定 `mcp==2.1.1`。测试临时状态限定在
本次专属目录，tiktoken 使用已存在的本机缓存；没有真实模型或交易所调用。

| 检查 | 最终结果 |
| --- | --- |
| Headless `tests/unit`、`tests/integration`、`tests/property`，`not live` | 1699 passed，9 deselected，83.85s |
| 官方 MCP stdio，合法 Packet→fact→knowledge 与错误参数分支 | 两分支通过，合法调用只计一次，拒绝审计可由 Worker 独立读取 |
| 隔离构建 wheel 与源码/知识/Runtime 资源比较 | 302 个包内文件字节一致，新调查模块与 MCP 补丁已包含 |
| wheel 解包安装位置的隔离 Python import | Headless 不加载 Qt；读取安装包知识与 Stage1 Skill，MCP 参数审计入口存在 |
| Ruff `E9,F63,F7,F82`，新增模块/契约测试的 `F,I` | 通过 |
| 原知识锚点顺序与集合 | 五文件不变，21 / 7 / 8 / 8 / 0 个 |
| 本次文档本地链接、`git diff --check` | 通过 |

最终回归命令使用 `python -B -m pytest tests/unit tests/integration tests/property`
加 `-o addopts='' -m 'not live' -k 'not panel_render_time and not demo_mode_replays_legacy_record'`
及 `-p no:cacheprovider --tb=short -o faulthandler_timeout=45 -q`，basetemp 指向本次
专属临时目录。9 deselected 是 live 用例和上述两个 GUI 函数，不是测试失败。

按用户的 headless 范围，逐个 `--ignore` 以下 GUI 文件：

- `tests/unit/`：`test_chart_fit_view.py`、`test_chart_skip_redraw.py`、
  `test_chart_widget_no_lines_when_not_trading.py`、`test_debug_widget_masks_key.py`、
  `test_decision_panel.py`、`test_future_trend_panel.py`、`test_gui_worker_lifecycle.py`、
  `test_order_opportunity.py`、`test_overlay_lines.py`、`test_support_resistance_chart.py`、
  `test_thread_lifecycle.py`、`test_token_indicator_thresholds.py`、`test_vpa_sidecar_payload.py`。
- `tests/integration/test_switch_mid_analysis.py`。

`tests/e2e` 为 Qt MainWindow smoke，不在本次范围。包含 Headless 隔离断言的 CLI、
发行元数据、production runtime 和 Web Dashboard 后端测试仍在上述回归集合中。
两项 warnings 来自已有 tvDatafeed 转义字符弃用提示，不涉及本次修改。
最终检查后已清理本轮专属测试、构建和临时产物；保留正式源码、测试与知识记录。

## 发布与回退边界

初次本地验收未 commit、发布或付费调用模型，也未改线上历史任务。2026-10-12 用户随后
明确授权 commit 并发布 VPS；发布回执在实际验证后追加。发布前仍须按既有流程
冻结可审查提交、构建目标平台 wheel、核验依赖锁与 Runtime identity；本地 wheel
smoke 不替代 Linux/VPS 验收。回退须恢复同一发布的 Python、知识和 Runtime 资产，
不能仅回退 Worker 后继续执行带新 marker 的 Job；旧 Worker 没有本次配额校验。
已经冻结的任务保留原身份并按对应版本处理，不迁移或重跑以改写历史。

本轮未验证真实模型质量改善、利润、胜率校准、token 节省或提速。
