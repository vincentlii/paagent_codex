# Stage 内子 Agent 研究与验收计划（Codex，2026-09-24）

当前补充（2026-09-28）：精确资源 Schema、有限批量交付、续接/失败用量、VPA 自动引用分类及局部互补分工已离线修复，210 项定向回归通过。新同快照 A1/B1/B2/A2 四臂真实试验遭隔离 CLI 限流，完整链 0/4，不能比较提速或质量；旧负面结果不改写，生产维持关闭。详见[修复后的真实 cohort 报告](subagent_complementary_pilot_20260928.md)。下文旧规格的单索引 Child 工具与旧 pilot 状态仅属历史记录；新冻结规格使用有限批量读取。

当前状态（2026-09-28）：**已完成 Stage 1 真实冻结行情 pilot 的现有结果审阅，未通过推进门禁。四份 Stage 1 Draft 已返回；完整链 3 完成/1 中断缺失。B Stage 1 平均比 A 慢 17.9%，两次委托中仅一次完成并收集，另一次参数拒绝；质量平均 +1.3 分不能归因 child。暂停第 4～6 步付费扩展和生产晋级，先按[真实试验报告](stage1_subagent_market_pilot_20260928.md)处理接口、计量与互补分工建议。生产仍关闭，无 VPS 部署。**

历史状态：**2026-09-27 已实现 `host_stage_challenge_v1`：Host 独立 Codex 子进程、受限只读 MCP、父子独立预算/审计、异步启动/收集及失败用量接线。本地真实合成父子 canary 通过；该日真实市场逐阶段 A/A/B/B、质量/性能晋级和 Linux/VPS 验收未进行，生产仍关闭。2026-09-26 的原生 CLI 失败结论保留，旧原生非 off Job 继续 fail-closed。**
本文取代 `runtime_chain_audit_20260914.md` 中先前仅设计的子角色方案；历史测量仍以原审计为准。

## 问题、预期与不能承诺的事

研究问题是在**同一冻结 Snapshot、同一模型 `gpt-6-luna/xhigh`、同一 Brooks/VPA 知识全文、同一 Validator/Router/Risk**下，给某一 Stage 一个只读、单问题的反证挑战者，能否让父角色少遗漏关键证据、缩短完整分析的关键路径，同时不降低交易判断质量。增加 Token 消耗可以接受，但仍须完整计价；不能用“更长的分析”代替专业增量。

子 Agent 并不自动缩短父 Agent 的 100 根 K 线、HTF、Brooks/VPA 必读原文或已验收上游：现行契约要求父角色自己完整读取。调整后的子角色**可以在同一 Frozen Job 内主动读取 Host 限定的只读证据**，不再依赖父角色复制原值；但子角色读取不能抵扣父角色必读，也不能改变 PA/VPA 的隔离。它能分散一个相对独立的争议核对，并且只有父角色在子角色调查期间处理其他判断时才有并行收益。单 Stage 耗时近似为 `必读交付 + max(父角色剩余独立判断, 子角色核对) + 合并校验`；若父角色立即等待、子角色复制整份基线或父角色重做其全部调查，耗时和上下文均可能增加。此前两次同快照完整 xhigh 为约 10.9/17.9 分钟，Stage 1 首稿约 304/515 秒；这不是 p95，也不证明子 Agent 能达到整链 300 秒门槛。

初版每 Stage 最多一个 child 是**因果辨识和权限面控制**，不是 Token 节约的教条。多个 child 可能同时核对真正独立的结构、HTF、量价问题，但也可能复制父输入、相互矛盾或把合并工作移回父角色。只有单 child 的真实触发、并行时段、质量增量与失败率可观测后，才另行预注册“两个正交争议”的剂量实验；不得从本版默认推断多 child 无价值。

## 已实施的最小实验边界

