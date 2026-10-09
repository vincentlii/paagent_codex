---
lifecycle: active
capability: paper-observation-limited
verification_scope:
  - local
  - VPS-controlled-live
最后核验: 2026-10-10
核验提交: b295aa711b66ec0b6d0c15f6d56405e2f0bf2ff5（运行代码）
权威来源:
  - docs/quality/pre_release_final_audit_20260813.md
  - docs/quality/acceptance.md
  - docs/architecture/ADR-0023-vps-observability-and-control-plane.md
  - docs/architecture/ADR-0024-codex-analysis-backend.md
  - docs/quality/codex_backend_final_audit_20260902.md
  - docs/quality/vps_profile_dashboard_audit_20260903.md
  - docs/quality/codex_backend_full_acceptance_audit_20260905.md
  - docs/quality/codex_backend_supplement_implementation_review_20260905.md
  - docs/quality/stage1_subagent_market_pilot_20260928.md
  - docs/quality/subagent_complementary_pilot_20260928.md
---

# 当前状态

## 2026-10-10 四项运行升级与并发 3（已发布）

用户批准的会话保留、黄金数据背景、三品种受控并行和三档 Stage 2 倾向已在当前
worktree 实现；初次 323 项离线回归及发布前 194 项定向回归通过，关键静态检查
和看板 JS 语法检查通过。用户随后明确授权并发 3、提交与发布，运行提交 `b295aa7`
已安装到 VPS；260 个代码/Runtime 文件与 38 个 Prompt 资源核验一致，隔离审计通过。
三服务 active、退出码与重启计数 0，Paper 恢复运行。没有强制模型重跑或启用 BTC/ETH；
安装成功不等于长期健康、三任务容量或交易效果已证实。

默认仍单黄金、并发 1；看板可显式选择 Binance XAU/BTC/ETH 与并发 1/2/3，
本次用户授权的当前 VPS 调度与实际 Worker 容量为 3，仍只启用黄金。控制 generation
24、Profile generation 3，desired/applied 一致；备份 `pa-agent-20261009T172623Z` 已验证。
每个品种沿原联合链顺序运行，不同品种有界并行、同品种 FIFO。停用新分析后，
正式 Paper 活跃订单继续观察。交易倾向只复用已有 Stage 2 选择性，旧冻结
Profile 指纹与原已知倾向保持兼容。记忆继续关闭；空闲 Worker 每小时有界
清理超过 14 天的专属会话，不删认证、正式 records、账本或恢复任务。
本次空闲维护清理 1 个符合条件的专属会话、errors=0；正式记录保留。

黄金产品/单市场证据/纽约 DST 参考时段在最新确认收盘边界冻结；假日日历
未知不猜测，不成为 Gate。知识与 Skills 原文未删减，没有新增模型分析阶段。
第 5 点交易位置继续通过持续 Paper 数据校准，不添加距离、止盈或固定胜率规则。
实施边界、三角色自审与发布顺序见 [运行升级报告](../quality/v1_operations_upgrade_20261010.md)。

## 2026-10-09 V1 Headless 体检与修复发布

最新排障基线为 `e4d57e2`。初次 VPS 联合链成功不代表此后持续健康：最近自动
分析在 Stage 1 的 Worker 边界快速失败，原记录没有保存底层启动分类。Fast 的
desired/applied 均为 true、generation 一致，最近冻结任务身份匹配；两次获批的
3 秒隔离启动未复现立即退出，不能据此宣称故障恢复或把 Fast 定为根因。

本轮已修复单 Agent 失败观测丢失、未知用量／首稿质量投影、同步调度回调竞态、
pipeline cohort 身份缺失、v3 开仓后缺棒的不确定成交路径，以及 CLI/MCP 私有
进程树清理。全量 PA/VPA 知识、Router、专业判断、订单数学和单 Agent 联合协议
保持不变；Runtime AGENTS 只补中文解释规则。Headless 扩大离线回归 905 项通过，
最后增补的直接影响范围再次通过。GUI 不属于本轮验收范围。

用户已授权备份、暂停、提交、发布并重启三项服务，恢复原有 Paper 调度；不额外
触发完整模型调用。修复运行提交 `49fc9ef` 已部署，297 个源码／安装文件逐一一致，
Runtime 资产及权限检查通过。三服务全部重启、预检成功，generation 19 的
running/Fast true 已应用，无旧任务重复；备份 manifest 校验通过。
**21:00 自然任务已完成三角色分析，但 Paper 因闭盘行情修订而拒收；历史快速失败
本次未复现，仍未闭合唯一根因。**本笔 Host 386.369 秒，零 repair/fallback，存在
Stage 2 无歧义规范化，不能称 first-pass clean。PA/VPA 判断及已核对数值基本自洽，
替代假设确认条件有语义错误；模型运行／索引元数据误标 DeepSeek，两条自然语言
校验有误报。21:52 只读审查之后，用户授权小范围 Paper 修订。本地已实现审计
版本、跨账户已消费价格保护、冻结观察时间传递、旧结果防倒灌；修正真实调用
模型身份投影及两处语言误报。Stage 1 只替换原有假设条件说明，不增加硬 Gate、
重试或必读内容。22:00 自然分析在旧 VPS 代码上仍遭同类 Paper 拒收。

