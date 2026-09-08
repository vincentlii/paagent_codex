# PA/VPA 联合分析 Agent 实施契约

状态：实施中；尚未完成运行验证，不代表交易有效性或 VPS 发布。

## 基线和授权

本计划整合用户桌面的《整体审查》《知识审查》《补充审查》及随后确认的方向。
审查对象为 `3a0b352` 加上一轮未提交实现；这些实现已由用户确认保留，保存为
`e445907`。不得回退到旧 Windows 版本覆盖已有成果；继承其成熟主链及历史修复。
本轮不使用 subagent；每阶段必要验收、三角色自审、独立提交。不部署 VPS，不实盘下单。

## 不得偏离的目标

Brooks PA 是主框架；独立 VPA 负责量价证据，联合 Stage 2 在既定 Router 内作出最终决策。
不是先生成 PA 订单再由 VPA 改单。Stage 1 和 Router 不可被下游改写；发现上游矛盾时
返回 review_required 并停止，不自动重跑 Stage 1。不保证市场判断唯一正确或盈利。

目标链路：冻结主周期、HTF、成交量和知识 → price-only Stage 1 → 唯一 materializer /
Validator → Brooks Router → 独立 VPA → 联合 Stage 2 → 同一 materializer / Validator /
Trader Equation / Risk → 持久化 → 新鲜度检查 → 简单 Paper 与看板。

Codex 仅调查冻结资源并提交 Draft；Host 拥有数据、时间边界、确定性测量、权限、校验、
调度与数据库写入。每角色独立 ephemeral 任务，无长期会话、无源码修改、无实时取数。
AGENTS 固化权限，Skills 编排分析程序，领域知识仍由 PA Agent 管理；不得复制另一套知识库。
保留完整核心价格、HTF 和必读知识；工具读取仍消耗上下文，不以短 Prompt 冒充效果改善。

新增 analysis_pipeline 与 Backend 正交；旧 off/shadow/研究 soft 语义不变。
新建本地配置最终可默认联合模式并标注效果未验证；旧配置、旧记录不静默迁移。
联合模式 VPA 运行失败为 incomplete；数据确实不可用的完整报告允许显式说明仅价格依据。
只有 availability 故障可冷回退，同一 Snapshot 重跑同一完整协议，不因语义失败回退或降权。

执行只保留一笔 entry、结构 stop、TP1 全仓退出；TP2 仅参考，不影响 TP1 资格。
不做动态退出、移动止损、分批止盈、加仓或 LLM 持仓管理。

## 顺序、实现指引和验收

