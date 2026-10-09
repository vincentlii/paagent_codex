# Stage 内互补子 Agent：修复验收与真实对照状态（2026-09-28）

结论：已完成本轮已知契约、交付和计量缺陷的离线修复；**尚未证明**子 Agent 比单 Agent 更快或更准。新登记的同快照四臂真实实验遇到隔离 Codex CLI 账户限流，完整 PA＋VPA 链 **0/4 完成**。保留原始失败回执，不补造速度或质量结论。生产子 Agent 保持关闭，不部署、不改交易主链。本报告不替代[上一轮负面 pilot](stage1_subagent_market_pilot_20260928.md)。

## 修复与边界

- `start_stage_challenge` 的公开嵌套资源 Schema 与 Host 允许的操作/参数同源；HTF 使用 `fact_ids`，拒绝后的启动仍占用唯一机会。Child 新规格在一个只读调用中交付全部有限 scope，逐项身份、预算和审计不变；旧单索引规格继续可读。
- 父角色保留全部 mandatory、完整 Brooks/VPA 知识、唯一 Draft 和裁决；Child 只核对一个会改变判断的局部争议，异步调查期间父处理互补工作。Stage 1/VPA 不强制有 Router 候选，Stage 2 只对真实候选委托。PA/VPA 源与角色隔离、Frozen Snapshot、Validator/Router/Risk、Direct 原交易决策语义不因子协作而放宽。
- CLI 的 `turn.completed` 视为会话累计快照。Stage 2 续接只减同线程已验证父 checkpoint，不减独立 Child；失败仍记录已观察用量下界。缺报字段、失败或非递增快照不伪装为完整计费。Host 的 VPA 引用自动绑定独立审计，不记为模型 normalization/repair。

## 验证证据

新输入由同一闭盘截止的公开 XAUUSDT 主周期 100 棒和 4h 60 棒经 Host freeze/replay 校验，四臂注册为 A1/B1/B2/A2，A 为单 Agent `off`，B 为 Stage 1 单问题 Child；其余均为完整 `pa_vpa_joint`、`gpt-6-luna/xhigh`、相同冻结输入和必读知识。研究回执在 `research/codex-luna/complementary-policy-20260928-01` 的 `registration.json`、`coverage.json`、`receipts/` 和 `metrics-final.json`；注册摘要 `27c1af5cecbe85220622f80f3b9841009997848e83ccf34e9f988f4b16f08e16`。该目录不作为可再次执行的当前源码注册：试验结束后已增加全失败非零退出保护，并补了 CLI 非递增用量下界。

| Arm | 政策 | 已完成角色 | 整链状态 | 可比较性 |
| --- | --- | --- | --- | --- |
| A1 | 单 Agent | Stage 1 首稿经 Host 验证；随后 VPA 限流 | failed | 仅单臂诊断，不是整链样本 |
| B1 | Child | Stage 1 调用限流 | failed | 无 Child 效果观测 |
| B2 | Child | Stage 1 调用限流 | failed | 无 Child 效果观测 |
| A2 | 单 Agent | Stage 1 调用限流 | failed | 无第二份单 Agent 基线 |

A1 Stage 1 Worker 用时 **437.891 秒**，记录输入 101,246（其中缓存输入 24,064）、输出 17,354、总计 118,600 tokens；必读决策包的一次证据响应为 181,351 bytes，证据服务约 **2.720 秒**。最后一次证据完成至 Draft 返回约 **422.228 秒**。这说明本次 A1 的服务端取证时间不是主要 wall；最后一段还包含模型/CLI 生成及调度，不能全部精确归为“独自思考”。A1 VPA 的限流调用及其他失败调用的实际费用覆盖不完整，不以零计。单臂也不能推断 Child 提速、质量非劣或 p95。

性能门禁仍未达成：单 A1 的 Stage 1 已超过现有整链 300 秒目标。已修的取证调用层、资源契约和用量统计不能抹去这 422 秒后段；Child 是否能通过有效并行缩短它，没有 B 臂结果可验证。不能以继续删知识、降推理强度或放松交易校验来宣称解决。

研究运行最初因历史 runner 只登记 coverage，四臂全失败仍给 Task Scheduler 成功退出码；现已改为写完 immutable coverage/review 后对任何 failed/missing 返回非零。该旧执行的原始结果不回填。一次性本地任务定义已移除；不会自动续跑或产生付费请求。

离线定向回归 **210 passed**（包括 Worker、真实 stdio MCP schema、Host Child、spool/backend、角色验证、研究协议、联合链路与两阶段集成）；Ruff 关键错误检查和 diff whitespace 检查通过。此处不是全量 CI，也不是 Linux/VPS 真实权限或真实市场质量验收。

## 三角色复核

- 软件架构师：Host 身份/预算/审计和只读能力边界闭合，单次派生与失败回执可追；新规格不破坏旧 Job。剩余阻断是实际整链、VPS 权限及异常恢复未验收。
- 量化工程师：四臂保持同快照单变量、失败纳入 ITT；累计/增量和 unknown 不混计。0/4 complete，无法计算政策速度差、p95、质量非劣或收益。
- PA/VPA 交易员：Child 仅局部反证，父仍完整检查 Brooks 背景、VPA 努力/结果与后续闭盘响应，不能代替交易判断。A1 局部 Draft 不代表 PA＋VPA 决策正确；B 臂没有可评分输出。

## 后续门禁

额度恢复后须**重新登记新 cohort/新冻结快照**，先核对隔离 CLI 账户有可用额度，再做同设置 A1/B1/B2/A2 完整链。旧失败四臂不可续接、重跑洗掉或与新源码/账户混为一个 cohort。先确认 Child 实际启动、有限取证和父子重叠，再盲评事实、反证、VPA 因果、Stage 1→2 连贯及 Trade/No Trade；只在完整性、质量非劣且有效提速同时成立时考虑扩到 VPA/Stage 2 Child。额度未恢复前维持单 Agent 正式默认与研究关闭。
