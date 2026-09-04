---
lifecycle: active
capability: research-only
verification_scope: local
最后核验: 2026-09-02
核验提交: 2bc71ae
权威来源:
  - ../architecture/ADR-0024-codex-analysis-backend.md
  - acceptance.md
  - ../knowledge/research-and-evaluation.md
---

# Codex Analysis Backend 最终本地审计

> 本文仅记录 `2bc71ae` 的历史本地审计。当前准入结论以
> [2026-09-05 Codex Backend 完整验收](codex_backend_full_acceptance_audit_20260905.md)为准。

## 结论

`new_paagent_vnext` 的 Codex Analysis Backend 已完成本地工程实现，可进入目标 VPS 安装、真实
ChatGPT account 认证、Paper-only dry run 和受控 A/A/B/B 数据收集。生产默认仍为
`deepseek_only`，Codex 保持 `research-only`；本审计不证明真实 VPS 可用、分析质量增量或盈利。

升级没有迁移旧 `codex-paagent` 的程序投票、Wyckoff authority 或截断知识结构。PA Agent 继续拥有
Frozen Snapshot、事实、知识选择、Two-Stage、Brooks Router、Validator、Risk、Trader Equation、
持久化与 Paper；Codex 只返回现有 Provider Draft。

## 阶段交付

| 阶段 | 提交 | 验收结果 |
| --- | --- | --- |
| 0 基线与问题审计 | `7c3da3f` | 固定原项目精华、历史错误族和完成标准 |
| 1 Backend seam | `5ed9939` | Direct 行为置于同一 role interface，主链未改权 |
| 2 Frozen Agent Runtime | `bbf7c2c` | Snapshot/cutoff/resource digest、Evidence allowlist 与预算 |
| 3 CLI Worker | `581b558` | ephemeral、read-only、no shell/Web、required MCP、结构化 outbox |
| 4 Codex Adapter | `0749449` | Draft 兼容、一次定向修复、失败分类与熔断 |
| 5 Production control/audit | `8ef7ee7` | Dashboard 三模式、safe apply、完整冷回退和审计投影 |
| 6 Backend A/A/B/B | `d04764b` | 四臂预登记、runtime 隔离、失败 coverage 与重复冲突 |
| 7 VPS production wiring | `2bc71ae` | bounded publisher、独立 Unix spool worker、Headless 接线与部署资产 |

## 三角色审计

### 软件架构师

通过：Backend seam 位于模型 role 调用边界，Codex 不拥有 orchestrator；Stage 1/2 使用独立
ephemeral task，Stage 2 只接收已验证 handoff。完整 Direct Prompt 不再复制到 Codex，核心 Context
受 32 KiB hard cap；facts、bars 和 PA-selected knowledge 通过 Frozen Evidence 按需读取。

VPS 部署资产设计为 `pa-agent`、`pa-agent-codex`、`pa-agent-dashboard` 三个用户；静态配置表明
Codex worker 不加载 Headless credential，且未授权 `/var/lib/pa-agent`，源码和 Python environment
只读。上述 OS 权限仍待目标机负向验收。model、
reasoning、Runtime assets 与 binary digest 进入 cohort、Frozen Job、dispatch、worker 重算和 result
复验。availability 才能在 `auto` 触发同 Snapshot、从 Stage 1 开始的完整 Direct 冷回退。

### 量化工程师

通过工程实验基础，不通过交易晋级。`analysis_backend_aabb_v1` 固定同 Snapshot 的 A1/A2 Direct 与
B1/B2 Codex，禁 fallback，保留 missing/failed arm、ABBA/BAAB、ITT、cohort 与重复冲突。
`initial_context_bytes`、Provider token usage、Evidence bytes/calls 和 latency 分开审计，避免把静态
Prompt 变短误报为总成本下降。

尚无真实 Codex 样本。20 个独立 Snapshot 仅允许 review repeatability；替换 Direct 至少需要
300 个独立 Snapshot、100 个明确 outcome、盲评、成本后样本外 Paper 结果与预注册功效分析。

### 顶级交易员

通过 authority 边界。Brooks 仍是主要市场框架，VPA 仍是二级 Shadow evidence；Codex 不能选择
另一套方法、创造 Setup、翻转 PA 方向、改写主观胜率、跳过 Gate 或修改结构止损。生产 Shadow 下
Codex 只负责 PA Stage 1/2，独立 Direct VPA role 只写 sidecar，official path 仍为 `pa_only`。

Validator 能阻止可复算事实矛盾和非法订单，但不能消除主观市场判断错误。分析更长、更专业或
单次交易盈利都不是晋级证据。

## 本地验证证据

- Phase 7 最终相关回归：160 passed。
- Phase 8 最终 focused suite（含跨 spool availability fault injection）：168 passed。
- Python compileall：通过。
- `git diff --check`：通过，仅 Windows checkout 的 CRLF 提示。
- 独立审计三轮：前两轮发现并修复 Unix UID 隔离、runtime identity 和 availability 分类；第三轮
  无剩余 P1，可提交。
- 本机没有 Bash/systemd，`install_vps.sh` 与 unit files 只完成资产级静态测试，不能标为 Linux 实测。

## VPS 必做验收

1. 以三个 Unix 用户分别做正向目录访问和负向读取/写入；Codex 必须不能读取生产 DB、Paper、
   DeepSeek credential，不能写 `/opt/pa-agent`。
2. 以 `pa-agent-codex` 完成 `codex login --device-auth` 与 `codex login status`；认证文件保持 0700/0600。
3. 执行 Headless 与 Codex worker `--check`，核对 binary digest、model、reasoning 和 Runtime version。
4. 注入 worker 停止、binary 不可执行、spool 只读、429、timeout 与 malformed result；确认只有
   availability 进入完整冷回退，identity/schema/事实错误 fail closed。
5. 先运行 `codex_only --once` Paper dry run，再验证 `auto`；观察队列、p50/p95 latency、token、
   Evidence、磁盘增长和 systemd restart。
6. 保存 commit、配置、runtime identity、命令输出与脱敏 audit 后，才能把 verification_scope 改为 VPS。

## 未完成与 No-Go

- 未在目标 Linux/VPS 验证 systemd、Unix 权限、ChatGPT auth、网络、rate limit 或真实模型 flag。
- 未完成 20 个真实独立 Snapshot repeatability、盲评、300/100 Paper forward 或收益检验。
- jobs/outbox 用于 replay，当前不自动删除；上线需设置磁盘告警和经审计的归档/保留策略。
- 未授权真实订单、Joint 生产、默认 Codex 或删除 DeepSeek Direct。Dashboard 只能请求已有三种
  Backend mode，不能改 model、Prompt、Skill、knowledge、Risk 或资产。