用户随后授权提交发布：修复提交 `8e41db4` 已安装到 VPS，暂停／备份／停服／
重启／恢复全部完成。备份 `pa-agent-20261009T153032Z` 的 manifest 与三个 SQLite
校验通过；171 项发布前核心回归通过，297 个源码／安装文件一致，Runtime 资产及
权限通过。23:38 三服务 active、NRestarts=0，generation 21 running/Fast true，
profile generation 2 仍为 `gpt-6-luna/xhigh`，没有 queued/running/unfinished 任务。
自然行情轮询已审计接收一处未影响执行的价格修订，Paper 不再被该旧棒阻塞；23 份
原始记录摘要未变，旧拒收回执不改写。新代码完整整点分析仍待自然任务验证，
不宣称长期稳定、专业效果或性能改善已获证明。未额外调用模型。
发布与自然任务完整证据见 [V1 体检报告](../quality/v1_health_audit_20261009.md)。
本节优先于下方历史能力表；B 分层与子 Agent 不晋级，真实下单仍禁止。

## 历史证据（以下条目保留当时状态）

2026-10-09 全量 A 已以运行提交 `48eb9b1` 安装到原 VPS `/opt/pa-agent`，替换旧
安装；已按用户授权启动有界持续 Paper，不接入真实下单。Codex CLI 固定 0.162.0，
ChatGPT 登录、AGENTS/Skill/Fast canary、全包源码／安装产物比对、用户权限负向
检查、公开行情探测、59 项 Linux 定向回归、备份隔离恢复和真实联合链通过。
首次 VPS 联合链三角色首稿 validated、零 repair/fallback/denied，调用合计
373.96 秒、Host 到结果可用约 377.21 秒、540882 Provider Token，选择等待。
任务／原始记录／独立 `pipeline-pa_vpa_joint` Paper 的 no_trade 回执一致；暂停
重启后没有重跑。三个服务与每日备份 timer 已启用，Fast requested/applied 为 true。
仍有 PA/VPA 引用表达警告，不宣称 p95、收益或固定 1.5× 加速达标。VPS 原始
记录原位保留，没有导出完整私有事实；看板只经 SSH tunnel 访问 loopback 8765。
详见[发布与三角色验收](../quality/codex_joint_local_release_audit_20261008.md)。

2026-10-09 用户批准全量 A 的有界 VPS Paper 观察发布，并豁免性能目标作为该次
Paper pilot 的阻断条件；这不代表专业质量、收益或实盘上线晋级。发布配置固定
单 Agent、`pa_vpa_joint`、`gpt-6-luna/xhigh`、单源 XAUUSDT/1h＋4h，B 分层与
Stage 内子协作保持研究隔离。看板已补充 Fast 的 requested/applied 控制，冻结
任务与 cohort 绑定 service tier；不保证 Luna 固定 1.5× 加速，订阅用量可能增加。
本地非在线回归 1573 项通过（7 项 live 排除），新增备份服务边界定向 6 项通过。
Linux 备份、恢复、权限、模型可用性及真实完整链仍须在 VPS 验收后才能启动持续
调度；下方 no-go 是各历史时点结论，不应当作用户本次 Paper 授权被撤销。

2026-10-09 同一新冻结 XAU 快照的新源码 A1/B1/B2/A2 分层知识对照完成 4/4，
三角色均首稿验收、无 fallback 或下单。匿名质量先评分后揭盲：全量 A 的 PA/VPA
均分 93.0/92.5，分层 B 89.25/92.5；B Stage 2 平均快 12.5 秒、Token 少约
3.3%，但全链平均慢 15.1 秒，且预注册 PA 质量门槛未过。只有一张独立行情快照，
不证明 B 质量下降或 A 稳定占优；旧快照结果方向相反。正式候选继续全量 A，
B research-only，性能门禁与 VPS/Paper 上线仍 no-go。见
[分层知识研究](../quality/layered_stage2_knowledge_research_20261008.md)。

2026-10-09 新冻结 XAUUSDT/1h＋4h、本地修正后源码的 Codex 单 Agent 全量
PA＋VPA 联合链完成三角色首稿验收，Host 273.53 秒、Provider 510,335 Token，
正式决策等待且不下单。Snapshot 闭盘与事实核对通过；VPA 有 6 条逐句引用
表述警告、Stage 2 有 1 条无歧义规范化。单次时延低于 300 秒但 Token 门禁未过，
不能推断 p95 或专业质量稳定。仅清理可重建的 Python/Ruff 缓存，全部研究和原始
回执保留；见[本地联合链新快照验收](../quality/codex_joint_local_release_audit_20261008.md)。
本地候选仍为 no-go，未部署 VPS 或启动持续 Paper。

2026-10-09 全量 A／分层 B 最后离线复审：修正新 Stage 2／VPA 合并基线回执
缺失却可能接受 Draft、普通 Codex 角色人设“裸 JSON”与 `result` 封套冲突，
并以精确具名窗口的净进展符号核对拦截四臂 A1 已发生的 VPA 方向误报。
该窄范围事实校验由共享 VPA Validator 执行，也适用于 Direct 的 VPA sidecar；
Direct PA-only 主链与 VPA-off 行为不变。
旧四臂回执不改写；B 仍 research-only，正式链仍全量必读。相关本地定向
回归与 Runtime 资产审计通过，未做修正后付费模型、Linux/VPS 或持续 Paper
验收；性能／专业质量及上线门禁仍 no-go。见[分层知识研究与复审](../quality/layered_stage2_knowledge_research_20261008.md)。

