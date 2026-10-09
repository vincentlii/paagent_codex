# ADR-0025：VPA 前置与联合 Stage 2

- 状态：Accepted for implementation；契约准备不等于运行链路启用。
- 阶段 5 已完成本地显式编排及必要离线验收，详见 `../quality/phase5_joint_pipeline_acceptance.md`；
  默认配置、生产晋级和 VPS 均未改变，执行交付与真实效果继续按后续阶段验收。
- 实施基线：`e445907`；进度见 `../quality/joint_agent_implementation_plan.md`。

## 决策与兼容范围

新增 `analysis_pipeline_v1`，区分 `pa_only`、`pa_shadow`、`pa_vpa_joint`，与 Backend 正交。
联合模式按 Stage 1 → Validator → Router → VPA → 联合 Stage 2 → Validator/Risk 运行。
不先生成 PA baseline 订单，不追加旧研究 joint_arbiter，不重复调用 Stage 2。

此授权只适用于新版本显式联合协议。旧 off/shadow 保留原义；旧研究 soft 不能重标为新联合模式，
不能混用旧实验 cohort。旧配置缺少 pipeline 时保持原行为。新配置默认值在全链接入验收后改变。
ADR-0009/0024 的旧模式规定继续生效，不追溯改变历史，不授权 VPS 或真实交易。

## 专业权限

Stage 1 仍 price-only，唯一 Router 读取其已验证字段。VPA 独立分析冻结量价证据，不输出订单、
方向、Setup 或胜率。联合 Stage 2 在冻结 PA 框架和 Router 范围内选择具体方案与合理等待，
提出结构 entry/stop/TP1 和自己的未校准主观先验；不是对旧 PA 胜率加减固定 VPA 分数。
上游框架存在不可化解的**专业解释**分歧时不能自行改写或自动重跑 Stage 1；
新执行合同 `stage2_host_gated_draft_v1` 下，完整证据内的市场分歧走有依据的合法等待，
身份、投影、必读交付等机械故障由 Host fail-closed，不让 Stage 2 模型伪装成市场判断。
旧冻结 Job 的 `analysis_role_stop_v1` 语义保持原样。

## 机器契约

- `pa_vpa_stage2_draft_v1` 从既有 Stage2 Schema 生成订单、PA trace 与 bar_analysis。
  joint_analysis 记录支持、挑战、与等待的比较、入场可达性、目标障碍、失效与独立 VPA 引用。
  PA trace 仍只允许 PA 引用。VPA 引用须由 Host 对已验证 report 和同一 catalog 核验；
  字符串合法不等于证据有效。数据不可用时可空引用并明确说明，不伪造依据。
- `pa_provider_contracts.py` 仍是唯一物化器，禁止平行订单/方程实现。
- `analysis_role_stop_v1` 是独立非决策对象：incomplete 或 review_required，不允许 decision 等字段。
  Host 不得将它物化成模型 No Trade，不得传入执行层。
- actual/estimated/unknown 指标分离；unknown 为 null，实际零为零，拒绝 NaN/Infinity。
- as-of、模型完成、校验完成、持久可用与挂单过期时间必须分开；不能用 K 线开盘时间冒充交付。
  时间字段和记录写入尚待后续接入。

## 失败与执行

联合模式 VPA 正常完成但数据不足，与运行/Schema/工具失败不同；后者停止链路，不静默改为 PA-only。
availability fallback 只能在同一快照重跑完整指定协议。旧 Shadow 失败仍保留 PA 正式结果。
退出只使用结构保护 stop 与 TP1 全仓退出；TP2 参考不构成执行 Gate。动态管理不在本次范围。

## 三角色自审及验证

软件架构：扩展现有 seam 和版本化契约，无新 Router/materializer，旧接口保持不变。
量化工程：旧研究处理不重标，未知不计零，正式决策与非决策停止分离。
PA/VPA 交易员视角：VPA 提供反证而非机械投票，联合角色保留等待与结构风险判断。

契约验收覆盖旧模式映射、角色顺序、Schema 复用隔离、停止对象拒绝订单及有限数值。
启用还须验证引用、Gate、Router、修复、记录和执行消费者，不以契约测试替代全链验收。
