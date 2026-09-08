# AGENTS

- 修改 Runtime、Skills、MCP、Worker 或联合编排前读 `docs/quality/phase4_runtime_acceptance.md`：联合 Stage 2 是 stage2 的显式 Skill 变体，不是研究仲裁；result 封套不替代唯一物化器；预算停止不得转成 No Trade 或 availability fallback；测量 digest 不是 catalog fact ID。真实 SDK/CLI/Linux 验证尚未完成，不得声称联合链路已启用。

- 修改 PA/VPA 知识与召回前读 `docs/quality/phase3_knowledge_acceptance.md`：原文 knowledge 锚点禁止重新编号；案例和反证不得摘要替代；理论/产品禁令/Host Draft 协议分层，章节及别名必须随 Frozen Job 冻结。

- 本轮联合分析升级必须先读 `docs/quality/joint_agent_implementation_plan.md`；其中明确区分新模式目标与尚未改变的旧运行协议，未接入验收不得宣称已实现。
- 修改闭盘数据、经验或实验回放前必须读 `docs/architecture/ADR-0026-causal-snapshot-and-experience.md`：过期的未确认响应不能重标闭盘；Stage 2 经验必须有冻结池、同源身份和已知时间。

- 上线前最终实施范围、三角色审计证据与未验证边界见 `docs/quality/pre_release_final_audit_20260813.md`。

