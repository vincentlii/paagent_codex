# V1 Headless 体检、修复与发布验收

日期：2026-10-09。审查基线：`e4d57e29e7a5343ee251fcbe117bc2ef54e7ba9c`。
范围：VPS Headless、全量 A 单 Agent PA＋VPA 联合链、Paper 和网页控制；不做 Windows GUI 验收。

## 结论

本轮查明并修复运行生命周期、任务身份、失败观测和 Paper 缺棒处理等确定性缺陷；
PA/VPA 专业框架、完整知识、唯一 Router/materializer/Validator/Risk 没有改写。
Fast 往返后历史快速失败的精确底层原因仍缺证据，不能宣称全部运行故障已闭合。
已获授权备份、提交、发布、重启并恢复持续 Paper；下一次自然分析是恢复证据，
不是额外付费重跑。盈利与长期稳定性必须由后续连续 Paper 观察验证。

## 一、修复清单与验收依据

| 问题 | 根因／最小修复 | 验收 |
| --- | --- | --- |
| 同步完成回调可能丢失 | submit 前预留 task ownership；拒绝时只回滚仍拥有的 reservation | 同步 callback、reject、Fast pending CAS 与真实 applied on→off→on 回归 |
| 显式联合协议与旧默认身份混用 | settings cohort 加显式 analysis_pipeline，None 保持旧身份 | task_factory 相同快照不同 pipeline 分离 |
| 单 Agent 失败丢 Worker 观察 | 所有 Codex 失败记录真实 wall time、请求模型/强度和可信分类；parse/repair 前保存已观察 usage | Worker 失败、返回后处理失败和 call ledger 回归 |
| 请求强度误报 high | requested effort 取 Frozen manifest，不从通用 request 默认值推测 | xhigh manifest 回归 |
| unknown 看似免费／零延迟 | typed actual/estimated/unknown 与 coverage；未知 prior attempts 不当免费；Host 与 Calls 分离 | projection/metrics 回归 |
| 首稿统计偏乐观 | audit review_context 存 canonical first-pass 和 role metrics；修复、回退、规范化不能记 clean | audit/projection 回归；无 DB schema migration/backfill |
| 中文等待误计 Trade | `不下单` 归 no_trade，订单只称候选，不称成交 | 中文决策和看板只读 projection 回归 |
| 开仓后缺棒仍确定计收益 | 仅新 v3 open order 检查时间连续性，缺棒 ambiguous/censored，不改 equity | long/short gap 与连续 bar 正常退出回归 |
| Linux CLI 超时／退出留 MCP | 每次独立 Popen session，清理自己的进程组；有界 drain，复用现有 tree cleanup | Mock 回归；Linux dummy 对照：旧 run timeout 留子进程，新 timeout/normal exit 均清理 |
| 更新包未 reload 的发布风险 | 安装器写入前拒绝 active 服务；发布暂停/备份/停止三服务/固定提交/重启 | 部署资产 RED→GREEN；实际发布见末节 |
| 模型解释语言不一致 | Runtime AGENTS 指定解释用简体中文，JSON keys/enums/IDs/引文不翻译 | Runtime asset parity；不为翻译改变 protected judgment |

无新增 dependency、数据库字段、数据回填、策略阈值或并行 materializer。
旧冻结任务、旧失败记录、研究结果和历史 Paper 不修改。

## 二、知识、调用和 Skills 审查

| 阶段 | 保留的质量要点 | 调用／执行边界与结论 |
| --- | --- | --- |
| Snapshot / measurements | 同源身份、闭盘 cutoff、HTF、volume provenance、事实 ID | Host 冻结；Codex 不能更新市场或决定截止点 |
| PA Stage 1 | Brooks 背景、八态、Always In、主／替代解释、反证、位置与跟进 | price-only；完整 mandatory 原文和 HTF；不由数值阈值命名 Setup |
| Router | 仅已验证枚举与 pattern keys 决定权限 | 不从自由文本路由；相关原文随 Job digest 冻结 |
| 独立 VPA | Background、Location、Effort/Result、Comparison、已闭盘 Follow-through、acceptance/rejection | 独立会话、同快照证据目录；不创造 PA Setup/方向/订单／概率 |
| 联合 Stage 2 | Setup/结构止损、TP1、Trader Equation、未校准主观先验、等待的合法性 | 只接已验收 VPA 报告，不续接 VPA/raw catalog；同快照 PA 首稿通过才合法续接 |
| repair / Host safety | 指针式修复、protected judgment、引用和确定性订单数学 | 不以 repair 换专业结论；typed resource/identity stop 不伪装 No Trade |
| Paper | 交付后才激活、expiry、单一执行路径、缺失/同棒不确定性 | v3 不倒填未来成交，旧 v1/v2 分层统计 |

六份 Runtime role Skills 与 AGENTS 已审阅；本轮没有修改 Skill 正文或删减领域原文。
五份 VPA 知识已逐份复核；PA 重点复核背景、H1/H2、突破/失败、Measured Move、
结构失效、TP1 与先验边界，29 个 PA 模块的完整原文、锚点及路由保全另由回归覆盖。
这不等于对每段教材进行逐字专业认证，也不证明每次模型解释正确。

理论、产品边界和 Host Draft 协议维持分层：产品不授权某种逆势/MTR 交易，不等于
改写 Brooks 教材；教材频率不能直接变当前品种的胜率；VPA 名称须在事实后，不先
贴 Wyckoff 标签再找依据。知识索引、section aliases 和引用范围随 Frozen Job 固定。

