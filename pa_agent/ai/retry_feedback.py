"""Build structured retry user messages from ValidationError."""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pa_agent.ai.retry_policy import extract_feedback_targets
from pa_agent.ai.validation_messages import format_validation_errors

StageLit = Literal["stage1", "stage2"]
RETRY_REWRITE_SENTINEL = "__REWRITE_REQUIRED__"

_RETRY_FIELD_PATH_RE = re.compile(
    r"^(?:(?:s1|s2|gate|trace|vpa|breakout_price|signal_chain|metrics):)?"
    r"(?P<path>[A-Za-z_][A-Za-z0-9_]*(?:\[\d+\]|\.(?:[A-Za-z_][A-Za-z0-9_]*|\d+))*)"
    r"(?=\s|$)"
)

_CATEGORY_ZH: dict[str, str] = {
    "a": "JSON 语法错误",
    "b": "缺少必填字段",
    "c": "字段值/一致性不符合规则",
    "d": "未输出 JSON（正文为空或纯文字）",
}

_FORBIDDEN_STAGE1 = (
    "direction / cycle_position / gate_result（除非反馈明确指出该字段自相矛盾）",
    "程序给出的 OHLC、EMA、ATR、比率和精确包含关系",
    "未被反馈点名的市场语义、形态标签与置信度",
)

_FORBIDDEN_STAGE2 = (
    "阶段一 cycle_position / direction / Setup（阶段二不重复输出诊断摘要）",
    "未被反馈点名时改变 order_type、order_direction 或三价",
    "交易者方程 10.3 的数值结论（须基于真实 entry/stop/target 重算）",
)


