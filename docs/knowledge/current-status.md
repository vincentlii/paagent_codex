---
lifecycle: active
capability: not-applicable
verification_scope:
  - local
issue_status: monitoring
最后核验: 2026-08-26
核验提交: 2e2d8b5
发布基线: pre-vps-ready-20260813 (5daeccd)
最后完整审计提交: 5daeccd
权威来源:
  - ../quality/pre_release_final_audit_20260813.md
  - ../quality/acceptance.md
  - ../architecture/ADR-0009-production-shadow-research-joint.md
  - ../architecture/ADR-0023-vps-observability-and-control-plane.md
  - ../linux_headless_deployment.md
---

# 当前状态

## 发布与证据基线

当前发布基线是标签 `pre-vps-ready-20260813`，提交 `5daeccd`。最后有记录的完整审计也绑定 `5daeccd`；这不是分支名称，也不把后续提交自动纳入该审计范围。

历史审计范围是 `e7e415b..5daeccd`。其中 2026-08-13 的 `1169 passed, 7 deselected` 是全量 `not live` 回归的历史证据，不能外推为 VPS、真实 Provider、真实行情、交易所或盈利验证。

2026-08-26 的 Dashboard 提交 `f8fffc0` 是审计后的已实现能力。它不在上述较早完整套件范围内；不要把该历史全量回归当作 Dashboard 的完整验证。提交 `311b12a` 与 `2e2d8b5` 分别是知识库设计与实施计划锚点，只用于知识工作，不是运行能力或验证证据。

## 模式矩阵

| 模式 | authority 与行为 | capability | verification_scope |
| --- | --- | --- | --- |
| `off` | 正式 price-only PA；不加载 VPA 内容 | implemented | 历史本地完整审计 |
| `shadow` | 正式结果仍为 PA Stage 2；独立 VPA evidence 仅供观察、审计与研究 | implemented | 历史本地完整审计 |
| `soft` | 生产请求的 effective mode 为 `shadow`；只有隔离 A/A/B/B 研究 B 臂可授权 effective `soft` | research-only | 历史本地完整审计覆盖研究实现，不是晋级证据 |
| Joint | 仅隔离 A/A/B/B 研究使用；不得进入正式分析或生产 Paper authority | research-only | 历史本地完整审计覆盖协议实现，不证明 uplift |

## 能力矩阵

| 能力 | 实现范围 | 验证范围与限制 |
| --- | --- | --- |
| PA 分析、冻结 Snapshot、Provider 契约、records/audit | 已实现 | `5daeccd` 历史 `not live` 审计；未进行 live probe |
| VPA Shadow | 已实现，独立 evidence 不改变正式 PA | 同上；不证明 VPA 提升分析质量或收益 |
| A/A/B/B 与 Paper 回放 | 已实现，仅研究 | 同上；缺少足够前向匹配样本和 Joint uplift 结论 |
| Windows GUI | 已实现 | 历史审计包含 GUI 生命周期证据；不等于目标环境部署验收 |
| Linux Headless / Paper | 已实现 | 代码与部署文档存在；真实 Linux、systemd、VPS 用户权限尚未验证 |
| VPS Dashboard | `f8fffc0` 后已实现，独立投影与 desired-state 控制 | 审计后新增，不受 2026-08-13 完整套件覆盖；目标 VPS 尚未验证 |
| 真实订单执行 | `no-go` | 无实现或验证主张 |

## 未解决的外部验证

- 真实 Linux、systemd 与 VPS 用户/目录权限。
- 真实 Provider、实时数据和交易所探测。
- Paper forward 的成本后盈利与稳定性。
- Joint 相对 PA-only 的增益、成本、滑点、频率、A/A 与 B/B 分歧。

## 下一步

### VPS 前

1. 在目标 VPS 按 [Linux Headless 部署](../linux_headless_deployment.md) 执行 `--check` 与 `--probe-data`，记录实际环境、权限和失败证据。
2. 使用 Paper 模式验证 Headless、Dashboard 投影、desired/applied generation 及暂停边界；不扩大为真实订单权限。
3. 将实际验证结果绑定环境、命令和 commit，再更新本页的 `verification_scope`。

### Joint 晋级前

1. 保持生产 `shadow` 与研究进程隔离，按冻结 Snapshot 运行预注册 A/A/B/B。
2. 收集至少 300 个独立 Snapshot 与 100 个明确结果的匹配样本，并同时报告 ITT、per-protocol、成本、滑点、频率、A/A 与 B/B 分歧。
3. 在成本后样本外证据满足 [质量验收契约](../quality/acceptance.md) 前，Joint 保持 `research-only`，不得宣称 uplift 或盈利。
