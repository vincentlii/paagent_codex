"""Validation retry policy: which errors may retry and immutable field guards."""
from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any, Literal

StageName = Literal["stage1", "stage2"]

# Format-like invalid field prefixes — safe to retry without changing trade thesis.
_FORMAT_PREFIXES: tuple[str, ...] = (
    "gate_trace",
    "decision_trace",
    "bar_by_bar_summary",
    "bar_range",
    "key_signals",
    "incremental",
    "next_bar_prediction",
    "next_cycle_prediction",
    "decision.reasoning",
)

# Semantic errors that must not be hidden by a formatting retry.
_NO_RETRY_PREFIXES: tuple[str, ...] = (
    "trace:§14",
    "s2:order_direction",
)

_SEMANTIC_STATE_FIELDS: frozenset[str] = frozenset(
    {
        "spike_stage",
        "market_phase",
        "transition_risk",
    }
)


IMMUTABLE_FIELDS: dict[StageName, tuple[str, ...]] = {
    "stage1": (
        "market_hypotheses.primary.direction",
        "market_hypotheses.primary.cycle_position",
        "market_hypotheses.primary.setup_hint",
        "market_hypotheses.primary.thesis",
        "direction",
        "cycle_position",
        "gate_result",
        "pa_evidence.fact_refs",
        "pa_evidence.theory_rule_ids",
        "pa_evidence.spike_analysis",
    ),
    "stage2": (
        "decision_intent",
        "decision.order_type",
        "decision.order_direction",
        "decision.entry_price",
        "decision.stop_loss_price",
        "decision.take_profit_price",
        "decision.take_profit_price_2",
    ),
}


def max_retries_for_category(category: str, settings: Any) -> int:
    """Return allowed retry count for a validation category."""
    if category == "e":
        return 0
    if not getattr(settings, "retry_enabled", True):
        return 0
    base = int(getattr(settings, "retry_max", 3) or 0)
    if category in ("a", "b", "d"):
        return base
    if category == "c":
        return min(base, int(getattr(settings, "retry_max_semantic", 1) or 1))
    return 0


def should_retry(
    category: str,
    invalid_fields: list[str],
    missing_fields: list[str],
    *,
    attempt: int,
    settings: Any,
    error_code: str = "",
) -> bool:
    """Whether another API call is warranted."""
    if error_code == "provider_draft_contract":
        return (
            bool(invalid_fields or missing_fields)
            and bool(getattr(settings, "retry_enabled", True))
            and attempt < 1
        )
    if category == "e":
        return False
    if attempt >= max_retries_for_category(category, settings):
        return False
    if category in ("a", "b", "d"):
        return True
    if category != "c":
        return False
    fields = list(invalid_fields or []) + list(missing_fields or [])
    if not fields:
        return False
    if any(_starts_any(f, _NO_RETRY_PREFIXES) for f in fields):
        return False
    if any(_starts_any(f, _FORMAT_PREFIXES) for f in fields):
        return True
    if any(str(field) in _SEMANTIC_STATE_FIELDS for field in fields):
        return attempt < int(getattr(settings, "retry_max_semantic", 1) or 1)
    if any(
        f.startswith(
            (
                "s1:",
                "s2:",
                "gate:",
                "trace:",
                "breakout_price:",
                "signal_chain:",
                "metrics:",
            )
        )
        for f in fields
    ):
        # Default: one semantic retry if enabled
        return attempt < int(getattr(settings, "retry_max_semantic", 1) or 1)
    return False


def contract_editable_pointers(issues: list[dict[str, Any]]) -> set[str]:
    """Return the exact JSON Pointer permissions granted by contract issues."""
    return {
        str(issue.get("pointer", ""))
        for issue in issues
        if isinstance(issue, dict) and "pointer" in issue
    }


def detect_unallowed_draft_changes(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    editable_pointers: set[str],
) -> list[str]:
    """Reject every repair diff not covered by a reported contract issue."""
    changes = _json_differences(before, after)
    violations: list[str] = []
    for pointer, old, new in changes:
        if _authorized_array_removal(old, new, pointer, editable_pointers) is not None:
            continue
        if any(
            pointer == allowed
            or (allowed and pointer.startswith(allowed + "/"))
            for allowed in editable_pointers
        ):
            continue
        path = _pointer_to_path(pointer)
        violations.append(f"{path}: {old!r} -> {new!r}")
    return violations


