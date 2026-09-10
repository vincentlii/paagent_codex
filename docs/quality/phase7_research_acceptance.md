# 阶段 7：离线研究协议与盲评证据验收

更新：2026-09-10。本次基线：`d81b91a`（Stage 7a）。状态：**阶段 7 本地实现与离线验收完成，真实研究未验收**。
不部署、不调用真实模型。Host 复用成熟交易链；Runtime 仅增加研究调查权限与审计，不改变生产默认行为，不删减知识或 Skills。

## 已实现范围

| 项目 | 实现 |
| --- | --- |
| 自足的冻结证据 | 主周期完整 K 线、HTF、来源上下文、VPA catalog、Host 提供的完整知识和测量；校验摘要、目录身份和已闭盘/as-of 边界。未来收益不进入证据包。 |
| 独立盲评材料 | 公开 packet 与解盲 key 分目录、拒绝覆盖；只投影专业分析字段，排除 Provider、调用账本、运行凭据与未来结果。内容和文风仍可能暴露处理组，不能保证双盲。 |
| 评分可靠性 | 拒绝 NaN、Infinity、布尔评分、重复案例及漏评；PA 四维权重保持原值。VPA 四维独立评分，缺失/不可评分保持 null，coverage 单独报告。 |
| 错误分类 | 区分事实错误、理论错误、合理分歧；禁止把未来赢亏作为专业评分错误类别。严重错误单列，不被综合平均分掩盖。 |
| 三类独立协议 | `analysis_study_v1` 显式区分 backend、joint、investigation；joint 只允许 pipeline 差异，investigation 只允许同 Codex/model/reasoning/pipeline 下调查方式变化。 |
| 预注册与重复臂 | 全部样本与 A1/A2/B1/B2 在调用前一次写入，ABBA/BAAB 顺序；客户端 token 与 runtime 对象不得复用，返回记录绑定快照及 pipeline，fallback 拒绝。 |
| 缺失与重跑 | 单次开始标记及逐臂回执不可覆盖；失败继续留在分母，进程中断留下 missing，不用成功重跑替换失败。原始 exception 文本不写入评审材料。 |
| episode/holdout | 按冻结市场身份及重叠前向窗口聚合；先平均重复臂，再平均同 episode 内快照，最后比较 episode。跨 holdout 边界窗口拒绝，holdout 公开需显式释放。 |
| outcome 分层 | 仅接收预注册 horizon 与 `paper_latency_v3` 的 Host outcome；已确认 no_order/entry_not_triggered 为 0R，pending/ambiguous/missing 不补零。完整 episode 的均值明确标为诊断，不能冒充缺失总体的 ITT。 |
| 只读研究入口 | `python -m pa_agent.experiments.study_cli` 支持 freeze/register/review/score/unblind/coverage/outcomes/release-holdout，不包含模型调用或交易命令。 |

`backend_review` 原评分 schema v1 仍可读取；新建完整 Backend 评审包必须显式提供
`frozen_evidence`，不能再生成只有结论、没有审核材料的“完整评审包”。
旧 `analysis_backend_aabb_v2`、PA/VPA A/A/B/B 和 SQLite 研究账本保持原协议，不被重标。

## 接口与实施指引

1. Host 用 `freeze_review_evidence(record, knowledge=..., measurements=...)` 冻结证据。必须传入当前任务真实授权的完整资源，不能传摘要，也不能在见到实验结果之后读最新知识替代原版本。
2. `register_study(root, plan, samples)` 写入独立私有目录。plan 记录 contrast、两个条件、Domain fingerprint、执行版本、主指标、样本量理由及 holdout 边界。每个 sample 记录快照 hash、分析后的前向窗口及完整 evidence。
3. `run_study(root, execute_arm)` 只向受信任 Host 执行器派发预注册请求。执行器必须实际构建独立 Backend，走原 TwoStageOrchestrator/Validator，返回 AnalysisRecord、实际 condition/domain fingerprint、client token、runtime 对象和 evidence digest。
4. 不允许执行器只复制请求中的 condition 冒充观测值。使用 `StudyHostExecutor` 重算资源指纹、核对 Backend 配置与逐次响应模型/effort；fixed 权限由 Frozen Job、Gateway 和 Worker 执行，不仅依赖回执自报。
5. `export_study_review` 导出单一 development 或 holdout 的 packet；key 和原 study 目录不得交给评审者。salt 保存在私有文件，不放命令行或公开 packet。
6. `score` 将评分与 packet digest 绑定并拒绝覆盖；`unblind` 检查 score/key 绑定。文件 digest 防止意外混用，不是防恶意修改的签名或 OS 权限隔离。
7. outcome 接口只消费 Host 已核验、扣除成本的回报，不能由模型填写。不能将旧 closed-OHLC 结果改名成 v3 forward Paper。

命令示例（相对路径均由操作者指定，输出必须是新文件）：

```text
python -m pa_agent.experiments.study_cli freeze record.json knowledge.json measurements.json evidence.json
python -m pa_agent.experiments.study_cli register study-spec.json private-study
python -m pa_agent.experiments.study_cli coverage private-study
python -m pa_agent.experiments.study_cli review private-study reviewer private/key.json private/salt.txt
python -m pa_agent.experiments.study_cli score reviewer/packet.json ratings.json sealed-scores.json
python -m pa_agent.experiments.study_cli unblind sealed-scores.json private/key.json diagnostic.json
```

holdout 默认不导出、不汇总收益。最终评审时使用 `release-holdout ROOT --reason ...`，
释放行为只记一次。私有原始目录仍需独立文件权限管理；代码不能阻止有管理员权限的人偷看 holdout。