| 阶段 | 实现范围与落点 | 必要验收 | 状态 |
| --- | --- | --- | --- |
| 0 | 保存既有工作区；固化本计划及审查项映射、Windows 历史故障基线 | 基线提交可回溯，既有成果未丢失 | 已保存基线和计划 |
| 1 | ADR、版本化 pipeline/角色结果/联合 Draft/指标语义；复用 pa_provider_contracts 唯一物化器 | 旧配置与记录仍可读；incomplete/review_required 不携带可执行候选 | 契约已验收；运行接入在阶段 5/8 |
| 2 | 行情观测时间先于抓取；主/HTF 同一 as-of；不重标旧未闭盘响应；暴露 VPA provenance；经验 UTC/身份/结果已知时间与冻结池 | 边界跨盘不前视；unknown 历史不入经验；回放身份稳定 | 本地实现；90 项聚焦通过，扩大回归有 2 项既有失败 |
| 3 | 完整 PA 与五模块 VPA 知识修订；区分理论/产品策略/协议；稳定章节 ID、别名、版本；修正 MM 符号、计数、机械阈值与知识召回 | 逐项内容迁移映射；保留案例、反证、阶段切片；不截断知识 | 本地完成；209 项聚焦通过，三角色自审及迁移见阶段 3 验收 |
| 4 | Runtime AGENTS/role Skills/工具/必读验收矩阵；PA 与联合 Stage2 技能分开；独立上下文、pointer repair；有界确定性测量工具 | skill 与可执行契约一致；越权拒绝；派生证据可复算；预算不足不伪装 No Trade | 本地实现与 194 项离线检查通过；真实 MCP SDK/CLI/Linux 验证待补，见阶段 4 验收 |
| 5 | 现有 two_stage 分支内 VPA 前置；compile_pa_handoff(stage1, None, frame)；联合决策引用 VPA；最多三项有意义方案而非强制数量 | 无额外 PA baseline Stage2；旧 off/shadow 不回归；VPA 不创建方向/Setup；联合概率是明确主观先验 | 待实现 |
| 6 | 结构目标与限价可达性；持久化后 available_at；下一主周期收盘挂单过期/显式替代；版本化 latency-aware Paper | 限价不劣于限价成交；无中途回溯成交；同棒不确定保留；旧账本不重写；TP2 非 Gate | 待实现 |
| 7 | 完整冻结证据盲评包、独立解盲 key、VPA rubric、finite 分数、coverage；Backend/联合增量/调查增量三独立协议 | 预注册、重复臂、episode 配对、missing/censored、holdout；不把诊断视为校准或自动晋级 | 待实现 |
| 8 | 实际/估算/未知分离；完整 token/cache/tool/process/queue/wall time；Direct 渲染延迟到需要时 | 不无条件拼大 Prompt；重复开销先优化；未证明非劣不默认删减必读内容 | 待实现 |
| 9 | dedupe 故障阻止新任务；先持久任务再派发；Worker 所有权/租约；完整备份与暂停恢复；有界全链预算 | accept-once/execute-once 边界明确；不宣称网络 exactly-once；备份含 raw records/冻结资源并保留 pin | 待实现 |
| 10 | 看板 pipeline/模型/强度/时间/依据/失败/新鲜度/简单订单；独立 outcome 类型与指标 | 只读无凭据；idle 生效；旧配置不变；联合默认带未验证提示 | 待实现 |
| 11 | 聚焦回归及最终非 live 跨平台验收、wheel 运行资产、迁移/恢复/发布文档 | 三角色复核；历史问题闭环；未运行项明确；只交付本地候选，不部署 | 待实现 |

阶段 1 的接口仅作为版本化契约建立，必须在阶段 5 接入后才声称联合链路可运行。
数据库变更采用增量迁移，执行前说明备份、回退与验证，历史未知字段保持 null。
事实层禁止生成趋势、Spring、Absorption 等主观标签；专业分歧不能被 regex 升级成交易 Gate。
新 VPA 引用独立于 PA-only 引用域，不建立第二套 materializer。

## 审查项追踪

- 整体审查：权限和联合顺序 → 1/5；调查与机械必读 → 3/4/8；假成功、指标与盲评 → 7/8/10。
- 知识审查：理论冲突/公式/计数/概率/过度禁令 → 3；provenance/缺失状态 → 2/4；Skills 协议冲突 → 4；VPA 实际作用 → 5。
- 补充审查：时间与经验前视 → 2；订单交付/成交/限价 → 6；盲评/概率/统计 → 7；指标 → 8/10；调度/租约/备份 → 9；发布资源 → 11。
- Windows 历史故障清单位于 `pa_agent/experiments/fixtures/historical_failures_manifest.json`；
  manifest 存在不等于已 replay。保留旧 materializer、Validator、Router、Risk 与 Paper 历史版本。

## 三角色阶段门禁

软件架构师：单一权威、兼容与失败隔离、可追溯版本、无平行主链。
量化工程师：同源因果、无未来泄漏、未知不当零、统计单位和执行时点正确。
PA/VPA 交易员视角：背景和竞争解释完整、事实与推断分开、结构止损、合理等待、VPA 不另造体系。
这些是同一实现者的三个自审视角，不宣称三位独立专家验收。

阶段 0 自审：只保存已批准成果，无交易行为变更；基线 diff --check 通过，未运行模型/交易测试。

## 当前实施证据

- 阶段 1a：pipeline identity、旧配置适配、联合 Stage2 Schema、非决策停止对象和指标契约已建立；
  尚未接入 orchestrator/settings/records/指标投影，不得启用或宣称联合链路完成。
- pipeline contract、既有 PA provider contract、独立进程 import boundary 共 42 项通过。
  首次收集发现上一轮包级导出造成循环导入，已移除研究服务的不必要 re-export，直接模块导入保留。
  随后遇到旧 pytest 系统临时目录权限错误，使用本次独立 basetemp 后通过，未改业务代码绕过错误。
