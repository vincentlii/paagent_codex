# 子 Agent 互补分工修复与复验依据

范围：保留现有未提交基线，只修复已确认问题。不自动 commit、不部署、不改知识全文、Router、Risk、交易权限。模型保持 `gpt-6-luna/xhigh`，本地不用 240 秒保护。

## 完成标准

1. 资源发现 Schema 与 Host 同源；HTF 用 `fact_ids`，不接受猜测别名。启动拒绝后不可再次派生。
2. 新 child 一次完整读取有限 scope；逐项预算、校验、审计和 PA/VPA 隔离不变；兼容旧单索引规格。
3. Stage 2 计同 PA 会话累计量的增量，只扣父 checkpoint，不扣独立 child。原始计数与 unknown 留档。
4. Host VPA 引用绑定独立审计，不冒充模型 normalization，交易输出及校验资格不变。
5. 各 Stage 尽早委托一个具体争议，父处理互补判断；综合仅更新相关结论，保留全部父 mandatory 与知识。
6. 离线验收和三角色复核通过后新登记真实冻结行情 A1/A2/B1/B2。失败/缺失不重跑洗掉；质量非劣且提速才讨论推进。

## 根因与取舍

旧 pilot 启动晚、子问题接近完整周期诊断，父收集后仍约 303 秒。改为事件边界/尺度/闭盘响应核对；不是添加另一完整分析者。预期提速待真实验证。

通用 resource object 导致字段猜错。公开 operation/arguments 联合 Schema，不放宽 Host。零散资源读取增加模型回合，旧提示还输出重复 envelope；新规格选 `scoped_batch_v1`，完整批量交付、只打印一次 structuredContent，预算不变。

CLI usage 是会话累计快照，续接不能按新角色重复计入。Host 存父会话 checkpoint，保留原始 observation；缺基线、非单调或缺报字段不推测计费。引用绑定是程序原值关联，和模型 normalization/warning/repair 分离。

假设确认/证伪相对本假设定义；回收注明旧/近边界；VPA 比较保留两段方向、幅度与限制。只用 Skill 引导，不加语义关键词硬 Gate。

## 三角色设计复核

软件架构师：唯一 Frozen Job/Gateway/Validator/Router 链、同源 Schema、异步互补任务、独立 capability 与预算；拒绝后不重启，历史规格不重写。

量化工程师：单变量、累计/增量分离、失败用量不隐藏、Host 绑定不污染合规指标。一个快照不能估 p95、收益或稳定性。

PA/VPA 交易员：Brooks 主框架、VPA 次级证据与父裁决不变；child 只查局部事件，不选 cycle/Setup/订单；原文与基线不压缩，不强造候选和标签。

## 进度

离线：177 项相关回归通过（16.61 秒）；追加父/子失败用量接线后 54 项 Worker/Backend/spool 回归通过（3.06 秒）；关键 Ruff 错误检查和 `git diff --check` 通过。不是全量 CI 或 Linux/VPS 验收。

独立三角色复核提出两个 P1：失败续接不能因 coverage unknown 清掉已观察增量；失败减法必须使用实际 stdout thread 身份。两者已修复并覆盖回归。Stage 2 候选前置条件的角色歧义和失败 VPA 绑定审计遗漏也已修复。

新研究入口为 `pa_agent.experiments.local_policy_pilot`，默认只注册零模型，冻结源码 fingerprint、CLI、Runtime assets、知识、设置和 evidence。新鲜公共 XAUUSDT 输入为主周期 100 棒、HTF 60 棒；通过原 Host freeze/replay 验证，PA/VPA 测量均基于同一可重放窗口，不混用 live frame 额外 warmup 与 replay。不同于上一轮快照。

Windows 本地用当前用户 Limited 的一次性无触发 Task 脱离对话进程；无自动重启、不存密码，用户退出 Windows 会话仍可能中断，不能当无人值守 VPS。正式启动器的 3 秒零模型探针在启动调用结束后独立完成，Task 返回 0，随后已移除。逐角色保存开始/返回 checkpoint 和 Worker outbox；只有完整 Host receipt 才计整链完成。旧 B2 missing 不恢复或重跑。

新登记顺序 A1/B1/B2/A2，A=`off`、B=`stage1_challenge_v1`，双方完整 `pa_vpa_joint`、`gpt-6-luna/xhigh`，role 3600 秒/cycle 10800 秒（不使用 240 秒保护）。VPA/Stage 2 child 实际效果未验证，仍不扩大。研究目录为本地 `research/codex-luna/complementary-policy-20260928-01`。

本轮真实四臂已运行，但隔离 CLI 账户限流，完整链 0/4；A1 仅 Stage 1 首稿验证，B1/B2/A2 未取得可比较 Draft。离线修复后 210 项定向回归通过；完整回执、A1 分段耗时、三角色复核与后续门禁见[新 cohort 报告](subagent_complementary_pilot_20260928.md)。不称提速、质量非劣、性能达标或生产可用；额度用尽期间不再调用模型。
