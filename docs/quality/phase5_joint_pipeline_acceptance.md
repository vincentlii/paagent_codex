# 阶段 5：VPA 前置与联合 Stage 2 验收

基线：阶段 4 `6a1df34`。本阶段仅本地实现，未部署、未调用真实模型或市场数据。
结论：显式联合协议已接入现有主链，必要离线检查通过；不是生产晋级或交易有效性证明。
三角色是同一实现者的三个自审视角，本轮没有使用 subagent。

## 实际链路与配置边界

`Settings.analysis_pipeline = {"protocol_version":"analysis_pipeline_v1","pipeline":"pa_vpa_joint"}`
是本阶段的显式入口，与 Direct / Codex Backend 正交。默认仍为 null，旧配置不静默迁移。
显式 pa_only、pa_shadow 分别复用原 off、shadow；旧研究 soft 不允许与版本化 pipeline 混用。
看板入口、新配置默认值和发布验证留在后续阶段，本阶段没有修改任何部署配置。

1. 冻结行情 → 原数据预检 → price-only Stage 1 → 原物化器 / Validator → Brooks Router。
2. Stage 1 Gate 为等待或背景不可辨时，沿用原确定性等待结果，不调用 VPA / Stage 2。
3. 通过 Gate 后，`compile_pa_handoff(stage1, None, frame)` 产生 PA hypotheses；此时没有订单、
   Setup candidate 或 Entry Attempt。独立 VPA 使用同 Backend 的隔离角色实例调查冻结证据。
4. VPA 报告通过原角色 Validator 后进入联合 Stage 2。没有额外 PA baseline Stage 2，也没有
   研究 joint_arbiter。Codex 使用阶段 4 的 `pa-vpa-stage2-decision` Skill 和独立临时任务。
5. 联合引用必须来自同一冻结 catalog 中、已验收 VPA report 使用过的证据；PA trace 引用域不变。
   验证联合扩展后复用原 PA Draft 物化器、Validator、Trader Equation 和执行资格策略。
6. 最终候选仍写入 `stage2_decision`，`pa_baseline_stage2` / `joint_stage2` / `joint_decision` 留空。
   `analysis_basis.win_rate_provenance=llm_joint_subjective_prior`、`calibration_id=null` 明示未校准。

VPA 完整完成但报告数据不足，不等于角色运行失败：前者允许联合角色明确仅价格依据，后者停止。
比较最多三项有意义方案与等待属于分析协议，不强制凑数量，不新增程序生成的 Setup 列表。
结构 stop、TP1、目标障碍和限价可达性仍由模型判断；程序不以指标阈值代替专业解释。

## 保留的成熟能力与本阶段修正

| 边界 | 落点及实现 |
| --- | --- |
| 唯一主链 | `two_stage.py` 增加显式前置分支；未新增 Router、订单物化器或交易数学 |
| 完整知识 | 原 Brooks 路由知识、阶段规则、HTF、历史连续性与必读机制保留；未修改或删减知识原文、Skills |
| VPA 协议 | 新旧路径复用同一完整角色协议，保留比较幅度/方向、竞争解释、序列、候选确认和即时 timing 边界 |
| 单一输出契约 | PromptAssembler 初次请求、Agent embedded rules、Validator 重试和 record digest 均选择同一联合 Schema，非追加冲突的 PA Schema |
| 重试保护 | 联合引用/格式错误仅有界修复；沿用 Draft-to-Draft pointer 保护，不以 canonical 对比迫使重写订单 |
| 停止与记录 | Direct 和 Codex 的 incomplete / review_required 均落盘为非决策，不造 No Trade、不自动重跑；预检失败映射到未能启动的 stage1 |
| 历史污染 | 已有联合决策可能在普通 reasoning 字段含 VPA，不靠删几个字段伪装成 PA-only；Stage 1 历史保留旧 PA 诊断、不注入旧联合 Stage 2，原记录不改写 |
| 失败回退 | 只有既有 availability 类故障可请求整条指定协议冷回退；factory 保留相同 pipeline，wrapper 透传同一冻结输入并记录 full_analysis_pipeline |

Direct 非决策结果使用显式 stop Schema；包含 `$defs` 的 VPA Draft 在 transport union 中保持根引用可解析。
Codex 继续使用既有 result 封套和 typed stop，不要求 DeepSeek 凭据；没有把它退化成一次大 Prompt API。
Direct Prompt 的构建仍有前期成本，延迟渲染属于阶段 8，本阶段未宣称性能或 Token 已改善。

## 必要离线验证

290 项聚焦检查通过，覆盖以下 16 个文件（`tests/unit/`）：

- `test_joint_pipeline_runtime.py`、`test_pa_provider_contracts.py`、`test_pa_vpa_joint_orchestrator.py`
- `test_role_contracts.py`、`test_validation_retry.py`、`test_full_chain_fallback.py`
- `test_settings_round_trip.py`、`test_prompt_assembler.py`、`test_higher_timeframe.py`
- `test_record_codec.py`、`test_analysis_audit_store.py`、`test_order_opportunity.py`
- `test_runtime_execution_contract.py`、`test_codex_analysis_backend.py`
- `test_codex_job_publisher.py`、`test_codex_production_runtime.py`

其中新文件 20 个用例覆盖调用顺序、无 baseline、Direct/Codex 三角色停止、预检与 Gate、
引用拒绝/单次修复、隔离 VPA Backend、旧 Shadow、配置往返、历史隔离、Schema 引用及冷回退身份。
Backend/Provider 使用离线替身；factory、Publisher、Worker 契约回归不等于真实 CLI 或 SDK 成功运行。
全 `pa_agent` 的关键 Ruff（E9/F63/F7/F82）、新增测试 F 检查和 `git diff --check` 通过。
收尾时统一联合 Schema digest 后，重跑上述新链路、Provider Contract、Publisher 三个文件，
52 项通过，是 290 项中的子集，不重复累计。本阶段生成的 7 个临时测试目录已清理；
未触碰此前不可访问的旧 pytest 目录。

## 三角色自审

| 视角 | 结论与证据 | 尚未证明 |
| --- | --- | --- |
| 软件架构师 | 单一主链、显式 opt-in、完整规则复用；协议/重试/digest 一致；typed stop 与旧模式隔离 | 真实 SDK、CLI、Linux 权限和部署；看板/执行消费者的新模式完整交付 |
| 量化工程师 | 同快照引用、接受报告身份核验、数据不足与执行失败分开；未校准概率不伪称胜率；旧研究不重标 | 样本外校准、同快照配对实验、实际时延/成本，以及阶段 6 的交付后成交时间 |
| PA/VPA 交易员视角 | PA 背景不可由 VPA 改写；保留反证与等待；背景支持不等于入场支持；结构 stop/TP1 不被机械分数替代 | 真实模型是否充分理解必读知识、能否减少误判、是否改善最终交易决策 |

## 后续与恢复

下一阶段为阶段 6：持久可用时间、新鲜度与挂单有效期、latency-aware Paper；不能提前用当前
历史闭盘回放声称新联合链已在真实交付后成交。阶段 7 盲评/校准、8 性能、9 调度、10 看板、
11 Linux/wheel/真实 MCP SDK 与发布验收仍待实施。

无数据库 migration、历史回填、凭据变更或 VPS 操作。回到旧行为只需未配置 analysis_pipeline，
保留既有 prompt.vpa_mode；不要把新联合记录重标成旧 PA 或旧研究数据。持久化使用阶段 1 已有
可选 JSON 字段，所有已有 Windows 优化保留为基线。
