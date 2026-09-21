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

同一实现者的三个视角，不是独立专家评审。

| 视角 | 当前判断 |
| --- | --- |
| 软件架构师 | 修复保持单一 Publisher / Gateway / Validator；反馈与知识继续冻结读取。链路未完整验收，不能晋级。 |
| 量化工程师 | 累计 token、缓存、局部估算、失败与缺失分开；诊断延长预算和复用 Draft 不可冒充生产性能。 |
| PA / Wyckoff VPA 交易员 | 补回几何及引用，保留全文与既有规则；仍需检查真实 VPA / 联合结果的证据、反证与阶段连贯性。 |
