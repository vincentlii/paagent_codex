---
name: draft-pointer-repair
description: Use when PA Agent Validator authorizes a bounded repair of a parseable role draft.
---

# Draft Pointer Repair

目标是修复 Validator 已定位的表示、Schema 或 grounding 问题，不重新分析市场。

1. 首先调用 `get_repair_findings`，读取原始 parseable draft、typed findings 和允许修改的 JSON Pointers；只在 finding 必需时读取额外冻结 Evidence。
2. 对每个 pointer 只做满足 finding 的最小替换；未列出的字段必须保持语义和值不变。
3. 不新增、删除、重排或置空未授权路径。pointer 授权只允许修复所指问题，不授权重新选择方向、Setup、订单语义、价格或 Risk；需要新市场判断或改写上游时必须 unrepairable。Host 仍执行原重试语义保护，不能以“路径在白名单”绕过它。
4. 不以新故事解释旧错误，不重新扫描市场，不调用未解决当前 pointer 所必需的 Evidence。
5. 输出受限 Patch Envelope：`status`、`changed_pointers`、`replacement_fragments`、`supporting_evidence_ids`、`unrepairable_reason`。每个 replacement 使用相同 pointer、`replace|remove` operation；replace 值放入 `value_json` 字符串。
6. `changed_pointers` 的顺序必须与 `replacement_fragments` 完全一致，且不得包含 allowlist 外路径。
7. 若修复需要越权、缺少 Evidence、原稿不可解析、Snapshot/contract 不匹配或存在 no-lookahead/policy violation，返回 `status=unrepairable`、空 patch 和简短原因，不猜测修复。

修复会话只安装本 Skill。工具仅允许 `get_repair_findings`、按精确 ID 读取 `get_facts` / `get_pa_evidence` / `get_htf_evidence` / 角色授权的 `get_vpa_evidence` / `read_allowed_knowledge`；禁止重新读取整图、整模块或调用派生测量工具。只输出原 Patch Envelope，不套 result 封套。