def project_allowed_draft_changes(
    before: dict[str, Any],
    candidate: dict[str, Any],
    *,
    editable_pointers: set[str],
) -> dict[str, Any]:
    """Apply only explicitly authorized Provider Draft edits to the prior draft."""
    if "" in editable_pointers:
        return copy.deepcopy(candidate)
    projected = copy.deepcopy(before)
    projected_arrays: set[str] = set()
    for pointer in sorted(editable_pointers, key=lambda value: value.count("/")):
        if any(pointer.startswith(parent + "/") for parent in projected_arrays):
            continue
        parts = _pointer_parts(pointer)
        if parts and parts[-1].isdigit():
            parent = "/" + "/".join(_pointer_escape(part) for part in parts[:-1])
            _, old_array = _pointer_value(before, parent)
            _, new_array = _pointer_value(candidate, parent)
            if isinstance(old_array, list) and isinstance(new_array, list) and len(new_array) < len(old_array):
                if parent not in projected_arrays and _authorized_array_removal(
                    old_array, new_array, parent, editable_pointers,
                ) is not None:
                    _assign_pointer(projected, parent, copy.deepcopy(new_array))
                    projected_arrays.add(parent)
                # Never reinterpret a shortened array's shifted index as replacement.
                continue
        candidate_pointer = _semantic_candidate_pointer(
            before,
            candidate,
            pointer,
        )
        if candidate_pointer is None:
            continue
        exists, value = _pointer_value(candidate, candidate_pointer)
        if exists:
            _assign_pointer(projected, pointer, copy.deepcopy(value))
        else:
            _remove_pointer(projected, pointer)
    return projected


def _authorized_array_removal(
    before: Any, after: Any, pointer: str, editable_pointers: set[str],
) -> tuple[int, ...] | None:
    """Match authorized original deletions/edits; all other survivors stay unchanged."""
    if not isinstance(before, list) or not isinstance(after, list) or len(after) >= len(before):
        return None
    allowed = {index for index in range(len(before)) if f"{pointer}/{index}" in editable_pointers}

    @lru_cache(None)
    def match(old_index: int, new_index: int) -> tuple[int, ...] | None:
        if old_index == len(before):
            return () if new_index == len(after) else None
        survivor_changes = (
            _json_differences(before[old_index], after[new_index], f"{pointer}/{old_index}")
            if new_index < len(after) else None
        )
        if survivor_changes is not None and all(
            any(path == grant or path.startswith(grant + "/") for grant in editable_pointers)
            for path, _, _ in survivor_changes
        ):
            rest = match(old_index + 1, new_index + 1)
            if rest is not None:
                return rest
        if old_index in allowed:
            rest = match(old_index + 1, new_index)
            if rest is not None:
                return (old_index, *rest)
        return None

    return match(0, 0)


def _semantic_candidate_pointer(
    before: dict[str, Any],
    candidate: dict[str, Any],
    pointer: str,
) -> str | None:
    """Resolve trace edits by node identity when a repair reorders the array."""
    parts = _pointer_parts(pointer)
    if len(parts) < 3 or parts[0] not in {"gate_trace", "decision_trace"}:
        return pointer
    if not parts[1].isdigit():
        return pointer
    before_trace = before.get(parts[0])
    candidate_trace = candidate.get(parts[0])
    before_index = int(parts[1])
    if (
        not isinstance(before_trace, list)
        or not isinstance(candidate_trace, list)
        or not 0 <= before_index < len(before_trace)
        or not isinstance(before_trace[before_index], dict)
    ):
        return pointer
    node_id = str(before_trace[before_index].get("node_id") or "")
    matches = [
        index
        for index, item in enumerate(candidate_trace)
        if isinstance(item, dict) and str(item.get("node_id") or "") == node_id
    ]
    if not node_id or len(matches) != 1:
        return None
    parts[1] = str(matches[0])
    return "/" + "/".join(_pointer_escape(part) for part in parts)


def _pointer_parts(pointer: str) -> list[str]:
    if not pointer:
        return []
    return [
        part.replace("~1", "/").replace("~0", "~")
        for part in pointer.lstrip("/").split("/")
    ]