- 生产 Factory、Direct、旧 Frozen Job 和默认 Codex Worker 不开放子 Agent。显式 `PA_AGENT_CODEX_SUBAGENT_RESEARCH=1` / Worker research 配置只允许新冻结的 `host_stage_challenge_v1` Job；旧原生协议、协议/预算漂移、非 `gpt-6-luna/xhigh`、非 agentic 或角色与 mandatory policy 不匹配均拒绝。正式服务保持研究开关关闭。
- 可预登记 `off`、`stage1_challenge_v1`、`vpa_challenge_v1`、`stage2_challenge_v1` 和最后的 `single_challenge_v1`（每个适用 Stage 各最多一个）；A/B 只允许这个字段不同。VPA 单阶段必须有 VPA 链路，组合臂必须是 `pa_vpa_joint`。失败/缺失保留在 ITT，不通过重跑洗掉。
- `pa_challenger.toml` 现在是 **Host 解析并冻结的子角色规格，不是 native CLI registry**；不安装到 `.codex/agents`。父工具 `start_stage_challenge(question, resources)` 启动独立任务，`get_stage_challenge(wait_seconds=0..10)` 收集。子进程只持有自己的 capability 和 `read_challenge_resource(resource_index)` 工具，不接收父 Job 路径/凭据。Host 将资源索引限定到同 Job、同角色的最多 8 项操作与参数；父完成 mandatory 后才能启动。子读取上限 8 次、单响应 64 KiB、合计 256 KiB、输出 16 KiB，使用父 Stage 剩余 deadline，取消时撤销入口并回收进程。完整基线、上游交接、repair、shell、Web、数据库和二次委托均不开放。
- 父角色只有在存在会改变诊断或决定的明确争议时才委托；交付问题、Job digest 和有限证据定位符，不复制整份 packet/知识、其他 Snapshot 或原始 VPA catalog。子角色可自行查询限定证据并给出带 ID 的核对意见。无争议不派生；Stage 2 纯等待、无真实 Router 候选不派生。父角色独占正式 Draft 和专业裁决；子意见不能代替任何父必读或 Validator。Host 原校验、唯一物化器及停止语义不变。
- 原生 CLI `spawn_agent` 只有在真实预检证明其不能继承父端点、审计可区分且总 usage 可核算时才可重新作为候选。否则研究版应由 Host 托管独立 Codex 子进程、专属只读证据 MCP 与独立审计，并在父角色可继续分析时异步执行；此执行机制需单独验收，不从当前 TOML 推断已具备。
- 不再依赖 native spawn 事件证明调用。Host 原子管理每 Stage **总计最多一个** child，审计 start 尝试/拒绝、scope digest、状态、父子起止、collect wait、实际已读原值 ID、查询/拒绝与父/子/合计 usage。父最终 Draft 失败也保存 sidecar 并经 spool/backend/record 接线；未报告计数保持 unknown，不能当免费。发现 native 协作事件仍拒绝。没有实际派生不冒充“用过子 Agent”；child 失败/未收集/拒绝属于不完整 treatment，不可筛掉后宣称效果。

## 逐阶段研究卡片与三角色否决点

| Stage / 单一子问题 | 架构师审查 | 量化工程师审查 | PA/VPA 交易员审查 | 预期有效增益与不派生条件 |
| --- | --- | --- | --- | --- |
| Stage 1：主市场解释与最强反证中的一项突破接受、leg 或位置冲突 | 父角色仍完整读取 price-only 100 棒、HTF、packet 与 Host 选中 Brooks 原文；child 独立查限定的 price-only 事实/窗口与 Brooks section，不见量价、Router/订单，不判断最终方向/Gate。检查同 Job 身份、父子端点隔离与并行。 | 用冻结 K 范围和 PA fact ID 复核比较对象、已发生/未来确认分离；分别记录父/子读取、首稿 wall、输入/缓存/推理 token、修复和重大事实错。不能删 mandatory 资料换速度。 | 主/替代假设必须有真实反证；不能把刺破等同接受、把未来跟随说成已发生，或机械套指标。若争议只需确定性算术、已无实质反证或基线不足，child 不派生。 | 可能减少漏掉最强反证与长篇重复推论；不能保证减少父必读 token。 |
| 独立 VPA：一个 Effort/Result 或事件后的闭盘序列 | VPA 任务仍独立于 PA 会话；child 独立查限定的同源量价事实/窗口与 VPA section，不能读 PA Setup 或造 Setup、订单、方向、PA 概率。父角色完成目录覆盖、最终报告。 | 每事件单独检查 provenance、严格因果基线、`later_closed_bar_count/range` 和引用窗口，核对查询未越 cutoff。按前两轮 K1/K2 误绑根因区分模型错与 Validator 词法误报，记录首稿与 repair。 | 背景支持、即时 entry timing 支持和信息不足分别判断；高量不自动吸收、低量不自动无需求；必须解释后续已闭盘响应与最强反证。无可比较成交量或只有空标签不派生。 | 可能降低序列误读与“背景支持即入场支持”的混淆；child 不做第二份完整 VPA 分析。 |
| 普通/联合 Stage 2：真实候选的位置、结构失效或 TP1 障碍 | Host 仍独占交接完整性、投影、身份、报告 catalog 与资源故障；child 仅查限定的 price-only 窗口/PA fact/Brooks section，不接收 VPA 报告或 raw VPA catalog、不产三价/胜率/最终 Draft。已验收 VPA 报告只由父角色综合，Router、Validator/Risk 不变。 | 分开报告 fresh/resume、继承上下文与实际新增 token；比较有候选案例的首稿、repair、结构三价、价格几何、完整链路 wall。不能把两次同快照 383→122 秒波动归因于优化。 | 对允许的候选与等待说明背景、位置、触发/未来触价、结构 stop、TP1 空间与 VPA 已验收反证；不能为 RR 挪 stop 或用 VPA 创建 Setup。只有合法等待、无真实候选或争议不改变决策时不派生。 | 可能减少候选遗漏和错误入场，但合并成本最高；需要含真实候选的快照，不只重复当前两次 wait。 |

