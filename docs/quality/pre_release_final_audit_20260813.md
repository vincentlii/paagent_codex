# PA Agent 上线前最终实施与审计报告

日期：2026-08-13

实施分支：`convergence-vnext`

审计基线：`e7e415b`
范围：上线前最终升级与审计计划的全部软件实现，不包含实际 VPS 部署、真实账户交易或盈利结论。

## 结论

计划内的软件改造已完成，代码库满足当前发布级工程验收，可以进入 Linux VPS 模拟盘部署准备。生产链路仍保持 `PA-only + VPA Shadow`，Joint 仅存在于隔离的 A/A/B/B 研究进程；没有把 Wyckoff 事件、PA 结构或交易方向改写为程序阈值策略。

本结论只代表实现、契约、回归与离线研究基础通过。Joint 是否提高成本后收益，必须由预注册的前向模拟盘样本证明，当前不得宣称已提高胜率或盈利。

## 计划完成矩阵

| Phase | 结果 | 主要实现 | 提交 |
| --- | --- | --- | --- |
| 检查点 | 通过 | 保存改造前状态、标签与可验证 bundle | `e7e415b` |
| P0 执行与发布安全 | 通过 | Stage 2 候选不可变；执行资格独立写入 `execution_disposition`；TP2 可空；Paper OHLC v2；失败、审计与运行状态显式化 | `3c18fcd` |
| P1 PA/VPA 交易语义 | 通过 | 胜率明确为未校准 PA 主观先验；结构止损与假设失效分离；VPA 窗口事实、因果 seasonality、相对量偏差和知识模块校准 | `6b99c3c` |
| P2 连续 Joint 研究 | 通过 | 生产 Shadow 快照抽样；原子租约、恢复、去重；同快照 A1/A2/B1/B2；ITT、per-protocol 和 Paper 回放隔离 | `0cb9bd8` |
| P3 性能与可复现发布 | 通过 | Prompt/contract/materializer/validator 同源；TradingView alias 缓存隔离；依赖 profile、`uv.lock`、wheel Prompt 资源、Windows/Linux CI；清理一次性诊断脚本 | `9a4db20` |
| 最终审计 | 通过 | 三角色复核、独立代码审查、全量非 live 回归、静态与发布资产验收 | 本报告提交 |

## 三角色审核

### 交易员

- PA Stage 1/Stage 2 保持 price-only。Brooks 负责市场框架、Setup、方向、结构止损、目标和主观胜率先验。
- VPA 是独立 Evidence Analyst，只解释 Background、Location、Effort versus Result、序列比较和 acceptance/rejection。它不创建 Setup、不翻转方向、不修改胜率，也不成为硬 Gate。
- VPA 知识继续按 Context 注入，核心知识始终可用，趋势、突破、区间事件和高潮模块只在 PA 已声明的相关 Context 中加载。程序只选择知识，不命名 Spring、UTAD、Absorption 或 Exhaustion。
- Stop 由 LLM 基于当前 Setup 的结构失效确定；程序只校验价格几何、真实 tick、风险和执行安全。固定 ATR 倍数、固定跳数或通用 RR 下限不替代交易判断。
- 没有以降低信号频率作为优化手段。VPA 的时机挑战只进入 Shadow 或研究 Joint；生产正式结果仍由 PA 决定。

### 量化架构

- OHLCV、时间、指标、相对量、窗口比较、因果 seasonality、订单数学和来源身份均可复算；市场结构和 Wyckoff 含义仍由 LLM 判断。
- 主周期、HTF、volume provenance、instrument identity 和 Prompt/contract digest 绑定同一冻结 Snapshot，防止跨资产、跨来源和跨版本污染。
- A/A/B/B 以 `market_snapshot_hash` 为独立样本，以 `cohort_id` 隔离 Provider、设置、代码和 Prompt 版本。重复运行与同一行情跨版本复核不重复计样。
- A/A 与 B/B 分歧用于估计模型随机性；收益主结论使用 episode-level ITT，未下单或未触发为 `0R`，不确定路径保持 censored，per-protocol 仅作交付诊断。
- 真实人工结果、Paper 反事实结果和不同模拟版本分开存储。数据库记录调用状态、token、latency、首次失败、最终接受、审计和恢复状态。

### 软件工程