2026-10-08 补充验收：本地 Codex 单 Agent `pa_vpa_joint` 已在同一冻结 XAU 快照
完成 Stage 1、VPA 和联合 Stage 2，输出等待且无订单；Host 约 324.5 秒、Provider
实报约 549.8k Token，未达到 300 秒／250k Token 性能目标。冻结任务的本地重启
恢复已按“未开始可重放，已开始须核对记录，否则阻断”闭合，真实 Linux/VPS 断电、
跨库/Paper 对账和持续运行仍未验收。原始模型回执后仅修正两处自然语言误报，
未来独立快照须重验；详见[本地发布候选补充验收](../quality/codex_joint_local_release_audit_20261008.md)。

2026-10-08 本地发布候选补充：GPT-only 显式联合配置、逐次 pipeline 审计／看板、
调度审计 fail-closed、Worker 有效租约、含接受标记的备份与校验完成定向离线检查。
**仍为 no-go**：冻结调度 payload／崩溃回执对账未闭合；本机 CLI 未登录，未做本轮
真实 `gpt-6-luna/xhigh` PA＋VPA 完整链、盲评或 p95；未部署 Linux/VPS。
详见[本地发布候选三角色审查](../quality/codex_joint_local_release_audit_20261008.md)。
旧时间点的子 Agent pilot 与耗时仍为历史证据，不得冒充本轮验收。

2026-09-28 补充：Stage 内子协作按旧 pilot 根因完成精确资源、批量交付、失败/续接用量及 VPA 指标分类修复，210 项定向回归通过。新同快照 `gpt-6-luna/xhigh` 四臂因隔离 CLI 限流完整链 0/4；仅 A1 Stage 1 首稿返回并通过 Host，B 臂无可评分结果。**质量/速度对照未完成、生产子协作关闭、未部署。**详见[修复后 cohort 与三角色门禁](../quality/subagent_complementary_pilot_20260928.md)。下方旧 pilot 数值与当时“尚未修复”描述保留为历史时点，不作为现行状态。

2026-09-28：Stage 1 真实同快照子 Agent pilot 已取得四份原始 Draft；完整 PA＋VPA
链 3 完成/1 中断缺失。B 平均 Stage 1 为 479.055 秒，A 为 406.453 秒，**慢 17.9%**；
两次 child 委托仅一次完成并收集，另一次嵌套 HTF 参数拒绝。独立模型盲审 Stage 1
A/B 为 76.95/78.25 分，但最高分出自未启动成功 child 的 B2，不能归因质量增益。
当前泛反证政策不晋级，暂停 VPA/Stage 2/组合 child 付费扩展；生产仍关闭、未部署。
resume usage 会话累计重复计量和常规 VPA auto-binding 指标分类错误已核实，
只做研究派生说明，runtime 尚未修复。详见[真实试验与三角色报告](../quality/stage1_subagent_market_pilot_20260928.md)。
该状态取代下方 2026-09-27 的“真实市场未进行”，不改变当时 canary 的证据范围。

2026-09-27：新 `host_stage_challenge_v1` 已实现 Host 独立子进程、限定原值 MCP、
异步协作、独立预算和审计、取消回收与父子用量/失败接线；AGENTS 和四个 Stage Skill
已统一。真实 `gpt-6-luna/xhigh` 合成父子 canary 通过（39.515 秒、38,445 total tokens），
父在子运行期间完成独立读取。该结果仅证明传输协作可用，不是行情性能或质量验收。
原生非 off 旧 Job 继续拒绝，生产研究开关仍关闭。剩余逐阶段真实快照 A/A/B/B、Linux
权限/部署与 Paper 晋级未完成。详见[研究计划与三角色复核](../quality/stage_subagent_research_plan.md)。

2026-09-26：子角色方向调整为同一 Frozen Job 内的独立、受限只读取证；
父角色必读、预算、审计和正式 Draft 均不得由子角色代替。证据 MCP 已在
服务端拒绝当前角色未列入 allowlist 的工具调用，并审计拒绝；这是权限基础，
**不是**父子端点隔离或子 Agent 运行验收。Host 托管的独立子进程与专属证据
入口在该日尚未实现；该状态已由上方 2026-09-27 更新取代。详见[研究计划](../quality/stage_subagent_research_plan.md)。

2026-09-26：本地 `codex-cli 0.155.0-alpha.16.4` 的合成子角色预检发现，
自定义 challenger 实际继承父角色的代码工具和 `pa_evidence` 可见性；CLI stdout
也未完整暴露 spawn 事件。Worker 已对非 `off` 的原生子 Agent 研究 Job
fail-closed。真实市场 A/B 未启动，当前冻结样本已按现行知识重建但未注册旧 pilot。
Direct、单 Agent Codex 主链及 VPS 保持原状。详见[研究计划与预检结论](../quality/stage_subagent_research_plan.md)。

2026-09-24：两处协议歧义已统一：旧 Shadow/研究 Joint 不得修改 PA-only
`estimated_win_rate`；显式联合 Stage 2 可在 PA/Router 内基于已验收 VPA 报告
独立形成未校准先验。共用 Direct 人设原文不变，仅 Codex 联合任务投影其
price-only/裸 JSON 措辞。Stage 内子 Agent 已完成默认关闭的逐阶段研究接线、
只读自定义 Agent、Skill 边界、实验单变量条件与脱敏事件元数据；尚未做真实
CLI 父子权限/事件预检、质量 A/B 或 VPS 部署，因此不能宣称提速/效果通过。
详见 [研究计划](../quality/stage_subagent_research_plan.md)。

