# 真实运行链路审计（进行中）

状态：阶段 8 **尚未通过真实运行验收**。阶段 9 暂停，先解决完整分析交付问题。
本轮不使用 subagent，只做可定位缺陷的离线回归和单任务工程诊断；没有部署 VPS。

## 证据与口径

### 2026-09-21：修复模型工具传输与分析权限混淆

9 月 20 日 Luna/max 在 Stage 1 的 25.047 秒停止不是有效分析：CLI 的
`item.error` 指出 Code Mode host 被禁用，模型随后报告无法读取必读工具。
Luna 的实际模型目录声明 `tool_mode=code_mode_only`；此前新分析路径关闭
Code Mode host，并要求直接调用 MCP，造成模型接口与执行配置相互矛盾。
旧合成探针只使用 DeepSeek 目录，不能证明 Luna 工具通道可用。

修复在 Worker 传输层完成：OpenAI 使用受限 Code Mode，DeepSeek 保留直接 MCP；
分析权限仍由同一个 FrozenEvidenceGateway、角色工具清单及预算决定。
完整 AGENTS、选定 Skill、知识和证据不压缩，不改 Router/Validator/Risk。
同时显式关闭 `agents.enabled`、`multi_agent_v2`，从 Code Mode 嵌套工具清单
排除 `functions`/`clock`，并移除继承的父桌面会话和 App bridge 身份环境变量。
不能把“关闭 code-mode”当作只读权限边界；也不能只靠旧 multi_agent 开关判断
新 CLI 是否暴露协作工具。read-only sandbox 与 never 审批策略继续保留。

验证：

- 51 项 Worker/执行契约/基线/Provider 定向回归通过；追加运行资产和联合 Host
  链路 24 项通过；本次代码 Ruff F/E9、git diff --check 通过。
- 当前真实 CLI + localhost 合成 Responses：Luna 两次请求、DeepSeek 三次请求，
  完整 AGENTS 与 Gateway 基线逐内容相等，策略单份、证据仅工具返回后出现。
  Luna 嵌套工具集合精确等于该角色 Gateway 工具集合；外层仅 exec/wait 和两种
  request_user_input 控制工具。后者不是交易数据访问或文件权限。
- 探针适配 Responses-lite 在 input 中携带的完整指令及 additional_tools namespace，
  不再错误地只检查传统顶层 instructions/tools。未修改模型目录来伪造通过。
- 上述是传输与边界证据，不是模型分析质量证据。历史“所有新角色直接 MCP、关闭
  Code Mode 三开关”的实现说明已由本节取代。DeepSeek 原直接 API Backend 未改。

三角色自审：架构上分离传输与授权，不引入第二套权限引擎；量化上冻结输入、保留
生产预算，不用合成 usage 证明提速；交易上保留完整 PA/VPA/HTF 与原校验链，不把
CLI exit 0 当作交易分析完成。阶段 8 仍为 no-go，真实运行结果另行记录。

#### 本次 fresh Luna/max 生产预算验收：未通过

研究记录目录名 `single-trace-20260921-000748`。同一冻结历史 XAU 输入，
完整 PA/VPA/HTF 配置，没有复用旧 Stage 1，没有改预算或降低思考强度。
Stage 1 实际 239.703 秒触及本次剩余预算 239.602 秒，Host 持久化
`incomplete / wall-clock budget exceeded`；没有 Draft、VPA 或 Stage 2。
因此不能评价该轮最终交易判断，也不能称全链路通过。

真实工具审计确认两次 MCP 调用：第一次合并交付全部必读基线，第二次
`get_pa_evidence` 成功；11 条资源审计处理耗时合计约 0.119 秒。
没有此前“Code Mode host disabled”错误；只记录了开发中功能提示，未隐藏提示。
工具交付恢复，但模型读取证据后仍未在预算内输出。数据不支持把剩余时间全归于
模型计算：CLI/网络/服务端等待尚未分别计时；同样不支持继续怪罪 Gateway 读取速度。
没有 turn.completed，Token/费用未知，不能记为零。未追加付费重试、扩大时限或部署。

剩余观察：控制台泛化为 `invalid_request / output rejected before PA validation`，
真实结构化 role_stop 保留超时原因。这是输出停止类别过粗，不是 JSON 已经生成后
校验失败；后续指标应优先解释 role_stop，不能据该泛化日志继续改 Schema。
下一步若获准继续实验，应先建立模型/服务端时延可观测性或做有明确假设的模型对照；
禁止凭本轮结果删减专业知识，或宣称 Luna/max 已达到性能要求。

后续已修复该分类缺陷：`role_stopped` 成为 Backend-neutral、非重试的独立 failure kind；
`CodexWorkerStopped` 不再继承输出错误。Worker 超时停止、Evidence 预算停止、周期预算停止
和 Direct 合法停止均沿同一语义进入 `AnalysisRecord.role_stop`，不触发 availability fallback，
调用账本记录 `role_stopped` 而不是 `request_failed`。非法停止封套仍是 `invalid_request`。
覆盖 Worker、Spool、Backend、Direct/Codex 联合链、运行契约和指标的 186 项本地回归通过。
Luna 与 DeepSeek 的真实 CLI→localhost 协议检查再次通过；专用运行配置与发布资产逐字一致。
这些验证不含付费模型推理，不能改变上述真实性能 no-go 结论。

#### 2026-09-21 延长预算的单次 fresh Luna/max 诊断

按用户授权只运行一次；研究记录目录名 `single-trace-20260921-124128`。正式默认预算
未修改，本次诊断临时采用单角色 900 秒、全链 2400 秒，仍使用同一冻结历史 XAU
快照、完整 PA/VPA/HTF、`gpt-5.6-luna/max`、四个独立 ephemeral 角色调用；没有部署或下单。

| 调用 | wall time | 输入 Token | 缓存输入 | 输出 Token | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| Stage 1 | 431.406s | 199,997 | 110,592 | 23,209 | 原 Validator 通过 |
| VPA Evidence | 607.954s | 49,431 | 5,888 | 19,253 | 原 Validator 通过 |
| Stage 2 首稿 | 561.391s | 420,334 | 280,576 | 15,715 | 事实绑定失败，进入一次定向修复 |
| Stage 2 修复 | 58.547s | 24,665 | 10,752 | 2,778 | 同一事实绑定仍失败 |

Host 到持久化前 1662.375 秒；四次 Backend wall 合计 1662.094 秒，总 Token 755,382，
其中输入 694,427、输出 60,955。44 条 Evidence 资源事件合计 0.601 秒、7 次 MCP 调用，
队列合计 1.950 秒；因此本轮慢速不能归因于 Gateway 或 spool 排队。CLI 没有超时，
解除 240 秒预算确实能让三个角色返回，但总耗时与 Token 均不具备上线性能。

Stage 1 判断近期为空头 Spike 向暂停/过渡阶段发展，`gate_result=proceed`；VPA 支持近期
空头背景，但因不存在 PA Entry Attempt，入场时机为 `insufficient_data`。Stage 2 首稿选择
`wait / 不下单`，没有三价和交易置信度，交易方向逻辑总体保守。其第二个 watch point 把
“K3/K2/K4 高点 4379.78 至 4382.86”写成聚合绑定；Validator 首次指出 K4.high 4379.78
与冻结 4382.86 不符。定向修复将文字改为“K3/K2 高点 4379.78 至 K4 高点 4382.86”，
虽修正 K4，仍把 K2.high 错写为 4379.78（冻结值 4380.13），故再次拒绝。最终状态
`incomplete`，Stage 2 不进入正式决策、Risk 或订单链。

结论：原 240 秒预算是一个真实截断点，但不是唯一问题；Luna/max 当前是“能够返回，
但极慢且修复后仍有精确事实绑定错误”。不能通过永久放宽预算宣布阶段 8 通过。下一步
性能优化应以 1662.375 秒 / 755,382 Token 为基线；效果优化应针对多 bar 聚合文字的
事实绑定和修复反馈可执行性，同时保留原 Validator，不能用程序猜测修改模型语义。

### 2026-09-18：第四步——确认后才做的无损交付去重

已完成本地真实 CLI + 真实 MCP + localhost 合成 Responses 对照。端点只监听 127.0.0.1，
使用独立临时 CODEX_HOME，剥离继承的 Codex 会话环境及常见凭据环境变量，不读取 API key，
不调用 DeepSeek/OpenAI 模型；端点按固定协议返回工具发现、基线调用和结束事件。人工
编写的模拟响应不是模型理解或交易质量证据，其伪造 usage 数值不进入性能指标或账本。

| 检查项 | 已确认的实际请求行为 | 处理 |
| --- | --- | --- |
| AGENTS 双路径 | model_instructions_file 与自动项目文档注入使同一原文在每个请求出现两份 | 新 Job 仅关闭自动项目文档重注入，保留显式完整模型指令绑定 |
| MCP text / structuredContent | SDK 仍保留两种表示，但当前 CLI 的后续模型请求仅有一份完整基线 | 不改 MCP 协议，不删任一表示，不做猜测性压缩 |
| Core / upstream | get_market_core 排除 Stage 1、Router、VPA 报告、previous_analysis 等独立上游对象 | 保留现有资源分工，不进一步改写上游报告或引用 |
| Skill / 协议 / Schema | 分别承担流程、任务和结构约束；没有证据支持删除语义相近的要求 | 不摘要知识，不缩写字段，不改变 Host 物化或角色会话 |

实现：新 Job 冻结 `instruction_delivery_policy=single_frozen_agents_v1`；只有同时使用分析
专用运行时及这个标记，Worker 才加入 `project_doc_max_bytes=0`。该配置不删除 AGENTS.md，
也不限制 `model_instructions_file` 正文；后者仍绑定同一份冻结完整策略。未带该标记的旧 Job
保持原注入行为，不回写旧 digest。禁止把此配置单独复制到依赖自动 AGENTS 装载的普通
Codex 会话，也不能在取消显式完整指令绑定后继续使用。

验证结果：

- 新路径三次捕获请求的策略标记数 `[1,1,1]`；旧路径 `[2,2,2]`。
- 两条路径知识标记数均为 `[0,0,1]`：只有完成基线工具后的请求携带一份证据。
- 完整生产 AGENTS 正文仅统一 CRLF/LF 后逐字符相同；完整 MCP 基线（包括生产 Stage 1
  Skill 和合成知识）与 Gateway 原始对象逐字段相同。不是只检查标记存在。
- 35 项 Worker / Publisher / 执行契约 / 基线定向测试通过；Ruff F/E9、git diff --check 通过。
- 探针编写初期的 Job 身份、模型元数据和工具 namespace 问题均发生在合成环境；最终
  完整路径使用当前 CLI 的原生 tool_search 再调用 namespace 下的函数。没有付费重试。

保留的人工诊断入口（不是默认 CI，也不是生产代理）：

```text
uv run --no-sync python scripts/check_codex_delivery.py --model-catalog <只读DeepSeek模型目录.json>
uv run --no-sync python scripts/check_codex_delivery.py --model-catalog <只读DeepSeek模型目录.json> --legacy-project-docs
```

