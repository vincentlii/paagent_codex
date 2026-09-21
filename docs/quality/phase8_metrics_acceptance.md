# 阶段 8：性能指标与 Direct 渲染边界

更新：2026-09-14。实现基线：`e21903d`。状态：阶段 8 指标代码已实现、原定向检查通过；阶段整体尚未完成，真实完整链路与性能未验收。先处理 [真实运行审计](runtime_chain_audit_20260914.md)，暂不进入阶段 9。

## 范围与实现

1. `analysis_metrics.py` 复用阶段 1 的 `AnalysisMetric`，为每次角色调用输出 `analysis_metrics_v1`。每个值带 actual/estimated/unknown、单位和来源。未知为 null；显式上报零才是 actual zero。
2. DeepSeek 与 Codex usage 增加 `reported_fields`，识别 Provider 未返回 usage/cache 的情况。旧数值字段保持兼容，不再用它们推断新指标的可观测性。Codex 多个完成 turn 的已报告用量累计；部分 turn 缺字段时该字段保持未知。
3. 区分首轮序列化字节、估算 token、Evidence 调用/拒绝/响应字节、估算 retrieval token、工具耗时、CLI 进程时间、Worker 时间、Spool 排队/往返时间和 Backend wall time。各耗时范围重叠，不能相加成总延迟；排队时间使用同机器 epoch 时钟。
4. Backend wall time 包含封装和传输；record 另记从分析开始到持久化前的 monotonic 时长，明确不含最后一次磁盘写入。真实交付时点仍由阶段 6 拥有。
5. Provider 未返回美元账单，`cost_usd` 保持 unknown。不把订阅额度换算为虚构 API 成本。Direct 隐藏传输重试之前的用量未观测时标注 `final_response_only`，不得把最终回复用量冒充全程成本。
6. `_persist_full` / `_persist_partial` 保存调用指标及汇总 coverage；新字段保存在现有 record JSON / role_runtime，不新增数据库 schema，不回填历史。旧 SQLite 的零值投影不具有 actual 语义。
7. Codex Stage 1、Stage 2、VPA 和旧研究 Joint 跳过不用的 Direct 渲染；完整 role_protocol、HTF、上游结果、冻结价格/测量、知识仍经原 Publisher/MCP/Worker 必读矩阵交付。Direct 继续使用原 PromptAssembler；完整链回退重新走 Direct 编排。没有删除或摘要任何 mandatory 模块。
8. 同一 Frozen Snapshot 内，仅已直接通过的 PA Stage 1 attempt 1 可在冻结身份校验后续接
   PA Stage 2；Stage 2 仅省略 digest 完全一致且已在会话中完整读取的 price frame、HTF
   和知识，新 Router/Stage 1/VPA Artifact 和角色协议仍必读。不同 Snapshot、VPA、研究
   Joint 与 repair 继续独立 ephemeral；Stage 1 repair 后不得 resume。Direct 链路不变。
9. PA 续接不依赖 CLI 是否重用 Stage 1 MCP 启动参数。会话 workspace 保留稳定的
   evidence binding 路径，Host 在 Stage 2 启动前原子切换至新 Job/audit；MCP 在调用边界
   重新校验 Frozen Job 并重置该角色预算。从而保证续接的是推理历史，不是旧证据权限。

## 使用

```text
python -m pa_agent.experiments.study_cli metrics path/to/record.json
```

输出分别列 actual/estimated 的已知小计、unknown_count 与 complete_actual；缺失样本不补零。
`usage_coverage_complete` 同时要求全部调用实际报告 input/output/total/cache 且无隐藏重试用量；只“没有重试”不代表用量完整。旧 Evidence 事件缺少字节/token/耗时字段时也保持 unknown。
首轮 token 和 retrieval token 是局部文本估算，不等于 Provider 多轮累计输入，不能相加替代 API usage。
合并基线后，`evidence_call_count` 仍统计逐资源 Gateway 查询；新增
`evidence_mcp_call_count` 按审计 `mcp_call_index` 去重统计工具往返。旧事件缺该身份时后者
保持 unknown，Direct 为 0，不把一次 MCP 内多份资源误记为多次模型往返。
优化成功不能仅以首轮 Prompt 大小判断；需结合调用总用量、成功率、完整证据和盲评分。

## 三角色自审

同一实现者的三个视角，不是独立专家审计。

| 视角 | 判断与边界 |
| --- | --- |
| 软件架构师 | Backend 选择决定是否渲染 Direct Prompt，未建立平行 Router/Validator。指标在原记录路径保存；无配置默认或 DB migration。Windows 工程验证不能替代 Linux 用户隔离与 systemd 验证。 |
| 量化工程师 | 未上报与真零分开，估算与实际分开；调用范围/重试缺失显式呈现。不用部分用量证明总成本下降，不用一个快照证明增益。 |
| PA / Wyckoff VPA 交易员 | Stage 协议、完整必读材料与理论判断权保留；优化删除的是 Codex 未使用的 Direct 文本渲染，不改变背景、Setup、反证或联合决策内容。 |

## 验证记录

2026-09-13：239 项定向离线检查通过（11.64 秒），覆盖指标值域与缺失、Codex 不触发 Direct 渲染、原 PA/Shadow/联合链路、Provider/Worker/Spool、真实 SDK stdio MCP、完整 Prompt、HTF、唯一物化器、记录 codec 及阶段 7 Host/Paper。
全 `pa_agent` 关键 Ruff `E9,F63,F7,F82` 和 `git diff --check` 通过。
真实 Luna/max 工程实验结果见 [阶段 7 实验补充](phase7_luna_pilot_20260913.md)；本报告不证明模型判断、性能分位数或交易增益。

### 未观测与未验证

- 失败调用未返回 Provider usage 或 Worker 结果时，相关指标保持 unknown；目前不能重建这类调用的 token/工具/进程分项，不把失败成本排除后宣称降本。
- `analysis_to_persist_ms` 含失败分析到保存前的整体时间；不是持久化完成或可交易交付时间。
- 未运行 Linux 权限/systemd 验证、真实成功四臂、前向 Paper 或全量发布测试；未部署、未晋级生产。
- Shared strict Schema 适配移除生成端不支持的 `uniqueItems`，原始本地校验不变；DeepSeek 默认 JSON object 路径不受该适配影响。