2026-09-24：后续工程实验统一使用 `gpt-6-luna/xhigh`（UI 的 extra high）；
历史 `max` 运行保持其原始身份，不混为同条件样本。此前因 Stage 2 交接误读
停止的运行，已由 Host-gated Draft 边界修订；修订后同一冻结 XAU 快照有两次
完整联合链路落盘（约 17.9/10.9 分钟，均 wait），但不能估计 p95 或证明专业质量。
VPA K1/K2 主语误绑的 Validator 修复只做了离线复核，尚无修复后付费重验。
Stage 1/VPA 仍保留全部必读知识、事实与反证；Direct 不变，旧 Job 保持旧交付语义。
未部署新版或通过性能晋级；当前性能门禁仍 no-go。
证据和限制见 [真实运行审计](../quality/runtime_chain_audit_20260914.md)。

2026-09-21：性能优化恢复原 Windows Direct 主链的 PA 阶段连续性，但不恢复长期会话。
不同 Frozen Snapshot 严格独立；同一 Snapshot 内仅 Stage 1 attempt 1 直接通过后可由
Stage 2 续接。VPA、研究 Joint 与 repair 始终独立；Stage 1 一旦进入 repair，Stage 2
便从已验证 Artifact 新开会话。Publisher 校验 parent/thread/task/Snapshot/cutoff/runtime，
并仅对 digest 完全一致的 price frame、HTF 和知识做无损继承；新 Stage 2 产物仍必读。
该实现已通过会话身份、VPA 隔离、delta baseline、Worker/Spool 与 Direct 相关离线回归；
本机 Codex CLI 0.155.0-alpha.9.2 的 localhost 合成验收进一步证明同 thread 续接与
Stage 2 delta 确实切换到新 Frozen Job。持久 PA workspace 使用 Host 原子更新的只读
evidence binding，防止 `resume` 恢复 Stage 1 MCP 参数后继续读旧 Job。尚未做新一轮 VPS
真实时延和专业质量验收，阶段 8 仍 no-go。

2026-09-21：已定位 Luna 工具不可用的配置根因：模型要求 Code Mode，而分析运行时
禁用了宿主。Worker 现将传输与权限分开：OpenAI 受限 Code Mode、DeepSeek 直接 MCP，
两者均使用原冻结 Gateway 与完整 Skill/知识。显式关闭协作工具，隔离父桌面会话身份。
51+24 项定向回归和两个 Provider 的真实 CLI 本机合成传输检查通过；完整指令、基线
无损，Luna 嵌套工具仅冻结证据。不能据此宣称性能或交易质量通过；阶段 8 仍 no-go。
本条取代下文历史“所有新模型直接 MCP、关闭 Code Mode”的实现口径，详情见运行审计。
随后一次 fresh Luna/max 真实验收：工具基线和追加 PA 调查成功，但 Stage 1 在
239.703 秒超时，无 Draft、VPA、Stage 2。工具处理累计约 0.119 秒；剩余延迟尚不能
细分为模型计算或服务端等待。传输缺陷修复不等于性能达标；未追加付费运行或部署。
该超时曾被泛化为 invalid_request；现已新增独立非重试 `role_stopped`，统一 Codex、
Direct、预算停止和调用账本语义，不再误导为 Schema 错误或触发 availability fallback。
186 项运行链定向回归及两条 Provider 本机真实 CLI 协议检查通过。

2026-09-21 延长预算单次 fresh Luna/max：临时单角色 900 秒、全链 2400 秒，正式配置
未改。Stage 1 431.406 秒、VPA 607.954 秒、Stage 2 561.391 秒、定向修复 58.547 秒；
Host 共 1662.375 秒，755,382 Token。Gateway 44 条事件仅 0.601 秒、队列 1.950 秒，
慢速集中在模型进程侧。前三角色均返回，但 Stage 2 首稿及修复先后错误绑定 K4/K2.high，
最终被原 Validator 阻止，状态 incomplete、无正式交易决策。故放宽预算证明链路非死锁，
但性能与精确事实质量仍不达标；不能永久放宽预算作为上线方案。详见运行审计。

2026-09-18 第四步：实际 CLI→localhost 合成 Responses 请求已确认 AGENTS 双重注入，
新 Job 以 `single_frozen_agents_v1` 冻结单份交付：保留完整 model_instructions_file，
仅关闭自动项目文档重注入。真实 MCP 基线在此路径本来就只交付一份，因此不改 MCP
双表示协议。35 项定向测试及新旧传输对照通过；完整指令、Skill、基线无损。未调用付费
模型、未部署，真实性能与交易质量仍未验收。CLI 升级时用 `scripts/check_codex_delivery.py`
复核，参见运行审计“第四步”节；不要在缺少显式完整指令绑定时关闭自动 AGENTS 装载。

2026-09-18 优化步骤 1–3：分析专用指令与功能配置、全模型冻结 Skill 交付、按角色工具
筛选、已交付/仅索引证据导航及 Stage 1 缺口驱动流程已实现。83 项相关离线测试、真实
MCP SDK 内存调用、资产审计和当前 CLI 功能配置检查通过。保留完整知识、PA/VPA、HTF
及原 Host 交易链；未追加付费分析或部署，尚不能宣布提速、质量非劣或阶段 8 通过。
新执行语义用 `analysis_execution_v1` 冻结；再次运行前需显式同步专用 CODEX_HOME 的
版本化策略文件，不能绕过摘要校验。细节与三角色自审见运行审计的“优化步骤 1–3”节。