三角色均有否决权。任何越权、前视、事实漂移、错误成交方向或非法订单都不可用平均得分抵消；专业盲评分开看事实与解释，文字优美度不计增益。

本次代码审查结论：架构师认可研究 arm 与生产/Direct 的隔离、冻结自定义 Agent、Host 原 Gate 保留，但把 CLI 自定义 Agent 的实际工具继承列为阻断预检；量化工程师认可逐阶段单变量对照与失败纳入 ITT，但尚无父子重叠和实际 child 使用率，不能判定时延收益；交易员认可 Stage 1 主/替代、VPA 因果、Stage 2 候选/等待的单问题边界，但真实盲评尚未进行，不能宣称质量改善。

## 顺序和实际验收

1. **离线构建审查（已实现部分）。** 对照当前分支固定 Direct/旧 Job `off` 行为，校验联合人设投影只作用 Codex 联合 Stage 2、知识 section ID 稳定、Runtime asset digest、实验 arm 仅单变量不同、Worker 默认拒绝及父/子配置。定向测试不等于模型效果。
2. **隔离权限与观测预检（原生 CLI 失败；Host 本地代码与真实合成传输已通过）。** 定向覆盖真实 child MCP 工具清单、服务端越权/预算拒绝、父 mandatory 不能由 child 抵扣、引用原值、Stage 2 重绑定与旧 token 失效、失败 usage、取消与 endpoint 回收。本地真实模型只做合成正向父子链路，未对恶意同 OS 用户做攻防；loopback capability 是工具权限边界，不是 OS 隔离。Linux 部署仍须独立只读用户、发布权限与 service cgroup 验收。
3. **Stage 1 单阶段 A/A→A/B。** 相同冻结快照和注册顺序，同模型/effort、知识/Router/Schema/父预算、双方均为 `agentic`；只改 Stage 1 child 政策。`fixed` 不允许子调查，不能作为本单变量对照。先小样本检验交付与盲评，再扩到趋势、区间、接受未明和分歧案例。无触发是合法样本，纳入政策 ITT；实际触发率另报。
4. **VPA 单阶段。** 仅 Stage 1 安全且有可能增益后实施；包含背景支持但即时证据不足、量价冲突、无成交量及相邻 K 序列。观察旧误拒修复后的真实 repair 率，不通过放松 Validator 追求速度。
5. **Stage 2 单阶段。** 必须有真实 Router 候选与不同结构风险的样本；纯等待样本用于核验不派生。先区分 fresh/resume，再评价三价与等待质量，不能只看整体成交率。
6. **组合臂。** 前三阶段分别证明可行才使用 `single_challenge_v1`。组合不等于单阶段提速相加；按真实串行 Stage 链测整链 wall、父子重叠、Token、成本、队列/Provider 波动和失败覆盖。
7. **晋级门禁。** 预登记最小有意义的整链时延降幅、可接受额外成本与质量非劣界；按独立 Snapshot、市场 regime、数据源、wait/候选分层报告 A/A、B/B、ITT 与 per-protocol。盲评至少覆盖 Context、Brooks Setup、VPA 因果、Stage 连贯、证据与结构失效。整链 p95≤300 秒、p95 tokens≤250k 是现有质量契约门槛；小 pilot 不能估计 p95 或交易收益。要在多快照稳定提速、质量非劣、重大错误为零和样本外 Paper 结果可解释后，才讨论生产启用；否则保持单 Agent 或放弃子角色方案。

## 风险与回退