升级 CLI 后可用此入口复核；探针限定当前 Responses/tool_search 传输格式，格式变化应显式
失败，不自动解释为交付通过。探针临时目录自动清理，不保存请求正文或隐藏推理。配置
语义参考 [官方配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。

三角色自审（同一实现者）：PA 与 VPA 视角均不改变任何知识、证据或专业判断程序；
量化/架构视角只删除已证实的相同指令副本，保留旧 Job 语义及完整交付校验。没有测量
真实模型延迟，不能把“两份变一份”表述为总 Token 减半、耗时减半或质量非劣已成立。
当前结论仅覆盖已检查的 CLI/Responses 路径，不外推所有模型通道或未来 CLI 版本。

### 2026-09-18：优化步骤 1–3 的代码与离线验收

本次只实施已批准的三个步骤；没有付费模型调用、VPS 部署、思考强度降级、知识删减或
自动回退 Direct。阶段 8 的真实性能和质量验收状态仍为未通过。

#### 1. 分析专用运行时

- Publisher 为新 Job 冻结 `analysis_execution_policy=analysis_execution_v1`，由 Job digest
  绑定。没有该标记的历史 Job 保留原 Skill 交付及 code-mode 提示语义，不改写旧记录。
- 新 Job 的 `model_instructions_file` 指向本次工作区中由冻结 `agents_text` 物化的 AGENTS.md，
  用交易域分析指令替换默认编程角色指令，而非再增加一份通用 coding prompt。
- 所有新模型角色直接调用 MCP，完整选定 Skill 通过 `core_context.role_skill` 交付；repair
  通过 `repair.role_skill` 交付。工作区仍保留原生 Skill 文件用于身份与资产审计，模型不再
  依赖 Shell、编辑工具或虚构 URI 去读取它。DeepSeek 的完整输出 Schema 交付保持不变。
- 显式关闭 plugins（只关闭 remote_plugin 不够）、tool_suggest、shell_snapshot、浏览器、
  computer、图片、依赖安装辅助等无关能力；新 Job 同时关闭 code-mode 的三个开关。
- `mcp_servers.pa_evidence.enabled_tools` 从 Gateway 既有约束派生，PA 不展示原始 VPA 工具，
  repair 不展示重新调查工具，fixed 组不展示可选调查工具。过滤只是可发现性约束，真正的
  role、Snapshot、修复范围和预算检查仍由同一个 Gateway 执行，没有第二套交易权限引擎。
- 修复响应容量按完整反馈 + 完整 Skill + 适用的完整 Schema 实际序列化字节计量，不能为了
  容量不足截断反馈。显式用户预算仍保持 fail-closed，不偷偷放宽。

#### 2. 有目的的证据调查

- 基线原 `resources` 原文、字段、顺序和逐资源审计均保留。附加 `delivery_index` 区分
  `delivered_value_ids` 与 `indexed_only_fact_ids`；前者不声称 catalog 元数据全部交付。
  只从实际 price_frame、HTF/VPA 数据记录取 ID，不把索引、历史判断或嵌套引用当作已读值。
- 已交付 OHLC、几何及指标直接使用；缺失值或需要复核的字段按 ID 查询；自主锚点算术才
  使用测量工具。相同工具的独立 ID 合并请求，有依赖的问题等待证据，不强制调用次数。
- Runtime AGENTS 与实际 SDK 工具说明都给出上述语义。复核有理由时仍允许重读；“避免重复”
  不是禁止查证，也不意味着用 ID 替代内容。保留完整反证调查与 fixed/agentic 实验边界。

#### 3. Stage 1 顺序与一致性

完整基线 → 按 Brooks 建立初步诊断和有依据的竞争解释 → 确定可改变判断的证据缺口 →
必要调查 → 更新受影响判断及依赖结论 → 整体一致性复核 → 原 Schema Draft。

保留全窗口、HTF、mandatory 知识原文、Always In、位置、突破与接受的区分、因果截止点、
反证和 grounding。没有替代解释就不强造；反证推翻整体背景时必须重新评估主解释，不能
借“局部更新”锚定旧结论。复核发现实质矛盾可以继续查证；不新增输出字段、硬性市场 Gate、
推理长度限制或调查轮数限制。阶段 2、VPA 和原 Host 校验/路由/物化链未因此改写。

#### 验证与三角色自审

- 83 项定向离线测试通过（6.16 秒）：新旧执行契约、两类 provider 命令、修复容量、全量
  基线对照、证据索引、角色隔离、SDK 实际注册/序列化与完整 Skill 交付。SDK 测试仅替换
  阻塞 stdio 主循环，调用和序列化使用已安装 SDK；不等于 CLI→模型→MCP 的在线验收。
- Runtime asset audit 无问题；Ruff F/E9 和差异空白检查通过。
- 当前 PATH 的 CLI 已升级至 `0.155.0-alpha.2.6`；只读 `features list` 校验所有选定开关
  实际为 false，配置可解析。此前实验使用另一 CLI 版本，后续配对实验必须记录新版 binary
  digest。此检查不证明模型实际工具列表、模型行为或 Linux 沙箱效果。

| 自审视角 | 结论与保留项 |
| --- | --- |
| PA 交易员 | 未削减背景、方向力度、全图和 HTF；竞争解释和整体反证可重开。阶段 1 无订单权限，未增加市场阈值。专业效果待新鲜结果审阅。 |
| Wyckoff VPA 交易员 | 原知识、provenance、完整授权量价证据和 Evidence-first 流程保留；PA 不因新工具导航读取原始 VPA，VPA 未成为另一 Setup Router。 |
| 量化/软件架构师 | 新行为带版本并冻结；完整资源可逐项对照；工具发现与网关权限不混为一谈。离线证明交付和边界，不证明耗时、幻觉率或质量非劣。 |

这是同一实现者的三个视角自审，不是独立专家或多 Agent 评审。Skill 根据既有真实失败
（无效工具、写 Skill 尝试、重复测量/索引读取）修订；本轮没有新增模型行为微测，不能
宣称模型已遵守新流程。

后续首次运行前，需显式同步专用 CODEX_HOME 的版本化 config/rules；Worker 会拒绝旧策略
文件与新版资产摘要不一致，不能通过关闭摘要检查解决。不能使用普通个人登录目录替代
隔离运行目录。至少一轮新鲜完整链路才能检查行为是否改善；单次变快仍不能证明质量非劣
或达到整链 p50≤220 秒、p95≤300 秒目标。旧样本的 392.578 秒不是本次优化后的结果。

### 2026-09-18：Codex + DeepSeek Flash 接入及完整诊断

用户授权同冻结快照比较，使用 `deepseek-flash/high`、官方 Responses endpoint，独立
CODEX_HOME/API key 环境变量。未覆盖 ChatGPT 登录，未改 Direct 默认、知识、Router、
原始 Validator 或交易执行。密钥文件加入 Git ignore，不写入配置、实验记录或报告。

官方配置来源：
- https://api-docs.deepseek.com/quick_start/agent_integrations/codex/
- https://learn.chatgpt.com/docs/config-file/config-reference

先前 `single-trace-20260917-235302`、`235338` 在分析开始前被 Responses 拒绝：对象
anyOf Schema 不被当前接口接受，并非密钥失效。无关插件 401 曾污染错误分类，现优先
使用结构化 turn 错误。`235528` 的 21.828 秒是取证失败，不是完整分析性能：新增 Schema
为 MappingProxyType，不能直接 JSON 序列化，已在 Gateway 边界转普通 dict 并回归。
所有失败样本保留，不从覆盖率中抹除。

成功走至持久化的工程记录：研究目录 `single-trace-20260918-010944/output.json`。
身份仍是同一历史 Frozen input，不改 as-of、不下单。诊断设置仍是每角色900秒、整链2400秒；
正式240/180/300秒及整链600秒预算未改。

| 角色 | DeepSeek high CLI秒 | 之前 Luna high CLI秒 | DeepSeek input | output（含reasoning） | reasoning |
| --- | ---: | ---: | ---: | ---: | ---: |
| Stage 1 | 230.563 | 308.843 | 365926 | 55005 | 50271 |
| VPA | 86.610 | 145.281 | 63484 | 17669 | 15456 |
| 联合 Stage 2 | 73.703 | 109.234 | 351735 | 13226 | 7617 |
| 合计 | 390.876 | 563.358 | 781145 | 85900 | 73344 |

Host `analysis_to_persist_ms`=392578（不含最后写盘）；三次调用通过原校验，无输出 repair，
record=`completed_with_warnings`。累计867045 tokens，缓存515712已包含在input中；不同
模型tokenizer和计费不同，不据此计算成本或纯解码吞吐。实际账单未知。
CLI比旧Luna high短约30.6%，但仍超过整链300秒目标；单样本不能声称p50/p95达标。

#### 归因

- Gateway三阶段共354.545ms，排队约987ms；Host相对CLI约1.702秒。指标范围重叠，
  不相加重复计时。本地读盘、编排和校验不是本次主要瓶颈。
- 时间集中于CLI/远程推理链路；三阶段reasoning共73344 tokens，Stage 1占50271。
  这证明大量生成存在，但没有逐请求TTFT/服务排队测量，不能把390秒全部叫纯模型计算。
- Agent大上下文被多轮重传：成功Evidence MCP共6次（3/1/2），Stage 1还有一次重复指标
  读取。初始Prompt短不等于总输入少。完整mandatory原文没有删减；无证据允许删知识换速度。
- CLI还有插件目录后台同步和无效工具调用，属于适配/启动开销；尚未分离其关键路径占比。
  不把网络401/429警告误认成DeepSeek模型失败。
- 两次不是严格单变量实验：模型、服务、tokenizer、模型元数据、Schema交付、工具模式和
  fact索引版本均有差异。只能给工程对照，不能给模型因果占比或非劣结论。

#### 质量与未通过项

DeepSeek返回限价空单（4380.20 / 4391.60 / 4345.66），Luna high返回等待；这不是实际
交易建议、不是前向收益，也不能仅凭trade/no-trade决定优劣。人工发现可核查的内部矛盾：

- estimated_win_rate=45%，解释却写“高于对称抛硬币”；
- 从4380.20做空、TP1=4345.66，却把更低的4328.91描述为目标路径首道障碍；
- 结构失效、EMA20锚点及被动限价成交前提还需专业复核，不能只因Trader Equation通过
  就称交易合理。不为让该样本通过而改变交易理论或自动改写市场判断。

Stage 2尝试apply_patch操作Skill，沙箱阻止；VPA尝试不支持的exec。本次自动
completed不等于操作合规，因此该样本**不通过整体验收**。没有授予写权限、没有部署。

#### 本轮已做的后续离线修正（不回填旧结果）

1. DeepSeek Provider身份绑定显式模型目录SHA-256；仅放行flash的low/high/max，Dashboard
   未晋级该模型。ChatGPT默认路径不变，DeepSeek使用独立环境变量认证。
2. 当前DeepSeek不使用CLI `--output-schema`，原始完整Schema通过digest-bound MCP基线
   交付；本地原Validator仍是唯一验收。生成约束弱于Luna严格Schema，报告必须保留此差异。
3. 原样选定Role Skill随基线交付，repair随冻结反馈交付；无需猜skill URI或用写工具读文件。
   DeepSeek提示使用直接MCP，不再带code-mode exec示例；其他模型保持原模式。
4. 派生只读模型目录 `deepseek-models-readonly.json` 将apply_patch_tool_type设为null；Host
   拒绝声明apply_patch的DeepSeek目录。原官方目录与旧记录保留，目录变化产生新身份。
5. 后续审计标签改为deepseek_codex/text，不继续沿用openai_codex/json_schema。上述旧
   成功记录仍含旧标签，实际provider需看冻结runtime identity；不修改历史记录。

38项定向离线回归通过；三角色基线交付预算验证通过，知识与协议原文未改变。
这些后续改动尚未重新付费实测，不能用前一版390.876秒为新版性能背书。Linux隔离、
只读目录在真实CLI的工具可用性和持续Paper仍待验收。未提交整个混合工作区或发布VPS。

下一步优先一次短只读工具/Skill验收，再做同版本、同快照的必要定向性能复核；避免继续
无差别整链重跑。模型可优化项是减少无效工具与冗余推理诱因；吞吐、排队、high策略属于
Provider约束，不能靠本地IO优化消除。降低effort或缩减mandatory知识需质量证据，不能默认采用。

三角色自审（同一实现者，不是独立专家）：架构上保留单一Host链及失败边界；量化上保留
失败、版本混杂、单样本和未观测项；交易上不把校验通过等同于语义正确或交易优势。

### 先前 Luna 诊断

沿用阶段 7 已授权的 Binance USD-M XAUUSDT / 1h / 4h HTF 冻结输入，
as-of `1789058015684`，模型 `gpt-5.6-luna/max`。这是历史快照的真实模型调用，
不是当前行情交易，也不是前向 Paper。原失败四臂不重写、不补跑洗掉 missing。

研究目录中的 `single-trace-20260914-002710` 记录一次完整 Host 诊断：

| 观测 | 实际结果 |
| --- | --- |
| Stage 1 CLI | 423.563 秒，退出 0，返回 Draft；没有超时 |
| 后续处理 | 原 Validator 拒绝 gate_trace 1.2 branch 与 cycle_position 的不一致；修复 Job 发布失败 |
| 完整分析 | 未完成；VPA / Stage 2 未执行 |
| 工具 | 12 次，网关响应合计 232,918 bytes，工具总耗时约 362.56 ms |
| 初始请求 | 1,441 bytes；不包含 CLI 的 AGENTS、Skill、工具定义及 Schema 等全部输入 |
| CLI 上报累计 input / cached input | 1,362,945 / 1,246,720 tokens；是多轮累计，不是单轮上下文长度 |
| CLI 上报 output | 20,746 tokens，含其报告的 reasoning output；未存储隐藏推理文本 |
| 诊断预算 | 单角色 900 秒、整链 2,400 秒；仅诊断实例覆盖，生产默认没有改变 |

工具事件未见截断标记，不等于证明模型理解了每条规则。没有 matched Direct、fresh fixed/agentic
完整结果或足够市场样本，因此不能宣称降本、提速、减少幻觉、校准或交易增益。

## 已确认根因与最小修复

1. **模型工具截断容量与 Frozen Evidence 不一致。** 本机模型缓存记录默认工具限额 10,000
   tokens，而完整知识模块 / 100 棒帧的局部估算超过此值。Worker 原先未覆盖限额。
   现按冻结响应 byte 上限设置逐工具 `output_token_limit`，预留序列化容量，不删材料。
   这是容量配置缺口；旧失败没有完整 CLI 事件，不能反推旧运行每次都实际发生截断。
   官方配置依据：[Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。
2. **MCP 并发审计覆盖。** 锁定的 Python SDK 在线程池执行同步工具。原调用在预算、网关审计、
   最终原子落盘之间没有事务边界，较旧的快照可覆盖较新的审计。双调用离线用例确实从两条
   退成一条。现在同一 EvidenceMcpServer 的调用与审计串行，查询内容、权限和预算不变。
3. **修复重新拼接 Direct 长反馈。** 实际约 19 KB 校验反馈含完整原始 Schema；剥离机器修复
   块后仍超过初始修复指令 8 KB 限制。离线重放捕获确切异常
   `repair instruction exceeds byte budget`。现在把完整反馈、原稿和 pointer 白名单存入
   digest-bound repair resource，初始 Prompt 仅指引 `get_repair_findings`。响应预算按已冻结
   的必读资源大小计算，不截断反馈，不改变可编辑范围，也不放宽 Validator。
4. **完整价格基线遗漏蜡烛几何与引用 ID。** 原 `get_price_frame` 只有 OHLC 与 indicator 值，
   缺少原 Windows catalog 的 body/wick/close-position 等几何字段以及明确 fact ID。
   fixed 组又不能通过可选查询补回 ID。现在帧直接携带同一 catalog 的几何与两个事实身份，
   不重算、不创造引用、不加入 volume。Publisher 按 50 棒分块完整交付，并同步调用预算。

上面是接入与交付缺陷，不是修改 Brooks / VPA 理论来迁就单个输出。

### 后续定位及无损批量交付

5. **VPA role request 错用量价 catalog hash。** PA price-only 与 VPA OHLCV catalog 的 hash
   本来就不同；Publisher 却需要统一的 analysis snapshot identity。请求现在使用
   `analysis_snapshot_hash(frame)`，嵌套 VPA catalog 的原始身份不变，仍由 Host 校验同源性。
6. **冻结 manifest 的 Mapping 类型被误判。** Frozen Job 使用 MappingProxyType；Gateway
   与 Worker 却只接受 dict，导致知识目录回落为缺少 mandatory/load_order 的简化索引，
   同时绕过必读知识验收。两处现接受 Mapping，发布后重新打开 Job 的测试覆盖此路径。
   旧运行不能作为 mandatory 顺序已被执行校验的证据。
7. **失败 Backend 被误记为 Direct。** call ledger 在缺 reply 时使用实际调用 Backend，
   不再将 Codex 失败标成 Direct；没有真实 model/usage 的字段仍为 unknown。
8. **整链预算耗尽被误判 availability。** 当前联合角色按 typed incomplete 停止，不打开
   availability fallback，不借完整 Direct 重跑掩盖原分析的预算失败。
9. **VPA 未知引用丢失字段路径。** 原 Validator 只给全局错误字符串，重试循环无法产生
   JSON Pointer，Codex 随后以两次读取的 repair budget 重开全量角色。现对 observation、
   hypothesis assessment、sequence claim 的未知引用提供精确路径；不自动替换错误 ID。
   Codex 的后续 attempt 若无 Validator-authorized pointers，发布前直接拒绝重新分析。
   Direct 的既有重试路径未改。
10. **逐资源模型往返重复累计上下文。** 用户明确批准无损合并后新增 `get_role_baseline`：
    Host 顺序交付原 manifest、核心协议、上游、mandatory 原文、完整价格/HTF 或 VPA 目录、
    Stage 2 经验。每份资源仍经原 Gateway 查询，单份/累计预算与逐项审计不变；错误时不返回
    部分成功包。原单资源工具保留供定向调查；repair 无权读取全量基线。Runtime AGENTS 与
    五种正常角色 Skill 同步调用步骤，专业分析步骤不删减，知识文件没有改动。
    审计新增 `mcp_call_index` 区分往返与资源读取；旧记录缺此字段不能补零。

分段工程诊断：`single-trace-20260914-215314` 使用旧 Stage 1，真实定点修复耗时 79.109 秒，
通过原 Stage 1 Validator 后遇到第 5 项发布缺陷；`single-trace-20260914-215858` 复用已修复
Stage 1，真实 VPA 耗时 319.610 秒，引用不存在的 `vpa-w005` / `vpa-w020`，然后遭遇第 9 项。
这两个记录均 incomplete，不能计作 fresh 完整分析或成功研究样本。

2026-09-14 后续定向离线检查：55 项（VPA Validator/role repair、四角色合并资源逐项一致性、
预算拒绝、真实 SDK stdio、CLI 配置）及 63 项（Publisher/Backend/联合编排）通过，关键静态
`F,E9` 检查通过。这些检查不证明模型行为或交易增益。新的真实诊断仍需单独记录结果。

### 首次合并读取真实诊断：仍未通过

`single-trace-20260914-222456` 是 fresh 单任务（没有复用旧 Draft），CLI 为
`0.154.0-alpha.6.2`，binary SHA256 为
`081e4de4be8e38fac6ed4d95e3b1a0b9f6d31c090ddc36e1696b349fe406f575`。
Stage 1 用时 325.328 秒，退出 0，但 Host 以 `evidence budget exceeded` 拒绝，VPA / Stage 2
未执行，结果仍为 incomplete。不能把它当性能合格或完整分析。

一次 get_role_baseline 内 10 份原资源交付成功，共 209,766 gateway bytes；CLI 工具事件
记录响应 528,256 bytes，无 truncated 标记。模型随后重复请求完整价格帧，一次请求 100 棒
先触发单响应预算，又重复读取 50 棒块，最终超过调用预算。CLI 报告累计 input 191,410、
cached input 152,064、output 16,956（含 reasoning output 12,866）。不同版本与失败样本不能
据此给出模型质量/成本改进结论；工具事件记录完整内容也不等于模型可见内容未被截断。

随后用合成长响应传输探针定位模型可见内容，未重复运行行情试错。
兼容验收另发现旧 Direct 集成测试的 assembler clone 替身未配置，返回 MagicMock 而非
JSON messages；修正 fixture 后原 5 项集成检查通过，没有为测试修改 Direct 生产代码。

### 2026-09-15：定位多层静默截断

同一 CLI / Luna max 下，1500 行纯合成文本分散设置 7 个不可从上下文猜出的哈希值。
探针只能调用一次合并基线，不允许另取资料或计算哈希。结果如下：

| 配置（已有逐 MCP 工具容量） | 可正确返回的位置 | 证据 |
| --- | --- | --- |
| 原配置 | 首尾 2/7 | `transport-probe-20260914-223412` |
| 仅增加 history 上限 | 首尾 2/7 | `transport-probe-20260915-132501` / `133026` |
| 仅增加外层 exec 上限 | 首尾 2/7 | `transport-probe-20260915-133217` / `133620` |
| 同时增加 history 与外层 exec 上限 | 全部 7/7 | `transport-probe-20260915-133725`，41.484 秒 |

为定位边界，少量纯合成诊断临时保留 CLI session；正式行情角色仍为 ephemeral。
只检查公开工具调用及输出，不读取隐藏推理。实际记录显示：没有 exec pragma 时，外层输出
约 40,984 字符并明确标记 `truncated output (original token count: 161863)`；带 pragma 时
公开输出约 652,835 字符、包含中间位置且无截断标记，但没有 history 上限仍只能回答首尾。
通过本地 app-server 的只读 config/read 确认配置确已生效，排除参数未加载。

根因是 **MCP 工具、外层 code-mode exec、后续 history 各有独立容量**。网关成功、完整
CLI MCP 事件或完整 rollout 工具输出，单独都不能证明模型下一轮收到了完整内容。
Worker 现按同一冻结 byte 预算设置 history 容量，并在短初始运行指令中提供每次 exec 的
首行 pragma；不改变 Gateway 权限/预算、知识内容、分析规则或生产时限。
这是交付完整性修复，不是把上限全部当作实际 token 用量，也不证明模型理解或交易增益。

新增 Worker 参数回归先因 history 上限缺失失败，修复后 31 项 Worker/完整基线/SDK/指标
定向检查通过。

fresh 行情工程诊断 `single-trace-20260915-133918` 的 Stage 1 用时 501.281 秒，CLI 退出 0，
完整基线后进行了 list_fact_ids / get_pa_evidence / measure_price_window 三次定向调查，
没有此前的整帧重复读取。累计 input 773,319 / cached input 576,256 / output 26,993
（其中报告 reasoning output 22,521），不等于低于生产 240 秒时限或已实现总成本优化。
原 Validator 拒绝 §1.2 branch 的组合叙述与 cycle enum 不一致；后续修复 5.031 秒因
认证失败中断，VPA / Stage 2 未执行。最终仍 incomplete。

2026-09-16 登录健康检查 healthy；分段诊断 `single-trace-20260916-234719` 复用该 Draft，
真实 repair 89.297 秒成功返回，说明实际调用认证恢复。模型仅把“空头 Spike 过渡”改成
“空头 Spike”，仍被同一原 Validator 拒绝。问题不是重新判断周期，而是 Provider schema
仅给 branch string 类型，未解释 §1.2 的机器标识用途。现只补充 Stage 1 Contract 的
description：该节点使用已声明假设的原始 cycle enum，方向/过渡解释进入 reason；§2.3
与声明 direction 一致。未放宽 Validator、增加别名、自动改市场结论或改变字段类型。
这条说明由既有契约路径同时交付初次分析和 pointer repair，不另建 Codex 规则副本。
37 项 Provider/Worker 回归通过；分段诊断 `single-trace-20260916-234945` 的真实 repair
62.125 秒返回 `branch=spike`，经原修复语义保护和 Stage 1 Validator 验收后进入 VPA。
这是同一失败 Draft 的定点验证，不是 fresh Stage 1，也不证明其他样本不会出错。

## 仍待处理 / 验收

### 离线性能排查与优化（2026-09-17，零模型调用）

结论：已完成本轮证据支持的两项小范围优化，不代表真实时延达标。

1. PA Stage 1/2 的完整授权 fact ID 索引冻结为 core_context.fact_id_index，并随原
   get_role_baseline 交付，不进入 initial prompt。SDK 和 JSON 工具描述均提示直接使用索引。
   保留 list_fact_ids 兼容旧 Job；不禁止合理复查、不增加新工具、不强制读取全部测量。
2. 共享 PromptAssembler 的旧说明要求 pa_evidence.fact_refs，而 Draft v2 由假设引用与
   trace.evidence_refs 提交、程序再物化该字段。现明确实际 Draft 引用位置，保留全部事实
   和理论约束。Direct 同样使用这条纠错后的说明，Direct 路由、校验、请求实现均未改动。

离线依据：high 原始 Stage 1 在基线后调用 list_fact_ids，再读取选择的 facts，形成3次MCP
往返。该发现请求仅返回213个ID、3414字节，没有新增市场证据。现在相同ID列表在基线可用，
不再依赖额外发现回合。可避免一次重复携带大上下文的模型请求，但模型是否实际省去调用、
推理节省多少、缓存和总时延变化均未知；没有根据少一回合推算节省百分比。

复建保存的真实 high 请求得到如下 transport 基线（保留该记录的原 role_protocol）：

| 角色 | initial bytes | baseline bytes（含索引） | mandatory 模块 | 知识 payload bytes | 协议 bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Stage 1 | 1440 | 216291 | 4 | 97867 | 26097 |
| VPA | 1448 | 78623 | 5 | 39029 | 1375 |
| Stage 2 | 1814 | 299968 | 11 | 164980 | 18241 |

PA 索引各3414字节；全部查询满足原预算，保存的阶段协议逐值相等。上表包含 JSON 元数据，
不是纯知识原文或模型 token，也不是最后一项提示纠错后重新运行的模型输入量。
离线重建工具保存在本地研究目录 audit_baseline.py；不联网、不访问账号、不重写历史记录，
临时 Job 使用 TemporaryDirectory 自动清理。

未采用的“优化”及理由：

- 不删除原知识中的案例、反证和重复强调：不能把词句重复直接等同于语义冗余。
- 不移除完整 Provider schema：CLI generation schema 是投影，不能替代带条件约束的原契约。
- 不额外预读所有 PA facts：会增加每次基线体积并改变 fixed/agentic 研究语义。
- 不并行 Stage 1/VPA/Stage 2：存在已校验上游与 Router 依赖，不能以过期或未验证上下文提速。
- 不优化约0.1秒的文件读取或增大超时：不足以解释数百秒的模型进程时间。
- 不把中文内部推理要求认定为慢的根因：现有日志无法证明，故不凭猜测改语言策略。

架构上的长期限制仍明确：目前是 semantic parity 的“完整基线 + 可选调查”，不是只读少量
证据的轻量检索。短 initial prompt 不意味着模型少读知识；若要显著缩减必读范围，必须另行
建立非劣证据，不能以这次性能修复为名实施。high 三角色无重试仍约563秒说明：只修重试
不足以保证300秒目标。服务等待与模型生成缺少分段观测，尚无“不可修复”的定论。

验收：新增索引回归先红后绿，56项相关离线回归通过，关键静态检查与diff check通过。
三角色自审：PA/VPA 原文和判断权保留；引用指令与唯一 Draft 契约一致；索引来自冻结
角色投影、预算和审计仍有效。没有 subagent、模型调用、部署、配置切换或阶段晋级。
离线验证证明交付完整性及兼容性，不证明模型行为非劣或实际性能达标。

### 2026-09-17：证据可见域不一致与性能归因修正

`single-trace-20260917-001558` 的 fresh Stage 1 507.000 秒、原 Validator 通过；VPA
261.422 秒，repair 152.594 秒后调用预算停止，没有 Stage 2。该次 Stage 1 input 326,808、
cached 229,120、output 27,189，其中 reasoning 23,498；本地 Gateway 合计 130.213ms。
VPA input 67,343、output 13,973，其中 reasoning 12,581；Gateway 合计 119.917ms。
此前 Stage 1 input 773,319、output 26,993、501.281 秒。输入观测下降未带来对应延迟下降；
这些不同调用不是控制实验，不能直接宣布优化百分比，也不能把 CLI 总耗时全算成模型计算。

关键根因修正：`vpa-w005` **存在于完整冻结 catalog，且此前已通过 MCP 交付**，并非模型
凭空编造。原 `prompt_vpa_evidence_ids` 只允许 PA 已引用窗口；Direct renderer 与 Validator
遵守这一选择，而 Codex Publisher 发布了完整 catalog，造成“模型看到了但不能引用”。
过去诊断中的 unknown ID 应区分不存在与未授权，不能一概计作模型幻觉。

Publisher 现在复用同一个 `prompt_vpa_evidence_ids`，在冻结前生成角色证据投影；索引、基线、
按 ID 查询和 repair 使用一致范围。选中事实逐字段保留，原 catalog 不修改，PA/VPA 知识不删，
不放宽 Validator 或扩张 VPA 选择权限。范围回归先红后绿；另统一 K 范围错误的字段冒号格式，
使原有 pointer parser 只授权对应解释字段。54 项 Publisher/基线/role contract/repair 回归通过。

分段诊断 `single-trace-20260917-194536` 复用上述已校验 Stage 1，验证修复后的 VPA 与 Stage 2；
不计为 fresh、配对研究或生产时延验收。该诊断运行期间不修改分析源码/资产。

性能归因分三层，后续不得混淆：

- 架构可修复：不一致的证据权限、重复交付/读取、截断、修复路径丢失、可避免的重试。
- 模型可优化但待证实：由契约歧义、冗余指令或不必要调查引发的额外推理；必须保留质量并实测。
- 当前模型/服务约束：生成吞吐、服务等待、max 推理策略；现有总耗时和 usage 尚不能分离服务等待
  与推理时间，不能提前宣称不可修复。需先贯通后评估是否仍超出契约，不用增大时限冒充提速。

用户要求以 Codex 保质优化为优先；只有证实无法保质达标才考虑 Direct 优化上线。不得自动切换。

`single-trace-20260917-194536` 分段运行结束：VPA 403.234 秒，无修复，通过原 Validator；
Stage 2 244.312 秒，重复 node_id 后 repair 99.328 秒。结果以 completed_with_warnings 落库，
但人工复核发现 repair 把重复的 10.1 改成 11.1、仍保留“交易者方程”解释；11.1 的原问题是
趋势/尖峰状态，语义不相符。**该记录仅证明执行至持久化，不是质量验收通过。**
VPA 这次 input 66,013、output 20,900（reasoning 19,149），基线单 MCP 调用、无额外调查，
Gateway 合计约 109ms；单次低工具开销仍伴随长耗时，不能用架构工具开销解释全部慢。

修复边界补充：Codex Patch Envelope 不得引入原 trace 没有的新节点身份，字段或整数组
editable pointer 都不等于选择新决策问题的权限；授权容器内仍可移除冗余重复项。需要新增
决策问题时应 unrepairable，而不是伪装成表示修复。Direct 的调用/重试入口不变。
共享 Stage 2 schema 补充原有节点唯一性、问题对应和 Host-owned 10.3 的说明；不是新增
交易规则。55 项 repair/provider/worker/role-call 回归通过。修复 Skill 已补对应边界；其
quick_validate 辅助脚本因本地缺 PyYAML 未运行成功，未为此安装依赖，实际 Worker 资产
装载与回归已通过。不能把格式检查替代真实决策行为检查。

用户另批准一次 Luna/high 对比，仅当质量不降才考虑采用。阶段 7 StudyHost 的 max 限制
未改变；`single-trace-20260917-200202` 被该限制拒绝，未发模型请求。独立工程诊断
`single-trace-20260917-200315` 使用相同生产 Orchestrator、Frozen input、知识、Router、
Validator、身份校验和持久化，不进入正式阶段 7 研究，也不修改正式默认 max 配置。
其 Stage 2 已包含上述契约修复，因而与旧 max 不是只变 effort 的严格全链控制实验；
Stage 1/VPA 角色耗时可作方向性工程比较，单样本不能证明非劣或 p95 达标。

该 high 诊断已完成 fresh Stage 1 → VPA → 联合 Stage 2 → 原校验 → 持久化，无 repair，
结果 completed_with_warnings、无 exception/role_stop。三个 CLI 耗时分别为 308.843、
145.281、109.234 秒，合计 563.358 秒（不是含全部 Host 开销的总时延），报告累计 input
505,714、output 28,537，合计 534,251 tokens；cached input 261,120 已含于 input，不能再次相加。
high 明显减少了这些样本的生成开销，但 Stage 1 仍超生产 240 秒，整链仍不符合 p95 300 秒
目标，使用900秒诊断时限才跑完，不具备原生产预算下上线资格。

输出初核：high 与 max 都偏空并选择等待，均识别 K4 后缺乏稳定空头接受、VPA 不补入场；
但 high 主周期为 spike/transitioning，max 为 trading_range/transitioning，位置措辞也不同。
因此同为 No Trade 不证明质量相等，更不能证明高低思考强度非劣。high 本次没有重复节点
及改编号修复问题；该行为结果与契约修改共同发生，不能仅归因于 effort。保留 VPA 未来
确认措辞 warning，需专业复核而不是自动增加硬门槛。

用户要求尽快收敛并减少额度消耗：本次 high 是唯一获批对比，已结束，暂不追加付费调用。
后续优先离线审查最慢 Stage 1 的知识/契约重复与索引发现回合，只有明确、可验证且保质的
改动才值得再次实验。尚不能证明服务等待/模型生成的具体占比，不能宣布 Codex 不可救。

后续离线回放必须经过 `normalize_strict_output_instance`，不能直接把 CLI nullable generation
JSON 当作业务 Draft；后者会错误引入原 Backend 已剥离的 optional null。按原路径回放真实
VPA 失败稿，当前能同时报告未知 ID 和 K1/K5/K6/K10 的引用范围错误。范围错误文本仍缺少
role_call 路径提取器要求的冒号，应在继续验收有界修复前统一接口格式，不扩大编辑权限。

Worker 去重交付改为成功时完整输出 structuredContent 一份，错误时仍输出完整响应。
`transport-probe-20260917-001456` 为同一合成文本，7/7 正确，input 123,052、output 1,827，
44.515 秒。此前双份完整探针 input 226,909、41.484 秒；不同调用不能据此宣称延迟降低，
只记录完整性与输入观测。54 项相关回归及关键静态检查通过。fresh 工程诊断
`single-trace-20260917-001558` 已启动，待落盘结果；诊断放宽时限不代表生产或性能验收。
质量契约实际要求整链 p50 <=220 秒、p95 <=300 秒，600 秒仅为运行保护上限。

2026-09-17 自动审批恢复后，VPA 引用修复已写入：Schema 合法时，在早退之前汇总
未知结构化 ID、叙述中的未知 VPA ID 和明确 K 引用范围错误；复用原唯一 K 绑定，不猜
替代 ID、不修改市场判断。38 项 role contract / role call 检查通过，diff check 通过。
测试另暴露共享 K_REFERENCE_RE 会排除紧接英文句点的 K 引用；本次范围用分号隔离验证，
该解析边界仍待单独核查，不能称已解决所有引用问题。真实 VPA 修复、Stage 2、全链性能
仍未验收；本次没有新增付费调用、部署或提交。

- 修复后贯通 Stage 1 → Validator → Router → VPA → 联合 Stage 2 → 原校验/物化/持久化。
  复用旧 Draft 的分段诊断必须明确标注，不能替代当前版本的一次 fresh 完整运行。
- 当前 Stage 1 实际时长大于生产 240 秒；VPA 180 秒、Stage 2 300 秒与整链 600 秒需按完整
  实测重新评估。单纯加预算不算性能优化，不为赶进度降低 max 或删除必读知识。
- 工具响应的序列化及重复读取可能增加累计输入；先测其实际交付行为再决定优化，不能仅凭
  wire bytes 双份就断言模型实际重复消费两份。
- 失败路径仍会丢失部分诊断分项；无 reply 的 Codex 修复失败曾在 call ledger 被默认标为
  direct。应保留真实 Backend 与可确认失败信息，未知 token 不填零，不能伪造有效模型。
- Stage 1 专业输出的“信号棒有效性”和“是否已有后续确认”需分别复核；不通过单样本措辞
  直接修改理论或增加硬性市场分类规则。
- Linux 权限、systemd、持续 Paper、样本外盲评仍单独验收；工程贯通不等于交易判断科学性
  已获得统计证明。

### 2026-09-21：PA 会话连续性优化决策

后续性能与链路优化不再坚持“每个角色都独立 ephemeral”，而恢复原 Windows Direct 链路中
Stage 1 → Stage 2 的阶段连续性。只保留两项隔离：

1. **不同 Frozen Snapshot 之间独立会话**：Snapshot hash、cutoff、资产、周期、cohort 或
   runtime identity 任一不一致均禁止 resume，不能把长期会话当作市场记忆。
2. **PA/VPA 隔离**：同一 Snapshot 内仅 PA Stage 1 与 PA Stage 2 可以共享受控会话；VPA、
   research Joint 与所有 repair 始终使用独立任务，不能污染 PA price-only 上下文或取得 PA authority。

计划实现与验收边界：

- 只有 attempt 1 直接通过原 Validator 的 Stage 1 才保存 session identity；Stage 1 经 repair 时，
  原会话已包含无效草稿，Stage 2 必须依据已验证 artifact 新开 PA 任务。
- Stage 2 resume 前由 Host 核验 parent job digest、Snapshot hash、cutoff、task/cohort、model、
  reasoning、AGENTS、Skill、knowledge/runtime fingerprint 与 thread identity；不匹配即禁止续接。
- VPA 可在两阶段之间运行，但它的输入和输出只通过冻结、已验证的结构化 Artifact 传递，不进入
  PA session history。Stage 2 的交易权限仍来自 Validator 与 Brooks Router，而非聊天历史。
- 续接 Stage 2 可省略已在同一 PA 会话完整读取且 digest 完全相同的 price-only frame、HTF 与
  mandatory PA knowledge；Publisher 必须冻结继承清单。Stage 2 新协议、Router、已验证 Stage 1、
  VPA report（仅联合链路）及新增知识仍须完整交付，不能借续接压缩语义。
- Stage 2 完成、失败、取消或超时后清理该 Snapshot 的 PA workspace/session；异常遗留只按专用
  目录和有界保留策略回收，不使用宽泛删除。
- Direct Backend 继续保持原 prefix continuation，不因 Codex 会话实现发生任何行为变化。

该调整的目标是减少同一 Snapshot 内 Stage 2 对完整 PA 背景、K 线、HTF 与知识的重复读取，
同时保留两阶段推理连贯性。它不是质量捷径：必须以同一冻结输入验证必读交付完整性、PA/VPA
零串线、跨 Snapshot 零续接、Stage 2 Validator 通过率、token 与时延；未通过前不部署。

实现补充：`codex exec resume` 可能恢复 Stage 1 的 MCP 启动配置，因此不能只在第二次命令中
替换 `--job-digest`。持久 PA workspace 现使用 Host 原子写入的只读 evidence binding；Stage 2
续接前将其切换为已校验的新 Frozen Job 和新 audit 路径。Evidence MCP 每次调用前重读该绑定，
仅在 digest 变化时重建 Gateway 并重置本角色预算/审计；Job 内容仍由 FrozenAgentJobStore 完整性验证。

2026-09-21 本机无付费调用验收：Codex CLI 0.155.0-alpha.9.2，localhost Responses 合成服务，
真实 CLI + 真实 MCP 完成 4 次 request 的 Stage 1 → `resume` → Stage 2；`same_thread=true`、
`stage2_delta_equal=true`、两次均完整交付单份 AGENTS，工具面只含受限 Evidence 与 Code Mode
传输工具。DeepSeek fresh 直接 MCP 合成路径同时通过。这证明传输/会话/证据绑定，
不证明真实模型时延、交易质量或 p95 达标；下一步仍是 VPS 单 Snapshot 真实验收。

## 三角色自审

### 2026-09-23：运行入口一致性

Stage 2 Codex 专用角色协议不再复用“独立 API 请求”任务说明，明确续接也必须完成当前
Stage 2 Draft；关闭预测的指引不再要求关注 Host 拥有且 Draft 禁止输出的 terminal /
next_cycle_prediction。仅修改 build_stage2_agent_protocol，Direct 常量和渲染不变。
PromptAssembler 60 项检查通过。曾怀疑旁边 QClaw 禁用工具规则进入 Codex，经调用点
核对否定，不得将其写成根因。指令一致性修正不代表时延改善已经验证。

真实诊断 `single-trace-20260923-012008` 使用 Luna/max、pa_vpa_joint，角色900秒/
整链2400秒而非生产240秒限制。01:20:23 已交付完整 packet（205,908 bytes，网关
110ms）；最后成功观察进程时仍未有 Stage 1 结果。01:25:29 exec session 89709
消失，提权只读 Win32_Process 检查未发现对应实验 Python/Codex 进程。无 output.json、
CLI 终态或 usage，只有 running marker 与 evidence.tmp。记为执行中断/终态缺失，
不是模型900秒超时、Provider失败、质量通过或性能成功。原因尚未确定；不自动重跑。
观察证据保存在该实验目录 observer-interruption.json，保留原始文件不篡改成功状态。

必读知识一致性修复：knowledge manifest 将全部 Host 已选模块标为 mandatory，但 packet
曾通过两个文件名的独立白名单只交付部分模块，Worker 的 packet 读取检查无法发现缺章。
已取消第二份文件名选择器，按 manifest.loading_policy 完整交付原文；不改 Host 选择、
知识内容或 Direct。真实历史 Job 离线重建从 2 模块/72 章节/163,172 UTF-8 bytes 变为
4 模块/114 章节/205,908 bytes。这是补回遗漏，不宣称输入缩小或时延改善。
测试逐章节比较 packet 与 mandatory 原文完全一致，并检查预算足够。相同完整原文可由
原继承校验传给 Stage 2，不重复读取；旧 packet 缺失模块仍不得继承。
packet/Publisher/bounded-context 共 16 项定向检查通过，未调用真实模型。

同日继续检查发现 Worker 把 generation Schema 按字母序写盘，使 Stage 1 的 bar_analysis
先于 market_hypotheses。现将 Stage 1 properties 显式排列为假设、结构/局部证据、Gate、
信号/风险/置信度，写盘保留该顺序；全部字段、required 和原约束保留，不从假设自动生成
局部判断。原 Host Schema 与 Direct 不变。bounded-context 3 项及 CLI Worker 13 项通过。
这只验证交付顺序和契约保真，不证明模型按此内部推理或时延下降；仍须真实对照。

最新 `single-trace-20260922-220623` 在 7.86 秒因 usage limit 退出，不能作为性能样本。
本轮 Runtime AGENTS 改为遵从 Host 指定唯一入口，正确说明 packet 的 role_skill/price_frame
与 resources 布局不同，不再要求 packet 角色寻找旧 get_market_core/get_price_frame 路径。
保留身份、必读、顺序、继承、禁止重复整图、PA/VPA 隔离与停止语义；未改知识选择、Direct、
Provider Schema 或模型配置。旧 Job 保留冻结 agents_text，新 Job 自动获得新的 asset digest。
Runtime assets 4 项定向检查通过，diff check 通过；不代表真实性能或模型理解已验证。

下一步契约去重的约束：Gate/static trace、Trader Equation、terminal 已经由唯一
materializer 生成，不能作为尚未实施的新优化。market_hypotheses、Gate 节点判断、逐棒
增量、信号质量、风险和置信度相关但不等价，不能因为文字相似就删除或自动互相推导。
必须先核对每个字段的消费者与历史输出，证明确定映射后才允许 Host 生成。

三视角自审：架构上入口说明已一致，契约级重构仍未完成；量化上输入减少并未显著减少
reasoning，wire 双份不能证明模型双份消费；交易上背景/局部信号/反证不能由程序代判。
没有启动新付费实验、部署或提交，完整性能验收仍未通过。

### 2026-09-22：生成负担与契约一致性修复（实施中）

真实 `single-trace-20260922-201923` 首稿 607.282 秒，累计输入 90,065、生成
21,946，其中 reasoning 19,369；修复另耗 136.719 秒且失败。输入减少不能代表
生成负担或时延改善，最后取证到进程结束包含服务等待和输出，不是纯推理计时。

已落实并定向验证：

- Draft 原生校验覆盖主/替代假设的 supporting/challenging 引用，返回原始数组项
  JSON Pointer；阻断物化的错误不再流入 canonical 聚合字段再生成错误修复路径。
  回归实际复现了主假设路径错误，以及替代假设未知引用此前被接受的问题。
- Codex generation projection 使用单个共享定义约束 PA 引用为冻结真实 ID；
  原 Host Schema 保留，HTF 不混入 PA 引用权限。真实 CLI 接受该投影仍待验证。
- Stage 1 packet 恢复原 embedded_rules 与既有 PA.BAR/PA.INDICATOR 测量；
  不能以删除成熟语义、迫使模型重算来宣称无损压缩。交付紧凑化仍待完成。
- Stage 1 Skill 明确假设、Gate、逐棒摘要、风险和置信度各自的输出职责。
  这是减少重复论证的待验证假设，不是已经证明的性能收益。

本轮 33 项 Provider Draft、packet 和 bounded-context 定向检查通过，diff check
通过。未付费调用、未部署、未提交，性能和质量验收仍未完成。下一步继续检查
修复入口权限、续接材料真实读取覆盖、其他角色 Skill，以及真实全链指标。

本轮后续进展：

- 几何/指标事实按同类 columns/rows 无损交付，测试逐字段还原为原 catalog，
  包含 null 与全部精度；审计提取器支持表格中的真实 fact ID。
- bounded Stage 1 的 manifest 不再冒充全文读取凭证：Stage 2 只继承 packet
  中完整交付且原文相等的知识模块，其余仍读取。旧 semantic-parity 继承规则保留。
- Publisher 在发起 repair 前检查 pointer 是否属于 Provider Draft schema；
  canonical-only 字段被提前拒绝，合法缺失字段仍可按原授权修复。
- Stage 2、联合 Stage 2、VPA Skill 补充字段职责，保留专业反证及原有阶段权限。
- 67 项相关定向检查通过；追加 Worker/Backend/交付检查 49 项通过、1 项旧工具
  列表断言失败（漏列已存在的 packet 工具），同步断言后该文件 4 项通过。
  修复入口新增检查后 31 项相关回归通过，不把重叠测试数量累加为独立覆盖。

真实冻结 100 棒输入离线重新发布：200 行 PA.BAR/PA.INDICATOR 事实，单响应
123,491 bytes，估算 48,558 tokens，原 embedded_rules 逐值相等。相比此前删规则
的 69,154 bytes 明显增大，因此这只是恢复质量基线，不能称为时延优化成功。
接下来仍需对成熟规则做逐项覆盖与无损去重、检查 generation projection 的真实
CLI 兼容性，再做关闭240秒保护的真实验收。未发生新付费调用、提交或部署。

同一实现者的三个视角，不是独立专家评审。

| 视角 | 当前判断 |
| --- | --- |
| 软件架构师 | 修复保持单一 Publisher / Gateway / Validator；反馈与知识继续冻结读取。链路未完整验收，不能晋级。 |
| 量化工程师 | 累计 token、缓存、局部估算、失败与缺失分开；诊断延长预算和复用 Draft 不可冒充生产性能。 |
| PA / Wyckoff VPA 交易员 | 补回几何及引用，保留全文与既有规则；仍需检查真实 VPA / 联合结果的证据、反证与阶段连贯性。 |

### 2026-09-23：gpt-6-luna/max 单快照及交付根因

同一冻结 XAU 快照 `single-trace-20260923-124952`，本地诊断限额为单角色
900 秒、全链 2400 秒；正式生产预算未改。Stage 1 360.297 秒、VPA 349.969 秒，
两者原 Validator 均通过；Stage 2 204.312 秒后返回 typed incomplete，
整轮约 914.6 秒，未形成正式决策。三角色 CLI 累计输出 63,380 token，
其中 reasoning output 57,244；Stage 2 累计输入 498,859 token。
一次样本不能推断 p95，也不能把 wall time 全部称为纯模型计算。

Stage 2 停止理由声称 `mandatory_pa_delta_v1` 未完整交付 Provider Contract 与
已验收 VPA 报告。原始 MCP 完成事件约 207 KB 文本，`get_market_core.role_protocol`
实际含完整 Provider Contract，`get_upstream_artifact.vpa_evidence` 实际含八项
报告字段；无截断标记。由此排除 Frozen Job 缺件，但不能凭 CLI 事件证明模型
确实定位、理解了所有内容。Host Gateway 资源处理毫秒级，不能继续把主耗时归因
于文件或数据库读取。大文本内嵌契约与泛型 resources 定位是可修复的交付风险。

本轮离线修正（仍待真实模型验收）：

- Stage 1 packet 保留全部 114 个必读 section 的原文与 alias，索引仅列未内嵌
  section；同一冻结 Job 重建后工具交付从 205,928 降至 180,331 bytes，
  逐 section 正文相等。只是无损重复元数据消除，不足以单独解决 15 分钟时延。
- Codex Stage 2 的 exact Provider Contract 改由冻结 `provider_output_schema`
  一级字段交付；Publisher 校验协议 digest 与输出 Schema 完全一致。原 Direct
  `_stage2_output_contract` 仍保留内嵌契约，不改变 Direct Prompt。旧冻结
  Job 无新 digest 时保持原交付方式；repair 不被初稿 digest 检查误拒。
- Stage 2 合并基线附 Host 核验的 `stage2_handoff_receipt`，给出协议、Schema、
  validated Stage 1、Router 及联合 VPA 报告的精确 resources 路径；报告原文
  不复制、不删减。Skill 先按路径读取实际值，不再把“长文本中没找到”当缺件。
  receipt 只证明 Host 交付，不证明模型理解或交易正确。

相关 packet、Publisher、MCP、PromptAssembler 定向检查通过，Ruff F/E9
通过。尚未做新模型调用、全链质量验收或部署；性能门禁保持 no-go。进一步
提速不能只靠减小工具往返：VPA 单角已超过 300 秒目标，必须用同快照质量
非劣比较检验 reasoning effort 或研究更本质的任务/上下文契约设计。固定
`max` 的正式性能是否可达，目前没有证据支持保证。

按用户要求，先保持 `gpt-6-luna/max`，继续完成角色交付优化。该 trace 的 VPA
合并基线约 89,875 bytes，Stage 2 约 256,186 bytes；二者先交付章节目录，
再交付所有已选必读模块的完整原文。新 Stage 2/VPA 冻结任务使用
`inline_mandatory_knowledge_v1`：只传模块 ID、digest、顺序的简短清单，仍
按顺序交付每个完整 section 原文和 alias。Gateway 与 Worker 继续逐模块
校验必读覆盖；旧 Job 保持旧目录协议。对上述旧 payload 做纯离线等价投影，
VPA 减 3,060 bytes，Stage 2 减 16,672 bytes；这不是实际模型 token 或时延。
旧 Job 到期后以 `allow_expired` 只读打开，再在内存中投影新交付策略的 Gateway：
VPA 实际序列化约 86,875 bytes；Stage 2 含新增 Schema 与 handoff receipt
约 248,382 bytes。没有重写旧 Job，也没有把投影冒充真实新运行。
定向 Publisher/MCP/Worker 与 VPA 知识原文检查通过。仍需处理更大的
Stage 2 会话累计上下文与三个角色的推理生成量，不能据此宣布性能达标。

未采用的更激进编码：本快照的 11 条 VPA evidence 原文约 19,260 bytes；
按 43/52 列无损表格化的离线投影可到约 8,488 bytes，但会让模型按长列序
重新绑定价格、量、基线和相邻响应。VPA 精确事实关联是交易质量核心；只有
字节节省、没有模型理解非劣证据，故不改当前 VPA evidence 工具形状。
Stage 2 已选 14 个知识模块原文共约 167 KB，只有 2 个与 Stage 1 完全同源
可继承；剩余大头是 Brooks Router 当前授权内容，不能靠 Skill 任意删掉。

同一旧 Frozen Job 的纯离线会话投影：新交付策略下，续接 Stage 2 基线约
248,382 bytes（另有 Stage 1 packet 约 180 KB 在会话历史），从 validated
Stage 1/Router 新开 Stage 2 则需补回 price/HTF，基线约 368,687 bytes。
这些静态字节不等于 CLI 累计 token、时延或专业质量；且新开 Stage 2 会改变
此前明确采用的同快照 PA 会话设计，因此只可经用户确认后建立实验对照，
不能暗中切换正式链路。思考强度保持 `gpt-6-luna/max`。

### 一次真实 trace 的瓶颈归因（2026-09-23，离线复核）

直接读取 `single-trace-20260923-124952` 的三个 CLI JSONL：Stage 1 仅有一次
`get_stage1_decision_packet`，VPA 和 Stage 2 各仅有一次 `get_role_baseline`；
三者都没有追加检索或测量调用。因此该样本中的长耗时不能再归因于“多次工具
往返”。对应累计 input/output/reasoning tokens 分别为 Stage 1
103,374/19,304/16,801，VPA 50,654/13,993/13,062，Stage 2
498,859/30,083/27,381。Stage 2 的 input 是续接线程整轮累计用量，不能
解释为一个基线包本身的大小，也不能据此精确分摊服务端缓存与推理时间。
VPA 的非 reasoning 输出仅约 931 tokens，却产生 13,062 reasoning tokens；
Stage 2 的非 reasoning 输出约 2,702 tokens，却产生 27,381 reasoning tokens。
这只能证明 max 档内部生成远大于交付文字，无法从隐藏 reasoning 反推它具体
重复思考了哪些问题，也不能把“减少 JSON 字数”当作主要提速手段。

旧 Stage 1 packet 中，`knowledge` 约 128,294 序列化字节，是当时约 20 万字节
包的主要组成；`role_contract` 约 26,156、`bar_measurements` 约 27,950。
本轮 lossless packet 已缩到约 180 KB，但全部必读知识仍保留。Stage 2 新策略
投影约 248 KB，其中 Router 已选知识原文约 167 KB；VPA 投影约 87 KB，仍耗时
约 350 秒。仅削减目录或表格包装不可能把三个角色的实测总时长约 915 秒降到
整链 p95 300 秒，更不能保证减少错误。需要检验的是模型承担的语义工作量、
全量知识交付策略及 PA 会话续接带来的累计上下文，而不是继续叠加小幅压缩。

任何分层知识或 fresh Stage 2 方案先作为独立实验候选：冻结原文章节、事实、
Router、Validator 和输出权限不变；正式路径保持既有全量必读和同快照 PA
续接，直到同快照专业盲评及运行指标证明非劣。不能把静态字节投影或一次
成功输出当成质量/性能验收，也不能靠缩短 Skill 说明替代关键交易知识。
本例 validated Stage 1 同时给出 `spike` 主判断与 `trading_range` 替代判断，
Router 因过渡与形态键同时授权下跌通道、震荡区间及突破失败等文件；这些
看似“多余”的策略文件实际上承载竞争解释，不能仅按主周期删去。若研究
分层交付，索引与按需读取必须同时覆盖主、替代及反证路线。

### Stage 1 / VPA / Stage 2 分角色性能诊断与修复门禁

以该 trace 的 `*-cli.json` evidence audit 与 CLI usage 为准，而不是用总耗时
反推每个角色：

| 角色 | wall | Gateway 内部读耗时 | 冻结资源响应 | 模型累计 input / reasoning output | 本次结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| Stage 1 | 360.30 s | 174 ms（1 个底层读） | 205,908 B | 103,374 / 16,801 tokens | 原 Validator 通过 |
| VPA | 349.97 s | 93 ms（10 个底层读，1 次合并交付） | 83,279 B | 50,654 / 13,062 tokens | 原 Validator 通过 |
| Stage 2 | 204.31 s | 114 ms（16 个底层读，1 次合并交付） | 247,983 B | 498,859 / 27,381 tokens | typed incomplete，误报上游缺件 |

Gateway 内部耗时并非端到端工具往返；wall 还含 CLI 启动、网络、模型处理和
输出。上述数值只能排除本地取数耗时为主因，不能测出服务端各段精确耗时。
Codex [当前公开实现](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/models.rs)
优先把 MCP `structuredContent` 交给模型，不能因 JSONL
同时保存 `content` 与 `structured_content` 就断言模型读了两份；要以本机
固定 CLI 版本和实际 token 观测核实传输优化，而不是盲目删除 MCP 兼容字段。

**Stage 1：**旧 packet 的知识正文约 128 KB，角色协议约 26 KB，测量约
28 KB；因此主要是完整知识与价格事实的语义负载，而非工具调用次数。已做的
无损表格化、索引去重和唯一 packet 交付保留全部证据，但静态减少约 12% 不足
以保证从 360 s 到生产 240 s，更不能单独保证整链 300 s。Codex 专用输出
措辞现在与 Runtime 封套一致，避免模型在裸 JSON 与 `result` 之间自我冲突；
Direct 原文不变。后续高杠杆候选是冻结全文仍可读的分层知识交付，但基础规则、
竞争解释、反证与知识 ID 必须留有可靠的选择/回溯门禁，质量盲评通过前不
取代正式全量必读。

**VPA：**交付约 83 KB、最终非 reasoning 输出不足 1,000 tokens，仍产生
13,062 reasoning tokens；压缩目录或缩短 JSON 对 350 s 的帮助有限。VPA
唯一的专业工作是用同源量价基线判别支持/挑战/信息不足，不能改成机械阈值
或省略反证。现有 Skill 已限定 Background → Location → Effort/Result →
Leg/Wave → Acceptance 的顺序、标签最后、一次基线、按真实 evidence ID
输出。可研究的实质改动是更聚焦的知识选择/交付与确定性比较预处理，必须
检验是否遗漏关键事件和 provenance 限制；43/52 列大表虽省字节但事实错绑
风险高，不因离线字节收益直接上线。若上述可证伪候选仍不降时延，须承认
`max` 模型本身的生成成本是主要剩余因素，而不是继续改 Skill 文案。

**Stage 2：**248 KB 本轮资源之外还继承 Stage 1 的大 packet 与会话历史，
498,859 是续接线程累计 input 而非单次 248 KB 的 token 化结果；缓存
225,280 已包含在 input。其失败不是“材料确实缺失”：旧基线已有 Provider
Contract、validated Stage 1、Router 和 8 个 VPA 报告字段。新增 Host
`stage2_handoff_receipt` 与第一类冻结 Schema 旨在消除这个具体误读，但真实
修复效果未验。继续削减 Router 竞争策略会损害可选方案审查。若研究 fresh
Stage 2，只能与现有续接在同快照、同知识、同 VPA、同 `gpt-6-luna/max` 下
对照完整性、时间、token 和专业盲评；正式设计维持同快照续接直到证据充分。

因此三角色的下一次付费验收不能只是“又跑一遍”：先确认实验架构候选及
专业非劣判据；一次完整正式候选运行必须同时记录原 Validator/Router/Risk、
缺失与事实错误、逐角色 wall/input/reasoning、最终持久化结果。单样本不能
宣称 p95 达标；质量不过关的快结果仍是失败。

### 2026-09-23 实验档位更正及无效网络样本

用户将后续实验档位从 `gpt-6-luna/max` 更正为 `gpt-6-luna/xhigh`
（UI 名称 extra high）。既有 max trace 仍按其实际身份保留，不能重命名为
xhigh 或与后续结果视为同条件样本。Study 的允许组合增加新的 xhigh 条件，
同时保留已经冻结的 max 注册以供 Replay；正式交易链与 Direct 不因此改动。

`single-trace-20260923-195035` 在受限网络权限下 900 秒内未完成模型调用：
CLI 报 Windows socket `os error 10013`，随后 HTTPS 连接失败；这不是 PA
推理耗时样本。扩展网络权限重试 `single-trace-20260923-200619` 时，
Stage 1 已通过原 Validator，但 CLI wall 813.359 秒且记录一次服务端
WebSocket 提前关闭与重试；其 `turn.completed` 仅报告 2,440 output、76
reasoning tokens，不能把这 813 秒归咎于模型深度思考。用户更正档位时该
max 任务正在 VPA，已停止确切子进程并保留输出，VPA/Stage 2 不算完成。
随后实验必须重新冻结并验证 xhigh 的真实 effective model/effort、逐角色
成功状态和连接重试情况，不能把这两次无效/中断运行当作性能改善证据。

### 2026-09-23 xhigh 单 Agent 真实链路与交接语义缺陷

`single-trace-20260923-202538` 使用新的冻结 Job、`gpt-6-luna/xhigh`、
PA＋VPA 联合链和仅本地诊断的单角色 900 秒/全链 2400 秒限额。Stage 1
504.047 秒、VPA 145.125 秒，两者返回原链路可验收内容；Stage 2
300.422 秒后 typed `review_required`，未形成正式决策，总 CLI wall 约
949.6 秒。三者都只读取一次角色基线，没有额外调查；usage 的 input / cached /
output / reasoning output 分别是 Stage 1 94,074 / 18,944 / 16,396 /
13,777，VPA 49,056 / 15,872 / 5,852 / 4,919，Stage 2 442,183 /
273,408 / 24,596 / 21,718 tokens。Stage 2 的 input 是续接会话累计口径。
本次无 WebSocket 重试记录；不能将各 wall 秒数精确拆为服务端计算与网络，
也不能由一例估计 p95。Stage 1 已超过生产单角 240 秒，整链远超 300 秒；
因此质量和性能均未验收通过。

Stage 2 声称 `stage1_diagnosis` 与 `pa_hypotheses` 字段集及 `source_digest`
不一致，无法安全决策。离线使用同一快照、同一 Stage 1 和
`compile_pa_handoff(stage1, None, frame)` 重算，结果与实际冻结交接的
`pa_hypotheses` **逐字段相等且 digest 相等**。两者本就不是同形对象：后者
是 Host 编译的派生交易假设投影，`source_digest` 哈希该投影及快照身份，
不是原 Stage 1 artifact 哈希。此为模型误解交接契约，不是真实数据冲突。
其停止理由还把 `setup_candidates` 错置到 Stage 1 artifact，提示大上下文中
的结构归属仍有混淆风险；不能只把这个失败解释为缺一句提示。

修复在新 Codex 联合 Stage 2 发布时由 Host 重算整份投影并比较，不一致
则发布前拒绝；一致时把有明确 digest 作用域的冻结 lineage 证明送入
`stage2_handoff_receipt`。Runtime AGENTS 和联合 Stage 2 Skill 说明该派生
关系，但仍要求真实事实、快照或 Router 冲突触发 `review_required`。
历史冻结 Job 不追补这个证明，Direct 链路不变。定向 lineage 正反例测试、
Ruff F/E9 与 diff check 通过；新模型调用尚未进行，修复效果及完整链路仍
需要真实验收，不得宣称已完成。

### 2026-09-23 第二次 xhigh 重验：Stage 2 delta 交付归属

`single-trace-20260923-204900` 的 Stage 1 CLI wall 516.235 秒、
94,556 input / 14,947 reasoning tokens，单次 packet 读取。VPA 首稿 wall
556.391 秒、50,825 input / 9,122 reasoning tokens；首稿把最新 K1 无后续
响应误延伸到更早的 K3/K4，尽管对应 VPA evidence 明确给出
`later_closed_bar_count`/`later_closed_bar_range`。原 Validator 拦截并限定
`observations[2].interpretation`，定向 repair 59.985 秒、2,339 reasoning
tokens。VPA Skill 已把逐事件后续闭盘核对并入原有第 8 步，不改变 VPA
证据、知识、标签或权限。

Stage 2 初稿 wall 326.000 秒、452,624 input / 25,982 reasoning tokens，
再次 typed incomplete，称 14 个 mandatory 模块只有部分原文交付、Role
Skill 未完整交付。冻结 MCP 回执实际含完整 2,552 字符联合 Stage 2 Skill；
14 个 mandatory 模块中 12 个在本轮 `read_knowledge_module` resources 完整
交付，另 2 个由同快照 Stage 1 会话验证继承。原交接回执只有上游字段位置，
未把完整必读集合与 delta 实际交付分区对应；模型误将“本轮资源数 12”判为
“应交付 14、缺 2”。新增 Host 核验的逐模块来源/位置与 Skill 位置回执，
校验本轮 section 原文和已继承 ID 的互斥完整覆盖；PA-only 与联合 Stage 2
Skill 共用这套语义。定向 7 项 baseline 测试与 Ruff F/E9 通过，尚待模型验收。

此轮 StudyHost 最终因域资源在运行中改变而拒绝样本：分析期间修改了 VPA
Skill，触发正确的 domain fingerprint guard。即使三个 CLI 的原始耗时与失败
可以作工程诊断，本轮不算有效稳定基线，也不能假称产生正式决策。下一次
付费验收前必须冻结所有域资源及代码，直到 Host 完成指纹检查；不得在试验
运行中改 Skill。这个治理失误与 Stage 2 的真实 typed stop 分别记录。

### 2026-09-23 Stage 2 统一交付回执离线验收

从 `mandatory_read_plan` 到 `get_role_baseline` 的数据流审查确认：同一 PA
会话续接时，`mandatory_modules` 仍列完整必读集合，但本轮只交付未继承的
原文；价格窗口与 HTF 也可能只由已验证 Stage 1 历史提供。`get_market_core`
有意排除 `experience_entries`，所以经验条目必须从冻结 Job 的 core context
核对，不能从已过滤的工具 core 判断其是否存在。

Stage 2 的 Host 回执现统一标出 Role Skill、Provider Contract、上游与 Router
位置、每项 mandatory 知识的继承/本轮完整原文及精确位置、price/HTF 来源、
冻结经验条目数量与路径。Publisher 仍先验证 PA 假设投影与同快照 Stage 1
逐字段相等；Gateway 核对模块覆盖互斥、完整 section 原文和角色 Skill，
Worker 保留原必读/顺序审计。回执只证明交付，不替代模型理解或 Validator。
PA-only 与联合 Stage 2 Skill 均按这份回执读取；VPA 仍独立。

定向 Publisher、bounded baseline、role baseline 共 26 项检查通过，Ruff F/E9
通过。使用上轮实际冻结 Stage 2 Job `a6f4f29e…223ae5a` 进行**无模型调用**的
只读 Gateway 回放，回执报告 14 项必读知识＝2 项已验证继承＋12 项本轮交付，
Role Skill 路径为 `resources[1].payload.core_context.role_skill`，价格与 HTF
均标为同快照 Stage 1 继承，经验条目为 0；无工具错误。此结果证明交付分类
契约在真实失败 Job 上自洽，不能证明模型新一轮必然理解或交易质量合格。
下一次真实 xhigh 全链验收必须在域资源冻结后进行，并记录首稿、repair、
有效 Host 结果、逐角色耗时与 token；不得把回放当作正式完整基线。

### 2026-09-23 第三次 xhigh 重验：模型误判完整交付为截断

冻结资源后运行 `single-trace-20260923-214002`，Stage 1 首稿 328.047 秒、
95,037 input / 17,513 output / 15,150 reasoning tokens；VPA 首稿
236.859 秒、51,092 input / 8,598 output / 7,620 reasoning tokens，均无
repair。Stage 2 约 159 秒后 typed `incomplete`，仍未形成完整链路结果；
其 CLI 记录 446,796 累计 input、203,776 cached input、25,698 output、
22,994 reasoning tokens。其停止理由把最后一个知识模块的结束误判为整份
交付截断，尽管本轮资源已完整送达。因此这仍是失败样本，不能宣称性能或
质量达标；Stage 1 与 VPA 合计已约 565 秒。

为区别真实传输截断与模型误读，离线核对了该轮 CLI `get_role_baseline`
事件及同一 PA 会话 rollout 的模型可见 `custom_tool_call_output`：MCP
调用状态为 completed、`truncated=false`；模型可见输出可完整解析为 JSON，
含全部 14 项 resources、最后一项知识模块及其后的 snapshot identity。
也未观察到上下文窗口被打满的证据。故本轮 `incomplete` 不能归因为
MCP 丢包或知识缺件；主要是 Stage 2 在长上下文中反复承担 Host 已完成的
交付审计，而没有进入交易判断。累计 input 不等于单次上下文长度，也不能
把 wall 时间完全归因于模型推理。

架构修正是把交付完整性归 Host：只有全部 `mandatory_read_plan` 操作成功、
交接字段与完整知识分区通过 Gateway 校验后，才在长 resources **之后**
返回 `stage2_handoff_receipt`，其中明确 `read_plan_complete`、资源数量和
最后一项资源。AGENTS 与 Stage 2 Skills 要求模型读取实际内容、核对真正的
事实或身份矛盾，但不重复推测资源是否还应继续。新回执在失败轮原 Frozen
Job `4437ac6e…764ed8375` 上只读回放：14 项资源、末项为
`read_knowledge_module`、回执位于 resources 之后、无工具错误。
19 项相关单测、Ruff F/E9 和 diff check 通过。这个离线验证只证明交付契约，
**尚未证明模型下一次会正确完成 Stage 2**；需保持资源冻结后做一次定向
真实验收，并与 Stage 1/VPA 的时延门禁分别评价。

### 2026-09-23 第四次 xhigh 重验与证据来源契约审查

冻结资源的 `single-trace-20260923-220340` 再次未完成：Stage 1 首稿
272.343 秒（95,451 input / 14,590 output / 12,201 reasoning tokens），
VPA 首稿 181.641 秒（50,846 / 9,622 / 8,620），两者各只进行一次合并
基线读取、无 repair。Stage 2 128.703 秒（428,176 累计 input / 21,151
output / 18,519 reasoning）后 typed `incomplete`，未形成交易决策，三角色
CLI wall 合计约 582.7 秒。Stage 1/VPA 的 MCP 内部处理仅约 0.128/0.102
秒，取证结束到结果约 261/170 秒；瓶颈主要在模型处理长上下文与生成大量
推理，而非工具循环。样本不足以估计 p95，但已明确未过生产时间门禁。

Stage 2 这次不再误判知识截断，而误将 `evidence_index.vpa_evidence_ids=[]`
视为已验收 VPA 报告不能引用 `vpa-*` ID。该索引的真实语义是本角色可直接
读取的**原始 VPA catalog**；PA Stage 2 按权限不能读原始 catalog，只接收
已验收的 VPA 报告。离线将该轮报告解析为 `VpaEvidenceReport`，报告列出的
6 个引用 ID 全在同一冻结 catalog 中，catalog ID 也相等；Host 的原联合
Validator 仍以报告 ID 集合约束 `joint_analysis.vpa_evidence_refs`。所以这不是
真实 VPA 引用越权，而是把原始读取权与报告引用权混同。

同一审查还发现 `mandatory_pa_delta_v1` 的 `delivery_index` 只统计本轮新
resources，错误地把 213 个 PA fact 全列为“仅索引”，尽管同会话 Stage 1
packet 的表格及 spine 已完整交付这 213 项的值。新执行契约现不在 PA
续接 delta 附带这个当前调用专属索引，而以回执和同会话 Stage 1 packet
界定继承；fresh 基线与 VPA 仍保留适用的 delivery index。PA 角色的初始
`evidence_index` 不再显示原始 VPA ID 字段；联合 Stage 2 回执从冻结报告
精确列出可引用 ID、报告 catalog ID，并声明 raw catalog access 为 false。
Stage 2 的冻结 Provider Schema 与回执对所有新分析 Job 交付，不依赖协议
digest 字段是否存在；存在时仍校验 digest。Runtime AGENTS、联合 Skill 与
Worker 初始指令使用同一权限/继承/截断语义，没有开放新工具或更改原 Validator。

失败轮 Frozen Job 只读回放显示 6 个报告引用 ID、回执在完整 resources
之后、delta 无当前调用专属 delivery index、工具无错；相关 51 项单测、
Ruff F/E9 与 diff check 通过。旧 Job 的冻结 core 仍有旧空索引，不能把
回放当作新发布 Job；这些离线结果不证明模型下一次一定正确完成 Stage 2。
在进一步付费验收前，应先保证上述权限与引用语义、Skill 路径、停止边界
在新冻结 Job 中一致，再以完整 Draft、原 Validator/Router/Risk、持久化
结果及逐角色时延作为验收，不再只看 CLI 退出码。

### 2026-09-23 第五次 xhigh：停止权限仍让模型复审 Host 不变量

`single-trace-20260923-223647` 在统一来源契约、81 项相关离线检查后保持
冻结运行。Stage 1 365.469 秒、96,010 input / 14,605 output / 12,266
reasoning tokens；VPA 222.046 秒、50,844 / 11,089 / 10,069；两者
首稿各一次基线、无修复，原 Validator 通过。Stage 2 156.782 秒、440,403
累计 input / 20,299 output / 17,687 reasoning 后再次 typed
`review_required`，整链约 744 秒且无正式决策。Stage 1 与上次 272 秒的
reasoning 量接近，远端生成/等待速率波动可见；不能将差额归因于 Gateway。

这次停止理由声称 Stage 1 bearish，而 `pa_hypotheses` 投影缺少方向，无法
确认二者关系。直接解析原 CLI MCP 结果和**模型可见**的会话
`custom_tool_call_output`（约 136,739 字符、完整 JSON）均显示：
`stage1_diagnosis.direction`、`stage1_diagnosis.market_hypotheses.primary.direction`
和 `pa_hypotheses.primary.direction` 全是 `bearish`；Host 冻结的
`pa_hypotheses_lineage.verified_stage1_projection=true` 及末尾 Snapshot
identity 都在。模型声称“缺方向”是可证伪的事实错误，而不是 Host 缺件。
此前同类停止已分别误报投影关系、知识/Skill、长基线截断和 VPA 引用权限；
每次只加一个提示无法根治。

根因应审查为**权限归属**：Host/Publisher/Gateway/Worker 已验证可机械
证明的交付、身份、投影、知识继承及报告引用，Stage 2 Skill/传输 Schema
却仍允许模型把这些不变量当作自己的审计任务，以 `incomplete` 或
`review_required` 提前退出而不作交易判断。后续不能继续为每个假警报
叠加 receipt 字段。候选架构是新 Codex Stage 2 在 Host 预检和成功基线
交付后只提交交易 Draft；真实工具/身份/契约故障由 Host fail closed，
市场不确定使用合法等待/No Trade，仍由原 Validator/Router/Risk 裁决。
此改变涉及停止权限，待用户确认后实施；若选择保留模型停止，则必须设计
可核验的停止证据与误报处理，而非再写自然语言提醒。Direct 不改变。

### 实验前分角色与交接审查门禁（2026-09-23）

以下以最近冻结 Job、原始 CLI/audit 和当前代码为准，不把待实施方向写成已优化效果。

| 环节 | 已定位的主耗时或失败机制 | 实验前必须闭合的契约 | 不允许的捷径 |
| --- | --- | --- | --- |
| Stage 1 | 一次 packet 的实际证据响应 180,509 UTF-8 bytes（canonical `packet_bytes` 为 254,357，口径不同）；最近两次约 272/365 秒、约 12.2k reasoning tokens；Gateway 约 0.13 秒 | 核对 Provider Schema 与协议内重复内容、packet 内全部章节和事实值、Skill 的主/替代假设与 Gate 职责；仅去除能证明语义等价的包装/重复，建立输出质量与时延对照 | 删 Brooks 反证、只看近端 K 线、让程序选方向/周期 |
| VPA | 一次基线读取、Gateway 约 0.10 秒；最近两次约 182/222 秒、约 8.6k/10.1k reasoning tokens；没有当前 PA Entry Attempt | 检查所选 5 模块原文、全部冻结量价证据、相对基线与每个事件的后续闭盘；明确背景支持与即时入场不足的不同语义，减少重复叙述但不减少独立反证 | 预设吸收/停止事件、用机械阈值替代 Wyckoff 判断、为了快而删 catalog |
| Stage 2 | 同会话续接累计 input 约 428k–440k tokens，最近两次约 129/157 秒且均为错误 typed stop；本轮资源约 191 KB，另有 Stage 1 历史 | Host 确定身份、投影、继承、报告引用和 Schema；模型只比较 Router 内候选/等待、结构锚点与 VPA 作用。停止权限变更先取得确认，不能把假停止改写为 No Trade | 再叠 receipt 文案、放宽 Validator/Risk、忽略真实上游矛盾 |
| 交接 | Stage 1 与派生 PA hypotheses 是不同形状；VPA 原始读取权与报告引用权不同；delta 与会话继承不同 | 新 Job 要逐项证明 Snapshot/cutoff、parent thread、Stage 1 校验、Router、PA 投影、VPA report/catalog、知识/HTF/价格继承、输出 Schema 和最终持久化；失败分类不混同市场不确定 | 凭模型声称“缺件”判定传输失败；用旧 Job 的离线回放冒充新发布 Job 验收 |

性能目标是 `config/quality_contract.toml` 的整链 p95 不超过 300 秒，
单次 p95 不超过 150 秒。当前串行链中，单是 Stage 1＋VPA 在第四轮就约
454 秒，第五轮约 588 秒；即使 Stage 2 瞬时完成也不能达整链目标。
去重目录、减少 MCP 往返与更短 Skill 文案都不足以解释或消除这个量级的
差距。对 `gpt-6-luna/xhigh` 必须分别验证模型生成量、远端吞吐波动和全量
专业任务的不可压缩部分；在不降低知识/判断质量之前不能宣称软件改动必能
达到 300 秒。新一轮付费实验必须等停止权限与交接契约定案、静态/离线
正反例检查通过，并在整个运行期间冻结代码和域资源。

补充核对第五轮持久化的角色调用指标：Stage 1 的实际证据工具响应为
180,509 UTF-8 bytes，`decision_packet_bytes=254,357` 是为 digest 使用
`ensure_ascii=True` 序列化的**规范编码大小**，不能当作模型接收的线缆大小；
两种口径不可混用。VPA 实际证据响应为 76,973 bytes。两角色最终结构化
结果约 8,822 / 3,474 UTF-8 bytes；从最后证据交付到 CLI 结束分别为
353.598 / 204.743 秒。因此首轮工具选择与 Gateway 不足以解释耗时，
也不能靠缩短最终 JSON 文案声称解决主要瓶颈。Stage 2 typed stop 在
`CodexWorker._unwrap_result` 抛出前虽已解析 CLI usage，但未形成成功的
`CodexWorkerResult`，持久化指标把该角色 usage 记为 unknown；进行下一次
性能验收前必须明确失败轮的使用量与时间证据来源，不得用不完整总量比较模型。

### 2026-09-24 新 Stage 2 Host-gated Draft 合同：仅离线验收

用户确认交接完整性、身份、投影、知识继承和资源故障由 Host 裁决。新发布的
Codex Stage 2 Job 冻结 `result_policy=stage2_host_gated_draft_v1`：CLI 输出
Schema 只允许 `{"result": <Stage 2 Draft>}`；模型停止对象被 Worker 明确
拒绝。旧冻结 Job 未带此 policy，仍按旧 typed-stop Schema 回放；Stage 1、
VPA 的停止语义不变。真正工具/预算失败仍由 Gateway/Worker fail-closed；
证据完整时市场不确定只能经原 Schema 合法等待或 No Trade，不得伪造交易。

Host 在联合 Job 发布前复算 PA hypotheses 投影，并核对已验收 VPA 报告的
catalog ID 与同 Snapshot 冻结目录身份；Gateway 在完整必读读取后再次
检查报告目录身份、知识/价格/HTF 交付分区和 Provider Contract。Runtime
AGENTS 与 PA-only/Joint Stage 2 Skill 同步为相同权限边界；Brooks Router、
Validator、Risk 和 Direct Provider 路径没有为此改写。定向 Worker、发布、
Evidence、同会话续接、Joint 编排回归通过，Ruff F/E9 通过。这只证明离线
契约与现有测试覆盖，不证明模型必定给出高质量 Draft 或时延达标。

后续实验身份经用户再次确认固定为 `gpt-6-luna/xhigh`；历史 max Job 只作
历史条件对照，不改名、不混入同条件质量/时延估计。Stage 1/VPA 暂不删除完整必读知识、价格窗口、量价目录或反证。第五轮的
真实工具响应约 180.5/77.0 KB，最终结构化结果约 8.8/3.5 KB，而最后
证据后模型仍耗时约 354/205 秒；从这组证据无法把主要瓶颈归于结果文字、
MCP 往返或 Gateway。下一次付费实验前须锁定本版源码/资源 digest、确认
固定思考强度，并完成本地正反例与 Direct 不变性检查；单次成功不能证明
p95≤300 秒或交易效果非劣。

第五轮已验收的 Stage 1/VPA 仍有非阻断专业文字警告，需要区分真实表达缺陷
与规则误报。Stage 1 的“本轮新低”没有明确比较窗口，VPA 的“回看区间新低”
及多 evidence selector 共用一段比较说明也欠精确；两个角色 Skill 现统一要求
写清冻结比较区间、具体前棒/ID 与各自因果基线，不删除原事实或反证。另两条
警告不应被当成已证实的市场错误：Stage 1 写的是“收盘**未**贴近本棒低点”，
程序 `PA.BAR.K4` 的 `close_position=0.295` / `middle_half` 与此相容；
VPA 的“观察后续收盘是否……”是未来条件，但当前有限词法规则未识别该措辞。
这些是 warning 而非硬拒绝；本次不为迎合词法规则改动 Direct 共用 Validator，
也不把新增 Skill 文字当作质量改善的实验证据。

### 2026-09-24 首次新版完整链路 xhigh 本地基线

`single-trace-20260924-005508` 使用同一历史 XAU Frozen input（evidence digest
`84fc93d0ab4aa54c5a9068af619269721a5d253b2f1c6b3dad425c99b1f8bc7d`）、
`gpt-6-luna/xhigh`、`pa_vpa_joint` 和新的 Host-gated Stage 2 Draft 合同。
运行前 Host 重验 100 根 K 线、完整知识与 VPA catalog；本版 domain fingerprint 为
`be5b456c19b220d5fd936581684e26581aef3efcd0db6ad7d25023aabf57a749`。
本地诊断仅对本轮放宽为每角色 900 秒、整链 2400 秒；没有启用 240 秒保护，
没有修改生产默认预算，也没有 VPS 部署或下单。

| 角色/尝试 | CLI wall | Provider 报告 input / output / reasoning tokens | 最后证据交付至结束 | 校验 |
| --- | ---: | ---: | ---: | --- |
| Stage 1 #1 | 515.141 s | 96,794 / 19,439 / 17,040 | 488.593 s | 原 Validator 通过 |
| VPA #1 | 150.203 s | 51,515 / 7,723 / 6,614 | 138.626 s | 旧 Validator 将 K1 的后续闭盘叙述误绑定 K2，触发修复 |
| VPA 定点修复 #2 | 27.797 s | 25,963 / 1,068 / 778 | 17.616 s | 原 Validator 通过 |
| Joint Stage 2 #1 | 383.172 s | 438,828 / 26,926 / 22,584 | 372.958 s | Draft 与原 Validator 通过；等待决策未触发下单 Risk |

四次模型调用 wall 合计 1,076.313 秒，约 17.94 分钟；实际串行 Host 全程还含少量
发布与校验时间。各基线只有一次 MCP 调用、没有可选调查，Gateway 单次约
0.09–0.11 秒且无 denied。Stage 1/VPA/Stage 2 的实际证据响应约为
180.7/77.2/160.1 KB，不能拿 CLI `result_bytes` 的重复序列化大小替代。
Stage 2 续接报告 264,192 cached input tokens，438,828 input 为 CLI 报告口径，
不等于 438,828 个彼此不同的新内容；最后证据后仍耗 373 秒。
该轮 Stage 2 stderr 有一次 `stream disconnected ... max_output_tokens` 的远端
sampling retry；目前不能量化其单独增加了多少时延。

最终 `run_outcome=completed_with_warnings`，`role_stop=null`，Stage 2 终点是
`wait` / `不下单`，没有执行订单。Stage 1 的空头 Spike 转换风险、VPA 的
“背景支持但即时入场证据不足”和 Stage 2 等待结构信号相互一致。初稿的
K1/K2 硬拒绝后经原文与冻结事实复核属于 Validator 主语误绑定，不是已证实
的模型事实错误；Validator 对明确错误和引用约束仍不可省。修复稿还有两条多 evidence selector 共用比较句的
专业文字 warning，但核对 K4–K1 原始 catalog 后未发现其量价方向或因果关系
相反。Stage 1 与 Stage 2 各有一项无歧义字段标准化，不是分析失败。

这证明**一轮**新合同可以完整运行并持久化，不证明稳定成功率、交易质量非劣或
生产 p95≤300 秒。Stage 1 单独 515 秒，Stage 1＋VPA 首稿已约 665 秒；
当前瓶颈是大任务下的模型处理/生成与续接上下文，不是 Gateway、MCP 往返或
最终 JSON 体积。不能为达到 300 秒而删 Brooks/VPA 必读知识、取消反证或
放宽 Validator。后续性能研究应把模型输出/推理量、续接上下文与服务端
波动分开测量；在没有质量盲评与多快照数据前，保留上线 no-go。

额外核对 CLI 事件的 MCP `result`：官方 SDK 同时记录 `content` 文本与
`structured_content` 对象，Stage 1 两者分别约 255.6/187.4 KB，解码后是
同一 packet。CLI 事件 JSON 的体积因此不能直接当作模型实际读入两份的
证据；Stage 1 的 96,794 reported input tokens 也无法单独确定内部选用了
哪一种表示。现阶段不据此修改 MCP 返回格式或旧 Frozen Job 回放语义；
若研究单份无损交付，须先确认模型可见内容、官方 SDK 行为与新旧 Job 兼容，
再用同快照验证质量及 token/时延。

### 2026-09-24 同快照第二次 xhigh 完整复验与 VPA 校验根因

用户确认后，以相同冻结 XAU 输入、`gpt-6-luna/xhigh`、代码/知识 domain
fingerprint、900/2400 秒诊断超时重新完整运行
`single-trace-20260924-012711`。没有重新部署、改变生产预算或下单。

| 角色/尝试 | CLI wall | Provider 报告 input / output / reasoning tokens | 最后证据交付至结束 | 校验 |
| --- | ---: | ---: | ---: | --- |
| Stage 1 #1 | 303.531 s | 96,806 / 16,235 / 13,754 | 291.906 s | 通过 |
| VPA #1 | 193.281 s | 52,371 / 10,266 / 9,104 | 182.519 s | 同类 K1/K2 主语误绑定触发修复 |
| VPA 定点修复 #2 | 32.266 s | 25,882 / 1,327 / 953 | 22.982 s | 通过 |
| Joint Stage 2 #1 | 122.312 s | 447,086 / 22,464 / 18,130 | 112.289 s | Draft 与原交易 Validator 通过 |

四次 CLI wall 合计 651.390 秒；Host 从分析到持久化约 654.172 秒（10.90 分钟），
首轮相应约 17.9 分钟。两轮均 `completed_with_warnings`、无 role stop，Stage 1
均为偏空 Spike 转换、VPA 均为背景支持但当前入场证据不足，Stage 2 均为
`wait` / `不下单`。第二轮 Stage 1 Router 额外选择交易区间知识，Stage 2
证据响应 216,202 bytes（首轮 160,098）；两轮虽同源同配置，却不是逐 token
相同负载。不能从 Stage 2 的 383→122 秒差额断言某一软件改动提速，也不能
据此估计 p95。第二轮四次 MCP 总处理时间仅约 0.439 秒、无可选调查和 denied；
主要时延仍在模型收到证据后的处理/生成，而非 Gateway 或往返。
两轮 `investigation=agentic` 都没有调用可选调查工具，因此只验收了受约束
Codex 角色的完整基线推理，不是“主动调查优于 Direct”的证据。

两轮 VPA 原始首稿都明确把“没有更晚已收盘棒”指向最新 K1，K2 仅作为前棒
或相对活动比较对象；冻结目录记载 K1 后续闭盘数为 0、K2 为 1。旧规则在
整句出现否定短语后遍历句中所有 `K#`，因此错误拒绝 K2。现在
`role_validator.py` 只对明确主语及明确并列事件做此项 hard error；明确的
`K2之后没有更晚已收盘棒` 仍拒绝。两份**原始首稿**离线重新校验均不再有
hard error，相关 48 项回归和 Ruff F/E9 通过。此修复发生在第二轮结束之后，
尚未做第三次付费全链路，不能把“离线可通过”冒充新的真实运行结果。

第二轮 VPA 的因果基线表述与目录数值相符：`vpa-w040` 当前相对基线
`above`、与先前窗口 `equal`，`vpa-w010` 当前相对基线及较先前窗口均为
`above`；剩余 `professional_prose_warning` 至少含词法误报，不能当成确定
事实错误，也不能据此宣称专业质量已通过盲评。`run_outcome` 的旧字段
`requested/effective_vpa_mode=off` 是关闭旧 Shadow/Soft 模式；本次实际链路
由版本化 `analysis_pipeline.pipeline=pa_vpa_joint` 指定，正式 Stage 2 中有
已验收 VPA report 的联合论证与引用。两轮证明新链路可重复完成，但 10.9–17.9
分钟均远超整链 p95≤300 秒，Provider 报告总 tokens 约 668k/672k 也高于
`quality_contract.toml` 的整链 p95≤250k 门槛。两个样本不能估计 p95，
但均已超过对应单次阈值，仍不满足性能上线门禁。

### Stage 内 subagent 研究方案（仅设计，未启用）

本节是 2026-09-24 之前的设计记录；当前版本化实现、无 MCP 子角色边界和逐阶段三角色验收，以 [Stage 内子 Agent 研究与验收计划](stage_subagent_research_plan.md) 为准。下文历史设想中“child 独立 MCP 审计”尚未实施，不应当作现有能力。

**研究问题。** 在保留完整 Brooks/VPA 必读原文、原 Validator/Router/Risk、
`gpt-6-luna/xhigh`、同一 Frozen Snapshot 与 PA/VPA 隔离的条件下，让一个
受限子角色并行核对单一争议，是否能减少父角色的关键路径推理时间，同时不增加
重大事实/交易错误？当前两轮成功是 single-Agent 对照，不是 subagent 效果
证据。一次子角色调用若要复制大量基线、等待后再综合，可能更慢、更贵；不得
把“可并行”当作“必提速”。阶段间仍按原 Stage 1→Validator→Router→VPA
独立报告→Joint Stage 2→Validator/Risk 串行交接，不拆成三个互相取代的 Agent。

1. **能力与权限预检。** 当前 Worker、发布配置和 runtime audit 显式关闭
   `features.multi_agent`、`multi_agent_v2`、`agents.enabled`；实验不能只改
   AGENTS/Skill 文案。先在锁定 Codex CLI 版本上离线验证 native child 的
   工具继承、进程/会话身份、超时、输出收集与审计路径，以及父角色委托后
   能否继续处理其他工作；若实际串行等待 child，就没有并行提速前提。只有 Host 能给每个
   child 限定同一 Job digest/cutoff、角色 Evidence allowlist、只读 MCP、
   无 shell/Web/文件写入/额外知识库，才准许真实模型试验；否则停止 native
   subagent 路线，另行研究 Host 管理的隔离只读并行任务，不把后者冒称前者。
   初始深度 1、每 Stage 最多 1 个 child，父/子总并发与 token、时间预算均
   有界；跨 Snapshot 禁共享会话或记忆。Direct、旧冻结 Job 和生产默认保持
   关闭，按新 Job 显式实验标志与 digest 隔离。
2. **Stage 1 单点处理。** 父角色完整读取 price-only、HTF、Router 前 PA
   必读知识和全部 100 根 K 线，仍负责主/替代假设、Brooks Gate 与最终 Draft。
   child 在父角色读完基线后只核对其指出的一项“主假设与最强反证”的已冻结事实、leg/acceptance
   关系，返回证据 ID、支持/挑战、未解决条件，不选方向、Setup 或交易。父角色
   委托后继续独立归纳其他问题，而不因 child 已读跳过自己的 mandatory read；
   child 结果是可质疑的分析，
   不是 Host 测量或 validated Stage 1。
3. **VPA 单点处理。** 在独立 VPA Job 内，child 只核对一个 effort/result
   或事件—后续闭盘序列，逐项使用各棒因果基线及 `later_closed_bar_count`，
   明示反证和未知。它不得看到 PA 原始会话、创建 PA Setup、交易方向或订单。
   父 VPA 仍负责完整报告、primary/alternative 关系及“背景支持”与“入场
   时机不足”区分；最终由现有 VPA Validator 验收。
4. **Stage 2 单点处理。** 只在 Router 已有真实候选且入场位置、失效位
   或目标障碍存在具体争议时，child 独立挑战该候选的信号链与价格空间；纯
   等待案例默认不生成 child。它只读 validated Stage 1、Router 许可知识及
   已验收 VPA report 的授权引用，不能读 raw VPA catalog、改上下游或产出
   订单 Draft。父角色独占候选/等待比较与最终 Draft；交接完整性、身份、
   投影、资源故障仍由 Host fail-closed，不能让 child 的文字复审这些不变量。
5. **固定交付与失败语义。** child 只能返回小型结构化
   `question / evidence_refs / support / challenge / unknown / source_digest`；
   不携带 order、胜率、role stop 或阶段最终结论。Host 分别审计父/子工具
   调用，child 读取不计入父角色 mandatory-read 完成。身份、权限、预算或
   子任务交付失败记为实验失败/覆盖缺失，不得伪装成市场 No Trade，也不能
   静默回退为“成功的单 Agent 样本”。专业分歧由父角色在冻结证据内裁决，
   原 Validator 和单一 materializer 不变。
6. **顺序实验。** 先仅对 Stage 1 作一个 child 的离线契约与小规模同快照
   A/A→A/B 试验；通过后分别试 VPA，再在含真实候选的快照试 Stage 2，
   最后才试同周期组合。对照与处理均用 `gpt-6-luna/xhigh`、相同
   `fixed/agentic` 调查政策、知识/Schema/冻结输入、预算与 Router 版本；
   预登记顺序和失败，不用重跑洗掉超时或错误。盲评主/替代解释、事实引用、
   VPA 因果比较、入场/等待、三价结构和重大错误；分层包含趋势、区间、
   突破接受不明、VPA 冲突以及等待/候选，不只重复本次一个 XAU 快照。
7. **验收门槛。** 同时报每角色和整链的持久化 wall/关键路径、p50/p95、
   推理/输入/输出/缓存 token、总成本、optional evidence、修复/停止率、
   Schema 合规、交易结果和盲评。小样本 pilot 只决定是否扩样，不能声称
   p95 或收益提升。先预注册可接受的额外成本与最小有意义时延降幅；重大
   事实、越权、no-lookahead 或交易安全错误为否决项。只有在多快照质量非劣、
   完整链路有稳定实质提速，且达到 `quality_contract.toml` 的整链 p95
   门禁后，才讨论生产启用；若只增加 token/解释而不改善决策或时延，弃用。

三角色审查各有否决权：软件架构师核权限、身份、失败语义及关键路径；量化工程师
核预注册、快照配对、无前视、覆盖和成本统计；交易员盲核 Brooks Context、
VPA 反证、Setup/等待与结构失效位，不以文笔长度替代交易判断。

本方案不更改当前生产默认、不实施子角色，也不提前降低思考强度。即使研究
成功，Stage 内 child 只是受约束的证据挑战者，不改变 PA Agent 的事实、
Brooks 主框架、VPA 辅助层、Router/Validator/Risk 或最终交易权限。

### 2026-09-24 清理、逐阶段三角色审查与子角色研究补充

**清理边界。** 先核对当前 branch、36 个既有未提交文件、目录引用、进程与
reparse point；只移除工作树根目录 15 个未跟踪的 `.pytest_*` 临时测试目录。
源码、完整知识、Skill、运行原始记录和现有未提交实现均保留。`decision_packet`
的完整 mandatory section、`evidence` 的 Stage 2 交接回执、Worker 的 Host-gated
Draft 与相应测试仍在当前正式路径上；没有证据证明这些代码或整篇知识是死代码。
Stage 2 Skill 有两处“不计算仓位/Trader Equation”的措辞重复，但删去一处
只会改变 Skill digest，不会解决模型主耗时，因此本轮不改冻结分析规则。

| 阶段 | 架构师：链路与权限 | 量化工程师：性能与可复算 | PA/VPA 交易员：质量与下一步 |
| --- | --- | --- | --- |
| Stage 1 | packet 一次交付完整 price-only、HTF、Host 选中 mandatory 原文；Router 仍只用 validated 输出。不能再按文件名暗删模块。 | 两轮首稿 515/304 秒，基线交付后分别 489/292 秒；MCP 非瓶颈。对事实引用、比较区间作有界离线核验，知识压缩须 matched replay 证明非劣。 | 主假设、最强反证、突破接受与位置判断不能换成机械指标。只针对明确争议减少重复推论，保留完整 Brooks 背景。 |
| 独立 VPA | 只收 PA 已验证假设与冻结 VPA catalog，不接管 Setup/Router；模块由 PA 枚举选择。 | 两轮首稿 150/193 秒；旧 K1/K2 主语误绑导致两次额外 repair，已离线修正、尚未经新付费整链复验。按单个事件核对 later-closed-bar 和比较对象，区分词法 warning 与事实错误。 | 背景支持与即时入场支持分开；高量/低量、吸收/衰竭须有同源基线和后续响应，不能凭标签补造信号。 |
| 联合 Stage 2 | Host 负责交接身份、知识继承、PA 投影、VPA 报告 catalog；模型仅交 Draft，Validator/Risk 独立。 | 两轮首稿 383/122 秒且同快照负载不同；CLI 报告累计输入 439k/447k，含缓存与续接，不等于本轮新增知识。先在相同冻结输入比较 fresh 与 resume 的完整成本/质量，不能仅据一次波动改默认。 | 在 Router 边界内比较候选与等待，结构 stop、TP1 障碍、VPA 反证不可省。两次 wait 不代表能评价可交易候选质量。 |

逐层核对：Stage 1 知识由 `stage1_prompt_txt_files` 定义且 `decision_packet.py`
保留全部已选 mandatory section 与反证；`stage1/SKILL.md` 保持主/替代假设、
K 序号、已发生/未来确认、引用域和两次可选调查边界；`evidence.py`/Worker
校验一次 packet 与身份。当前可研究的是有损输出重复和事实错绑，而非删除
Brooks 原文。VPA 知识由 `vpa_context_profile.py` 仅依已验证 PA 枚举/pattern
选取，`knowledge_bundle.py` 将选中全文设为 mandatory；
`vpa_evidence/SKILL.md` 要求逐事件的因果基线、后续闭盘、独立反证与 volume
provenance；`role_validator.py` 的已定位误拒修复需要在后续真实样本中证实
repair 率变化，不应为提速放松事实和 catalog 校验。Stage 2 知识仍由
`stage2_prompt_txt_files` 与 Brooks Router 控制；`joint_stage2/SKILL.md`
明确 PA 候选、结构三价、已验收 VPA 支持/挑战与合法等待；Publisher、Gateway、
Worker 和唯一 materializer 分别掌管冻结/交付/传输/决定性数学。优先研究
续接上下文的实际成本以及候选案例，而不是删掉有权限区别的普通/联合 Skill。

**规范冲突待单独修订，不当作本轮性能补丁。** 根 `AGENTS.md` 与
`CONTEXT.md` 的“VPA 不得修改 estimated_win_rate”针对旧 Shadow/研究补丁；
ADR-0025 与联合 Skill 允许新版联合 Stage 2 在 PA/Router 内自行提出未校准的
联合主观先验。应给前两处加清晰协议作用域，保留旧禁令，不让 VPA 独立角色
产出概率。公共必读的 `提示词大纲_人设与思维方式.txt` 同时含 price-only 与
裸 JSON 语句；联合 Stage 2 则需要已验收 VPA 报告和 Runtime `result` 封套。
原文已有 Host 协议优先说明，目前只能判为潜在误读/认知负担，不能说它造成
这两次耗时或错误。若角色化分层，须保留 Brooks 专业原文，并先做 Direct/Codex
同快照 replay 与规则逐条等价检查；不在本轮改 Direct 共用知识。

**子角色方案的追加预注册要求。** 前一节是候选设计，不是已实现功能；当前
Worker 明确关闭 multi-agent。研究先锁定 CLI/模型版本，验证真实父子并行、
同一 Frozen Job 身份、只读 MCP allowlist、工具审计、无 shell/Web/文件写入、
超时与回收。子角色使用新版本化小 Skill/Schema，仅回答一个明确事实矛盾或最强
反证，不复用 Stage 1/VPA/Stage 2 的最终 Draft Skill；父角色独立完成所有
mandatory read、最终判断与原 Validator。无真实争议、只有合法等待或仅需
确定性算术时不 spawn；可预先定义触发问题和停止条件，不为展示 Agent 能力
强行派生。VPA child 仍与 PA 会话隔离；Stage 2 child 只见被允许的 validated
VPA 报告，不见 raw VPA catalog。初始深度 1、每 Stage 最多 1 child，仅实验 Job
显式开启，Direct、旧 Job 与生产默认不变。

先仅试 Stage 1，再 VPA、最后有真实候选的 Stage 2；每项使用同快照同模型
`gpt-6-luna/xhigh` 与完整知识的 A/A 后 A/B。预登记触发率及 no-spawn 情况，
按 intention-to-treat 纳入 child 超时、无交付和权限拒绝，不只报告成功样本。
记录父子实际重叠区间、串行 Host 关键路径、角色和整链 wall、实际/缓存/推理
token、成本、repair、停止及重大错误；父子耗时不相加冒充整链。质量盲评
主/替代假设、VPA 因果、候选/等待和结构三价，重大越权、事实漂移、前视或
坏单为否决。child 失败不得伪装成市场 No Trade；若父角色独立继续，处理组
仍记 child 失败。阶段一加 VPA 的首稿已分别约 665/497 秒，单个 child 不能
预设突破整链 300 秒门槛；只能在多快照、完整成本、质量非劣且关键路径确有
实质缩短时继续扩样，否则停用这一研究方向。此方案不承诺既定提速：预期收益
是对明确争议的并行反证核对与可能减少漏证；主要风险是重复上下文、额外 token、
协调等待和权限面扩大。
