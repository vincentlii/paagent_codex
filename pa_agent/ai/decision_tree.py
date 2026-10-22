"""Binary decision tree loader and trace helpers (方案 A)."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from pa_agent.ai.kline_references import K_BAR_RANGE_RE
from pa_agent.config.paths import PROMPT_DIR

_BINARY_DECISION_FILE = "二元决策.txt"

_SECTION_RE = re.compile(r"^##\s+(\d+)\.\s+(.+)$")
_NODE_RE = re.compile(r"^###\s+([\d.]+[A-Z]?)\s+(.+)$")
_NODE_HEADER_LINE_RE = re.compile(r"^###\s+([\d.]+[A-Z]?)\s+")
_BRANCH_OUTCOME_LINE_RE = re.compile(
    r"^(是|否)(?:[，,]([^：:\n]+))?[：:]\s*(.*)$"
)
_BRANCH_OUTCOME_STOP_RE = re.compile(
    r"^(处理|判断依据|条件|说明|多头条件|空头条件|上涨|下跌)[：:]"
)
_QUESTION_BAR_BASIS_SUFFIX_RE = re.compile(r"（基于[^）]+判断）$")

GATE_RESULTS = frozenset({"proceed", "wait", "unknown"})
TRACE_ANSWERS = frozenset({"是", "否", "中性", "等待", "不适用"})
TERMINAL_OUTCOMES = frozenset({"wait", "reject", "trade", "proceed"})
_EXECUTABLE_ORDER_TYPES = frozenset({"限价单", "突破单", "市价单"})
_STAGE2_MODEL_SECTION_IDS = frozenset(str(value) for value in range(3, 12))

# Stage 1 owns only the semantic gate after the program data preflight.
STAGE1_GATE_NODE_IDS = (
    "1.2",
    "1.3",
    "2.1",
    "2.2",
    "2.3",
    "2.4",
    "2.5",
)

# 阶段一禁止当作闸门的节点（原则/执行层，非诊断闸门）
STAGE1_FORBIDDEN_GATE_NODES = frozenset({"0.3"})


def stage2_provider_node_ids(path: Path | None = None) -> tuple[str, ...]:
    """Return model-owned Stage 2 node IDs from the canonical decision tree."""
    return tuple(
        node_id
        for node_id in load_decision_tree(path).get("node_index", {})
        if (
            node_id != "10.3"
            and (
                node_id == "14"
                or node_id.split(".", 1)[0] in _STAGE2_MODEL_SECTION_IDS
            )
        )
    )


def _gate_trace_sort_key(node_id: str) -> tuple[int, int, str]:
    """Numeric sort key for gate_trace node_id values used by the ordering validator.

    Converts '1.1' -> (1, 1, '1.1'), '2.3' -> (2, 3, '2.3') so that chapter-
    section pairs sort in natural document order.  Non-numeric suffixes fall
    back to string comparison in the third component.
    """
    parts = str(node_id or "").split(".", 1)
    try:
        major = int(parts[0])
    except (ValueError, IndexError):
        return (999, 999, node_id)
    if len(parts) == 1:
        return (major, 0, node_id)
    sub = parts[1]
    try:
        return (major, int(sub), node_id)
    except ValueError:
        return (major, 999, node_id)

def _node_sort_key(node_id: str) -> tuple[int, str]:
    """Sort key for decision_trace ordering checks."""
    prefixes = (
        ("3.", 30),
        ("4.", 40),
        ("5.", 50),
        ("6.", 60),
        ("7.", 70),
        ("8.", 80),
        ("9.", 90),
        ("10.1", 101),
        ("10.2", 102),
        ("10.3", 103),
        ("11.", 110),
        ("14.", 140),
    )
    for prefix, rank in prefixes:
        if node_id.startswith(prefix) or node_id == prefix.rstrip("."):
            return (rank, node_id)
    return (999, node_id)


def _trace_node_ids(trace: list[dict[str, Any]]) -> list[str]:
    return [str(x.get("node_id", "")) for x in trace if isinstance(x, dict) and x.get("node_id")]


def _index_of(nodes: list[str], node_id: str) -> int:
    try:
        return nodes.index(node_id)
    except ValueError:
        return -1


def _collect_branch_outcome(block_lines: list[str], branch_char: str) -> str:
    """Extract summary text after ``是：`` / ``否，…：`` in a node block."""
    collecting = False
    parts: list[str] = []
    qualifier = ""

    def _flush_qualifier() -> None:
        nonlocal qualifier
        if qualifier and (not parts or parts[-1] != qualifier):
            parts.append(qualifier)
        qualifier = ""

    for raw in block_lines:
        line = raw.strip()
        if not line:
            if collecting and parts:
                break
            continue
        m = _BRANCH_OUTCOME_LINE_RE.match(line)
        if m:
            if m.group(1) == branch_char:
                collecting = True
                qualifier = (m.group(2) or "").strip()
                rest = (m.group(3) or "").strip()
                if qualifier and rest:
                    parts.append(f"{qualifier}：{rest}")
                    qualifier = ""
                elif rest:
                    parts.append(rest)
                elif qualifier:
                    parts.append(qualifier)
                    qualifier = ""
            elif collecting:
                break
            continue
        if collecting:
            if _BRANCH_OUTCOME_LINE_RE.match(line):
                if parts:
                    break
                continue
            if line.startswith("###") or line.startswith("##"):
                break
            if _BRANCH_OUTCOME_STOP_RE.match(line):
                break
            bullet = line.lstrip("-• ").strip()
            if qualifier:
                parts.append(f"{qualifier}：{bullet}")
                qualifier = ""
            else:
                parts.append(bullet)
            if len(parts) >= 5:
                break
    _flush_qualifier()
    text = " · ".join(p for p in parts if p)
    if len(text) > 140:
        return text[:137] + "…"
    return text


def _parse_all_branch_outcomes(text: str) -> dict[str, dict[str, str]]:
    """Map node_id -> {branch_yes, branch_no} from decision tree source text."""
    outcomes: dict[str, dict[str, str]] = {}
    chunks = re.split(r"(?=^###\s+[\d.])", text, flags=re.MULTILINE)
    for chunk in chunks:
        header = chunk.strip().splitlines()[0] if chunk.strip() else ""
        hm = _NODE_HEADER_LINE_RE.match(header)
        if not hm:
            continue
        nid = hm.group(1)
        lines = chunk.splitlines()[1:]
        outcomes[nid] = {
            "branch_yes": _collect_branch_outcome(lines, "是"),
            "branch_no": _collect_branch_outcome(lines, "否"),
        }
    return outcomes


def get_node_branch_outcome(
    node_id: str,
    branch: str,
    *,
    path: Path | None = None,
) -> str:
    """Return human-readable outcome for the ``是`` or ``否`` arm (branch=yes|no)."""
    tree = load_decision_tree(path)
    node = tree.get("node_index", {}).get(str(node_id), {})
    key = "branch_yes" if branch in ("yes", "是", "y") else "branch_no"
    text = str(node.get(key, "") or "").strip()
    if text:
        return text
    return "继续下一步" if key == "branch_yes" else "等待 / 切换逻辑"


@lru_cache(maxsize=16)
def load_decision_tree(path: Path | None = None) -> dict[str, Any]:
    """Parse ``二元决策.txt`` into sections + nodes for the UI tree."""
    txt_path = path or (PROMPT_DIR / _BINARY_DECISION_FILE)
    text = txt_path.read_text(encoding="utf-8")
    branch_outcomes = _parse_all_branch_outcomes(text)
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in text.splitlines():
        sec_m = _SECTION_RE.match(line)
        if sec_m:
            if current is not None:
                sections.append(current)
            current = {
                "id": sec_m.group(1),
                "title": sec_m.group(2).strip(),
                "nodes": [],
            }
            continue

        node_m = _NODE_RE.match(line)
        if node_m and current is not None:
            current["nodes"].append(
                {
                    "id": node_m.group(1),
                    "question": node_m.group(2).strip(),
                }
            )

    if current is not None:
        sections.append(current)

    node_index: dict[str, dict[str, Any]] = {}
    for sec in sections:
        for node in sec["nodes"]:
            nid = node["id"]
            bo = branch_outcomes.get(nid, {})
            node_index[nid] = {
                **node,
                "section_id": sec["id"],
                "section_title": sec["title"],
                "branch_yes": bo.get("branch_yes", ""),
                "branch_no": bo.get("branch_no", ""),
            }

    return {
        "version": 1,
        "source": txt_path.name,
        "sections": sections,
        "node_index": node_index,
    }


def normalize_bar_range(item: dict[str, Any], *, default_max_seq: int | None = None) -> str:
    """Return a canonical display token from the required ``bar_range`` field."""
    from pa_agent.ai.trace_normalize import fix_bar_range_string

    raw = item.get("bar_range")
    if raw is not None and str(raw).strip():
        return fix_bar_range_string(str(raw), default_max_seq=default_max_seq)

    return ""


_BRANCH_DISPLAY_ZH = {
    "bullish": "多头",
    "bearish": "空头",
    "neutral": "中性",
    "yes": "是",
    "no": "否",
    "lower": "下边界",
    "upper": "上边界",
    "middle": "中间",
    "trading_range": "普通交易区间",
    "trending_tr": "趋势型交易区间",
    "pullback": "楔形回撤",
    "reversal": "楔形反转",
    "path_a": "路径A",
    "path_b": "路径B",
    "path_c": "路径C",
    "AIL": "Always In 多头",
    "AIS": "Always In 空头",
}


def strip_question_bar_basis_suffix(question: str) -> str:
    """Remove trailing ``（基于K…判断）`` from question text (AI or legacy UI)."""
    q = str(question or "").strip()
    while True:
        m = _QUESTION_BAR_BASIS_SUFFIX_RE.search(q)
        if not m:
            break
        q = q[: m.start()].strip()
    return q


def plain_trace_question(item: dict[str, Any]) -> str:
    """Question only — K-line basis belongs in bar_range / dedicated column."""
    q = str(item.get("question", item.get("node_id", ""))).strip()
    return strip_question_bar_basis_suffix(q)


def format_trace_answer(item: dict[str, Any]) -> str:
    """Display answer (+ branch label when present)."""
    if item.get("skipped") and str(item.get("answer", "")) == "不适用":
        return "不适用"
    ans = str(item.get("answer", "") or "").strip()
    branch = item.get("branch")
    if branch is not None and str(branch).strip():
        b = str(branch).strip()
        bzh = _BRANCH_DISPLAY_ZH.get(b, b)
        if bzh and bzh != ans:
            return f"{ans}（{bzh}）" if ans else bzh
    return ans


def format_bar_basis_suffix(item: dict[str, Any]) -> str:
    """Format UI suffix, e.g. ``（基于K50-K1判断）``."""
    if item.get("skipped") and item.get("answer") == "不适用":
        return ""
    br = normalize_bar_range(item)
    if not br:
        return ""
    if br in ("不适用", "—", "-", "N/A"):
        return ""
    if br.startswith("（"):
        return br if "判断" in br else f"{br}判断）"
    return f"（基于{br}判断）"


def question_with_bar_basis(item: dict[str, Any]) -> str:
    """Legacy: question + basis suffix. Prefer ``plain_trace_question`` in new UI."""
    q = plain_trace_question(item)
    suffix = format_bar_basis_suffix(item)
    if suffix and suffix not in q:
        return f"{q}{suffix}"
    return q


def validate_bar_range_field(item: dict[str, Any], path: str) -> list[str]:
    """Validate bar_range on one trace item."""
    errors: list[str] = []
    if item.get("skipped") and item.get("answer") == "不适用":
        return errors

    br = normalize_bar_range(item)
    if not br:
        errors.append(f"{path}: bar_range is required (e.g. K50-K1)")
        return errors

    if br in ("不适用", "—"):
        return errors

    if "填写" in br or br.startswith("<") or "由你" in br:
        errors.append(f"{path}: bar_range must be actual K-line seqs from the table, not a placeholder")
        return errors

    m = K_BAR_RANGE_RE.fullmatch(br)
    if m:
        first = int(m.group("first"))
        second_raw = m.group("second")
        if second_raw is not None and first < int(second_raw):
            errors.append(
                f"{path}: bar_range K{first}-K{int(second_raw)} invalid; "
                "older bar must have larger seq (e.g. K50-K1)"
            )
        return errors

    errors.append(f"{path}: bar_range must look like K50-K1 or K1 (seq1=newest bar)")
    return errors


def node_label(node_id: str, tree: dict[str, Any] | None = None) -> str:
    """Return human question text for a node id."""
    if tree is None:
        tree = load_decision_tree()
    entry = tree.get("node_index", {}).get(node_id)
    if entry:
        return str(entry.get("question", node_id))
    return node_id


def merge_traces(
    gate_trace: list[dict[str, Any]] | None,
    decision_trace: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Combine Stage1 gate_trace and Stage2 decision_trace in walk order."""
    merged: list[dict[str, Any]] = []
    for item in gate_trace or []:
        if isinstance(item, dict):
            merged.append({**item, "phase": "gate"})
    for item in decision_trace or []:
        if isinstance(item, dict):
            merged.append({**item, "phase": "decision"})
    return merged


