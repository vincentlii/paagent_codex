---
lifecycle: active
capability: not-applicable
verification_scope: not-verified
issue_status: monitoring
最后核验: 2026-09-01
核验提交: 965f2df
权威来源:
  - AGENTS.md
  - CONTEXT.md
  - docs/quality/pre_release_final_audit_20260813.md
  - docs/architecture/
---

# 问题历史

本页记录跨样本成立的问题族，而不是逐次运行流水账。单次错误先保留原始 record、audit、日志和模型输出；只有形成稳定根因、错误修法或复发检查后才进入这里。

2026-10-12 对 PH-03、PH-13、PH-18 补充本地契约验证与复发检查；实现提交
`c20d04f` 已固定发布到 VPS，具体发布证据见
[修订报告](../quality/knowledge_prompt_investigation_20261012.md)。这不代表重新
验收其余历史问题或证明真实模型质量改善。

### PH-01 程序越权解释 PA/Wyckoff 语义

- `issue_status`：resolved，持续 monitoring。
- **症状：**程序从指标、窗口方向或自由文本推导趋势、推动/回调、Setup、Spring、UTAD、Absorption 等标签。
- **影响：**把多解交易理论变成僵硬阈值；对单一样本看似稳定，跨资产/周期失真并锚定 LLM。
- **根因：**混淆“可复算测量”和“依赖 Background/Location 的专业解释”。
- **错误修法：**继续增加 ATR、RVOL、连续 K 线或位置阈值，让代码替代交易员。
- **正确原则：**程序只构建 Evidence Graph 和多种候选上下文；PA/VPA 标签由 LLM 比较假设后给出。
- **当前实现/证据：**[ADR-0017](../architecture/ADR-0017-deterministic-facts-and-execution-semantics.md)、[ADR-0018](../architecture/ADR-0018-evidence-context-and-provider-drafts.md)、[原则 ARCH-01](principles-and-boundaries.md)。
- **复发检查：**新增布尔阈值若直接改变 cycle、direction、Setup 或 Wyckoff event，先停止评审。

### PH-02 自由文本校验过严

- `issue_status`：resolved，持续 monitoring。
- **症状：**LLM 事实和订单均有效，但因“方程通过”等措辞、同义词、窗口描述或专业解释差异导致整次分析未通过。
- **影响：**重试风暴、token/延迟增加、有效 PA 被丢弃，规则越补越多。
- **根因：**把质量提示 regex 当成结构化事实/权限校验。
- **错误修法：**为每种模型措辞增加新的字符串特判，或强迫 LLM 复述程序字段。
- **正确原则：**结构契约、明确事实、证据身份、权限和订单安全才 hard reject；专业文本差异进入 warning/audit。
- **当前实现/证据：**[ADR-0002](../architecture/ADR-0002-provider-validation-boundary.md)、[质量契约第 7 项](../quality/acceptance.md)。
- **复发检查：**任何新 validator 若读取 reasoning 文本并触发 retry，必须证明它检测的是唯一明确事实。

### PH-03 Prompt、Schema、materializer、validator 资源漂移

- `issue_status`：resolved。
- **症状：**模型按一套 Prompt/Contract 输出，却由另一目录或版本的 Schema/validator 校验；catalog hash、字段要求随机不匹配。
- **影响：**同一输出在不同入口表现不同，出现无法复现的“分析未通过”。
- **根因：**import-time 全局路径、GUI/Headless 各自解析资源、重试阶段重新定位 Prompt。
- **错误修法：**修改环境变量或复制 Prompt 目录掩盖依赖注入缺失。
- **正确原则：**一次分析的 Prompt、Contract、materializer、validator、retry 和 digest 使用同一个显式 `prompt_dir`/resource snapshot。
- **当前实现/证据：**[ADR-0022](../architecture/ADR-0022-runtime-resource-and-release-reproducibility.md)、[AGENTS](../../AGENTS.md)。
- **复发检查：**任何组件重新调用默认路径解析或重新 hash 当前文件，都视为漂移风险。
- **角色契约补充：**同一资源目录仍可能包含语义冲突：Provider Draft 不允许的 canonical
  字段不能由 Prompt 要求；显式 Joint 的 system、protocol 与最终提醒必须使用同一角色，
  普通 PA 保持 price-only。验证实际 Direct 请求和冻结 MCP 交付，不能只检查单个字符串。
  见 [知识/Prompt/调查修订](../quality/knowledge_prompt_investigation_20261012.md)。

### PH-04 normalizer/router/repair 猜测模型意图