- **Context 污染**：child 不能拥有长期会话；不同 Snapshot 坚决独立，同 Snapshot 仅原 PA Stage 1→Stage 2 受控续接。child 不传自己的策略结论进后续角色；Stage 2 只见已验收 VPA Report。
- **错误归因**：模型响应时延、父角色必读负载、child 等待、CLI 服务端波动和 Host 工具时间分开。Token 增加被接受不意味着无限制；仍要证明单位时延或质量有价值。
- **失败语义**：真实 child 失败不能冒充市场 No Trade，也不触发 Direct fallback。父角色凭自己的完整基线可提交有效 Draft，但 record 必须保留 child failed/cancelled/not-collected/start-rejected，政策 ITT 保留该结果，per-protocol 标为不完整。无争议不派生是合法政策样本，不证明获得过子意见。父进程失败也不能抹掉已花费的父子 Token。
- **发布边界**：无 VPS 部署、无自动下单、无 Direct 变更。移除研究环境开关或选 `off` 即不创建 child；旧冻结 Job 继续按原语义回放。任何正式升级需要另行验收与部署决定。

## 2026-09-26 原生 CLI 预检结论与下一决策

本地 `codex-cli 0.155.0-alpha.16.4`，只用合成 canary，没有输入真实行情。最初以 `gpt-6-luna/xhigh`、`--ephemeral` 运行 360 秒超时；超时前原脚本未保存事件，不能从该次推断是否派生。随后仅为**权限诊断**用同模型 `low`（不作为市场质量或性能实验条件）验证：`--ephemeral` 的 spawn 报 `no thread with id`；改为非 ephemeral 后能派生自定义 `pa_challenger`。

但该 child 的本地会话记录显示 `functions.exec` 与 `send_message` 工具调用，且能看到父 `pa_evidence` 的合成 canary。合成 canary 调用被 `approval_policy=never` 拒绝，只说明这次调用未成功，不证明只读生产 MCP 不可访问。把父配置从 CLI `--config` 移至动态 profile 后，child 仍能看到 canary。尝试项目配置时，本次临时工作区未加载出派生工具，未证明它能替代现有 Worker 配置。**因此 TOML 写着 disabled 与父 CLI 的实际合并结果不一致；不得据此宣称 child 无 MCP/shell/二次派生能力。**

CLI stdout 的 `collab_tool_call` 只出现 `tool=wait`、没有可识别 spawn；持久会话 JSONL 才记录 `spawn_agent`，以及 child 的调用和独立 token usage。现有 `_subagent_events` 无法从 stdout 完整核实派生次数、父/子重叠或总成本。父子会话另有一处副作用：非 ephemeral 才可派生，独立 VPA 的原 Worker 路径为 ephemeral。三个问题都是阻断项，不能以 A/B 的失败率或漂亮输出掩盖。

审查结论：架构师否决当前原生 CLI 子角色的权限边界；量化工程师否决缺 spawn 覆盖和独立 usage 的实验因果口径；PA/VPA 交易员认为未经边界核实的 child 意见不能进入 Stage 判断。第 2 步的 Stage 1/VPA/Stage 2/组合市场 A/B 按预注册门禁**未启动**。已从旧完整 record 仅重冻同一 XAU 闭盘行情与当前知识，供修复后的新研究注册；旧 pilot 知识已漂移，不能复用旧登记或结论。

以上是 2026-09-26 历史否决记录，不代表现在仍使用原生子角色。原生诊断脚本 `check_codex_subagent_cli.py` 已停止执行当前新规格，避免将 Host TOML 当 CLI agent 配置。当前入口是 `scripts/check_host_stage_challenge.py`，不传 `--execute` 不调用模型。

## 2026-09-27 实现验收与三角色复核

