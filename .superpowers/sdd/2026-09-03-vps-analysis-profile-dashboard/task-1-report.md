# Task 1 report: Persist the Codex Backend North Star and amend decisions

## 状态

完成。仅更新任务 brief 指定的文档；未修改 runtime code、配置、测试或部署资产。

## 文件变更

- `docs/knowledge/codex-analysis-north-star.md`：新增中文长期架构依据，固定 Direct 对照、Agentic Analysis 验证假设、Frozen Evidence、角色 authority、Skill 边界、Profile 控制面与晋级指标。
- `AGENTS.md`：增加 Codex Backend、Dashboard、Runtime rules、Skills 与部署工作的强制阅读规则。
- `CONTEXT.md`、`docs/knowledge/README.md`：链接 North Star，并把它加入对应工作阅读路线。
- `docs/architecture/ADR-0024-codex-analysis-backend.md`：修订第 7 节，限于无凭据、allowlisted、版本化的 Analysis Profile 在 Headless idle boundary 应用；初版资产、VPA Shadow、HTF 固定，其余禁令保留。
- `docs/knowledge/current-status.md`：明确本阶段起点为本地 Backend 已存在、Profile UI 与真实 Codex live evidence 待验证。
- 本报告：记录范围、理由、验证、提交和未验证边界。

## 覆盖理由

North Star 明确保留 PA Agent 的事实与执行 authority、Brooks 的主框架地位、VPA 的 Secondary Shadow 角色，以及 Router、Validator、Risk、Trader Equation 与 execution 的既有所有权。Codex 只能在同一 Frozen Snapshot 内按 role allowlist 调查 Evidence 并返回 role draft；AGENTS 约束 immutable authority/no-lookahead，Role Skills 只描述程序而不复制领域知识。

Evidence-on-demand 取代的是同一 Frozen Snapshot 内的单体 Prompt 拼接，不是放宽 Evidence、事实、Schema 或权限边界。评价不以 Prompt 长度为成功条件，明确要求衡量 factual errors、evidence omissions、Stage coherence、calibration、schema compliance、retries、latency/cost 与 Paper outcomes，并保留 ITT、coverage 与真实/反事实结果分离。

## 验证

- `rg -n "Codex Analysis|North Star|Analysis Profile" AGENTS.md CONTEXT.md docs/knowledge docs/architecture/ADR-0024-codex-analysis-backend.md`：通过；North Star、阅读入口、Profile 与当前阶段状态均可检索。
- `git diff --check`：通过；无空白错误。
- 人工一致性复核：ADR 的 Profile 窄例外不扩大资产、VPA、HTF、Prompt、AGENTS、Skills、Knowledge、Validator、Router、Risk、Paper、凭据、路径或交易权限；当前状态与 `research-only` 边界一致。

## 提交

`docs: preserve Codex analysis north star`（本报告与上述文档同一提交）。

## Concerns

本次只固化架构与阶段状态，未执行真实 Codex、VPS、Dashboard Profile UI 或 Paper 验证；这些证据取得前不得将 Codex Backend、Profile 或交易结果视为已晋级。
