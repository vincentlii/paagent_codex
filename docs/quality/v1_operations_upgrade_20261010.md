# V1 运行与控制面升级

## 已批准范围与完成标准

用户批准实施会话生命周期、黄金数据背景、多品种受控并行、看板交易倾向四项；交易位置只通过持续 Paper 观察校准，不新增距离 Gate。沿用现有模块，默认单黄金，不自动增加付费分析、提交或发布。

1. 会话：记忆关闭；只在 Worker idle 时清理超过 14 天、无待执行引用的专属服务会话，调用官方 UUID delete，不直接操作 Codex 数据库；保留认证、正式 record 与恢复任务。
2. 黄金：冻结产品身份、单市场证据范围及常规/周末参考时段。美国节假日日历没有权威实时来源时标记未知，不猜测指数模式，不据此设置交易 Gate；旧快照不回填。
3. 并行：XAUUSDT/BTCUSDT/ETHUSDT 白名单；默认黄金与并发 1，可在 idle boundary 配置并发 1/2/3；保留同品种 FIFO、独立可变调用对象、固定快照、统一 Paper 风险。退出分析的品种若仍有 Paper 仓位，继续行情观察，不中断回放。
4. 倾向：看板提供 conservative/balanced/aggressive，只影响 Stage 2；旧配置和旧冻结 Profile 指纹继续可验证。全量 A、联合 Stage 2、VPA/HTF 与安全规则不变。

## 执行及验收记录

- 基线：`4a64ada`，`codex-analysis-backend-v1`，干净工作区，已有独立 worktree。
- 三角色预审：PA 保留结构判断及唯一 Router；VPA 只补数据来源/时间背景；量化架构复用既有控制、调度与执行模块，不增加依赖或修改账本 Schema。
- 验证：先定向失败回归，后验证修复与相邻 Headless 范围；不跑 GUI 或真实模型。
- 实现状态：本地代码及离线验收完成；未提交、未部署、未新增模型调用或自动启用资产。

## 接口与兼容选择

Profile 与 Headless/Frozen task 共用白名单和指纹。新增默认字段不改变旧 Profile hash；非默认范围/倾向进入新指纹。会话清理与分析通过 Worker idle 容量边界隔离。数据背景只增加已知来源事实，不新增策略判断或硬性阻断。

## 实际实现与三角色复核

| 项目 | 实现与边界 |
| --- | --- |
| 会话保留 | `session_retention.py` 只识别服务专属 runtime/pa-sessions 的 UUID rollout；超过 14 天、无 workspace/dispatch，才用官方 CLI delete。每小时空闲检查，最多 8 次/3 秒；保留 auth、正式记录与恢复任务。workspace 删除从每次角色启动移到空闲维护，避免并行时删除别人的会话。 |
| 黄金事实 | `market_context.py` 按已确认最新收盘冻结纽约 DST 周规则，官方变更前保持历史未知；假日未知不推断实际指数模式。来源与单合约量证据边界进入 compact snapshot、Publisher 和 VPA provenance，不删知识或增加交易 Gate。 |
| 控制面 | `AnalysisProfile` 白名单、旧单 symbol 兼容与默认 hash 保持；Dashboard 增加勾选、并发和倾向。Headless 排空后重建调度；跨品种并行、同品种 FIFO、独立源/client/orchestrator 继续使用原链。非原生旧监控不静默换源。 |
| 持仓观察 | Coordinator 从原账本读取正式账户 pending/open market key；未启用分析的白名单品种仍观察已有订单，但不取 HTF、读取经验或调用模型。重启后仍从账本恢复观察；不改 Schema/风险/收益。 |
| 倾向 | 复用已有 Stage 2 protocol 的 stance guidance；原 Stage 1、Router、结构位、Trader Equation、Risk 与全量 A 单 Agent 联合链不变。Direct 也使用该任务冻结 Profile；旧任务缺 stance 时保留配置基线，不静默采用新的选择。 |

三角色是同一实现者的三个自审视角，不是三位独立审计者；本轮未使用 subagent。

- PA 交易员：通过。只扩展运行选择；止损/目标仍从 Brooks 结构解释产生，激进不强制交易，未新增距离/ATR/胜率 Gate。
- Wyckoff VPA 交易员：通过。单市场量及黄金时段限制是证据适用范围，不是假日预测、全球参与者推断或第二套 Setup。Unknown 不补造，完整必读理论/案例/反证不变。
- 量化软件架构师：通过本地范围。默认 hash、冻结序列化、重启恢复、Paper 活跃市场查询和 idle 切换有回归覆盖；SQLite `BEGIN IMMEDIATE` 的原组合风险事务继续共享。无新依赖、数据库迁移或额外分析调用。真实 Linux 并发额度/峰值内存和原生删除仍须发布后观察。

## 验证证据

