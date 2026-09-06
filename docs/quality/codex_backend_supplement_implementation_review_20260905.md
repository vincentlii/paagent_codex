---
lifecycle: active
capability: research-only
verification_scope: static-local
plan_baseline: C:/Users/85394/Desktop/Codex 版 PA Agent vNext 升级计划.md
code_baseline: 965f2df472f0e5af11b684c7864e62c7de66f8d1
branch: codex-analysis-backend-v1
---

# Codex Backend 补充实现与三角色验收

## 1. 结论

本轮已按照原升级计划和 2026-09-05 完整验收报告的整改顺序补齐代码候选。实现方向通过静态架构审查：
Windows vNext 的 PromptAssembler、Provider Draft、materializer、Validator、Brooks Router、Risk、Trader
Equation 和持久化仍是唯一成熟主链；Codex 只替换分析角色的执行 Backend，没有获得行情、协议、风控、
下单或源码修改权。

当前结论仍是 **research-only / not tested / not deployed**。本轮没有运行测试、历史 replay、真实 Codex
调用或 VPS 验收，因此不能宣称性能、幻觉率、分析质量或交易结果已经改善，也不得启用 `auto` 或默认 Codex。

## 2. 效果优先的语义完整性

本轮没有把“减少超长 Prompt”实现为删减交易信息。Frozen Job v4 使用显式
`context_policy=semantic_parity_v1`：初始请求只携带身份和 Evidence 索引，但 Codex 在形成 Draft 前必须
读取角色安全的完整价格帧、全部 HTF、由现有 PromptAssembler 生成的阶段协议，以及 PA Agent 根据当前
Context/Router 选定的 mandatory Brooks/VPA 知识。

知识 manifest 同时冻结 `module/version/digest/source_digest/content_digest/load_order`。Stage Skill 明确要求
按 `load_order` 加载，Worker 再从 Evidence audit 机械核对覆盖和顺序；缺失、乱序、越权、超预算或 digest
漂移均拒绝 Draft。这样减少的是一次性静态上下文和无关章节，不是 Brooks/VPA 分析深度。旧 Frozen Job
没有新策略标记时继续按旧契约回放，避免迁移规则污染历史可重放性。

## 3. 计划与整改覆盖矩阵

| 关注项 | 当前实现 | 审查结论 |
| --- | --- | --- |
| Backend 权限 | Codex 只返回现有 Provider Draft/role draft；PA 主链继续物化、校验、路由、风控和持久化 | 通过静态审查 |
| 角色隔离 | Stage 1、Stage 2、VPA、Joint 每次独立 ephemeral 任务；Stage 2 只接收结构化 handoff | 通过静态审查 |
| 语义完整性 | `semantic_parity_v1` + mandatory reads + `load_order`；阶段协议由成熟 PromptAssembler 代码生成 | 通过静态审查 |
| 图表证据 | PA 使用完整 price-only OHLC/measurement frame；HTF 全量必读；VPA 单独读取量价证据 | 通过静态审查 |
| PA/VPA 权限 | Brooks Primary；VPA Secondary Shadow；Joint 仅 research；PA roles 无原始 volume | 通过静态审查 |
| Evidence 调查 | Frozen Snapshot 内只读 MCP；digest、cutoff、role、call/byte budget 和审计约束 | 通过静态审查 |
| 输出与修复 | strict-schema 投影后仍进入原 Validator；每角色最多一次 allowlisted JSON Pointer patch | 通过静态审查 |
| 错误与回退 | stdout JSONL typed failure；Schema/request/policy/unknown fail closed；仅 availability 可冷回退 | 通过静态审查 |
| Replay 安全 | Job/runtime/model/knowledge/contract identity 冻结；20 条 Windows 历史故障 manifest 固定 | 工具已实现，尚未执行 replay |
| Codex 专属治理 | 独立 Runtime AGENTS、5 个 role Skills、read-only rules、required MCP、独立 CODEX_HOME | 通过 Runtime 静态审计 |
| Linux 隔离 | 独立 Unix worker；Job 只读；无 DB、Paper ledger、DeepSeek credential 和源码写权 | 配置已实现，尚未 VPS 复验 |
| 认证可观测性 | 周期 auth/heartbeat 仅投影枚举和 Runtime identity，不保存 token、账号或原始输出 | 通过静态审查 |
| 运维 | 熔断、timeout、lease/idempotency、retention、logrotate、SQLite online backup、Dashboard Quality/Performance/Backend AABB 只读区 | 已实现，尚未运行验证 |
| 研究设计 | Backend A/A/B/B、同 Snapshot、禁 fallback、盲评 packet/独立 key、closed-OHLC outcome 联结 | 已实现，实验结论为零 |

## 4. 对旧 `codex-paagent` 失误的防复发检查