def build_stage2_gate_wait_response(stage1_json: dict[str, Any]) -> dict[str, Any]:
    """Synthesize Stage2 JSON when Stage1 ``gate_result`` blocks trading."""
    gate_trace = stage1_json.get("gate_trace") or []
    last = gate_trace[-1] if gate_trace else {}
    node_id = str(last.get("node_id", "1.3"))
    label = str(last.get("reason") or last.get("action") or "闸门未通过，等待更清晰信号")

    key_signals = stage1_json.get("key_signals") or []

    return {
        "decision": {
            "order_direction": None,
            "order_type": "不下单",
            "entry_price": None,
            "entry_basis_bar": None,
            "entry_basis_extreme": None,
            "entry_rule": None,
            "take_profit_price": None,
            "take_profit_price_2": None,
            "stop_loss_price": None,
            "reasoning": (
                f"阶段一闸门结论为「{stage1_json.get('gate_result', 'wait')}」，"
                f"在节点 {node_id} 停止：{label}。"
                "未进入阶段二策略分支评估。"
            ),
            "diagnosis_confidence": stage1_json.get("diagnosis_confidence", 0),
            "diagnosis_confidence_reasoning": (
                stage1_json.get("risk_warning")
                or f"闸门在 {node_id}，市场诊断置信度沿用阶段一。"
            ),
            "trade_confidence": None,
            "trade_confidence_reasoning": "闸门未通过，不执行交易决策。",
            "estimated_win_rate": None,
            "estimated_win_rate_reasoning": None,
            "key_factors": list(key_signals)[:5],
            "watch_points": ["等待结构明朗或闸门节点转为可继续"],
            "risk_assessment": stage1_json.get("risk_warning") or "闸门等待",
            "invalidation_condition": None,
        },
        "bar_analysis": {
            "signal_bar": {
                "bar": None,
                "quality": "invalid",
                "pattern": "none",
                "reason": "阶段一闸门未通过，未评估信号棒。",
            },
            "entry_bar": {
                "bar": None,
                "strength": "not_triggered",
                "follow_through": None,
                "still_valid": None,
                "freshness": "invalid",
            },
            "second_entry": {
                "is_second_entry": False,
                "type": "none",
            },
        },
        "decision_trace": [],
        "terminal": {
            "node_id": node_id,
            "outcome": "wait",
            "label": label,
        },
        "gate_shortcircuited": True,
        "next_bar_prediction": {
            "direction": None,
            "probabilities": None,
            "reasoning": (
                f"阶段一闸门结论为「{stage1_json.get('gate_result', 'wait')}」（节点 {node_id}），"
                "未进入策略分支评估，亦不预测下一根 K 线方向。"
            ),
            "unpredictable": True,
            "features_used": ["stage1_diagnosis"],
        },
        "next_cycle_prediction": {
            "cycle": None,
            "direction": None,
            "probabilities": None,
            "reasoning": (
                f"阶段一闸门未通过（{node_id}），未进入阶段二周期演变评估。"
            ),
            "unpredictable": True,
            "features_used": ["stage1_diagnosis"],
        },
    }