基线为 `4a64ada` + 本轮未提交工作区。先加入回归确认缺失实现失败，再接线验证。
受影响范围共 **323 passed，33.31 秒**：会话、黄金、Profile、Headless、调度、
Worker、Dashboard HTTP、冻结恢复、PA/VPA Joint、旧 VPA 模式、A/A/B/B、HTF、
订单数学、Paper、records 路径及两阶段集成/property。未测 Qt GUI，未访问真实模型。
关键 Ruff `E9,F63,F7,F82` 通过；登录页/看板两段脚本 `node --check` 通过。
本轮临时测试数据清理，不删除任何生产会话、账本或原始 record。

末端补验：CLI 生命周期/Publisher/数据源/任务工厂 **65 passed，2.97 秒**；
实际 Stage 1 packet 与 Stage 2 baseline **29 passed，2.74 秒**；未知会话格式与
spool 维护 **17 passed，1.74 秒**。这些范围有重叠，不相加为独立质量样本。
实际交付检查曾发现 market context 只保存于 core、遗漏专用 Stage 1 packet；
定向回归确认失败后，补到该包已有编译路径，旧无该字段的包保持原组成/digest。
不靠增加 Prompt 规则、必读次数或额外调查调用修复交付遗漏。

VPS 只读核验确认 CLI 0.162.0 支持 `delete <UUID> --force`，专属 memories 关闭、
目录为空；没有执行线上删除、付费 canary 或新版安装。这不是模型质量或提速实验。

### 并发 3 发布前复核

用户随后授权固定提交与 VPS 发布，当前配置改为 3；代码默认 1、原资产勾选不变。
一个只读 subagent 协助对既有升级做架构/PA/VPA 复核，发现控制面仍按旧单资产状态
限制 `Run Once`，并过滤新增资产标记。已改为核对启用范围与完整 applied Profile，
继续要求所有资产 idle、暂停、generation/Fast 一致且一次最多提交一个任务；
资产投影只保留启停标记与黄金产品标签，不透传任意市场背景对象。

定向回归 **194 passed，27.44 秒**，覆盖 Profile 1/2/3、旧指纹、Headless、
HTTP 控制、Run Once、调度/Worker、会话、黄金、冻结交付及 Paper；Ruff 关键检查、
两段看板脚本语法及 diff whitespace 检查通过。前一组出现一次 Windows HTTP
断连，隔离复核与完整定向组随后通过；没有为此改动生产连接处理或放宽安全边界。
三角色复核没有要求改变 Router、知识/Skills、原组合风险事务或交易数学。

VPS 发布前已确认暂停/排空，Worker 非密容量原为 1；新一致性备份验证通过，
三库 `quick_check=ok`。既有 Worker 历史峰值约 255 MB，不等于三任务峰值证据。
后续核对安装摘要、服务预检、desired/applied 并发 3 和恢复调度；不强制付费验收。

## 发布后的最短核验

1. 用户授权固定提交发布后，暂停并等待 Host/Worker idle；验证原备份，停止三个
   服务后安装，再核对 source/installed 与 Runtime identity。不覆盖认证/账本。
2. 2026-10-10 用户明确授权提交发布，并将当前 VPS 的调度与 Worker 容量设为 3。
   代码默认仍为 1，保留原黄金、当前已登录模型/Fast/全量 A 联合配置；核对新
   Profile desired/applied、无重复恢复任务，以及自然整点分析/Paper 回执。
3. BTC/ETH 仍须用户通过看板显式启用；之后观察同资产串行、跨资产并行、每任务
   identity、额度限流、队列/内存/延迟。既有 Worker 1 GB 内存限制不放开，真实
   三任务资源峰值未经付费并发实测；当前只启用黄金也不会使单品种链提速。
4. idle 时核对 Worker 保留计数；只有实际超过 14 天、无恢复引用的服务会话可删除。
   正式 records/账本始终保留；不为验证人为老化或批量删除近期会话。
5. 交易位置、未成交与成本后收益继续由前向 Paper 分层校准；不以本次工程回归
   或更多交易数量宣称盈利改善，不将 B 分层/子 Agent 自动晋级。

## 第一手依据

- [Codex CLI reference](https://learn.chatgpt.com/docs/cli/reference)：ephemeral 与 UUID delete。
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)：记忆配置；当前实际执行仍强制 `features.memories=false`。
- [Binance 2026-09-15 指数规则公告](https://www.binance.com/en/support/announcement/detail/f69c05ebdba7422e9c614685abe84dc5)：纽约周时段、非交易时段 EWMA 与公共假日边界。
- [原 PA Agent 使用说明](https://github.com/rosemarycox5334-debug/PA_Agent/blob/main/PA_Agent使用文档.md)：交易倾向只作用于 Stage 2；本项目复用已优化指引，不恢复强制下单或机械扩大止损行为。