1. 不复制或重写 Brooks Router、Validator、Risk、Trader Equation；Codex 无投票权和交易权。
2. 不用截断知识换取短 Prompt；mandatory 知识与完整 price frame/HTF 是 Draft 接受条件。
3. 不让 Codex 任意遍历知识库；知识选择属于 PA Agent，Skill 只规定分析程序。
4. 不提高 Wyckoff/VPA 权限；VPA 仍是独立 Shadow evidence，不覆盖 PA official decision。
5. 不让 Stage 2 继承长会话；只传递冻结且已验证的 Stage 1/Router artifact。
6. 不用自由重写修 Schema；可信 Host 只应用 Validator 授权的 pointer patch，再走原 Validator。
7. 不把请求、Schema、权限或未知错误伪装成“不可用”后 fallback；这些错误一律 fail closed。
8. 不用一次成功、专业文风或单次盈利证明升级有效；必须经过 replay、matched AABB、盲评和 Paper holdout。

这些约束能显著降低旧错误再次进入正式链的机会，但软件不能在未执行验证时保证“完全不会再发生”。真正的
防复发证据仍须来自固定历史 replay、故障注入和目标 VPS 权限负向测试。

## 5. 三角色审查

### 软件架构师

**结论：代码候选可以进入验证阶段，不能进入生产晋级。**

统一 `AnalysisRoleBackend` seam 避免在 Orchestrator 内继续堆 Codex 特判；Frozen Job、Runtime identity、
Evidence Gateway 和独立 worker 建立了清晰的可信边界。`semantic_parity_v1` 把“效果基线”和未来可能的
检索优化分开，是防止架构优化损害交易语义的关键。主要风险是当前 working tree 变更面较大且本轮未测试，
以及 Windows 上现存锁定 `__pycache__` 使 `compileall` 无法写入缓存；这不是语法失败，但必须在干净环境复验。

### 量化工程师

**结论：实验工具链可进入取样，当前实验结论仍为零。**

四臂协议保持相同 Frozen Snapshot、HTF、VPA catalog、Router/Validator 和 PA-only authority，并记录失败
coverage、Context、Evidence、token、latency、PA/VPA signature。盲评数据与解盲 key 分离，评分权重固定为
市场 Context 35%、Brooks Setup 30%、Evidence 可追溯性 20%、Stage 连贯性 15%；结果只联结既有
closed-OHLC ledger，不制造未来数据。验证顺序应为 20 条历史故障 replay、至少 30 个独立 Snapshot 工程
pilot、50 Snapshot 盲评，再到至少 300 Snapshot/100 decisive outcomes 的前向 Paper holdout。

### 顶级 Brooks PA/VPA 交易员

**结论：交易方法的主次关系和关键上下文已保留，判断增量尚未证明。**

Stage 1/2 保留成熟 Brooks 阶段协议、完整 price-only frame、全部 HTF、Context/Router 选中的知识以及
Stage 1 → Router → Stage 2 的纪律；No Trade、alternative hypothesis、invalidation 和不确定性仍是合法输出。
VPA 只作为 Secondary Evidence，拥有独立量价上下文，不反向污染 PA baseline。Codex 能按争议点继续调查
breakout acceptance、leg comparison、pullback 和 volume context，但是否真的减少错判与遗漏，必须由盲评
和结果分层验证，不能由解释长度判断。

## 6. 本轮静态验收证据

| 检查 | 结果 |
| --- | --- |
| Runtime asset audit | `ok=true`；8 个资产；digest `6d1f15a899e0eda9d0a882886f4d31d14e330298e5f6feb0a201b3d654aa6e73` |
| 全仓 Python 源码内存语法编译 | `pa_agent` 与 `tests` 共 414 个文件通过 |
| Dashboard JavaScript 静态语法 | Node `--check` 通过 |
| `git diff --check` | 通过；仅报告工作区既有 CRLF 警告 |
| 全目录 `compileall` | 未形成有效结论：现存锁定 `pa_agent/experiments/__pycache__` 导致 PermissionError；改以内存编译核对语法 |
| 自动化测试 | 按用户要求，本轮未运行 |
| 历史 replay / live Codex / VPS | 本轮未运行、未部署 |
| Commit | 本轮未提交 |

## 7. 后续验证门禁

1. 在干净、锁文件一致的环境运行 Codex/Direct 聚焦测试和全仓非 live 回归。
2. 对固定 20 条 Windows 历史故障逐条 replay，验证 Context、K0、Schema、Evidence、repair、warning 和
   provider failure 不回归。
3. 单 Snapshot 依次完成 Stage 1、Stage 2、VPA 与 research Joint live smoke，并执行越权/篡改/超预算/
   认证过期/重启恢复负向测试。
4. 在目标 Linux VPS 验证 Unix 权限、systemd、heartbeat、retention、backup/restore 和 Dashboard loopback。
5. 执行 30 → 50 → 300/100 的预注册实验，不按中间结果改变评分、样本或筛选条件。

门禁完成前继续保持 `deepseek_only` 默认、Codex `research-only`、Paper-only、单数据源/单品种、HTF + VPA
Shadow，并禁止以下表述：Codex 已降低幻觉、已节省总 Token、已提高性能、已提高胜率或已适合自动交易。