def _pointer_value(obj: Any, pointer: str) -> tuple[bool, Any]:
    value = obj
    for part in _pointer_parts(pointer):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return False, None
    return True, value


def _assign_pointer(obj: Any, pointer: str, value: Any) -> None:
    parts = _pointer_parts(pointer)
    if not parts:
        return
    parent = obj
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    leaf = parts[-1]
    if isinstance(parent, list):
        index = int(leaf)
        if index == len(parent):
            parent.append(value)
        else:
            parent[index] = value
    else:
        parent[leaf] = value


def _remove_pointer(obj: Any, pointer: str) -> None:
    parts = _pointer_parts(pointer)
    if not parts:
        return
    parent = obj
    for part in parts[:-1]:
        if isinstance(parent, list) and part.isdigit() and int(part) < len(parent):
            parent = parent[int(part)]
        elif isinstance(parent, dict) and part in parent:
            parent = parent[part]
        else:
            return
    leaf = parts[-1]
    if isinstance(parent, list) and leaf.isdigit() and int(leaf) < len(parent):
        parent.pop(int(leaf))
    elif isinstance(parent, dict):
        parent.pop(leaf, None)


def _json_differences(
    before: Any,
    after: Any,
    pointer: str = "",
) -> list[tuple[str, Any, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[tuple[str, Any, Any]] = []
        for key in sorted(set(before) | set(after)):
            child = pointer + "/" + _pointer_escape(key)
            if key not in before:
                changes.append((child, "<missing>", after[key]))
            elif key not in after:
                changes.append((child, before[key], "<missing>"))
            else:
                changes.extend(_json_differences(before[key], after[key], child))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return [(pointer, before, after)]
        changes = []
        for index, (old, new) in enumerate(zip(before, after, strict=True)):
            changes.extend(_json_differences(old, new, f"{pointer}/{index}"))
        return changes
    if type(before) is not type(after) or before != after:
        return [(pointer, before, after)]
    return []


def _pointer_escape(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _pointer_to_path(pointer: str) -> str:
    if not pointer:
        return "draft"
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    out = ""
    for part in parts:
        if part.isdigit():
            out += f"[{part}]"
        else:
            out += ("." if out else "") + part
    return out


def _starts_any(field: str, prefixes: tuple[str, ...]) -> bool:
    text = str(field or "")
    return any(text.startswith(p) or p in text for p in prefixes)


def _get_path(obj: dict[str, Any], path: str) -> Any:
    cur: Any = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def detect_immutable_field_changes(
    stage: StageName,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    *,
    feedback_mentioned: set[str] | None = None,
) -> list[str]:
    """Report retry changes to decision fields unrelated to the validation feedback."""
    if not before or not isinstance(after, dict):
        return []
    mentioned = feedback_mentioned or set()
    violations: list[str] = []
    for key in IMMUTABLE_FIELDS.get(stage, ()):
        if key in mentioned:
            continue
        b = _get_path(before, key)
        a = _get_path(after, key)
        if b is not None and a is not None and str(b) != str(a):
            violations.append(f"{key}: {b!r} → {a!r}")

    return violations


def extract_feedback_targets(invalid_fields: list[str], missing_fields: list[str]) -> set[str]:
    """Map error lines to field paths mentioned in retry feedback."""
    targets: set[str] = set()
    for raw in list(missing_fields or []) + list(invalid_fields or []):
        text = str(raw)
        if "order_type" in text:
            targets.add("decision.order_type")
        if (
            "unbounded new-high/new-low" in text
            or "unbounded negated new-high/new-low" in text
            or "must cite PA.WINDOW instead of restating" in text
        ):
            targets.add("pa_evidence.fact_refs")
        for key in (
            "direction",
            "cycle_position",
            "gate_result",
            "pa_evidence.fact_refs",
            "pa_evidence.theory_rule_ids",
            "pa_evidence.spike_analysis",
            "decision.order_type",
            "decision.order_direction",
            "decision.entry_price",
            "decision.stop_loss_price",
            "decision.take_profit_price",
            "decision.take_profit_price_2",
            "next_bar_prediction",
            "next_cycle_prediction",
        ):
            if key in text:
                targets.add(key)
    return targets