def _try_parse_obj(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _geometry_excerpt(frame: Any, limit: int = 8) -> str:
    try:
        from pa_agent.ai.kline_features import compute_kline_geometry_features

        feats = compute_kline_geometry_features(frame, limit=limit)
        lines = ["程序可复算 K 线事实（仅供核对，不提供 bar_type 或 Setup 结论）："]
        for f in feats:
            lines.append(
                f"  PA.BAR.K{f.seq}: direction={f.candle_direction}, "
                f"close_zone={f.close_zone}, "
                f"relation_to_previous={f.relation_to_previous}"
            )
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return ""


def _pa_extreme_fact_excerpt(frame: Any) -> str:
    """Provide retry-safe prior-window and adjacent relations without arithmetic."""
    try:
        from pa_agent.ai.fact_catalog import build_analysis_fact_catalog

        catalog = build_analysis_fact_catalog(frame)
        lines = ["K1 前窗极值的程序事实（仅在确需比较时引用）："]
        for requested in (5, 10, 20):
            fact_id = f"PA.COMPARE.K1.PRIOR{requested}"
            try:
                value = catalog.fact(fact_id).value
            except KeyError:
                continue
            lines.append(
                f"  {fact_id}: high_relation={value['high_relation']}, "
                f"low_relation={value['low_relation']}"
            )
        for newer in range(1, 6):
            fact_id = f"PA.PAIR.K{newer}.K{newer + 1}"
            try:
                value = catalog.fact(fact_id).value
            except KeyError:
                continue
            lines.append(
                f"  {fact_id}: newer_bar={value['newer_bar']}, "
                f"older_bar={value['older_bar']}, "
                "newer_high_vs_older_high="
                f"{value['newer_high_vs_older_high']}, "
                "newer_low_vs_older_low="
                f"{value['newer_low_vs_older_low']}, "
                "newer_close_vs_older_close="
                f"{value['newer_close_vs_older_close']}"
            )
        return "\n".join(lines) if len(lines) > 1 else ""
    except Exception:  # noqa: BLE001
        return ""


def build_retry_feedback(
    err: Any,
    *,
    stage: StageLit,
    attempt: int,
    max_attempts: int,
    frame: Any = None,
    previous_raw: str | None = None,
) -> str:
    """Compose a concise retry user turn."""
    category = str(getattr(err, "category", "c") or "c")
    missing = list(getattr(err, "missing_fields", None) or [])
    invalid = list(getattr(err, "invalid_fields", None) or [])
    allowed_values = dict(getattr(err, "allowed_values", None) or {})
    provider_contract = getattr(err, "code", "") == "provider_draft_contract"
    item_limit = len(missing) + len(invalid) if provider_contract else 24
    detail = format_validation_errors(
        invalid,
        missing_fields=missing,
        max_items=max(24, item_limit),
    )
    cat_zh = _CATEGORY_ZH.get(category, category)

    lines = [
        f"## 校验未通过（第 {attempt}/{max_attempts} 次重试）",
        "",
        f"阶段：**{stage}**",
        f"失败类型：**{cat_zh}** (category={category})",
        f"说明：{getattr(err, 'message', '')}",
    ]
    if getattr(err, "parse_position", None):
        lines.append(f"JSON 解析位置：{err.parse_position}")
    lines.append("")
    lines.append("**必须修正（仅修下列项；其余字段保持与上一轮一致）：**")
    if missing:
        for i, m in enumerate(missing[:item_limit], 1):
            lines.append(f"{i}. [缺少] {m}")
    remaining_slots = max(0, item_limit - min(len(missing), item_limit))
    for i, inv in enumerate(
        invalid[:remaining_slots],
        start=min(len(missing), item_limit) + 1,
    ):
        lines.append(f"{i}. [无效] {inv}")
    omitted = max(0, len(missing) + len(invalid) - item_limit)
    if omitted:
        lines.append(
            f"- 另有 {omitted} 项未逐条展开；草稿中所有 "
            f"`{RETRY_REWRITE_SENTINEL}` 均必须重写。"
        )
    if detail:
        lines.append(f"摘要：{detail}")
    if allowed_values:
        lines.append("")
        lines.append("**Schema 合法值：**")
        for path, values in list(allowed_values.items())[:12]:
            lines.append(
                f"- `{path}`: {json.dumps(values, ensure_ascii=False)}"
            )
    if provider_contract:
        from pa_agent.ai.pa_provider_contracts import render_provider_draft_contract

        lines.extend(
            [
                "",
                "**本次 Provider Draft 的唯一结构契约：**",
                render_provider_draft_contract(stage),
            ]
        )
    editable_targets = extract_feedback_targets(invalid, missing)
    if "pa_evidence.fact_refs" in editable_targets:
        lines.append(
            "Additional editable path: pa_evidence.fact_refs. Add only the required "
            "catalog fact ID and preserve existing valid IDs."
        )
    if previous_raw and _try_parse_obj(previous_raw) is not None and not provider_contract:
        lines.append(
            f"- 上一轮草稿中被点名的具体字段已替换为 `{RETRY_REWRITE_SENTINEL}`；"
            "请整句重写这些字段，只保留最终结论。禁止写入核对、纠错、修正、应改为或旧句复述过程。"
        )

    lines.append("")
    lines.append("**禁止为通过校验而修改：**")
    forbidden = _FORBIDDEN_STAGE1 if stage == "stage1" else _FORBIDDEN_STAGE2
    for item in forbidden:
        lines.append(f"- {item}")

    geo = _geometry_excerpt(frame) if stage == "stage1" and frame is not None else ""
    if geo:
        lines.append("")
        lines.append(geo)

    if stage == "stage1" and any(
        "unbounded new-high/new-low" in inv for inv in invalid
    ):
        lines.append("")
        lines.append("**极值引用提示：**")
        lines.append(
            "- 先区分单根 K 线内部位置与跨窗口极值：局部位置改写为"
            "“收盘靠近本棒高点/低点”，并与 `PA.BAR close_position` 一致；"
            "它不需要 `PA.COMPARE`，也不得暗示创新高/新低。"
        )
        lines.append(
            "- 只有跨窗口的“新高/新低/窗口最高/窗口最低”才需写明具体 `K#-K#`，"
            "或引用下方真实 `PA.COMPARE` 的 relation；"
            "并把该 fact ID 同时放入 `pa_evidence.fact_refs`。"
        )
        extreme_facts = _pa_extreme_fact_excerpt(frame) if frame is not None else ""
        if extreme_facts:
            lines.append(extreme_facts)

    if stage == "stage1" and any(
        "local close position conflicts" in inv for inv in invalid
    ):
        lines.append("")
        lines.append("**单棒收盘位置提示：**")
        lines.append(
            "- 逐棒核对上方 `PA.BAR close_zone`：只有 `upper_quartile` 可写“收盘靠近本棒高点”，"
            "只有 `lower_quartile` 可写“收盘靠近本棒低点”；`middle_half` 改写为收盘居于本棒中部。"
            "这是程序几何分区，不得由模型改写。"
        )

    if stage == "stage1" and (
        any("gate_trace" in inv and "answer" in inv for inv in invalid)
        or "冲突" in getattr(err, "message", "")
    ):
        lines.append("")
        lines.append("**gate_trace answer 枚举提示：**")
        lines.append(
            "- §2.2：`answer` 只能用 **是/否/中性/等待/不适用**；"
            "「同向/冲突/背景中性」写在 `branch`（如 aligned / conflict / neutral_background），"
            "**禁止**把「冲突」写在 answer。"
        )

    if stage == "stage1" and any(
        "gate_trace" in inv and "reason 引用了 bar_range" in inv
        for inv in invalid
    ):
        lines.append("")
        lines.append("**gate_trace 引用范围提示：**")
        lines.append(
            "- `bar_range` 是 reason 中全部 K 线引用的审计覆盖范围；"
            "同一节点比较长程与近期窗口时，写能覆盖两者的完整包络范围（如 K100-K1），"
            "不要只保留其中一个子窗口。"
        )

    if stage == "stage1" and any(
        "bar_by_bar_summary" in inv and "role" in inv for inv in invalid
    ):
        lines.append("")
        lines.append("**bar_by_bar_summary.role 格式提示：**")
        lines.append(
            "- `role` 必须是 1-32 个字符的简洁 PA 功能描述，例如 "
            "structure / signal / entry / confirmation / continuation / pullback / trap / test；"
            "不要在此重复完整 Setup 名称或写成长句。"
        )

    invalid_text = "\n".join(str(item) for item in invalid)
    if stage == "stage1" and "numeric market facts" in invalid_text:
        lines.append("")
        lines.append("**PA program-fact citation:**")
        lines.append(
            "- Remove hand-copied OHLC, ratio, EMA, and ATR numbers from bar reasons. "
            "Cite PA.BAR / PA.PAIR / PA.WINDOW / PA.COMPARE facts and write only the "
            "contextual Brooks interpretation."
        )
    if stage == "stage1" and "must cite PA.WINDOW instead of restating" in invalid_text:
        lines.append("")
        lines.append("**PA count citation:**")
        lines.append(
            "- Remove the exact bullish/bearish bar count or run length from the reason. "
            "Cite the matching PA.WINDOW fact ID, add it to pa_evidence.fact_refs if "
            "needed, and keep only the contextual Brooks interpretation."
        )
    if "htf_context must cite at least one injected" in invalid_text:
        lines.append("")
        lines.append("**HTF 引用提示：**")
        lines.append(
            "- `htf_context` 至少引用一根输入中实际存在的 `HTF.H#` K 线；"
            "只说明它对当前周期的背景约束，不得用 HTF 直接改写当前周期方向或 Gate。"
        )

    if "EMA slope" in invalid_text:
        lines.append("")
        lines.append("**EMA 事实提示：**")
        lines.append(
            "- EMA slope 只能引用程序给出的 EMA delta 方向或数值；"
            "close 与 EMA 的距离不能替代 EMA slope。"
        )

    if "claims one tick without verified tick size" in invalid_text:
        lines.append("")
        lines.append("**Tick size 提示：**")
        lines.append(
            "- 快照小数位只是显示精度。若输入没有交易场所确认的 tick size，"
            "必须写“真实 tick size 未确认，执行前校验”，不得声称已精确放置 1 tick。"
        )

    if stage == "stage1" and any(
        "bar_by_bar_summary" in inv and "trapped_side" in inv for inv in invalid
    ):
        lines.append("")
        lines.append("**bar_by_bar_summary.trapped_side 枚举提示：**")
        lines.append(
            "- `trapped_side` 只能用 **bulls / bears / both / none / unknown**；"
            "**禁止** null/空值；无明确被套方向时写 **none**。"
        )

    if stage == "stage1" and any(
        "bar_by_bar_summary" in inv and "context_effect" in inv for inv in invalid
    ):
        lines.append("")
        lines.append("**bar_by_bar_summary.context_effect 枚举提示：**")
        lines.append(
            "- `context_effect` 只能用 **strengthens_bull / weakens_bull / "
            "strengthens_bear / weakens_bear / neutral / transition**；"
            "必须逐字使用 schema 枚举，禁止自造近似拼写。"
        )

    if stage == "stage2" and any("decision.reasoning" in inv for inv in invalid):
        lines.append("")
        lines.append("**decision.reasoning 字数提示：**")
        lines.append(
            "- `decision.reasoning` **≤280 字**（中文）；只写结论 + 1–2 个关键依据，"
            "细节放在 decision_trace §10.3 或 key_factors。"
        )

    if stage == "stage2" and any(
        "decision_trace" in inv and "answer" in inv for inv in invalid
    ):
        lines.append("")
        lines.append("**decision_trace answer 枚举提示：**")
        lines.append(
            "- `answer` 只能用 **是/否/中性/等待/不适用**；"
            "「部分一致」写 **中性**，「尚需下一根K线确认」写 **等待**；"
            "**禁止**写「是部分」「部分」「待确认」等。"
        )

    if category == "a":
        lines.append("")
        lines.append("**JSON 语法提示：**")
        lines.append(
            "- `content` 必须是**裸 JSON 对象**（以 `{` 开头、以 `}` 结尾），"
            "**禁止** ` ```json ` 代码围栏或前后缀说明文字。"
        )
        prev = (previous_raw or "").strip()
        if "```" in prev:
            lines.append(
                "- 上一轮使用了 markdown 围栏或非 JSON 前缀；请删掉围栏与说明，"
                "只输出可 `json.loads` 的完整 JSON。"
            )

    lines.append("")
    lines.append(
        "请根据以上说明，在 assistant 正文 `content` 输出**完整**阶段"
        f"{'一' if stage == 'stage1' else '二'}裸 JSON（不要 markdown 围栏）。"
        "交易结论须与 K 线分析一致，不得仅为修字段而反转方向。"
    )

    if category == "d":
        if not (previous_raw or "").strip():
            lines.append(
                "⚠️ 上一轮正文 content 为空：请把 JSON 写在 content，不要只写在思考区。"
            )
        if stage == "stage2":
            lines.append(
                "⚠️ 禁止输出英文说明、Markdown 表格/摘要、「修改完成」「已写入文件」等对话文字；"
                "禁止 ` ```json ` 围栏。content 必须整段为可 `json.loads` 的阶段二裸 JSON。"
            )

    return "\n".join(lines)


def parse_json_object(raw: str | None) -> dict[str, Any] | None:
    """Parse a prior model response for retry consistency comparison."""
    return _try_parse_obj(raw or "")


def build_sanitized_retry_draft(
    previous_raw: str | None,
    invalid_fields: list[str],
    missing_fields: list[str],
) -> str:
    """Redact invalid concrete leaves so retries cannot copy a failed sentence."""
    obj = _try_parse_obj(previous_raw or "")
    if obj is None:
        return previous_raw or ""
    changed = False
    for raw in list(invalid_fields or []) + list(missing_fields or []):
        path = _retry_field_path(str(raw))
        if path and _replace_existing_path(obj, path, RETRY_REWRITE_SENTINEL):
            changed = True
    if not changed:
        return previous_raw or ""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _retry_field_path(error_text: str) -> str | None:
    match = _RETRY_FIELD_PATH_RE.match(error_text.strip())
    return match.group("path") if match else None


def _replace_existing_path(obj: dict[str, Any], path: str, value: Any) -> bool:
    parts: list[str | int] = []
    for name, bracket_index, dot_index in re.findall(
        r"(?:^|\.)([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]|(?:^|\.)(\d+)",
        path,
    ):
        if name:
            parts.append(name)
        elif bracket_index:
            parts.append(int(bracket_index))
        elif dot_index:
            parts.append(int(dot_index))
    if not parts:
        return False
    current: Any = obj
    for part in parts[:-1]:
        if isinstance(part, int):
            if not isinstance(current, list) or not 0 <= part < len(current):
                return False
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
    leaf = parts[-1]
    if isinstance(leaf, int):
        if not isinstance(current, list) or not 0 <= leaf < len(current):
            return False
        current[leaf] = value
        return True
    if not isinstance(current, dict) or leaf not in current:
        return False
    current[leaf] = value
    return True
