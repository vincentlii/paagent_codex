# 阶段 7：Luna/max 真实工程实验补充

更新：2026-09-14。pilot-03 运行基线 `5067a26`；结束后另修复停止状态身份比较。结论：真实工程试跑已执行并揭示适配缺陷，尚无成功完整分析；不得宣称调查增量、非劣、降本或交易增益。

## 输入与实验边界

- 单资产、单来源：Binance USD-M `XAUUSDT`，1h 主周期 100 根已闭盘 K 线，4h HTF；包含冻结 VPA catalog 和完整授权知识资源。
- 冻结 as-of：`1789058015684` ms；快照 hash：`231e5fc40656507ff08aed916b09414ee7917dfce2961c2d3aff1980b74c1eb9`。
- 模型固定 `gpt-5.6-luna`，推理 `max`。两个条件均 `codex_agent / pa_vpa_joint`，只比较 fixed 与 agentic，不能据此比较 DeepSeek 与 GPT。
- 每批调用前登记四个独立臂，实际顺序 BAAB。一个冻结市场样本，不是四个独立样本；跨版本复核也不增加独立样本数。
- Windows 独立 CODEX_HOME 完成登录，使用生产 Spool/Worker/Orchestrator。未部署 Linux，未启用 Paper、监控或真实交易。
- 原 Windows 旧记录缺失严格 as-of/HTF 证据，未猜测补齐；新 `study_capture` 入口复用现有行情与快照校验器，仅捕获输入，不调用模型。

## 已执行结果

| 批次 | 登记摘要 | 完成 / 失败 / 缺失 | 已定位原因 |
| --- | --- | --- | --- |
| pilot-01 | `7d389ec1e04b1c6446c4ae6240f5b904a9133c7d9776bcbb3edb5bd725ee745f` | 0 / 4 / 0 | 本机未安装项目已锁定的 MCP optional dependency；MCP 握手前退出。 |
| pilot-02 | `b37f25372e497bebcc70076510e4b5c7095a4812d55ae5d6fd3bfea03fad08ea` | 0 / 4 / 0 | Codex 服务拒绝输出 Schema 中的 `uniqueItems`；Stage 1 生成前失败。 |
| pilot-03 | `e50af3aff2b017b4a2ef2e05ac51e243d8f9c37b9875e04ca2a39773e1abdb8e` | 0 / 2 / 2 | 真实 MCP 读取成功；前两臂约 240 秒在 Stage 1 失败，与角色时间预算一致。停止剩余调用以避免相同配置的冗余消耗。 |

失败回执、原始冻结输入及研究记录保留，不替换、不洗掉失败、不倒填收益。两批不能合并成八个独立行情样本。

额外诊断不计入四臂结果：Schema 修复后 CLI 返回 0，但模型返回合法 `incomplete`，说明只读 Evidence 调用要求审批、无法读取 mandatory 数据。CLI 退出成功不等于分析成功。2026-09-13 再读取旧 Job 时因过期被拒绝，未更改 expiry 或冻结身份。

## 根因修复

