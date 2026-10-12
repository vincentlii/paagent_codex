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
  破坏。真实记录与纯函数已复现，本地已修复；索引身份及删除位移必须保留，不能
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
  见 [自然运行诊断与修复](../quality/v1_runtime_failure_paper_audit_20261012.md)，VPS 待发布。

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
