# 质量验收契约

本文件定义发布前的最低质量要求。交易表现需要独立样本外验证，本契约不承诺盈利。

## 硬不变量

1. 已接受结构化 Claim 的程序事实矛盾为 0。
2. VPA 创建 Setup、翻转方向或改写胜率的权限越界为 0。
3. 同一 Snapshot Bundle 的主周期与高周期来源身份漂移为 0。
4. 非法订单被接受、失败被记录为成功、运行记录静默损坏均为 0。
5. VPA `off` 模式中的 VPA Prompt、测量、Schema 与历史字段泄漏为 0。
6. 普通 PA Stage 1/Stage 2 的 VPA 内容泄漏为 0；Joint 输出订单、价格、胜率或 PA trace 的权限越界为 0。
7. 自由文本 regex 质量发现触发 validation retry 或阻断 PA-only 正式结果的次数为 0。
8. 可执行候选缺少独立 TP2 时的错误拒绝为 0；存在 TP2 时错误几何被执行的次数为 0。
9. VPA 知识模块泄漏 Joint action enum，或窗口 seasonality 在不完整/非因果覆盖下产出可用关系的次数为 0。
10. 持续 A/A/B/B 对同一 `(pipeline, cohort, market_snapshot)` 的并发或重启重复执行为 0；
    研究 Joint/账户进入生产正式结果或 `pa-official` 账户的次数为 0。
11. 同一分析中 Prompt、Provider Contract、materializer、validator 与审计 digest 的资源目录漂移为 0。
12. wheel 安装后缺失 `prompt_engineering` 知识文件的次数为 0。
13. 不同 `PA_AGENT_HOME/config` 之间 TradingView 用户别名缓存污染为 0。
14. Codex 的跨 Snapshot、超 cutoff、未授权 Knowledge/Evidence 读取被接受的次数为 0。
15. Codex 绕过 Draft materializer、Brooks Router、Validator、Risk、Trader Equation 或直接写生产数据库/交易状态的次数为 0。
16. Codex 与 DeepSeek 在同一正式 PA Stage 链中混用，或因 Schema、事实、权限和专业判断失败触发 availability fallback 的次数为 0。

## 结构与鲁棒性

- 认证支持严格结构输出的 Provider，首次结构成功率目标不低于 99.5%。
- 1000 组合法格式和措辞扰动中，错误硬拒绝率低于 1%。
- Provider 返回 400、401、429、5xx、timeout、截断 JSON 时均得到可解释状态。
- Codex CLI/auth/worker/evidence runtime 故障必须得到可解释 availability 状态；Codex role 每次使用独立 ephemeral 任务。
- SQLite busy、磁盘写入失败、数据断连时不产生幽灵成功记录。

## 性能

- PA-only 无重试 Prompt 的 p95 token 不超过迁移前匹配基线的 60%。
- 研究 B 臂总 token 不超过同 Snapshot PA-only 的 1.35 倍；生产 Shadow 不调用 Joint。
- Prompt 压缩不得删除 Background、八态频谱、Always In、Setup、竞争假设、HTF、Gate 或核心 VPA 证据域。
- 20 个资产并发排队时无丢失、重复、跨资产身份污染或重复数据库记录。
- GUI 连续 1000 次取消、切换品种与关闭，不发生 `QThread` 生命周期崩溃。
- Windows 与 Linux CI 必须使用 `uv.lock` 运行全部 `not live` 测试；CI 不得访问真实行情、Provider
  或交易 API。wheel smoke 必须从安装位置读取 Prompt，并证明 Headless import 不加载 Qt。
- Codex 初始静态 Context p95 不高于匹配 Direct Prompt 的 40%，包含 Evidence retrieval 的总输入
  p95 不高于 Direct 的 75%；完整运行仍受现有 300 秒 p95、250k token p95 和 1.35 倍研究成本上限约束。

## 交易与研究

- PA `estimated_win_rate` 是明确标注为未校准的 Setup 主观先验，可参与 Brooks Trader
  Equation；不得伪装成历史统计。VPA、外部固定阈值或单次样本不得修改它或建立第二道数字 Gate。
- Stop 必须来自当前 Setup 的结构失效判断；固定跳数、ATR 倍数或区间比例不得替代该判断。
  TP1 是执行与方程基线；TP2 仅是有独立结构依据时的可选研究字段。
- 任何 Joint 生产晋级提案前至少收集 300 个独立 Snapshot 与 100 个有明确结果的匹配样本；
  最终样本量按预注册效应量和统计功效重新计算。
- 相比 PA-only，触发频率下降超过 15% 时，必须由扣除成本后的 EV、MAE 或回撤改善支持。
- 生产环境保持禁止逆势订单；MTR/反转候选仅进入 Shadow 研究。
- A/A/B/B 需要臂顺序平衡，同时报告 intention-to-treat 与 per-protocol。
- 持续 A/A/B/B 必须按生产 Shadow 冻结快照抽样，协议无效为终态；Provider/Paper 暂时失败
  有界退避。已有有效四臂只允许重放并恢复 Paper，不得再次调用模型。
- 定向修复必须同时保留首次失败与最终接受状态：首次失败计入 first-pass 指标；最终已验证
  输出进入 grounding/status 指标；所有尝试均计入 token 与 latency，不得重复惩罚或隐去成本。
- `first_pass_clean` 只评估 Provider 首轮契约：warning（包括程序 execution policy）可接受；
  retry、normalization、fallback、未验证调用和运行失败必须分别给出机器可读原因。
- Stage 2 候选不得因订单数学或置信度被改写；实际执行资格只读取
  `execution_disposition`。Paper `paper_ohlc_v1` 与 `paper_ohlc_v2` 不得混合统计。

## 每阶段完成条件

每个 Phase 必须满足：

1. 新行为有直接测试或可复现验收命令。
2. 相关非 live 回归通过。
3. `git diff --check` 通过。
4. 清理缓存、临时日志与一次性回放产物。
5. 单独提交，提交信息标明 Phase。