- **本地真实传输通过**：`codex-cli 0.155.0-alpha.16.4`、父子均 `gpt-6-luna/xhigh`，新合成冻结 Job，无真实行情。总 wall 39.515 秒；child 15.052 秒，collect 阻塞约 7.875 秒。父在 child 开始后、完成前做了一次独立 `get_bars`；child 用专属入口成功读取 `PA.BAR.K1`，父收集后提交合法 canary。父 total_tokens=29,495，child=8,950，合计=38,445（缓存 token 已包含在 input，不另加）；两者 usage 均有 CLI 实报 coverage。这是调用可行性，不是行情质量或提速对照。
- **可复现证据**：本地研究目录 `host-challenge-20260927-01/report.json`，Job digest `77d52daa20c17620c8a5648676fe946668a909b9046b198397e3028d7b3fa050`。报告保留查询、actor、起止和用量，不含凭据。重跑必须新输出目录、新 Frozen Job，不覆盖旧报告。
- **架构师**：同 Stage 主从异步协作已接通；父子不同进程、工具入口和审计。Stage 1→2 维持受控同会话；Stage 2-only treatment 预先使用稳定 binding，续接才激活子工具。role/context 不匹配、引用未读原值、父 mandatory 混入 child、旧 capability、native 协作均被拒绝。不是另一套 Router/Validator/交易框架。
- **量化工程师**：程序测量的 child 回执同时交付真实源 bar 值与已有 fact ID，先计字节/hash再交付；父测量 API 未改。保留 start 拒绝、所有已报告 usage 和不完整 coverage。父子 wall 重叠不求和，失败不能被清洗成干净 treatment。单次合成结果不能估 p95、成本收益或质量非劣。
- **收尾修复**：VPA child 的同次受预算回执附冻结 catalog_id/data_status/provenance/capabilities，避免用相同数值误解 real/tick/proxy 的解释上限；不增加完整目录或 PA 权限。父失败和 backend 后处理失败的已观察 usage/collaboration 经统一 failure metadata 进入 Stage 1、VPA、Stage 2 账本；不伪造 response_hash，未报告计数仍为 unknown，不以失败没有正文为由把消耗清零。
- **质量复核产物**：新的 Worker sidecar 保存有界问题/资源 scope 及已通过引用校验的子意见，供后续重放和人工核查；阶段 metrics 只携带 `review_artifact` 文件名，不复制意见全文。不保存 credential 或隐藏 reasoning；accepted_result 只表示 Schema/引用交付通过，不代表专业判断正确。上述首次 canary 报告早于此归档字段，不补写历史结果。
- **PA/VPA 交易员**：Stage 1/2 只调查 PA、VPA 只调查量价；Brooks 主框架、VPA 证据层和父专业裁决不变。四个 Stage Skill 与 AGENTS 已统一问题—证据—反证—未知—父裁决顺序。无争议不派生、纯等待不为并行而造候选；没有精简原始知识或 mandatory 资料。该判断是协议审查，真实输出盲评尚未通过。
- **剩余验收**：按第 3～7 步新登记逐阶段真实快照对照，再决定组合臂与是否晋级。没有 VPS 部署、未开启生产子协作、未声称完整行情性能达标。
- **本次最终离线验证**：217 项通过（31.14 秒），范围为 Host challenge、Worker、真实 MCP SDK、spool、Backend/失败账本、Runtime/资产、study、原 Evidence、PA/VPA Joint 与 A/A/B/B、HTF、VPA off、Router/无订单不变量及原 two-stage happy path。关键静态错误检查和 `git diff --check` 通过。不是全量 CI 或 Linux/live 发布验收。

## 2026-09-28 Stage 1 真实 pilot 决策

该更新只增加真实运行和盲评证据，不改变上述权限契约。模型仍 `gpt-6-luna/xhigh`、Brooks/VPA 原文与父 mandatory 未精简，Direct 和默认单 Agent 未改。

- A1/A2 Stage 1 为 360.219/452.687 秒；B1/B2 为 556.407/401.703 秒。政策 ITT 局部均值 406.453→479.055 秒，未达到预登记 B/A≤0.90。A/A 与 B/B 分别差 92.468/154.704 秒；不能估计 p95。
- B1 child 92.593 秒、collect 阻塞 0ms，但收集后父仍约 303.134 秒；父 Token 445,227，child 22,712。父角色新增回合与综合负担比 child 本身重要，当前较泛反证没有实现关键路径互补。
- B2 的 `get_htf_evidence` 嵌套参数把 `fact_ids` 写成 `evidence_ids`，Schema 离线重放已复现；Host 正确拒绝。资源接口目前公开通用对象，后续需与服务端同源 typed 参数，不猜别名放宽。其 Stage 2 在会话中断后无最终回执，保留 missing；不补造、重跑或修改 expiry。
- 一名独立模型三视角盲审及主审程序解盲：Stage 1 A/B 平均 76.95/78.25。最高分 B2 没有获得 child，成功协作 B1 为 75.0，未显示质量增量。竞争假设确认/证伪字段倒置及旧/近突破边界不清是实际缺口；周期合理分歧不变成代码 Gate。
- 已验证 resume CLI usage 为同 PA 会话累计，本轮仅在研究派生表扣除已验证父计数，原 runtime/record 不改。普通 VPA 引用自动绑定被计为模型 normalization 的口径漂移也列待修，不能以计量修正冒充物理提速。
- 第 3 步取得局部诊断但没有四份完整链验收；第 4～6 步暂停，第 7 步仍 no-go。下一步先离线合同/计量修正、确认互补任务设计与独立任务生命周期，再另注册新行情 cohort；不重复优化这一快照到迎合评分。

详见[试验、三角色评价、证据与未完成边界](stage1_subagent_market_pilot_20260928.md)。本段不覆盖或删除 2026-09-26/27 的历史失败与 canary 证据。