2026-09-18：Codex + DeepSeek Flash/high 同冻结快照已贯通 Stage 1→VPA→联合 Stage 2→
原校验/持久化，无输出修复；Host 至持久化前 392.578 秒，CLI 合计 390.876 秒，仍超过
300 秒目标。本次含被沙箱阻止的写 Skill 尝试，且交易文本有语义矛盾，不能作为合格样本
或质量非劣证明。后续只读模型目录、直接 MCP 提示、完整 Skill/Schema 交付及审计标签
已离线修正，未追加真实复测、未部署。完整数据与限制见运行审计文档的 2026-09-18 节。

2026-09-17 离线补充：PA 基线增加冻结的精确 fact ID 索引，省去必须单独发现 ID 的依赖，
索引不进入初始 Prompt；共享提示纠正旧 pa_evidence.fact_refs 输出位置。56项定向回归通过，
无付费调用。原知识/HTF/Validator保留，真实提速尚未验证；不能把该局部优化称为性能达标。

2026-09-17 最新：阶段 8 尚未通过真实完整链路验收，阶段 9 暂缓。已修复并发审计、长修复
反馈、价格几何/引用及 VPA 身份交接缺口；无损合并后补齐 MCP / exec / history 交付容量。
最新 fresh Stage 1 原校验通过但耗时 507 秒，性能不合格。VPA 暴露 Codex 发布完整 catalog、
原 Validator 只允许 PA 已引用窗口的范围不一致；`vpa-w005` 并非不存在。Publisher 已复用原
选择器，保留选中事实全部字段、原 catalog 和知识全文；引用错误路径同步修复，54 项定向
回归通过。当前分段验证 VPA → Stage 2；尚不代表全链通过，也不自动切回 Direct。
补充：唯一获批的 Luna/high 工程对比已 fresh 贯通并落库，无修复；三个角色 CLI 合计
563.358 秒，仍超过性能目标。与 max 主周期判断存在分歧，未证明质量非劣；正式配置
不变，停止追加付费测试。max 分段记录虽 completed，但 trace 改编号存在语义错配，
已补修复身份保护并通过55项回归，不把该旧记录算质量通过。
后续贯通与性能证据见 [真实运行审计](../quality/runtime_chain_audit_20260914.md)。
下文关于“先进入阶段 9—11”的历史安排不再适用。

## 联合分析升级进行中

阶段 8 已接入 actual/estimated/unknown 指标、调用 coverage、进程/队列/全程耗时与按 Backend 渲染。
Codex 不再预先构建未使用的 Direct 大 Prompt，mandatory 知识与完整 Evidence 保留。
2026-09-13 的 239 项定向离线检查通过，详见 [阶段 8 验收](../quality/phase8_metrics_acceptance.md)；真实实验仍按独立研究记录评价。

阶段 7 的本地实现已补齐 Host adapter、fixed/agentic 调查权限、完整冻结资源与 Paper outcome 绑定。
Codex 实验固定 `gpt-5.6-luna/max`；前两批均 0/4 完成。获外发授权后 pilot-03 已验证真实 MCP 读取，前两臂约 240 秒失败；为减少冗余实验中止后两臂，最终 0 完成/2 失败/2 缺失。
已修复 typed stop 的身份比较误报，45 项定向检查通过；本地停止同配置重跑，后续阶段 9—11 发布准备及 VPS 单任务贯通/持续模拟盘分开进行，未部署。详见 [Luna 实验补充](../quality/phase7_luna_pilot_20260913.md)。
真实盲评、前向样本和效果校准仍未完成，不代表上线或专业效果通过。
详见 [阶段 7 验收与研究边界](../quality/phase7_research_acceptance.md)。

阶段 5 联合链路与阶段 6 交付后 Paper 已完成本地实现；最近阶段 6 的 188 项必要离线检查通过，
三角色自审及边界见 [阶段 6 验收](../quality/phase6_delivery_paper_acceptance.md)。下一步为阶段 7。
下文旧日期和证据是历史状态，不代表这次 VPS 已更新；默认权限、部署和专业效果均未晋级。

阶段 3 已完成共享 PA/VPA 知识一致性修订、持久章节 ID、冻结别名与无损交付；209 项聚焦回归通过。
内容迁移、三角色自审及未验证边界见 [阶段 3 验收](../quality/phase3_knowledge_acceptance.md)。
这不代表模型分析效果、Linux/VPS 或完整发布已经验收；Runtime 角色协议继续在阶段 4 实施。

阶段 2 已实现因果闭盘/as-of、VPA provenance 读取、冻结经验池及回放绑定，90 项聚焦测试通过。
扩大回归有两项已在修改前源码复现的失败，详见实施契约；不宣称全套验收通过或可发布。
长期规则见 [ADR-0026](../architecture/ADR-0026-causal-snapshot-and-experience.md)。

