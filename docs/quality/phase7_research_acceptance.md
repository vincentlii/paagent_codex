# 阶段 7：离线研究协议与盲评证据验收

日期：2026-09-09。基线：`e6121a4`。当前状态：**部分实现，阶段 7 尚未完整验收**。
只修改离线研究代码；不部署、不调用真实模型，不改变 PA/VPA 交易主链、Runtime Skills 或生产默认配置。

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
4. 不允许执行器只复制请求中的 condition 冒充观测值。当前 engine 校验返回身份及记录一致性，但无法仅靠回执证明外部执行器真正禁用了调查工具。
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

## 尚未完成：不得越过的验收边界

1. 新三类协议仍通过注入的 `execute_arm` 运行，尚未提供连接实际 Direct/Codex 的新版统一 Host adapter。
2. **同模型 fixed/agentic 条件尚未连接 Frozen Job/Gateway 的可执行调查权限。** 必须在同模型、同推理强度、同核心必读内容下实际限制可选调查，不能把换模型/换 API 一起混入纯调查增量。
3. Frozen Job 完整知识/测量资源自动收集器尚未接入；当前冻结函数验证所提供内容的一致性，但不能证明调用者没有漏传授权资源。不能将“非空知识字典”等同 semantic parity。
4. Paper outcome 到新 assignment 的自动绑定尚未接通；现在只有严格的导入/汇总接口。需要绑定运行记录、实际交付时点、预注册窗口，不能人工猜测 outcome。
5. 真实盲评、前向 Paper、样本量/功效分析、置信区间与概率事件校准均未开展。输出始终 `ready_for_promotion=false`、`calibrated_probability=false`。

上述是阶段 7 的剩余实现项，不擅自挪到阶段 8，也不宣称整个阶段已完成。
后续应先完成真实 Host adapter、调查权限和冻结资源绑定，再做一次定向验收；不要重写已成熟交易主链。

## 三角色自审

同一实现者的三个视角，不是三位独立专家或 subagent 审计。

| 角色 | 判断 |
| --- | --- |
| 软件架构师 | 离线协议、记录及只读评审独立于交易 authority；未改 SQLite schema。接口与失败保留基本成立，但真实 Host adapter/权限仍缺失，因此不通过阶段 7 完整验收。 |
| 量化工程师 | 三类 estimand 分离、重复臂/episode/holdout、missing/censored 与有限分数已实现；描述性均值不能替代 ITT、功效或概率校准。外部 outcome 来源及实际 treatment 仍待绑定。 |
| PA / Wyckoff VPA 交易员 | 背景、Setup、证据与阶段连贯性保留；VPA 单独关注背景位置、努力结果比较、接受/跟随、反证和不确定性。合理解释分歧不应等同错误；知识未删减，但资源完整性接入仍待补。 |

## 验证记录

实现过程中 104 项直接相关回归通过，覆盖新盲评/协议、旧 Backend/PA-VPA A/A/B/B、回放、实验 store、联合运行、HTF、审计与订单计划。
提交前重新运行上述 104 项，全部通过（5.31 秒）；全 pa_agent 关键 Ruff `E9,F63,F7,F82` 和 `git diff --check` 通过。
没有调用 Provider、行情或交易服务；只使用临时文件和测试数据库。
本报告是未完成项的交接约束，不是研究效果报告。