1. 依赖：按 `uv.lock` 安装 `desktop + vps + dev` extras；不新增依赖，不删 Windows GUI 依赖。研究运行使用已同步环境，避免无 extras 的再次同步移除 MCP。
2. 错误分类：CLI 新消息 `required MCP servers failed to initialize` 纳入 availability；仍不让 Schema、事实、权限或专业判断失败触发 fallback。
3. Schema：共享 generation projection 移除 `uniqueItems`，本地原始 Schema 继续验证唯一性；同名业务属性不误删，其他约束不因单次错误放宽。参照 [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) 的受限 Schema 原则，具体拒绝由真实错误确认。
4. MCP SDK：旧定义有只读 annotations，但 SDK decorator 未携带，实际 `tools/list` 缺失。所有冻结 Evidence 工具现在复用原只读、非破坏、封闭世界标记；未更改沙箱和 `approval_policy=never`，未设置全局无条件放行。参照 [Codex MCP 配置](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。真实审批问题是否完全消失仍待新 Job 验证。
5. MCP 反馈：只将受控权限/预算拒绝转为 SDK `ToolError`，保留可执行的停止说明；非预期异常仍由 SDK 隐藏。测试客户端补足标准 initialize、capabilities、clientInfo 和 initialized 通知，参照 [MCP 生命周期](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle)。

这些是接入层缺陷，不是 PA/VPA 理论、Router 或交易数学的缺陷；没有通过修改知识或交易结论来让样本通过。

### pilot-03 的观测与最小修复

- 工具审计直接确认 `get_job_manifest`、`get_market_core`、`read_knowledge_module`、`get_htf_evidence`、`get_price_frame` 已返回 ok；之前的审批阻塞已解除。只读元数据修复已在真实 CLI 验证，但读取成功不等于模型正确理解。
- 前两臂分别为 agentic B1、fixed A1，均未交付完整 Stage 1。原有错误摘要未保存原始 CLI 终止细节；约 240 秒耗时与配置时间预算一致，不能据此断言唯一延迟来源是模型推理、MCP 或网络。
- 发现并用离线回归复现：Spool 成功结果按不可变 Runtime 资产比较，typed stop 却比较整个字典，导致 Job 的 profile/investigation selection 被误认为资产漂移。停止分支现复用既有 `_runtime_identity_matches`；request/job/acceptance identity 及实际资产漂移仍严格拒绝。不改变预算，也不将 incomplete 转为 No Trade 或 fallback。
- 45 项定向检查通过（Spool、Codex Backend、联合 Runtime、Study Host），包含 fixed/agentic 正常 stop、资产漂移拒绝和 acceptance key 拒绝；关键静态检查通过。未重复跑全部 239 项，未增加真实模型调用。
- 按用户减少冗余动作的要求，在前两臂相同预算失败后停止自有 pilot 进程树。A2 已开始但无终态回执，B2 未开始，两者保持 missing。中断说明及派发/Evidence 临时审计保存在研究目录；派发移出 inbox，避免 Worker 重启自动重试。未改写原回执或 frozen identity。
- 本地没有完整专业输出，三角色只能验收工程边界；模型准确性和速度优化仍待 VPS 工程样本。不能把上述修复标为四臂成功。

## 验证和三角色自审

239 项本地定向检查通过；其中真实 SDK 子进程验证完整工具列表、全部只读标记、冻结事实读取与越权拒绝。不是模拟 MCP 握手。关键 Ruff 与 diff 检查通过。

| 视角 | 判断 |
| --- | --- |
| 软件架构师 | 继续使用单一 Host/Router/Validator；修复 SDK 边界而不放开 shell/网络/代码修改权限。Windows 验证不替代 Linux 隔离验收。 |
| 量化工程师 | 两批失败留在覆盖率分母；缺失 usage 不记为零成本。单快照无统计效力，不能盲评不存在的完整结果或编造校准数据。 |
| PA / Wyckoff VPA 交易员 | 未删 mandatory、HTF、反证或案例；没有真实完整输出，尚不能评价专业分析质量。模型无法取证时停为 incomplete 而非编造 No Trade，是正确失败边界。 |

以上为同一实现者的三个视角，不是独立专家盲审。

## 后续执行顺序与当前阻塞

2026-09-14 用户更新要求：以下“先进入阶段 9—11”的旧安排已被替代。当前先完成
[真实运行链路审计](runtime_chain_audit_20260914.md) 的缺陷修复与完整分析验收，再推进阶段 9；
持续前向 Paper 仍在 VPS。旧失败和中断证据保留。

pilot-03 曾因缺少具体数据外发授权被整体拒绝。用户随后明确授权发送冻结行情、PA/VPA 知识及分析上下文至 OpenAI Codex，并要求减少本地冗余实验，持续模拟盘在 Linux VPS 进行。该批已登记、执行并按上述原因中止；无待授权阻塞，不发送凭据、数据库或项目源码。

1. 数据外发授权已取得；本地范围限 Luna/max 工程验证，保留失败，不为取得漂亮结果反复重跑。
2. 不继续本地相同配置四臂重跑；按计划完成阶段 9—11 的本地发布准备，期间保留本次失败证据。历史输入不冒充当前可执行行情，不重写 as-of。
3. VPS 上先做单任务贯通，核对 required reads、Schema/Validator、完整 Stage 1→VPA→Router/Stage 2 交付、角色/全链预算和失败指标。贯通后再开启独立持续模拟盘；未产生完整结果不得填盲评分。
4. VPS 持续收集足量前向、跨市场背景样本，完成盲评、重复臂稳定性、完整用量和延迟评估；到这一步之前不做效果晋级。新的研究使用新登记，不补跑已中断登记洗掉 missing。

本机研究产物位于用户指定的研究目录（当前 `D:\new_paagent\research\codex-luna`）；凭据仅在其独立 `home/auth.json`，不进入 Git、评审包或报告内容。Linux VPS 未改动。

### 本地交付与 VPS 持续研究分开

本地先完成阻碍执行的缺陷修复、必要定向回归及阶段 9—11 的发布准备，不等待统计显著性或模拟盘收益。VPS 在独立模拟盘环境持续采样，保留 assignment、交付时间、失败与 outcome 分层；工程可运行不等于实盘获准。不能以赶上线为由跳过数据身份、订单数学、必读证据或失败状态检查。