def validate_gate_result_consistency(stage1: dict[str, Any]) -> list[str]:
    """Return list of consistency error messages (empty if ok)."""
    errors: list[str] = []
    gate_result = stage1.get("gate_result")
    if gate_result not in GATE_RESULTS:
        errors.append(f"gate_result must be one of {sorted(GATE_RESULTS)}")
        return errors

    trace = stage1.get("gate_trace")
    if not isinstance(trace, list) or not trace:
        errors.append("gate_trace must be a non-empty array")
        return errors

    seen_node_ids: set[str] = set()
    for i, item in enumerate(trace):
        if not isinstance(item, dict):
            errors.append(f"gate_trace[{i}] must be an object")
            continue
        nid = str(item.get("node_id", ""))
        if nid in seen_node_ids:
            errors.append(f"gate_trace node {nid} 不得重复")
        seen_node_ids.add(nid)
        if nid in STAGE1_FORBIDDEN_GATE_NODES:
            errors.append(
                f"gate_trace[{i}] node {nid}: 交易者方程不得在阶段一评估，"
                "请在阶段二节点 10.3（止损止盈确定后）评估"
            )
        elif nid not in STAGE1_GATE_NODE_IDS:
            errors.append(
                f"gate_trace[{i}].node_id {nid!r} 不属于阶段一模型闸门"
            )
        ans = item.get("answer")
        if ans not in TRACE_ANSWERS:
            errors.append(f"gate_trace[{i}].answer invalid: {ans!r}")
        errors.extend(validate_bar_range_field(item, f"gate_trace[{i}]"))

    if gate_result in ("wait", "unknown"):
        last = trace[-1]
        if isinstance(last, dict):
            node_id = str(last.get("node_id", "")).replace("§", "")
            answer = str(last.get("answer", "")).strip()
            valid_stop = (
                (node_id == "1.2" and answer in {"否", "等待"})
                or (node_id == "1.3" and answer == "是")
            )
            if not valid_stop:
                errors.append(
                    "gate_result wait/unknown 只能由 §1.2 无法识别（否/等待）"
                    "或 §1.3 极端混乱（是）结束"
                )

    # The model emits the trace in decision-tree order; no program nodes are injected.
    node_ids = [
        str(item.get("node_id", ""))
        for item in trace
        if isinstance(item, dict) and item.get("node_id")
    ]
    for idx in range(1, len(node_ids)):
        prev_key = _gate_trace_sort_key(node_ids[idx - 1])
        curr_key = _gate_trace_sort_key(node_ids[idx])
        if curr_key < prev_key:
            errors.append(
                f"gate_trace node ordering error: {node_ids[idx - 1]} "
                f"should come before {node_ids[idx]} "
                "(章节顺序错乱)"
            )

    return errors


