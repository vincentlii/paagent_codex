# AGENTS

- 回应与生成文件优先使用中文；代码、API、错误信息、产品名保留英文。
- 修改 PA / Wyckoff VPA 融合链路前，先阅读 `CONTEXT.md`、`docs/wyckoff_vpa_integration.md`、`docs/architecture/ADR-0001-analysis-authority.md`、`docs/architecture/ADR-0002-provider-validation-boundary.md`、`docs/architecture/ADR-0003-pa-vpa-joint-roles.md` 与 `docs/architecture/ADR-0004-context-sliced-prompts.md`。
- 每个实现 Phase 必须按 `docs/quality/acceptance.md` 完成测试、临时文件清理和独立提交；机器阈值以 `config/quality_contract.toml` 为准。
- VPA 必须保持开关化：以 `resolve_vpa_mode(...) == "off"` 为准，VPA prompt、测量块及历史 `vpa_*` / `wyckoff_*` 字段不得进入原 PA 链路。
- 程序只拥有可确定、可复算、无歧义的事实测量、Schema、引用范围、订单数学和执行安全；趋势、周期、Setup、位置、信号质量及 Wyckoff 解释属于 LLM。
- `stage1_normalizer.py`、`stage2_normalizer.py` 与 `trace_normalize.py` 只能做字段白名单、容器复制和无语义记号规范化，不得补结论、改价或创造节点。唯一的审计元数据例外是：当 `reason` 明确引用快照内 K 线时，程序可将已有 `bar_range` 扩展为覆盖这些引用的最小包络；不得据此新增事实、修正引用、补全缺失范围或改变任何市场结论。
- Brooks Router 只读取已验证的 Stage 1 枚举与 pattern keys，不得从自由文本或程序指标推断策略。
- 正式分析目标链路为：程序冻结事实，PA Framework Analyst 提出主/替代假设，VPA Evidence Analyst 支持或挑战这些假设，Joint Decision Arbiter 在既有 PA 候选内决定时机，程序最后计算交易指标并执行安全约束。
- 普通 PA Stage 1/Stage 2 必须保持 price-only；VPA 只经独立 Evidence Analyst 进入。Joint 只输出有界 `decision_patch`，不得复制完整 Stage 2、订单价格、胜率、PA trace 或预测。
- Shadow 的正式结果始终是 PA-only；Soft 的正式结果是 Joint，VPA 或 Joint 失败时降级为 PA-only。A/A/B/B 仅用于离线或抽样研究，不得成为每次生产分析的固定开销。所有模式都不得让 VPA 创建 Setup、翻转方向或修改 `estimated_win_rate`。
- VPA observation 的 `bar_range` 由程序根据 evidence ID 生成。解释字段不得复写 OHLC/volume/RVOL 等数值或声明无明确窗口的新高/新低；基础事实只能引用程序目录。
- §9.0P 计划型限价只由程序校验 entry 相对 K1.close 的正确一侧；不得因 K1 影线历史触价、推测 tick size 或自由文本而自动判失效、改价或改单。
- `analysis_outcomes` 只保存人工核验的真实执行结果；`counterfactual_outcomes` 只保存闭盘 OHLC 对计划的反事实回放。两者不得混为真实 PnL、胜率或训练标签。
- 完整 A/B 与反事实报告只接纳当前 `pair_identity_version` 且通过 protocol/first-pass/authority 校验的记录，并按 `pair_id` 聚合重复实验；market order、同 K 入场与出场、同 K 同触止损与目标、突破跳空越价必须保留为 unsupported/ambiguous。同 timestamp OHLC 冲突时拒绝历史，原始快照滚出窗口时停止推进，不得猜测成交路径。
- 自动回放目标周期由 `general.counterfactual_horizon_bars` 配置，但单条记录不得超过 `analysis_bar_count - 1`；分析最少需要 20 根已收盘 K 线。
- 冻结快照分析期间必须暂停网络刷新；Shadow/Soft 的 PA baseline 与 Joint 判断都发生在置信度执行策略之前，执行策略只对正式结果应用一次。
- DeepSeek 原生 OpenAI-compatible 请求使用 `extra_body.thinking.type` 与顶层 `reasoning_effort`；不得混入 Anthropic `output_config.effort` 语义。
- 非瑣碎改动至少回归原 PA、VPA off、VPA Shadow/Soft 三角色链、HTF、订单数学及 records/audit 链路；测试后清理临时目录。