上一轮未提交成果已保存为 `e445907`，新实施顺序与验收边界见
[联合分析实施契约](../quality/joint_agent_implementation_plan.md) 和
[ADR-0025](../architecture/ADR-0025-joint-stage2-pipeline.md)。阶段 1a 仅建立新契约并通过
42 项聚焦测试；阶段 1b 补齐可选记录时间/停止契约和联合引用边界，29 项聚焦测试通过。
旧记录不补造时间，不静默切换 pipeline。未启用联合运行链路、未部署、未证明交易增益。
下面历史验收结论仍只对应各自基线。

## 结论

当前 Codex 升级分支从 `965f2df` 创建；该提交是 vNext 的现有功能与知识基线。Codex Analysis
Backend 已完成 ADR-0024 设计冻结、Frozen Runtime、受约束 CLI Worker、role Adapter、真实 Frozen
Job publisher、环境驱动的生产 Factory 以及 Backend A/A/B/B 冻结协议与隔离统计。Codex 接入保持
opt-in，尚未完成真实账号、VPS、长时间回放或交易增益验证。

项目已完成 versioned Analysis Profile、单次运行控制、模型选择看板、Codex Runtime 资产/权限审计与
live AGENTS/Skill canary，具备进入真实 VPS 受控 smoke 的工程基础，但尚不能写成“VPS 已验证”或
“Codex/PA+VPA 已提高盈利”。2026-08-13 的 `1169 passed, 7 deselected` 是绑定当时审计基线的历史证据；
本轮 276 项聚焦回归覆盖新增链路，仍需目标 Linux/VPS 实机验收。

当前完整验收结论见 [Codex Backend 完整验收](../quality/codex_backend_full_acceptance_audit_20260905.md)：
工程骨架保持 research-only，禁止 `auto`。当前 working tree 已针对该审计补充 stdout typed failure、
统一四角色 Backend、Frozen Job v4 按需 Context、官方 MCP SDK、pointer repair、持久化熔断和运维资产；
这些补充尚未按用户要求运行测试或部署，因此仍属于 `not-verified`，不能覆盖审计结论或晋级。

当前发布候选固定 `binance_usdm/BINANCE/XAUUSDT/1h`、HTF `4h/60`、VPA Shadow、并发 1；
Profile UI 已完成，真实 Codex 分析、权限负向测试和 live canary evidence 尚待目标 VPS 取得。

## 版本锚点

| 锚点 | 类型 | 说明 |
| --- | --- | --- |
| `e7e415b` / `pre-vps-audit-20260813` | 回退检查点 | 上线前 P0-P4 审计前状态 |
| `5daeccd` / `pre-vps-ready-20260813` | 发布审计锚点 | P0-P4 软件实现和当时全量非 live 审计完成 |
| `f8fffc0` | 当前功能基线 | 新增隔离 VPS 控制 Dashboard，晚于 2026-08-13 全量审计 |
| `965f2df` | Codex 升级基线 | `convergence-vnext` 上创建隔离 worktree 的固定起点 |
| `2bc71ae` | Codex VPS 接线 | 独立 Unix worker、生产 publisher/factory、runtime identity 与 VPA Shadow 接通 |
| `db43846`–`03c618a` | Profile 发布候选 | Profile/control、safe apply、Dashboard、Runtime audit 与固定单资产 VPS 配置 |
| `311b12a` | 文档设计 | 项目知识库设计规范 |
| `2e2d8b5` | 文档计划 | 知识库实施计划 |

当前 worktree 分支不是长期状态事实。部署和审计应记录确切 commit、Prompt digest、Provider/settings 和 cohort。

## 模式状态

| 请求/运行模式 | capability | 正式结果 | verification_scope | 当前结论 |
| --- | --- | --- | --- | --- |
| `off` | implemented | PA Stage 2 | local + 历史 CI/全量回归证据 | VPA Prompt、测量、Schema 和历史字段应零泄漏 |
| `shadow` | implemented | PA Stage 2 | local + 历史 CI/全量回归证据 | VPA 独立追加；失败保留 PA-only 并记录 `completed_with_warnings` |
| 正式运行请求 `soft` | no-go | 收敛为 `shadow` | local + 历史回归证据 | 不授权 Joint |
| 隔离 A/A/B/B B 臂 | research-only | 研究臂结果 | local + 历史回归证据 | Joint 只输出有界 patch，不进入正式分析 |
| 真实订单执行 | no-go | 无 | not-verified | 当前仅 Paper，不声明提交真实订单 |

## 能力与证据矩阵