def validate_stage2_trace_consistency(
    stage2: dict[str, Any],
    *,
    decision_tree_path: Path | None = None,
) -> list[str]:
    """Return list of consistency error messages (empty if ok)."""
    errors: list[str] = []
    if stage2.get("gate_shortcircuited"):
        return errors

    terminal = stage2.get("terminal")
    if not isinstance(terminal, dict):
        errors.append("terminal must be an object")
        return errors

    outcome = terminal.get("outcome")
    if outcome not in TERMINAL_OUTCOMES:
        errors.append(f"terminal.outcome invalid: {outcome!r}")

    trace = stage2.get("decision_trace")
    if not isinstance(trace, list) or not trace:
        errors.append("decision_trace must be a non-empty array when not gate-shortcircuited")
        return errors

    decision = stage2.get("decision", {})
    order_type = decision.get("order_type") if isinstance(decision, dict) else None
    executable_order = order_type in _EXECUTABLE_ORDER_TYPES
    disposition = stage2.get("execution_disposition") or {}
    program_price_reject = False
    if disposition.get("price_adjustment") and disposition.get("status") == "suppressed":
        from pa_agent.ai.execution_policy import effective_decision
        from pa_agent.util.trade_metrics import validate_order_trade_metrics
        program_price_reject = bool(
            outcome == "reject" and terminal.get("node_id") == "10.3"
            and validate_order_trade_metrics(effective_decision(stage2)))

    if order_type == "不下单" and outcome == "trade":
        errors.append('order_type 不下单 cannot pair with terminal.outcome "trade"')
    if executable_order and outcome in ("wait", "reject") and not program_price_reject:
        errors.append(
            f"order_type {order_type} cannot pair with terminal.outcome {outcome!r}"
        )
    if (
        executable_order
        and outcome == "trade"
        and str(terminal.get("node_id", "") or "").strip().startswith("14")
    ):
        errors.append(
            "terminal.node_id must not be §14 for a trade; §14 is only a prohibition scan, "
            "use the final §11 order node or 10.3"
        )

    allowed_node_ids = {
        *stage2_provider_node_ids(decision_tree_path),
        "10.3",
    }
    seen_node_ids: set[str] = set()
    for i, item in enumerate(trace):
        if isinstance(item, dict):
            errors.extend(validate_bar_range_field(item, f"decision_trace[{i}]"))
            node_id = str(item.get("node_id", "") or "").strip()
            if node_id == "0.3":
                errors.append(
                    "decision_trace must not include 0.3; use 10.3 after stop/target are set"
                )
            elif node_id not in allowed_node_ids:
                errors.append(
                    f"decision_trace[{i}].node_id {node_id!r} 不属于阶段二模型路径"
                )
            elif node_id in seen_node_ids:
                errors.append(f"decision_trace node {node_id} 不得重复")
            seen_node_ids.add(node_id)

    node_ids = _trace_node_ids(trace)
    terminal_node_id = str(terminal.get("node_id", "") or "").strip()
    if terminal_node_id not in node_ids:
        errors.append("terminal.node_id 必须对应 decision_trace 中已评估的节点")

    # §10 子节点顺序：10.1 → 10.2 → 10.3
    for a, b in (("10.1", "10.2"), ("10.2", "10.3")):
        ia, ib = _index_of(node_ids, a), _index_of(node_ids, b)
        if ia >= 0 and ib >= 0 and ia > ib:
            errors.append(f"decision_trace order: {a} must appear before {b}")

    idx_103 = _index_of(node_ids, "10.3")
    idx_11 = next((i for i, n in enumerate(node_ids) if n.startswith("11.")), -1)
    idx_14 = _index_of(node_ids, "14")

    if executable_order:
        if "9.0" not in node_ids:
            errors.append("order_type requires decision_trace node 9.0")
        for required in ("10.1", "10.2", "10.3"):
            if required not in node_ids:
                errors.append(f"order_type requires decision_trace node {required}")
        if idx_14 < 0:
            errors.append("order_type requires decision_trace node 14 prohibition scan")
        elif trace[idx_14].get("answer") != "否":
            errors.append("node 14 must be 否 when placing an order")
        if idx_103 < 0:
            errors.append(
                "order_type requires trade: decision_trace must include node 10.3 "
                "(交易者方程) with numeric stop/target assessment"
            )
        else:
            item_103 = trace[idx_103]
            if isinstance(item_103, dict) and item_103.get("answer") != ("否" if program_price_reject else "是"):
                errors.append("node 10.3 must be 是 when placing an order")
            if idx_11 >= 0 and idx_11 < idx_103:
                errors.append("§11 下单方式 nodes must appear after 10.3 交易者方程")
            # §9 须在 §10 之前
            idx_9 = next((i for i, n in enumerate(node_ids) if n.startswith("9.")), -1)
            idx_101 = _index_of(node_ids, "10.1")
            if idx_9 >= 0 and idx_101 >= 0 and idx_9 > idx_101:
                errors.append("§9 入场信号 must appear before §10.1 止损")

    idx_90 = _index_of(node_ids, "9.0")
    idx_90p = _index_of(node_ids, "9.0P")
    if idx_90 >= 0 and str(trace[idx_90].get("answer", "")).strip() in {"否", "等待"}:
        if idx_90p < 0:
            errors.append("§9.0=否/等待 时必须继续评估 §9.0P")
        elif executable_order:
            if trace[idx_90p].get("answer") != "是":
                errors.append("§9.0=否/等待 的可执行订单要求 §9.0P=是")
            if order_type != "限价单":
                errors.append("§9.0P 背景计划只能生成限价单")

    if terminal_node_id == "14":
        if idx_14 < 0 or trace[idx_14].get("answer") != "是":
            errors.append("terminal.node_id=14 仅允许用于已触犯 §14 禁止项")

    if order_type == "不下单" and outcome in ("wait", "reject"):
        term_nid = str(terminal.get("node_id", ""))
        if idx_103 >= 0 and trace[idx_103].get("answer") == "否" and term_nid != "10.3":
            errors.append(
                "when 10.3 answer is 否, terminal.node_id should be 10.3"
            )

        # Detect the "no entry plan → terminal=reject" anti-pattern.
        # When §9.0 answer is 否/等待 (no valid signal bar) or §10.1 answer is 否
        # (no stop-loss anchor), there is no trade plan to evaluate.
        # In that case outcome="reject" is semantically wrong (you can't reject a
        # plan that never existed) — outcome must be "wait".
        #
        # An explicit §14 answer=是 takes precedence because the prohibition
        # terminated the analysis. Free-text reason wording is never parsed.
        idx_90 = _index_of(node_ids, "9.0")
        idx_101 = _index_of(node_ids, "10.1")
        idx_90p = _index_of(node_ids, "9.0P")
        no_entry_plan = False
        if idx_90 >= 0:
            ans_90 = str(trace[idx_90].get("answer", "") or "").strip()
            if ans_90 in ("否", "等待"):
                if idx_90p >= 0:
                    ans_90p = str(trace[idx_90p].get("answer", "") or "").strip()
                    if ans_90p == "是":
                        no_entry_plan = False
                    else:
                        no_entry_plan = True
                else:
                    no_entry_plan = True
        if not no_entry_plan and idx_101 >= 0:
            ans_101 = str(trace[idx_101].get("answer", "") or "").strip()
            if ans_101 == "否":
                no_entry_plan = True
        if no_entry_plan and outcome == "reject":
            sec14_genuinely_violated = (
                idx_14 >= 0 and str(trace[idx_14].get("answer", "")).strip() == "是"
            )
            if not sec14_genuinely_violated:
                errors.append(
                    "terminal.outcome should be 'wait' (not 'reject') when there is no "
                    "entry plan to evaluate: §9.0=否/等待 or §10.1=否 means no trade "
                    "scheme existed, so nothing can be 'rejected' by the trader equation"
                )

    # 整体章节顺序：已评估节点的 rank 应非递减
    ranks = [_node_sort_key(n)[0] for n in node_ids if not n.startswith("0.")]
    for i in range(1, len(ranks)):
        if ranks[i] < ranks[i - 1]:
            errors.append(
                f"decision_trace chapter order violation near node {node_ids[i]}"
            )
            break

    return errors