性能结论：保持无损合并必读交付和同快照 PA 受控续接；短初始 Prompt 不等于低总
Context/Token。没有证据支持删原文、缩减 HTF、降低强度或上线分层/子 Agent。
仍固定 `gpt-6-luna/xhigh`、A 全量、单 Agent；Direct PA-only 主协议保持兼容。

## 三、Fast 失败证据与不确定性

历史定时任务 Stage 1 在约 455–621 ms 失败，明显不是长时间模型分析。旧完整记录
只有笼统 `CodexWorkerOutputError`，最底层可信分类没保留下来；旧 Spool 结果已清理，
CLI logs 数据库没有可恢复的相应日志。不能从旧记录还原精确错误。

当前 generation 17 的 desired/applied Fast 均 true，profile generation 2，control_error
为空；最近 8 个 Job 的 service tier 与 Runtime identity 匹配，固定 config/rules 相符。
Fast 控制只影响新快照，不改旧 Frozen Job、登录或 Runtime 文件。离线合成服务的
配置解析与 required MCP initialize 成功；两次分别获批的 3 秒启动检查都未立即退出，
已清理私有进程树，无完整分析、无 Paper 提交。这些结果不证明正式服务恢复。

另有 Worker/Headless 10:31 启动而安装包 mtime 11:16 的未重新加载风险。mtime 本身
不能证明字节变化或 Fast 失败的唯一根因；固定包发布并重启用于消除这类混用风险。
发现旧 PPID=1 的 CLI/MCP 残留，但未观察 TasksMax/MemoryMax 触顶，不能归因限额。
不盲目增加权限、内存或任务上限。

## 四、三角色校验

| 角色 | 判断 | 尚未能背书的部分 |
| --- | --- | --- |
| PA 交易员 | Brooks 背景、Router、结构止损/TP1、独立先验和唯一交易链保留；无新机械 Setup/Gate | 每次输出专业合理性、长期决策效果 |
| Wyckoff VPA 交易员 | 量价是同快照证据层，背景/位置/比较/跟进与替代解释保留；不变成第二套策略 | 不同市场形态的标签准确率与增量 |
| 量化／软件架构师 | 任务身份、callback ownership、观察覆盖、owned process cleanup、censored Paper 和停服发布各归单一责任方 | 新版自然任务成功、持续恢复与统计稳定性 |

本表是主 Agent 三角色自审。只读协助审查曾尝试但额度中断，不能称独立子 Agent
完整验收，更不能把角色自审当市场盈利证明。

## 五、验证与清理

- Headless 扩大离线 Unit/Property 905 项通过，约 35 秒；包括原 PA、VPA off/shadow、
  联合、HTF、订单数学、records/audit、冻结与运行恢复。
- 最后新增 Fast 往返、失败分类及部署保护后，直接影响范围 143 项再次通过。
- Worker/Backend/challenge/audit 定向 79 项通过；看板脚本 Node 语法和关键 Ruff
  F821/F823、git diff --check 通过。
- GUI E2E 试跑遇到既有 MagicMock clone 链未配置导致 JSON serialization 失败，未改
  GUI 或其 mock；遵用户指示不继续 GUI 测试，不声明全仓测试通过。
- Linux 无模型 dummy 证实旧 runner 留活子进程，新 timeout/normal-exit 全组清理。
- 原始行情和解释保留 VPS 原位，未导出完整私有数据；不输出 auth/API key/密码。
- 本轮临时检查脚本发布后清理；不清原始记录、研究结论、登录或运行目录。

初次 VPS 成功调用约 373.96 秒、Host 377.21 秒、540882 Provider Token 只是单次
基线，不等于 p95、Fast 固定 1.5× 或新修复后真实性能。本轮不额外触发付费分析。

## 六、发布验收（待填实际结果）

用户已明确授权备份、提交、发布、重启与恢复原 Paper。调度已原子置为 paused
（generation 18），模型/Fast 保持不变；确认 scheduler running/queued/unfinished
均 0，running dispatch 0。备份服务成功生成
`/var/backups/pa-agent/pa-agent-20261009T121826Z`，`--verify` 通过。
固定发布提交、三服务 PID 和恢复状态在实际操作后补记；下一次自然完整分析尚未验收。

完成标准：固定源码与安装产物一致；备份完整；无旧任务重复；服务预检成功、
desired/applied 恢复一致；随后自然任务的角色状态、错误分类、回执和延迟可核对。
若自然任务仍失败，按新增可信分类继续定位，不提前宣称 V1“无 bug”或改交易规则。

## 参考与长期依据

- [Brooks 官方术语](https://www.brookstradingcourse.com/price-action-trading-terms-glossary/)：背景、趋势/区间、Setup 等专业概念。
- [Wyckoff 官方方法](https://www.wyckoffanalytics.com/wyckoff-method/)：量价、供需与背景下的证据解释。
- [Python subprocess](https://docs.python.org/3/library/subprocess.html)：run 的超时处理与 Popen 私有 session/process lifecycle。
- 项目 authority：[North Star](../knowledge/codex-analysis-north-star.md)、
  [交付与 Paper](phase6_delivery_paper_acceptance.md)、[联合本地/VPS验收](codex_joint_local_release_audit_20261008.md)。
