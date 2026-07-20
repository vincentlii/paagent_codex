"""Notation-only normalization for gate and decision traces."""
from __future__ import annotations

from typing import Any

from pa_agent.ai.kline_references import K_REFERENCE_RE, parse_k_bar_range_bounds


def infer_max_bar_seq_from_trace(trace: list[Any]) -> int | None:
    maximum = 0
    for item in trace:
        if not isinstance(item, dict):
            continue
        bounds = parse_k_bar_range_bounds(item.get("bar_range"))
        if bounds is not None:
            maximum = max(maximum, bounds[1])
    return maximum or None


def fix_bar_range_string(text: str, *, default_max_seq: int | None = None) -> str:
    """Canonicalize notation without changing the referenced set of bars."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if raw.lower() in {"global", "all"} or raw in {"全局", "全图", "全部"}:
        return f"K{default_max_seq}-K1" if default_max_seq else raw
    bounds = parse_k_bar_range_bounds(raw)
    if bounds is not None:
        return _format_range(bounds[1], bounds[0])
    return raw


def normalize_trace_item(
    item: dict[str, Any],
    *,
    default_max_seq: int | None = None,
) -> None:
    """Normalize trace notation and factual citation coverage, not conclusions."""
    if isinstance(item.get("node_id"), str):
        item["node_id"] = item["node_id"].strip().lstrip("§")
    if isinstance(item.get("bar_range"), str):
        item["bar_range"] = fix_bar_range_string(
            item["bar_range"], default_max_seq=default_max_seq
        )
        _expand_bar_range_to_valid_reason_references(
            item,
            max_seq=default_max_seq,
        )


def normalize_trace_list(
    trace: list[Any] | None,
    *,
    default_max_seq: int | None = None,
) -> list[Any] | None:
    if not isinstance(trace, list):
        return trace
    for item in trace:
        if isinstance(item, dict):
            normalize_trace_item(item, default_max_seq=default_max_seq)
    return trace


def normalize_stage1_traces(
    obj: dict[str, Any],
    *,
    default_max_seq: int | None = None,
) -> None:
    normalize_trace_list(obj.get("gate_trace"), default_max_seq=default_max_seq)


def normalize_stage2_traces(
    obj: dict[str, Any],
    *,
    default_max_seq: int | None = None,
) -> None:
    normalize_trace_list(obj.get("decision_trace"), default_max_seq=default_max_seq)


def _format_range(older: int, newer: int) -> str:
    return f"K{older}" if older == newer else f"K{older}-K{newer}"


def _expand_bar_range_to_valid_reason_references(
    item: dict[str, Any],
    *,
    max_seq: int | None,
) -> None:
    """Make trace coverage include every valid explicit K reference in its reason.

    ``bar_range`` is audit metadata, not a market conclusion. Expanding its
    envelope is deterministic and prevents a two-window explanation from
    failing solely because its declared range captured only one window. A
    missing frame or an out-of-frame citation remains untouched for the
    validator to reject.
    """
    if max_seq is None:
        return
    declared = _range_seqs(str(item.get("bar_range") or ""))
    cited = {
        int(match.group(1))
        for match in K_REFERENCE_RE.finditer(str(item.get("reason") or ""))
    }
    if not declared or not cited or any(seq < 1 or seq > max_seq for seq in cited):
        return
    covered = declared | cited
    item["bar_range"] = _format_range(max(covered), min(covered))


def _range_seqs(value: str) -> set[int]:
    bounds = parse_k_bar_range_bounds(value)
    if bounds is None:
        return set()
    return set(range(bounds[0], bounds[1] + 1))