- 回应与生成文件优先使用中文；代码、API、错误信息、产品名保留英文。
- 修改、调试、优化、审查或部署本项目前，必须读取并遵循 `.agents/skills/paagent-change-guardrails/SKILL.md`。
- 非琐碎任务开始时先读取 `docs/knowledge/README.md`，再按任务读取相关主题笔记及其权威来源；若任务改变架构、能力状态、验证范围、可复用根因、排障路径或弃用状态，完成前必须同步知识库。知识摘要不能替代本次涉及的源码、测试、`CONTEXT.md`、ADR 或质量契约。
- 进行任何 Codex Backend、Dashboard、Runtime rules、Skills 或部署工作前，必须阅读 [Codex Analysis North Star](docs/knowledge/codex-analysis-north-star.md)，并以其中的长期 authority、Frozen Snapshot 与证据边界为准。
- Codex Frozen Job v4 的首次 Prompt 只能携带身份、角色与 Evidence 索引；完整角色 Context、HTF、上游 Artifact、经验、Measurements、K 线和知识必须作为 digest-bound 资源经只读 MCP 读取，不得重新拼回单次大 Prompt。首版必须先满足 semantic parity：Stage 1/2 完整读取 price-only frame、全部 HTF、PromptAssembler 阶段协议及 PA/Router 选择的 mandatory 知识切片后，才可按需调查争议 Evidence；未经过 matched replay/盲评证明非劣的模块不得为压缩 Token 而省略、摘要或截断。
- 修改 PA / Wyckoff VPA 融合链路或运行时前，先阅读 `CONTEXT.md`、`docs/wyckoff_vpa_integration.md` 与 `docs/architecture/ADR-0001` 至 `ADR-0024`。
- PA provider 使用 Draft v2：模型输出市场假设、证据引用、交易候选与意图；`pa_provider_contracts.py` 唯一负责物化静态 trace 元数据、Gate、Stage 1 冻结字段、Trader Equation 与 terminal。不得另建平行 materializer，重试保护必须比较 Draft 与 Draft。
- 每个实现 Phase 必须按 `docs/quality/acceptance.md` 完成测试、临时文件清理和独立提交；机器阈值以 `config/quality_contract.toml` 为准。
- VPA 必须保持开关化：以 `resolve_vpa_mode(...) == "off"` 为准，VPA prompt、测量块及历史 `vpa_*` / `wyckoff_*` 字段不得进入原 PA 链路。
- 程序只拥有可确定、可复算、无歧义的事实测量、Schema、引用范围、订单数学和执行安全；趋势、周期、Setup、位置、信号质量及 Wyckoff 解释属于 LLM。
- PA 到 VPA/Joint 的唯一交接入口是 `compile_pa_handoff(...)`；它只复制已验证 PA 语义并生成订单数学、身份及 PA 已引用窗口的数值位置，不得补 Setup、位置标签或交易结论。合法限价单/市价单不得因可选 `entry_rule` 为空而丢失 candidate；TP2 是可选研究扩展，无独立结构依据时必须保持 null，且不得影响 TP1 执行资格。
- `stage1_normalizer.py`、`stage2_normalizer.py` 与 `trace_normalize.py` 只能做字段白名单、容器复制和无语义记号规范化，不得补结论、改价或创造节点。唯一的审计元数据例外是：当 `reason` 明确引用快照内 K 线时，程序可将已有 `bar_range` 扩展为覆盖这些引用的最小包络；不得据此新增事实、修正引用、补全缺失范围或改变任何市场结论。
- Brooks Router 只读取已验证的 Stage 1 枚举与 pattern keys，不得从自由文本或程序指标推断策略。
- 正式分析链路只有两种：`off` 运行原 PA；`shadow` 运行原 PA 并追加独立 VPA Evidence Analyst。两者的唯一正式交易结果均为 PA Stage 2，VPA 失败只记 warning，不得使有效 PA 分析失败。
- 普通 PA Stage 1/Stage 2 必须保持 price-only；VPA 只经独立 Evidence Analyst 进入。Joint Decision Arbiter 仅存在于隔离的 A/A/B/B 研究 B 臂，只输出有界 `decision_patch`，不得复制完整 Stage 2、订单价格、胜率、PA trace 或预测。
- 生产不得采用 Joint 或 `soft` 决策。旧配置请求 `soft` 时必须显式记录 requested=`soft`、effective=`shadow`；只有研究 executor 可授权 effective=`soft`。所有模式都不得让 VPA 创建 Setup、翻转方向或修改 `estimated_win_rate`。
- VPA observation 的 `bar_range` 由程序根据 evidence ID 生成。解释字段不得复写 OHLC/volume/RVOL 等数值或声明无明确窗口的新高/新低；基础事实只能引用程序目录。
- VPA 知识模块只教 Background、Location、Effort/Result、序列比较与 acceptance/rejection，不得包含 Joint action enum 或执行补丁语义。窗口 seasonality 只能聚合同一窗口每根棒各自的严格因果精确时槽基线；覆盖不完整时保持 unavailable，不得外推或成为 Gate。
- VPA 解释中明确出现的 `K#` 若能唯一映射到单棒 evidence，程序可写入独立 `auto_bound_evidence_ids` 并进入结构化审计；不得修改模型声明的 `evidence_ids`，也不得把常规自动绑定算作模型 normalization 或独立量价信息。已被窗口 evidence 覆盖时不得重复补，未知或歧义引用仍是 hard error。没有 PA Entry Attempt 时，`entry_timing_relation` 必须由程序收敛为 `insufficient_data`，但 VPA 背景观察与独立后续条件可保留。
- 相邻窗口净推进方向与 HTF 点位相对当前 K1.close 的上/下方属于程序事实。程序只校验明确的同向/反向、窗口方向标签和空间词矛盾，不据此命名 leg、磁力位、支撑阻力、Test 或 Absorption。
- VPA `sequence_claim` 倒序是 hard error；证据窗口重叠时只删除该 claim 并记录 normalization + warning，保留 observation 与 PA 正式结果。
- §9.0P 计划型限价只由程序校验 entry 相对 K1.close 的正确一侧；不得因 K1 影线历史触价、推测 tick size 或自由文本而自动判失效、改价或改单。
- `analysis_outcomes` 只保存人工核验的真实执行结果；`counterfactual_outcomes` 只保存闭盘 OHLC 对计划的反事实回放。两者不得混为真实 PnL、胜率或训练标签。Stage 2 模型候选不可由程序改写；订单数学与置信度只写入 `execution_disposition`，执行层必须使用其中的 effective 语义。
- Stage 2 的 stop 由 LLM 根据当前 Setup 的结构失效判断；信号棒、摆动或边界极点只是候选锚点。程序只核对价格几何、真实 tick、风险尺度和执行安全，不用固定跳数、ATR 倍数或区间比例替代结构判断。教材频率只作定性先验，未经同品种、周期、数据源和执行协议校准时不得复制为 `estimated_win_rate`。
- PA/VPA 增量只能用当前离线 `pa_vpa_aabb_v2` 四臂协议估计：模型调用前登记 A1/A2/B1/B2，使用同一冻结输入、四个独立客户端及 ABBA/BAAB 顺序，并同时报告 ITT、per-protocol、A/A 与 B/B 分歧。生产 Shadow 不得自称 A/B；每条 record 的反事实只回放自己的正式订单。挂单柱内触发后同柱触及退出价、同 K 同触止损与目标、无法确定顺序的突破成交必须保留为 unsupported/ambiguous；开盘已成交的订单可在同柱按保守止损优先回放。同 timestamp OHLC 冲突时拒绝历史，原始快照滚出窗口时停止推进，不得猜测成交路径。Paper v1/v2 统计必须按 simulation version 分离。
- A/A/B/B 统计以 `market_snapshot_hash` 为独立样本，以 `cohort_id` 隔离 Provider/settings/源码/Prompt 版本；重复臂、重跑和同一行情的跨版本复核不得重复计样，质量门禁不得混合 cohort。
- 持续 A/A/B/B 是独立研究进程，只消费可重放的生产 Shadow record；按 `(pipeline_id, cohort_id, market_snapshot_hash)` 原子租约去重。已完成四臂但 Paper 暂时失败时只重放 artifact 并重试 Paper，不得重新调用模型；Joint 与四个研究账户不得进入生产链路。
- A/A/B/B 收益主结论使用 episode-level ITT：`no_order` / `entry_not_triggered` 计 `0R`，pending/ambiguous 为 censored，缺失 arm 保留在 coverage 并阻止验收；重叠前向窗口先合并 episode。per-protocol 仅为处理交付诊断，不得取代 ITT。
- 自动回放目标周期由 `general.counterfactual_horizon_bars` 配置，但单条记录不得超过 `analysis_bar_count - 1`；分析最少需要 20 根已收盘 K 线。
- 多资产队列只能接收已冻结 Snapshot；同资产必须 FIFO 单飞、全局并发与 pending 数必须有界。每个任务使用独立 client/orchestrator，任务身份必须绑定 source、venue、symbol、timeframe、snapshot 与 cohort，禁止静默丢弃或复用 GUI 的共享 client。
- Linux Headless 入口不得导入 Qt；`PROJECT_ROOT/PROMPT_DIR` 为只读资源，配置、日志、records、audit 与 experience 必须写入 `PA_AGENT_HOME`。只有已落盘的成功分析进入去重历史，Provider/持久化失败按同一 task ID 有界退避重试。
- 同一分析的 Prompt 文本、Provider Contract、decision-tree materialization、validator、retry contract 与审计 digest 必须来自同一个显式 `prompt_dir`；不得重新引入 import-time 路径分裂或以修改全局环境变量掩盖依赖注入。
- Codex 只能作为 `AnalysisRoleBackend` 产生现有 Provider Draft；不得拥有 Snapshot、Brooks Router、materializer、Validator、Risk、Trader Equation、VPA/Joint 权限或持久化。Stage 1、Stage 2 与 VPA 必须是独立 ephemeral 任务，且只读同一 Frozen Snapshot 内的授权 Evidence。
- VPS Codex 使用独立只读运行用户和发布固定的 Runtime AGENTS/Skills；不得直接读取生产数据库、Provider credential、修改源码或写交易状态。Codex availability fallback 必须重跑完整 Direct PA 链，Schema、事实、权限或专业判断失败不得触发 fallback。
- 发布依赖以 `uv.lock` 为准；Windows/Linux CI 必须运行全部 `not live` 测试、wheel 资源 smoke test 与关键静态错误检查。真实 Provider、行情和交易所测试只能显式标记 `live`，不得进入 CI。
- 业务结果信号不得覆盖 Qt 原生 `QThread.finished()`。运行中的线程必须保持强引用；窗口关闭时若仍有线程，只能协作取消并延迟销毁，禁止 `terminate()` 或对运行线程调用 `deleteLater()`。
- 冻结快照分析期间必须暂停网络刷新；生产置信度执行策略只对正式 PA 结果应用一次。研究 B 臂的 PA baseline 与 Joint 判断均发生在该策略之前。
- DeepSeek 原生 OpenAI-compatible 请求使用 `extra_body.thinking.type` 与顶层 `reasoning_effort`；不得混入 Anthropic `output_config.effort` 语义。
- 非瑣碎改动至少回归原 PA、VPA off、生产 Shadow、研究 A/A/B/B、HTF、订单数学及 records/audit 链路；测试后清理临时目录。