- `issue_status`：resolved。
- **症状：**缺失字段被补成交易结论、订单价格被“修正”、数组按下标恢复、自由文本被 router 解释成 pattern。
- **影响：**记录不再代表模型真实判断，事实审计和 A/B 对比失效。
- **根因：**用“提高通过率”替代契约边界，混合了格式恢复和语义创造。
- **错误修法：**增加更聪明的 normalizer、兜底订单或默认 K1 Evidence。
- **正确原则：**normalizer 仅复制容器、白名单和无语义记号；repair 只修改被 JSON Pointer 授权的路径；Router 只读已验证枚举。
- **2026-10-12 新 repair 边界（local resolved / VPS active）**：canonical 支撑列表排序后错误下标未逆映
  原 Draft，另有授权删除数组项被旧 index 投影成重复项；正确模型修复会被程序
  破坏。真实记录与纯函数已复现，修复 `227e197` 已发布；索引身份及删除位移必须保留，不能
  直接扩大整个数组权限。见 [自然运行诊断](../quality/v1_runtime_failure_paper_audit_20261012.md)。
- **当前实现/证据：**[ADR-0018](../architecture/ADR-0018-evidence-context-and-provider-drafts.md)、[AGENTS](../../AGENTS.md)。
- **复发检查：**若程序输出了模型草稿中不存在的市场结论、Evidence 或价格，立即追踪其创造点。
- **修复路径补充：**canonical required 路径保留父级与数组下标；若原 Draft 缺失
  整个容器，先确认完整目标属于 Provider Schema，再授权第一个实际缺失的 key。
  不提升 null、错误类型或 canonical-only 字段，不由程序补市场判断。两个赋值器
  不各自造父对象。见 [ETH / BTC 契约根因](../quality/v1_contract_failure_repair_20261011.md)。

### PH-05 PA/VPA/Joint authority 与实验身份混淆

- `issue_status`：resolved，Joint 保持 research-only。
- **症状：**VPA 被当成 PA Gate；Shadow 自称 baseline A/B；`soft` 在正式运行触发 Joint；Joint 结果进入 PA official。
- **影响：**信号频率和决策被未经验证的 VPA 隐性改变，研究结论无反事实基础。
- **根因：**把角色并行误写成决策权并列，未区分正式分析与研究 executor。
- **错误修法：**让 VPA-enabled LLM 自述“没有改变 baseline”，或用一次 Shadow run 比较自己。
- **正确原则：**PA defines、VPA informs、LLM arbitrates；正式 authority 为 PA Stage 2，Joint 仅隔离 A/A/B/B。
- **当前实现/证据：**[ADR-0009](../architecture/ADR-0009-production-shadow-research-joint.md)、[研究与评价](research-and-evaluation.md)。
- **复发检查：**检查 official account/result、effective mode 和 Joint 调用堆栈是否可从生产 Headless 到达。

### PH-06 Snapshot、HTF、catalog hash 与 Evidence identity 漂移

- `issue_status`：resolved，真实多源场景 monitoring。
- **症状：**source record 宣称有 HTF 却无完整 bars；frozen VPA catalog hash 与 frame 不匹配；K# 引用映射到错误 Snapshot。
- **影响：**模型基于不同数据判断，程序事实校验误报或遗漏，研究样本失去身份。
- **根因：**Snapshot 冻结、资源生成和验证各自重新取数/排序/hash。
- **错误修法：**忽略 hash、按最新行情重建 catalog，或为缺 HTF 填合成 bars。
- **正确原则：**一次请求只解析一次 identity 并冻结主周期/HTF/volume/catalog；所有下游复制稳定 hash 和 Evidence ID。
- **当前实现/证据：**[ADR-0008](../architecture/ADR-0008-canonical-record-and-validation-boundaries.md)、[ADR-0015](../architecture/ADR-0015-runtime-recovery-and-frozen-execution-facts.md)。
- **复发检查：**比较 task、frame、record、handoff 和 research artifact 的 source/venue/symbol/timeframe/hash。
- **hash 范围补充：**PA 价格 / 指标 hash 与 VPA OHLCV / 来源 hash 不是同一种身份，
  不能直接比较字符串。新 Job 在完整 catalog 校验后交付同 frame binding 与范围名；
  不忽略真错配、不修改原算法或 catalog ID、不重标旧 Job。见上述契约根因报告。

### PH-07 Candidate、执行资格、结构失效与 Paper 语义混合