| 能力 | capability | verification_scope | 证据与限制 |
| --- | --- | --- | --- |
| PA Draft v2 + 单一 materializer | implemented | local；2026-08-13 历史全量回归 | [最终审计](../quality/pre_release_final_audit_20260813.md)，当前功能提交后未在本文件中声称重跑全部套件 |
| VPA Context 模块化与 Shadow | implemented | local；2026-08-13 历史全量回归 | [VPA 集成规范](../wyckoff_vpa_integration.md)；未证明收益增量 |
| 主周期 + HTF + volume provenance | implemented | local；2026-08-13 历史全量回归 | 真实数据源/venue 仍需目标环境 probe |
| 多资产有界调度和恢复 | implemented | local；2026-08-13 历史 20 资产回归 | VPS 长时间稳定性尚无实机证据 |
| Paper OHLC v2 与结果隔离 | implemented | local；2026-08-13 历史回归 | 反事实不等于真实成交或真实 PnL |
| 持续 A/A/B/B | research-only | local；2026-08-13 历史回归 | 缺少足够独立 Snapshot 和明确结果，不能晋级 |
| Linux Headless 与发布依赖 | implemented | local + VPS partial | working tree 已补 retention、logrotate、SQLite online backup/timer 和资源限额；尚未测试、安装或演练恢复 |
| VPS Dashboard | implemented | local + VPS partial | 可选择 allowlisted DeepSeek/Codex model/effort、pause/resume、三 Backend mode 与 fixed-scope Run Once；working tree 新增 Quality、Performance、Backend A/A/B/B 只读区，以及最近 200 次的成功率、首轮通过、p50/p95、初始/检索 Context 与 Evidence 指标，尚未测试或部署 |
| DeepSeek role reasoning profiles | implemented | local/provider-dependent | requested effort 与 provider 实际 applied effort 必须分别记录；某些模型可能不支持细粒度强度 |
| Analysis Backend seam | implemented | not-verified working tree | Direct 与 Codex Adapter 均返回现有 Provider Draft；Codex 统一接管 Stage 1、Stage 2、VPA 与 research Joint，`codex_only` 不创建 Direct client；VPA 仍不改变 PA official path |
| Frozen Agent Job / Evidence | implemented | local focused | v4 将完整 role context 独立冻结；新 Stage 1 使用 `bounded_context_v2`：一次 digest-bound Decision Packet 交付完整 price-only frame、全部 HTF、PA fact spine、原文 foundation sections 与完整 section index，并把 optional 调查硬限为两次。Stage 2/VPA/研究角色及历史 Job 继续 `semantic_parity_v1` 原样回放；所有资源受 digest、cutoff、role 与预算约束 |
| Codex CLI Worker | research-only | local focused | 不同 Snapshot 独立；同 Snapshot PA Stage 1 首稿通过后可受控续接 Stage 2；VPA/Joint/repair 独立 ephemeral。ChatGPT-only auth、read-only/no-shell/no-Web、required MCP、AGENTS/role skills、Runtime identity、strict-output schema 投影、stdout failure taxonomy 与无敏感信息的 auth/heartbeat 已实现；真实 CLI+MCP 合成续接已通过，仍待 VPS 真实模型验收 |
| Codex Analysis Backend | research-only | local focused | 已有有界 core context、独立 Unix spool worker、端到端 runtime identity、一次定向修复、熔断、完整 Direct PA 冷回退与环境 Factory；部署默认仍为 `deepseek_only` |
| Codex audit projection | implemented | local focused | `analysis_calls` 投影 backend、provider、job/thread identity、Decision Packet bytes/tokens、optional 调查次数、Evidence 调用/字节量与 `last_evidence_to_final_ms`；完整 JSON record 仍是唯一原始记录 |
| Analysis Backend A/A/B/B | research-only | not-verified working tree | `analysis_backend_aabb_v2` 强制四臂共用 HTF + VPA Shadow catalog，预登记 Direct/Codex 各两臂、ABBA/BAAB、独立 runtime、禁 fallback、失败 coverage、PA/VPA signature、cohort 去重、盲评 packet/独立解盲 key、已有 closed-OHLC ledger 结果联结与 CLI 入口已实现；尚无真实 Codex 样本，不能晋级 |

“历史全量回归”只表示 [2026-08-13 审计报告](../quality/pre_release_final_audit_20260813.md)记录的 `1169 passed, 7 deselected`，不能自动覆盖之后的功能提交。

## 当前已知风险

### 外部环境部分验证

- 目标 VPS 已验证三 systemd 服务、用户/目录权限、Runtime asset parity、ChatGPT device auth 与 live AGENTS/Skill canary；尚未完成重启恢复、磁盘保留和 backup/restore。
- 尚未在目标 VPS 验证真实 Provider、行情源、venue symbol/contract metadata、网络超时与速率限制。
- Dashboard 尚需验证仅监听 loopback、SSH tunnel、desired/applied generation 和 Headless 不可写越权。
- Codex home/runtime/outbox 与生产 DB、Paper ledger、DeepSeek credential 的 OS 权限已通过负向审计；spool restart、rate limit 与 typed request failure 仍未完成实机验收。

### 交易与研究未验证

- 尚无足够前向 Paper 样本证明 VPA Shadow 或 Joint 提高成本后 EV、胜率或回撤表现。
- Joint 晋级至少需要 300 个独立 Snapshot、100 个明确匹配结果，最终样本量仍需按预注册效应量和统计功效确定。
- `estimated_win_rate` 是 PA Setup 的未校准主观先验，不是历史统计胜率。
- LLM 仍可能作出理论上合理但结果错误的判断；程序只能消除明确事实矛盾、权限越界和非法订单，不能消除市场不确定性。

### Codex 阻断项与变更治理

- 联合 Stage 2 的 `layered_stage2_research_v1` 仅作为隔离研究 B 臂完成本地合同实现；
  完整 Router 原文仍冻结，正式 Codex/Direct 不变。上一份真实冻结知识的离线投影
  将 4 个策略模块的 46,182 bytes 原文留作按需读取。随后同快照四臂
  `gpt-6-luna/xhigh` 盲评显示 B 在单样本专业筛查中较好，但 Stage 2 均值
  89.3 秒对 A 的 78.2 秒、Provider total tokens 均值约 406k 对 335k；
  字节减少未转成性能增益，故不晋级正式链。该实验仅有一个独立市场快照，
  不证明统计非劣、盈利或跨行情泛化。
  详见[分层知识研究](../quality/layered_stage2_knowledge_research_20261008.md)。