- 三角色 1a 复核：PA 订单/trace 原 Schema 未变；VPA 引用域独立；旧研究身份不重标；未知不计零。
  尚无模型、回放收益、Linux 或 VPS 验证结论。
- 阶段 1b：记录增加可选版本化 pipeline、UTC delivery timing 与 role_stop；旧记录不补字段，
  停止记录禁止正式决策及伪成功。结果晚于 expiry 合法保留为迟到事实，不向后移动 expiry。
  联合引用只接受同一完整性校验通过 catalog 中、已验证 VPA report 引用过的证据，
  上游身份错误不转为模型可编辑路径。冻结目录与 frame 的绑定仍由既有调用边界负责。
- 1b 聚焦验证 29 项通过，关键 Ruff 检查和 diff --check 通过；仅扩展 JSON 的可选字段，
  未运行数据库 migration，未改写历史文件。1a 提交 `63dbaae`。
- 阶段 1 三角色结论：兼容旧订单/记录/研究语义；时间未知保持未知；VPA 专业解释未机械化；
  版本化契约通过局部验收，但完整联合链路依然未接入，不可启用。
- 下一步：阶段 2 因果数据边界与 VPA provenance，随后知识和 Runtime。阶段 5 接入上述契约，
  阶段 8 接入指标值域；不能把已定义接口冒充已生效的运行行为。

### 阶段 2 实施与验收

实现细节和长期约束见 [ADR-0026](../architecture/ADR-0026-causal-snapshot-and-experience.md)。
已实现 Binance/Bitget 请求前时钟、快照严格 closed/as-of 筛选、主/HTF 共享边界、观察时间留存；
VPA-only core 元数据；经验因果筛选和池冻结、任务/两类实验身份绑定、记录恢复；回放保留 amount 零值类型。
数据修订仍产生不同事实 hash，冻结对象复制保留原输入；没有原地覆盖历史记录。

阶段 2 聚焦验收：12 个直接相关测试文件共 90 项通过；全 pa_agent 关键 Ruff 检查和 diff --check 通过。
扩大到 Publisher 和角色编排的 115 项回归：113 通过、2 失败；用 HEAD `4915673` 的对应模块在内存中
替换后同样失败，未改写工作区用于验证。失败项：

- `test_publisher_replaces_long_direct_prompt_with_bounded_core_context`：初始索引合法含 previous_analysis 名称，
  旧测试要求该字符串完全不出现；完整内容仍不在初始 Prompt。
- `test_codex_owns_pa_roles_while_shadow_vpa_stays_independent_direct`：旧 mock 未实现当前 bind_role，
  且仍要求 VPA Direct；需要在 Runtime/角色验收阶段按当前 Backend 契约改写，不恢复旧混用路径。

两项不是本阶段新增回归，但发布验收不能将其算通过。阶段 3/4 继续前须保留这两项跟踪。
三角色自审通过上述变更方向及局部证据；尚未执行 Linux/live/VPS 验收，也没有专业判断改善结论。
本轮不新增数据库 migration，不回填历史经验时间，不启用联合模式或实盘。

### 阶段 3 实施与验收

详见 [知识验收与逐项迁移](phase3_knowledge_acceptance.md)。覆盖全部 29 个 PA 与 5 个 VPA 文本，
共享知识源、阶段切片和 mandatory 完整读取保留；修正理论冲突、MM 多空符号、H/L 计数、固定分类门槛，
新增冻结别名和持久章节标识。209 项必要回归、关键 Ruff 与 diff --check 通过。
本轮没有运行模型、市场或收益测试，也未部署；阶段 2 的两项旧测试失败仍跟踪于阶段 4。
阶段 4 本地实现及三角色自审见 `docs/quality/phase4_runtime_acceptance.md`，194 项聚焦检查通过。
真实 MCP SDK/CLI/Linux 与专业效果未验证；没有删除领域知识、部署或调用真实模型。
下一步阶段 5 在现有 two_stage 内接入 VPA 前置、联合 Stage 2 和 typed role_stop 持久化；
不能先生成 PA baseline 再改单，不跳过既有 Validator / Risk。
