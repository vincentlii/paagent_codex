# 阶段 4：Runtime / Skills 执行契约验收

基线：阶段 3 `1f3d1cb`；范围为本地代码，不部署、不调用模型、不启用阶段 5。
结论：本地实现与下列离线检查通过；真实 SDK 启动、CLI 行为及交易效果未验证。
三角色是同一实现者的三个自审视角，不是三位独立专家；本轮未使用 subagent。

## 角色—Skill—必读验收矩阵

所有正常角色先 `get_job_manifest`、`get_market_core`、`list_allowed_knowledge`；
全部 mandatory 模块按冻结 load_order 用 `read_knowledge_module` 完整读取。
新 `analysis_runtime_v2` 由 Host 验收上述调用；旧 semantic_parity_v1 保持既有必读协议。
工具成功读取和覆盖检查不能证明模型理解了内容，更不能证明交易有效。

| 角色 / Skill | 额外必读 | 输出和权限 |
| --- | --- | --- |
| stage1 / pa-stage1-diagnosis | 完整 price frame、全部 HTF；存在 previous_analysis 时读取上游 | PA Draft v2；price-only；Gate/static trace 由唯一 materializer 生成 |
| stage2 / pa-stage2-decision | 完整 price frame、全部 HTF、Stage 1 / Router；存在经验时完整读冻结池 | PA Draft v2；不读取 VPA；保留被动限价的未来触价条件与结构目标判断 |
| stage2 / pa-vpa-stage2-decision | 同上，另有 PA hypotheses 与 accepted VPA report | pa_vpa_stage2_draft_v1；只综合被验收报告，不另做 VPA 或 PA baseline |
| vpa_evidence / vpa-evidence-analysis | 上游 PA hypotheses、全部授权 VPA catalog、provenance/capability/status | 独立 VPA Draft；background/location 与即时 timing 分开；无 Setup/订单/胜率权限 |
| joint_arbiter / joint-arbiter-research | 上游 PA candidate / Router / VPA report、完整 mandatory 知识 | 仅旧研究 proposal / 有界 patch；不等于联合 Stage 2 |
| repair / draft-pointer-repair | get_repair_findings；必要的精确 ID / section | 原 Patch Envelope；只安装修复 Skill；不允许重读整图、整模块或测量 |

联合 Stage 2 仍是 `stage2` 角色，由显式 `joint_stage2_agent_protocol_v1` 选择 Skill。
Publisher 要求该协议与联合 Draft schema 同时出现，并检查交接资源键；仍使用 price-only
facts/bars，VPA 只通过上游报告进入。此处不替代阶段 5 的报告验收与正式编排。
现有 off/shadow/研究入口、Stage 1、Router、materializer、Risk、Trader Equation 未改变。

## 非决策停止与修复边界

- 新正常角色用 `{"result": Draft | AnalysisRoleStop}` 传输；这是 CLI 输出封装，
  不是新业务物化器。Worker 拆封后原 Draft 继续进入现有校验。VPA schema 的定义引用
  保留在封套根节点；不改变其原 Provider schema。
- 模型可显式返回 `analysis_role_stop_v1`：执行缺失为 incomplete，不可变上游矛盾为
  review_required。停止对象不携带可执行候选；role 和可选引用由 Host 检查。
- 调用/字节预算拒绝后的市场 Draft 必须拒收；新正常角色的本地 wall-clock 预算耗尽也
  是 incomplete。真正 Provider availability 故障及旧 Frozen Job 语义保留原分类。
- `CodexWorkerStopped` 不是 availability；跨 Unix-user spool 保留类型、停止对象及身份，
  不产生成功 outbox，也不触发 availability fallback。Backend 的 ModelCallFailure 保留
  original exception。阶段 5 仍需将该对象接入新 pipeline 的 record.role_stop/run_outcome；
  当前旧 Shadow 的 VPA 失败仍只影响 sidecar，不能拖垮正式 PA 结果。
- Repair 独立 ephemeral workspace 只装载修复 Skill。网关拒绝重新分析所需整图/整模块/
  测量工具；pointer 白名单不授权市场语义重选，仍由既有 patch / retry 保护最终验收。
- 已读完整证据后的市场模糊可以等待/unknown；neutral 不代表 VPA 数据缺失。
  数据确实不足的完整 VPA 报告使用契约内 insufficient_data；资源读取失败不能伪造这种报告。

## 有界测量，不建立行情分类器

`measure_price_window`：显式指定 1–100 根冻结 K 线，返回 high/low/range、bar_count、
mean_bar_range 和 net_change（最新 close 减最早 open）。比较两段窗口需分别明确指定，
程序不自行选腿，也不判定趋势、吸收、Spring、突破接受或 Setup。

`project_price_leg`：三个明确 OHLC 锚点，按时间先后且跨度不超过 100 根，计算
`origin + (end - start)`，保留正负号。不是目标建议；锚点意义与 MM 适用性属于 Brooks 判断。
非法 K 线、未闭盘/越界、缺失/非有限/布尔数值均拒绝，不能补零或借未来行情。