- `pa_draft_v2` 是 Provider 唯一输出契约，单一 materializer 负责程序字段。Prompt、首次校验、重试契约、Joint 二次校验和审计 digest 使用同一显式 `prompt_dir`。
- 编排器在任何模型调用前拒绝 assembler/validator 的 Prompt 快照不一致，避免“按 A 提示、按 B 校验”。
- Hard reject 只覆盖结构化契约、明确程序事实、证据身份、权限、时序和订单安全。专业解释差异与自由文本措辞只进入 warning，不触发文本 regex 重试风暴。
- 生产 `soft` 请求被显式收敛为 `shadow`；只有隔离实验 executor 可以授权 Joint。VPA 失败保留有效 PA，并形成可诊断的降级状态。
- 核心、GUI、TradingView、原生交易所、通知、MT5、VPS 和开发依赖分组；`uv.lock` 固化解析结果，wheel 携带完整 Prompt 知识文件。
- 删除无长期价值的一次性诊断、修复和真实服务测试脚本；未保留 debug cache、临时日志或回放产物。

## 验收证据

| 维度 | 结果 | 证据 |
| --- | --- | --- |
| 全量回归 | 通过 | `1169 passed, 7 deselected`，全部 `not live` |
| 合法格式容错 | 通过 | 1000 组合法 JSON 表面扰动，错误 hard reject 低于 1% 契约 |
| 多资产 | 通过 | 20 个资产有界排队，终态与审计记录唯一 |
| GUI 生命周期 | 通过 | 1000 次 QThread 启停/回收无残留 |
| Prompt 性能 | 通过 | Stage 1 `39153` tokens；迁移基线比 `0.4981 <= 0.60` |
| Joint Prompt 成本 | 通过 | PA-only `64549`，研究 Joint `74189`；比值 `1.1493 <= 1.35` |
| 静态检查 | 通过 | Ruff 关键错误集、`compileall`、`pip check`、`uv lock --check`、`git diff --check` |
| 分发 | 通过 | wheel 隔离安装可读取 Brooks 与 VPA Prompt；Headless import 不加载 Qt |
| VPS 依赖 profile | 通过 | frozen hash requirements 含核心和原生交易所依赖，不含 Qt、TradingView、MT5 或测试依赖 |
| 敏感信息 | 通过 | 跟踪文件无真实 API key、Bearer token 或 private key；配置示例不含秘密 |
| 临时产物 | 通过 | `.tmp`、cache、`__pycache__`、build 和 egg-info 在 Phase 结束时清理 |

## 原则符合性

1. 程序只拥有可确定、可复算、无歧义的事实、契约、订单数学和安全约束。
2. LLM 保留 Background、位置、市场假设、Brooks Setup、Wyckoff 解释和入场时机的专业裁决权。
3. Evidence first、hypothesis plural、label last。程序不实现第二套 Strategy Router，也不实现半套 Wyckoff 状态机。
4. PA defines，VPA informs，LLM arbitrates。VPA 不审批 PA，不以未校准数字污染 Trader Equation。
5. 宽容尺度服务于正确分析：格式和明确事实严格，专业措辞和多解市场判断不机械拒绝。
6. 生产稳定性优先：PA 正式结果不依赖 VPA 或 Joint 成功，研究输出不能进入生产账户。

## 尚未替代的外部验证

- 未在本机执行真实 Linux kernel、systemd 或 VPS 安装，符合“除 VPS 部署部分”的范围。GitHub Actions 已配置 Ubuntu shell、wheel 和全量非 live 测试，实际 runner 结果应在推送后确认。
- 未调用真实 DeepSeek、行情或交易所 API；CI 明确禁止 live 调用。上线前仍需在目标 VPS 执行 `--check` 和 `--probe-data`。
- 尚无足够前向样本证明 Joint 优于 PA-only。任何生产晋级至少需要 300 个独立 Snapshot、100 个有明确结果的匹配样本，并通过成本、滑点、频率、A/A、B/B 和样本外质量门禁。
- 模型仍可能做出合理但错误的市场判断。程序可以消除事实矛盾和非法订单，不能无风险地消除交易不确定性。

## 发布与回退

- 最终发布锚点：`pre-vps-ready-20260813`。
- 发布 bundle：`PA_Agent-pre-vps-ready-20260813.bundle`，创建后使用 `git bundle verify` 校验。
- 回退检查点：`pre-vps-audit-20260813` 和对应 bundle。
- 初始 VPS 模式必须保持 `shadow` 与 Paper Trading；Joint 研究监控进程和生产 Headless 进程分离。