- `issue_status`：resolved。
- **症状：**程序因 RR/置信度改写模型订单；可选 TP2 为空导致 candidate 消失；保护 stop 触达被写成假设失效。
- **影响：**无法区分模型判断与执行政策，回放和真实建议语义失真。
- **根因：**Stage 2、订单数学、execution policy 和 Paper outcome 共用一组可变字段。
- **错误修法：**直接把“不执行”压回 `order_type=不下单`，或机械移动 stop/entry。
- **正确原则：**candidate 不可变；订单数学/有效资格进入 `execution_disposition`；结构 stop 与 invalidation condition 分开。
- **当前实现/证据：**[ADR-0019](../architecture/ADR-0019-immutable-candidate-and-paper-ohlc-v2.md)、[CONTEXT](../../CONTEXT.md)。
- **复发检查：**比较 provider draft、materialized candidate 和 execution disposition，确认前者未被 policy 改写。
- **2026-10-12 新执行边界（local resolved / VPS active）**：历史已消费 OHLC 冲突经完整滚动窗口事务反复
  拒收新 trade；candidate 与 eligible 不代表 submitted。必须对账 paper_decisions 的
  status/order_id/reason，再读订单；保护旧执行事实与隔离未来行情版本分开设计。
  用户已授权本地隔离无潜在活跃依赖的旧冲突，保留旧价与审计，拒绝延迟新单回放；
  见 [自然运行诊断与修复](../quality/v1_runtime_failure_paper_audit_20261012.md)。修复 `227e197`
  已发布，VPS 自然轮询隔离旧冲突通过，旧价、原订单和收益不变；完整新模型链待观察。

### PH-08 Provider、持久化、线程和重试错误分类

- `issue_status`：resolved，运行时 monitoring。
- **症状：**余额/401/429/timeout 被误报“分析未通过”；SQLite 写失败却显示成功；取消后线程销毁崩溃；失败重试创建新 task。
- **影响：**用户无法判断应换模型、改配置还是修代码；产生幽灵成功、重复分析或 GUI 崩溃。
- **根因：**异常在层间被统一包装，业务完成和线程完成混为一谈。
- **错误修法：**外层捕获所有 Exception 并返回空结果，或使用 `terminate()` 强停 QThread。
- **正确原则：**Provider/input/model/internal/persistence/cancelled 分开；任务身份稳定、有界退避；线程协作取消并保持强引用到真实结束。
- **当前实现/证据：**[ADR-0015](../architecture/ADR-0015-runtime-recovery-and-frozen-execution-facts.md)、[AGENTS](../../AGENTS.md)。
- **复发检查：**先读 RunOutcome、failure role、provider code、persist status 和 thread event，不以 UI 文案定根因。

### PH-09 单样本叠补丁与样本坍塌