- 2026-09-23 本地 `gpt-6-luna/xhigh` 尚无有效完整链路基线：五次重验的
  Stage 2 均因交接/证据来源误读而 typed stop。第四次 Stage 1/VPA/Stage 2
  各约 272/182/129 秒；第五次约 365/222/157 秒，整链约 744 秒。
  第五次模型可见 JSON 中 Stage 1 与 PA 投影的 primary direction 均明确
  为 bearish，模型仍误报“投影缺方向”。统一来源契约的 81 项离线检查
  通过，但自然语言澄清未消除系统性误停。用户已确认新 Codex Stage 2
  改为 Host 校验机械交接、模型只提交 Draft；旧冻结 Job 和 Direct 保留原
  传输语义。新版已完成定向离线合同检查，并在同一冻结 XAU 快照取得两轮
  `gpt-6-luna/xhigh` 完整 PA＋VPA 本地诊断结果：首轮 Stage 1/VPA 首稿/
  VPA 修复/Stage 2 为 515/150/28/383 秒，总调用约 17.9 分钟；第二轮为
  304/193/32/122 秒，Host 至持久化约 10.9 分钟。两轮均
  `completed_with_warnings`、`wait`、无 role stop，且没有订单。两次 VPA
  首稿的“K2 缺后续闭盘”拒绝经原文与冻结目录核实为 Validator 把最新
  K1 的叙述误绑定 K2；现仅对明确主语/并列事件硬拒绝，两份原始首稿离线
  重验均无 hard error，明确错误断言仍拒绝。修复后尚无第三次真实运行；
  两轮只证明可重复完成，不能证明稳定成功率、交易质量非劣或 p95 300 秒；
  Provider 报告每轮约 668k/672k 总 tokens，也未达到 250k token 门槛。
  性能与质量上线门禁仍 no-go。详见 [运行链路审计](../quality/runtime_chain_audit_20260914.md)。
- 2026-09-05 使用 `uv.lock` desktop+dev 环境完成全仓非 live 回归：`1305 passed, 7 deselected`。
- working tree 已冻结 Windows 20 条历史故障 manifest，并实现 stdout taxonomy、Codex VPA role 与 matched
  performance 字段；本轮继续加入 Windows PromptAssembler 语义等价协议和 mandatory-read acceptance
  invariant、知识加载顺序、Worker auth/heartbeat 投影、盲评和 closed-OHLC outcome 联结。在测试、VPS smoke、历史 replay 和前向样本完成前，仍禁止切换 `auto` 或宣称已解决幻觉/交易质量问题。
- 临时 [提示词优化日志](../提示词优化.md) 不得被误当成 Prompt 权威规范。
- 任何新修复必须避免针对单条 record 增加硬编码或自由文本硬拒绝。

## VPS 前置验收

1. 固定部署 commit、`uv.lock`、配置样例和 Prompt/contract digest。
2. 在 VPS 执行安装预检、`--check`、数据 probe 和 Paper-only dry run。
3. 验证 systemd 用户、目录 ownership、重启恢复、日志轮转和磁盘容量。
4. 验证 Headless 不导入 Qt，源码/Prompt 只读，状态全部写入 `PA_AGENT_HOME`。
5. 验证 Dashboard 无凭据、无内部数据库权限、loopback 监听和 SSH tunnel。
6. 先运行 `shadow` + Paper；Joint 研究进程与正式 Headless、账户和状态目录隔离。
7. 保存部署证据后，才把相应 capability 的 `verification_scope` 更新为 `VPS`。

详细操作见 [Linux 部署文档](../linux_headless_deployment.md) 和 [运行与排障](operations-and-debugging.md)。

## Joint 研究下一步

1. 持续收集同源冻结 Shadow Snapshot，不因 VPA 成败筛选样本。
2. 用独立研究 executor 运行 A1/A2/B1/B2，隔离 cohort 和 provider/settings/源码/Prompt 版本。
3. 同时报告 A/A、B/B、A-B 配对差异、ITT、per-protocol、coverage 和 censored。
4. 使用 Paper v2、成本和滑点做 episode-level 评价，不混入真实 outcome。
5. 达到预注册门禁后再讨论 Joint 是否晋级；未通过时记录负面结果而不是调整到迎合样本。

## 最近知识变更

- `03c618a`：完成固定单资产 VPS 配置、Codex Runtime 规则/Skills digest、非 editable
  installed/source parity、POSIX 权限契约与 live canary；服务默认 paused，仍待 VPS 实测。
- `db43846`–`5b653ed`：完成 versioned Analysis Profile、idle safe apply、exact-once Run Once 与模型选择 Dashboard。

- `2bc71ae` / Phase 7：完成 Codex 生产接线与独立 VPS worker；当阶段本地相关回归 160 项通过。Phase 8 最终 focused suite 为 168 项；目标 Linux/systemd、真实账号与交易增益仍未验证。

- `311b12a`：记录仓库内、单一事实源的项目知识库设计；实施完成后设计状态已同步为 `Accepted`。
- `2e2d8b5`：给出六阶段实施和验收计划。
- `f28aea2`：知识库主题、维护流程和 `AGENTS.md` 强制入口完成接入；中间提交保留各阶段内容与审查修正。

后续功能、验证范围、可复用根因或弃用状态改变时，按 [维护手册](maintenance.md) 更新本页。