每份回执包含公式协议版本、Snapshot identity、输入参数、源 K 线、结果和 SHA-256；
成功测量回执进入 Evidence audit / outbox。用同一 Frozen Job 和 request 可重算相同结果。
回执不是新 catalog fact ID：Draft 仍使用真实源 K 线/fact 引用，不得把 measurement_digest
填入 fact_refs。原事实目录不被工具修改，不新建平行 Validator。

## 自定义规则与知识保真

Runtime AGENTS 固定 Host 权威和停止语义；Skills 编排调查，不复制领域知识库。
完整价格、HTF、阶段切片和 mandatory 知识仍保留，没有删除 PA/VPA 原文、案例或反证。
初始 Prompt 保持身份与索引，测量、历史、知识和上游详情通过同一个 digest-bound Job 读取。
工具读取仍消耗上下文；本阶段不声称更少 Token、更低幻觉或更高收益已被证明。

配置与 Worker 命令同时禁用 shell、Web、Apps、multi_agent、memories、hooks、goals、
remote_plugin 和 Skill 自动安装 MCP 依赖；只配置 pa_evidence MCP，使用 read-only sandbox、
approval=never、独立 CODEX_HOME 和 ephemeral workspace。不使用忽略 AGENTS/rules 的参数。
发现隔离 CODEX_HOME 内存在未版本化 AGENTS/AGENTS.override 时拒绝启动，不删除或覆盖它。
规则文本是纵深防御，不代替 VPS 用户、文件权限和真实 CLI canary。

使用 skill-creator 的范围/元数据/渐进加载规范完善六份 Skill；源码包中的资源目录名是角色键，
Worker 实际安装的目录名与各 Skill frontmatter name 一致。
官方依据：[Skills](https://learn.chatgpt.com/docs/build-skills)、
[AGENTS 加载](https://learn.chatgpt.com/docs/agent-configuration/agents-md)、
[Rules](https://learn.chatgpt.com/docs/agent-configuration/rules)、
[配置字段](https://learn.chatgpt.com/docs/config-file/config-reference)。

## 三角色自审结论

| 视角 | 本阶段证据与结论 | 未被证明 / 下一阶段责任 |
| --- | --- | --- |
| 软件架构师 | 角色/Skill 显式映射、修复隔离、停止对象跨 spool、旧 schema 兼容；无第二物化器和主链 | 真实 SDK/CLI 和 Linux 权限尚未运行；阶段 5 负责联合编排与停止持久化 |
| 量化工程师 | 同 Snapshot 锚点、符号正确、有限值检查、测量可复算；预算失败不当 No Trade | 工具调用日志不等于认知理解；实际 Token/时延/校准/收益需后续配对实验 |
| PA/VPA 交易员视角 | 保留完整背景/HTF/反证；限价触价与已出现信号分开；VPA 背景与即时 timing 分开；结构 stop/TP1 | MM 数值正确不等于锚点选择正确；Skills 的真实专业效果需盲评，不能用静态检查替代 |

## 验证与已知限制

194 项聚焦离线测试通过，覆盖 Runtime contract / Worker / 冻结 Job / Gateway / Publisher /
Assets / Spool / Backend、原 PA/VPA Shadow/研究、知识、Provider Contract、HTF、records/audit。
新测试文件为 `tests/unit/test_runtime_execution_contract.py`；其 SDK 注册检查使用注册替身，
只证明新旧 MCP 入口复用相同网关与工具名，**不证明真实 SDK 启动成功**。

阶段 2 遗留的两项测试已修正测试契约：初始 Prompt 允许索引出现 previous_analysis 名称但不
携带正文；Codex Shadow mock 正确绑定独立 PA 与 VPA Backend，不恢复已淘汰的强制 Direct。
另将认证分类 fixture 改为明确 `HTTP 401`，不让裸数字成为 availability 判据。
新增 Worker 测试最初错误地改写 Frozen Job 后未重算 digest，已改为通过 Builder 构造，
保留完整性校验而非绕过它。

真实 stdio 两项测试因当前 venv 未安装可选 `mcp==2.1.1` 无法启动，未纳入通过计数，
没有安装新依赖或回退自制 transport。skill-creator 的 quick_validate.py 因缺 PyYAML 未运行
成功；改用项目 Runtime audit 与元数据/工具名契约测试，六份 Skill 均检查。未运行模型压力
测试、Web 行情、交易收益、VPS 或全仓库测试。

阶段 11 必须补真实 MCP SDK、发布 CLI 配置兼容、wheel 资源与 Linux 用户权限验证。
阶段 5 前必须读取本文件，不得把 Runtime 接口准备误报为联合运行链路已启用。

收尾复核：文档同步后再运行 Runtime execution contract 与 assets 两个测试文件，25 项通过；
它们是上述 194 项中的子集，不重复计数。`git diff --check` 通过。本阶段独立临时测试目录
已清理，未触碰此前不可访问的旧 pytest 目录。代码保留在阶段 3 基线之上，未修改部署环境。