- `issue_status`：monitoring。
- **症状：**每次针对最新 record 加一条 prompt/validator 规则，上一错误消失后出现新错误，通过率依赖固定样本。
- **影响：**Prompt 变长、模型注意力下降、合法变体被拒、跨市场泛化恶化。
- **根因：**没有先问“这是确定性代码错误还是合理 LLM 偏差”，也没有独立 holdout。
- **错误修法：**根据单次 K 线、某模型措辞或某资产阈值直接上线规则。
- **正确原则：**先归因和预注册；只修跨样本机制；保留负面样本、alternative hypotheses 和中性状态。
- **当前实现/证据：**[项目 guardrails](../../.agents/skills/paagent-change-guardrails/SKILL.md)、[研究防坍塌](research-and-evaluation.md#防止样本坍塌)。
- **复发检查：**每项修改写出独立单位、成功/失败标准、反例和 holdout 影响。

### PH-10 VPS 权限、状态投影与 desired/applied 混淆

- `issue_status`：monitoring，软件已实现但 VPS verification 未完成。
- **症状：**Dashboard 需要读取生产 DB/API key；暂停请求写入后立即显示已应用；Web 进程试图管理 systemd。
- **影响：**扩大凭据和交易数据暴露面，控制状态误导，权限配置难以审计。
- **根因：**把运维 UI 直接接入 Headless 内部状态和进程管理。
- **错误修法：**共享整个 `PA_AGENT_HOME`，Dashboard 使用同一 Unix 用户或读取 SQLite。
- **正确原则：**Headless 发布脱敏 projection；Dashboard 独立用户只读/写 operation directory；generation 明确 requested/applied；systemd 管生命周期。
- **当前实现/证据：**[ADR-0023](../architecture/ADR-0023-vps-observability-and-control-plane.md)、提交 `f8fffc0`。
- **复发检查：**在 VPS 用文件权限和进程用户证明 Dashboard 无法读取配置、API key、records 和 Paper DB。

### PH-11 Draft v2 定向修复越权与状态混淆

- `issue_status`：resolved。
- **症状：**修复请求改变未点名字段、重排数组或把首次失败隐藏成 first-pass success。
- **影响：**模型结果被重写，质量指标失真，重试 token/latency 被低估。
- **根因：**按最终 JSON 覆盖原草稿，没有 Draft-to-Draft 权限投影和 attempt ledger。
- **错误修法：**接受“修复后能过 Schema”即成功，忽略未授权语义变化。
- **正确原则：**按 JSON Pointer 投影被授权路径，完整重验；分别记录 first-pass 与最终接受，所有 attempt 计成本。
- **当前实现/证据：**[ADR-0018](../architecture/ADR-0018-evidence-context-and-provider-drafts.md)、[质量契约](../quality/acceptance.md)。
- **复发检查：**比较首次 Draft、修复 Draft、授权路径和最终 materialized digest。

### PH-12 OHLC 路径、Paper 版本与真实 outcome 混淆

- `issue_status`：resolved，forward data monitoring。
- **症状：**同柱 stop/target 被强行判胜负；Paper v1/v2 混合；反事实回放写成真实交易收益。
- **影响：**系统性高估胜率/EV，研究标签污染。
- **根因：**OHLC 不含柱内路径，结果表缺 simulation version 和 outcome provenance。
- **错误修法：**统一假设 target-first，或只保留有确定结果的订单。
- **正确原则：**不确定保持 ambiguous/censored；真实、反事实和版本化 Paper 分表/字段隔离；ITT 保留未触发为 `0R`。
- **当前实现/证据：**[ADR-0019](../architecture/ADR-0019-immutable-candidate-and-paper-ohlc-v2.md)、[研究与评价](research-and-evaluation.md#paper-证据边界)。
- **复发检查：**报告必须列 ambiguous、censored、simulation version 和 outcome source。

### PH-13 多 venue identity、合约单位与精度漂移

- `issue_status`：monitoring，软件已实现但 live probe 仍需验证。
- **症状：**同名 XAU 在 Binance/OKX/Bitget 映射不同合约；tick、quantity precision 或 contract multiplier 错用。
- **影响：**订单价格/数量、止损风险和 Paper PnL 错误，甚至跨 venue 污染。
- **根因：**只用 display symbol 作身份，没有绑定 provider/venue/instrument type/spec。
- **错误修法：**维护全局 symbol 特判表或静默采用默认 tick。
- **正确原则：**`ResolvedInstrumentIdentity` 与 `InstrumentSpec` 同源冻结；订单数学使用 venue 原生单位和真实精度。
- **当前实现/证据：**[ADR-0010](../architecture/ADR-0010-canonical-instrument-identity.md) 至 [ADR-0014](../architecture/ADR-0014-vps-preflight-and-paper-reporting.md)。
- **复发检查：**上线前逐 venue `--probe-data`，核对 identity key、contract size、tick 和 quantity step。
- **教材参照补充：**教材的时间、点数和 tick 示例也有单位与锚点；例如 5m 的 24 根不等于
  任意周期的两小时，E-mini 从信号极点数出的 tick 不等于从 entry 算出的收益。
  保留示例与背景，明确参照及假设；不得把固定数值变成跨品种的交易 Gate。见上述修订报告。

### PH-14 Volume provenance、真实零/缺失零和非因果 seasonality

- `issue_status`：resolved，asset coverage monitoring。
- **症状：**缺失 volume 被当成零成交；tick/proxy volume 当真实交易量；seasonality 使用未来或不同时间槽。
- **影响：**VPA 核心证据反向，产生伪 no-demand、climax 或 absorption 解释。
- **根因：**数据适配层没有携带 volume provenance/quality，聚合未严格因果。
- **错误修法：**用固定 RVOL 阈值机械判事件，或跨 source 比较绝对量。
- **正确原则：**程序明确 volume 类型、可用性和同源比较；完整覆盖前 seasonality unavailable；LLM 解释时降权。
- **当前实现/证据：**[ADR-0020](../architecture/ADR-0020-contextual-risk-probability-and-vpa-seasonality.md)、[VPA 原则](principles-and-boundaries.md#wyckoff-vpa-交易员视角)。
- **复发检查：**检查 frame、catalog、Prompt 和 VPA report 是否一致携带 provenance/availability。

### PH-15 多资产去重、重启恢复、QThread 与 Headless Qt 污染

- `issue_status`：monitoring，离线问题已修复但 VPS soak 尚未验证。
- **症状：**同资产并发、跨资产共享 client、重启重复分析；GUI 关闭销毁运行线程；Headless 因 import 链加载 Qt/pyqtgraph。
- **影响：**身份污染、重复账本、崩溃，以及 VPS 无 GUI 依赖安装失败。
- **根因：**调度、对象生命周期和入口依赖边界不清。
- **错误修法：**全局共享 orchestrator、无界线程池、`terminate()`，或为 Headless 安装完整 Qt 掩盖 import 污染。
- **正确原则：**冻结任务、同资产 FIFO、全局有界、每任务独立可变对象；协作取消；Headless import smoke 不加载 Qt。
- **当前实现/证据：**[ADR-0006](../architecture/ADR-0006-bounded-multi-asset-runtime.md)、[ADR-0007](../architecture/ADR-0007-linux-headless-runtime.md)、[AGENTS](../../AGENTS.md)。
- **复发检查：**20 资产调度、重启去重、1000 次 GUI 生命周期和 wheel Headless import 分别验收。

### PH-16 wheel Prompt、依赖 profile、路径与 alias cache 污染

- `issue_status`：resolved。
- **症状：**源码运行正常、wheel 缺 Prompt；Linux 安装拉入 Qt；不同 `PA_AGENT_HOME` 共用 TradingView alias；Prompt 路径依赖当前目录。
- **影响：**本地/CI/VPS行为不一致，部署后才失败或跨配置污染。
- **根因：**资源未纳入 package/lock，缓存键和运行目录未隔离。
- **错误修法：**VPS 复制本机目录、手工补文件、把所有可选依赖全装上。
- **正确原则：**`uv.lock` 固化 profile；wheel 包含 Brooks/VPA 知识；Prompt 由显式路径读取；alias cache 归配置目录。
- **当前实现/证据：**[ADR-0022](../architecture/ADR-0022-runtime-resource-and-release-reproducibility.md)、[最终审计](../quality/pre_release_final_audit_20260813.md)。
- **复发检查：**隔离 wheel 安装、资源 smoke、Headless import 和两个配置目录缓存测试。

### PH-17 DeepSeek requested thinking 与 applied effort 不一致

- `issue_status`：monitoring，provider-dependent。
- **症状：**UI 选择 low/medium，但日志显示三角色请求实际为 high；用户认为已降时延但 Provider 未应用。
- **影响：**性能预期、模型比较和 cohort 身份失真。
- **根因：**Provider 支持的 effort 粒度与本地配置不同，或请求字段使用了错误 API 方言。
- **错误修法：**本地日志只记录 requested value，或假装映射成功。
- **正确原则：**DeepSeek OpenAI-compatible 使用其原生字段；requested 与实际 applied 分别记录；不支持时显式降级而非伪装。
- **当前实现/证据：**[ADR-0016](../architecture/ADR-0016-role-based-reasoning-profiles.md)、[AGENTS](../../AGENTS.md)。
- **复发检查：**核对实际请求 payload、Provider response/日志和 record 中每角色 applied profile。

### PH-18 Agent 改造吞并 PA authority 与知识截断

- `issue_status`：active prevention。
- **症状：**Codex 自行选择交易体系，程序信号先投票锚定方向，Wyckoff 成为隐性 Gate，或知识只取固定前 N 段；Stage 1/Stage 2 被 CLI 工作流替代。
- **影响：**Brooks Router、竞争假设、结构止损、Trader Equation 与 PA-only 正式结果被削弱，分析看似更长但不再是同一 PA Agent。
- **根因：**把 Codex 当成新的自治交易系统，而不是现有 Draft/Validator 前的 Analysis Backend。
- **错误修法：**从旧 `codex-paagent` 复制框架、fast scanner、五信号投票和截断知识包。
- **正确原则：**当前 vNext 是唯一源码；Codex 只实现 `AnalysisRoleBackend`，知识由 PA Context/Router 授权，输出继续进入同一 materializer、Validator、Risk 和正式 PA authority。
- **当前实现/证据：**[ADR-0024](../architecture/ADR-0024-codex-analysis-backend.md)；
  当前能力和环境证据以 [当前状态](current-status.md)为准，本次调查流程补充只在本地验证。
- **复发检查：**任何 Codex 代码若能命名 Setup、绕过 Router、直接写 DB/订单，或在 Prompt 外读取未授权知识，立即停止 Phase。
- **流程审计补充：**Skill 写明的 baseline-first、一次事实/一次知识调查，不能由仅统计成功
  总调用数的 Worker 冒充执行。新 Job 显式冻结策略版本，以同一纯流程校验器约束 Gateway
  与 Worker；失败尝试占配额、denied 不能被成功 Draft 掩盖。历史无标记 Job 保留原语义，
  child/repair 保留各自 scope 与预算；此限制不参与专业市场判断。见上述修订报告。

### PH-19 长会话、Backend 混合与 availability/quality 混淆

- `issue_status`：active prevention。
- **症状：**定时任务 resume 其他 Snapshot 的旧对话；Stage 2 继承未经验证或经过 repair 的 Stage 1 prose；Codex 输出质量错误被 DeepSeek 静默兜底；同一交易链混用两个 Backend。
- **影响：**上下文污染、不可重放、ITT 幸存者偏差和无法解释的正式决策。
- **根因：**没有把 Snapshot、role、attempt、runtime fingerprint 和失败类型作为稳定身份。
- **错误修法：**无限 retry、自动换模型重写，或只保存最后成功结果。
- **正确原则：**不同 Snapshot 独立；同一 Snapshot 只允许 Stage 1 attempt 1 → PA Stage 2 受控续接，
  并由冻结 parent/thread/runtime identity 和已验证 Artifact 约束；VPA/Joint/repair 独立 ephemeral。
  fallback 仅限 availability，且从 Direct Stage 1 重启完整链；实验禁用 fallback 并保留失败 arm。
- **当前实现/证据：**[ADR-0024](../architecture/ADR-0024-codex-analysis-backend.md)。
- **复发检查：**审计 role session、backend、failure kind、fallback reason、Snapshot/runtime digests 和 ITT coverage。

## 使用方式

1. 先按症状定位一个或多个 `PH-*`。
2. 回到原始 record/audit/日志证明是否为同一机制，不按相似文案猜测。
3. 若根因已覆盖，优先修复偏离当前原则的实现；不要再新增平行规则。
4. 若是新机制，完成三角色审查和跨样本验证后新增问题族或扩展现有条目。
5. 修复后记录回归证据；可接受的 LLM 专业偏差不伪装成代码 bug。

继续阅读：[运行与排障](operations-and-debugging.md) · [原则与边界](principles-and-boundaries.md) · [维护手册](maintenance.md)

### PH-20 多假设知识覆盖与跨层指令口径漂移

- `issue_status`：本地交付/契约修复已验证，未发布；真实模型质量与成本待观察。此前研究/候选记录保留原日期。
- **症状/风险：**完整 alternatives 在交接中保留，但旧 Router 只读第一个替代周期并沿用主方向；同一组替代假设换序可能改变知识覆盖。VPA 数据可用时“至少有增量”的Prompt压力与neutral空报告合法状态不完全一致；逆向订单反馈要求重做Stage 1，与Joint/repair冻结上游矛盾。
- **证据：**[2026-10-13判断空间审查](../quality/v1_runtime_failure_paper_audit_20261012.md)。50项相关测试通过；纯函数复现，不代表已证明自然误判或收益损失。
- **错误修法：**删全部知识、放宽事实/引用/方向Gate、自动重跑上游、强迫VPA改变订单，或借pointer修复重新分析交易。
- **建议：**先统一可用/有诊断性及失败反馈的口径；再逐条映射当前角色指令并去重。多假设知识路由须同时处理替代方向及后续过滤，以唯一Router和版本化同快照对照验证，不自动扩大执行权限。
- **复发检查：**两项替代假设顺序与知识覆盖、上游重做字样是否进入repair、数据可用但无增量是否允许中性/空观察、旧Frozen Job资源是否仍冻结。

- **2026-10-13离线候选：**方向冲突仍拒绝，仅统一反馈；Direct/协议/Skill/mandatory core同时允许已读无增量，不掩盖未读或失败。旧Frozen Skill/协议/知识不改写；模型质量和收益未验。验收与未发布边界见同一报告的“前两项离线候选实施”。

- **2026-10-13进一步研究（未实施）：**VPA知识selector同样只读旧第一项替代周期；聚合窗口selector遍历全部hypotheses，但不收challenging_fact_refs的独有窗口。完整覆盖必须同时核对Router、方向过滤、VPA知识/实值与冻结manifest，不以完整handoff或允许搜索证明材料齐全。Runtime现已单次AGENTS注入；建议稳定common AGENTS、现有Skill/protocol按marker冻结交付当前说明，保持完整发布身份及Job实际资源身份。按role动态替换AGENTS不是首选：role-specific identity会影响严格续接，共同variants身份虽可相等，CLI正文切换仍未验。设计与验证边界见同一报告末节，不代表已优化或提高收益。

- **2026-10-14本地实施：**公共AGENTS保持稳定，当前角色合同及原policy适用的研究扩展
  经`role_guidance_v1`冻结；未知policy、角色/模式或交付副本错配拒绝，旧Job不迁移，PA续接
  共同身份不变。唯一Router遍历全部合法替代枚举，Direct/Codex投影保留review原文与用途/来源；
  VPA selector同步全部周期及catalog已有支持/挑战窗口，不扩大Setup/方向/Gate权限。
  先完整备份，清理不适用常驻说明和两个无调用helper，完整教材/历史契约保留。
- **复发检查补充：**实际Orchestrator→Publisher→MCP的mandatory summary也要携带用途/来源；
  修复/fixed测试走真实发布流程，不能直接给新普通Job改模式而遗漏冻结合同。检查
  0/1/2替代、换序、同周期反方向、挑战独有/缺失窗口、正常/repair/研究与旧无marker行为。
  完整验收和未发布边界见同一报告2026-10-14节；字节减少和本地测试不证明token或收益改善。

- **真实单次配对边界：**2026-10-14同一旧快照旧/新各一次完整Joint均首稿通过且wait，
  token略降、耗时增加；只有一项区间内替代，实际教材集合相同，未验证新增教材分支的模型
  增益。Warning要回到原值及匹配源码：HTF文字中的周期/引用数字被宽范围匹配误当EMA差值
  是解析风险，“此前区间低点”可有正确实值但范围表述不足；不把所有warning当成已证实
  事实错误，也不按一个样本继续叠Prompt规则。新版原Draft杂字保留，不能用首稿成功冒充
  零质量问题。完整指标/原件边界见同一报告的“旧快照单次配对结果”。

### PH-21 前置VPA的订单时机不适用被写成数据不足

- `issue_status`：修复已发布；真实模型质量与持续自然运行待验证。
- **症状与影响：**前置VPA没有正式订单，timing仍必填并被程序统一置为insufficient_data；部分Joint等待理由引用了这一占位，存在误读偏置。
- **根因：**将阶段适用性、数据覆盖和专业证据关系合并；旧后置报告被复用于前置阶段。
- **错误修法：**强改neutral/supports、强迫VPA影响交易、再叠文字禁令或强造候选让字段适用。
- **正确原则：**按版本交付可回答的问题，Host写适用性，VPA解释真实量价，Stage 2独立裁决；不把全部wait认定为错。
- **证据与复发检查：**[研究第2–7节](../quality/runtime_reliability_vpa_remediation_20261021.md)；检查新前置投影不带旧不足占位、完整反证仍在，旧Shadow/研究不改写。

### PH-22 历史订单复盘依赖近期分析窗口

- `issue_status`：修复已发布，VPS现场历史复盘验收通过。
- **症状与影响：**Demo订单仍可见，复盘在分析滚出最近30项后空白；原始分析其实仍在。
- **根因：**页面只查近期数组，订单未绑定自己的历史review，投影白名单也不允许该字段。
- **错误修法：**扩大近期列表、用最新同品种分析顶替、让Dashboard读生产DB或重新调用模型补复盘。
- **正确原则：**Host按订单源身份批量补读原分析并经完整allowlist发布；缺失明确显示，不污染交易事实。
- **证据与复发检查：**[研究第11节](../quality/runtime_reliability_vpa_remediation_20261021.md)；点击超过30分析窗口的旧Demo订单并通过两轮清洗验收。

### PH-23 成交流水游标滚出接口保留窗口后永久阻断

- `issue_status`：修复已发布，VPS现场覆盖恢复验收通过。
- **症状与影响：**近期成交接口成功但fills覆盖为false，长时间无新成交也不能ready，新增入场被阻断。
- **根因：**最后成交ID被用作完整同步证明；空响应不更新完整观察水位，旧ID超出保留期后永远不可达。
- **错误修法：**清空cursor、跳过coverage、把空账户当历史完整、静默忽略冲突或补发旧交易建议。
- **正确原则：**事件身份和完整覆盖时间分开；历史有界接回、分页可续、重叠去重与冲突审计，确认完整后才推进水位。
- **证据与复发检查：**[研究第10节](../quality/runtime_reliability_vpa_remediation_20261021.md)；跨3/7天空闲、分页耗尽与历史不可恢复均须回归。

### PH-24 挂单提交、实际触价与后续确认被写成同一时点

- `issue_status`：修复已发布；真实模型质量待验证。
- **症状与根因：**通用口诀及信号字典要求先有入场/确认棒再提交计划，和合法 pending 路径冲突；只改主决策树会被相邻必读教材重新覆盖。
- **错误修法：**强迫模型 trade、禁止合理等待、删除所有 Setup 的确认要求，或不断在末尾加例外。
- **正确原则：**资格已成立可待未来触价；资格还依赖新闭盘/未形成结构则当前 wait；已发生的入场及跟随才评价实际质量。原所有者成套替换，执行器不按说明文字判断市场条件。
- **证据与复发检查：**[复查第19节](../quality/runtime_reliability_vpa_remediation_20261021.md)；用实际新旧 Schema、限价/突破、PA/fixedRR、Direct/Codex 与完整物化校验链验证 pending，同时保留合法 wait 与冻结教材锚点。

### PH-25 跨轮保留市场诊断却丢失无单等待条件

- `issue_status`：已发布d9c9fe6；真实模型行为待验。
- **症状与根因：**Headless按新闭盘自动分析，但price-only过滤移除旧Joint Stage 2；连续性代码仅识别可执行订单，无单reasoning/watch_points消失，模型可能每轮重新提出“等下一根”。
- **错误修法：**跨快照续接旧会话、把旧VPA意见注入Stage 1、用自然语言正则自动确认条件、强迫trade，或从可变CSV补读历史。
- **正确原则：**用已有成功记录作为有界历史意见，冻结同源身份、可用时间和原棒；仅Stage 2用当前证据重新判断，旧引用不得作为当前事实。旧方案触价仍不等于成交。
- **证据与复发检查：**[等待条件连续性验收](../quality/wait_continuity_20261022.md)；检查WAIT也可交接、旧K1正确映射、数据修订/未来可用/跨来源/损坏历史不污染当前、冻结篡改被拒绝、旧任务不迁移、Direct/Codex实际交付与off隔离。

### PH-26 公共任务工厂的空HTF表示不一致

- `issue_status`：2026-10-26新入口已修复，本地验证；当前生产Headless原本不走该输入。
- **根因与范围：**公共工厂接受htf_context=None，以None计算hash，却将payload存为{}；codec按{}复算失败。生产Headless的HTF开/关均生成明确状态字典，未受影响。
- **错误修法：**将该不一致当作新历史身份篡改红测、全局改hash或直接跳过恢复校验。
- **修复原则：**首次冻结前None→{}并用同一值算hash/存payload；旧损坏身份严格拒绝，不双hash兼容或重标。新增12项回归与136项聚焦通过，最终发布证据见[工程交接验收](../quality/engineering_handoff_release_20261026.md)；旧发现见[验收边界](../quality/wait_continuity_20261022.md)。

### PH-27 维护重启与禁用入场共用撤单路径

- `issue_status`：2026-10-26改为维护排空，本地验证；实际发布见工程交接验收。
- **事实：**Demo Worker SIGTERM退出先设entry_enabled=false再tick；独立计划管理因此撤未成交余量。维护重启会影响原挂单，不是TTL到期或新分析替代。
- **修复原则：**维护不改entry_enabled，不撤未到期零成交余量；不直接tick后退出，而是同进程继续管理和保护至完整新鲜对账且空单空仓。取消systemd强制停止超时，维护需先排空；正常TTL/partial/显式禁入仍撤，未知不释放预算。不补发旧计划，停机后无人保护的挂单保留不是修复，见[工程交接验收](../quality/engineering_handoff_release_20261026.md)。
- **证据：**[本次发布回执](../quality/wait_continuity_20261022.md)，原BTC live→canceled、fills保持18。

### PH-28 分析限价可执行但目标tick拒绝，提交前计划又被流水隐藏

- `issue_status`：2026-10-23用户授权修复，167d482已发布VPS；旧XAU拒绝流水与复盘现场通过，历史记录不改写，未补发。
- **现场证据：**北京时间2026-10-22 23:00:06 XAUUSDT，run_id=`1f86f54a65a24066a5b5b5c66d4b611d`；Stage 2为trade且execution_disposition=eligible。Worker于23:07接收，intent=`5c4392059d86eb2eb3a3ea399b37f1bb2573069aeef96217c36ee6d0be920902`最终trade_state=rejected、reason=target_tick_mismatch、request=null，无entry leg。
- **根因：**来源Binance tick=0.01，目标OKX XAU-USDT-SWAP tick=0.1；entry=4101.32、stop=4109.69不合规，TP1=4088.8合规。固定RR只对程序目标按目标tick计算，未保证模型Entry/SL与目标tick一致；Host eligible不代表Worker执行已通过。
- **可见性缺口：**DemoStore.projection_rows仅选request非空进入orders；原计划只在demo.decisions中有拒绝理由，面板流水不显示该项。receipt accepted表示持久接收，不表示交易所挂单成功。
- **复现：**使用真实bind_demo_limit及原三价、XAU映射与tick=0.1，离线得到同一target_tick_mismatch；entry/stop除tick余数分别0.02/0.09。
- **正确原则：**目标交易所价格网格应明确交付并在可执行候选确认前核验所有执行价格；结构仍由LLM判断，不在执行末端静默改Entry/SL。提交前拒绝须可查且与已取消区分；不补发旧计划，不把accepted回执伪装成已下单。
- **授权实施：**用户随后明确允许纯程序自动转换；采用独立有版本执行投影，在唯一materializer先取整再计算fixed_rr/方程；原候选保留，旧marker不升级。未提交拒绝显示及验证见[本地验收](../quality/execution_price_grid_20261023.md)，不把新执行适配追溯成旧分析价格正确。

### PH-29 Demo成交与真实市场路径混用导致效果误归因

- `issue_status`：已取证；佣金后数学已授权本地实现，未发布；Demo/实盘环境差异仍须持续区分。
- **事实：**10-23 XAU 17:11 Demo分钟高4053.7触4040.3止损，实盘分钟高4035.4、该小时高4039；16:36 Demo入场4030时实盘分钟高4026.2。BTC另一笔止损时实盘也越过止损价，不能把两笔都归为Demo差异。
- **正确原则：**带/不带模拟标头的行情环境单独记录；原始私有fills决定Demo结果，实盘OHLC仅诊断。不能把Demo入场时间搬入实盘证明盈利，换成同交易所实盘数据也不等于同环境。
- **成本边界：**两笔正式单手续费约0.24/0.27R；当时线上方程为毛收益，pass不代表正净期望。新标记任务的佣金后检查见[第一批验收](../quality/trader_entry_execution_acceptance_20261025.md)；保留毛方程及原模型候选，未知滑点/资金费不当零，不统一扩大止损或提高模型自报概率。
- **后续必读：**[完整归因、来源与优先级](../quality/demo_plan_performance_attribution_20261024.md)。两笔异版本结果与重复同结构限价均不足以证明策略胜率；原报告保留只读取证身份，不把后续实现倒写为历史已执行。