## 本次补齐的实现

1. `study_host.py` 每臂创建独立 Backend，复用 TwoStageOrchestrator、Router、Validator、PendingWriter。禁用 fallback，清除 GUI 角色推理覆盖；校验实际模型/effort，持久化或审计失败不算成功。启用经验时必须有非 null 冻结池，不能读当前经验。
2. `study_resources.py` 在注册前采集完整 Prompt 目录原文、price-only fact catalog、HTF/VPA 与冻结经验。Host 在执行前后重算源代码、Runtime assets、知识和共享设置指纹；测量重算比对。不会把已完成 Codex Job 的上游结论混入公共盲评证据。知识包是完整资源保管，不授权模型绕过 Router 任意选择策略。
3. fixed/agentic 进入 digest-bound runtime identity、首轮索引与调用账本。fixed 禁止可选查询/测量，Worker 再检查成功调用；所有 mandatory 读取及受限 repair 保留。默认 agentic，不改变旧 Job 行为。
4. `study_run.py` 默认离线预检，只有 `--execute` 才创建 Provider；实验 Codex 固定 `gpt-5.6-luna/max`。注册条件非法即拒绝，不自动换模型。
5. `study_paper.py` 每 assignment 独立账户，消费已持久化回执、实际激活时间和 `paper_latency_v3`。入场 expiry 与 outcome horizon 分离；绑定失败独立记录，不重跑模型。已落库订单可恢复绑定，迟到未激活保留 censored。共享 Paper 参数首次绑定后锁定；观察只接受同标的/周期、已闭盘且未超预注册 horizon 的数据。收益来自扣成本账本，不能由模型填写。

本次复核并修正上一轮工作中的权限身份比对遗漏、调查元数据遗漏、资源自报、结果持久化缺口、冻结经验 null、expiry/horizon 混淆及绑定恢复问题。未另建交易规则或物化器。

### 使用顺序与接口

```text
python -m pa_agent.experiments.study_cli freeze-host record.json prompts evidence.json
python -m pa_agent.experiments.study_cli domain-fingerprint settings.json prompts
python -m pa_agent.experiments.study_cli register study-spec.json private-study
python -m pa_agent.experiments.study_run private-study --settings settings.json --prompt-dir prompts
```

冻结来源必须是无未来数据的 Host 快照；历史 record 只用于重建市场输入，不把旧模型结论作为新证据。
设置、代码或知识改变后必须重新注册，不覆盖旧研究。Direct 条件填写实际生效的模型/effort。
额度恢复并批准真实实验后才加 `--execute`；前向 Paper 另加 `--paper`，且 settings.paper_trading.enabled 必须为 true。
Host 后续闭盘数据通过 `observe_study_paper(root, db, spec=..., timeframe=..., bars=...)` 更新隔离账本；
`collect_study_paper_outcomes(root, db, split=...)` 只读导出，再交 `summarize_outcomes`。不新增行情抓取器或后台调度。
故障恢复仅调用 `bind_study_paper` 补绑定，不重跑分析；失败细节在私有 `paper_errors`，不得塞入盲评包。

## 尚未完成：真实研究边界

Codex 无额度，本次没有真实 SDK/CLI/VPS 联调、真实盲评、前向 Paper 样本、样本量/功效分析、置信区间或概率校准。
这些仍属于阶段 7 的实证验收，不挪到阶段 8，不宣称“整个研究已完成”。输出保持 `ready_for_promotion=false`、`calibrated_probability=false`。
离线 stub 验证 Host 接线，不证明远程 Provider 行为；文件 digest 不是恶意篡改防护。前向实时数据馈送由既有 Host 承担，历史数据不能伪装成及时激活的 Paper。

## 三角色自审

同一实现者的三个视角，不是三位独立专家或 subagent 审计。

| 角色 | 判断 |
| --- | --- |
| 软件架构师 | 本地接线通过：复用唯一成熟编排和记录路径，无新 DB schema；权限在 Gateway/Worker 生效。真实进程及 VPS 隔离仍待联调。 |
| 量化工程师 | 本地设计通过：三类对照分离，失败保留、冻结共享资源、实际交付/成本/窗口绑定；未开展真实样本，不通过效果、功效或校准验收。 |
| PA / Wyckoff VPA 交易员 | 未删减原文、mandatory 知识、HTF、PA Router 或 VPA 证据语义；fixed 不是精简专业基线。实现通过不代表专业判断正确，需盲评严重错误与合理分歧。 |

## 验证记录

实现过程中 104 项直接相关回归通过，覆盖新盲评/协议、旧 Backend/PA-VPA A/A/B/B、回放、实验 store、联合运行、HTF、审计与订单计划。
提交前重新运行上述 104 项，全部通过（5.31 秒）；全 pa_agent 关键 Ruff `E9,F63,F7,F82` 和 `git diff --check` 通过。
没有调用 Provider、行情或交易服务；只使用临时文件和测试数据库。
上述 104 项为 Stage 7a 历史记录。本次 Stage 7b 验证另记于下；本报告不是研究效果报告。

Stage 7b：78 项定向离线测试通过，覆盖 study Host/协议/Paper、盲评、Spool、CLI Worker、Evidence Gateway、Backend、Publisher 与 production factory；全 `pa_agent` 的 Ruff `E9,F63,F7,F82` 与 `git diff --check` 通过。
重点验证冻结经验拒绝、持久化失败拒绝、固定模型、调查权限、回执篡改、真实激活时间、恢复去重、成本配置锁定与 horizon 边界。未运行真实 Provider 或部署测试。
