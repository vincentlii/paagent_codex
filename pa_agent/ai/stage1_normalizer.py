"""Shape-only normalization for Stage-1 model output.

This module may canonicalize notation and attach reproducible incremental
metadata. It never infers or repairs market semantics; trace citation coverage
is deterministic audit metadata derived from explicit K references.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from pa_agent.ai.pattern_routing import normalize_detected_patterns
from pa_agent.ai.price_levels import price_level_midpoint
from pa_agent.ai.trace_normalize import normalize_stage1_traces
from pa_agent.ai.vpa_sanitizer import strip_vpa_fields

_INCREMENTAL_TRACKED_FIELDS = (
    "cycle_position",
    "alternative_cycle_position",
    "direction",
    "diagnosis_confidence",
    "spike_stage",
    "climax_risk",
    "market_phase",
    "transition_risk",
    "detected_patterns",
    "key_signals",
    "entry_setup",
    "support_levels",
    "resistance_levels",
    "gate_result",
)
_CONTEXT_EFFECT_ALIASES = {
    "weakened_bull": "weakens_bull",
    "weakened_bear": "weakens_bear",
}
def normalize_stage1(
    obj: dict[str, Any],
    *,
    kline_frame: Any = None,
    incremental_new_bar_count: int = 0,
    incremental_previous_stage1: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a copy with deterministic notation and audit-metadata changes."""
    out = strip_vpa_fields(copy.deepcopy(obj))

    if isinstance(out.get("detected_patterns"), list):
        out["detected_patterns"] = normalize_detected_patterns(
            out["detected_patterns"]
        )
    _normalize_key_signals(out)
    _normalize_closed_enum_aliases(out)
    _normalize_spike_analysis_alias(out)
    _normalize_structure_level_order(out)
    normalize_stage1_traces(out, default_max_seq=_max_bar_seq(kline_frame))
    _attach_incremental_facts(
        out,
        new_bar_count=incremental_new_bar_count,
        previous_stage1=incremental_previous_stage1,
    )
    return out


def _normalize_key_signals(out: dict[str, Any]) -> None:
    """Preserve structured summaries as canonical strings for the text-only field."""
    signals = out.get("key_signals")
    if not isinstance(signals, list):
        return
    normalized = [
        json.dumps(
            signal,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if isinstance(signal, dict)
        else signal
        for signal in signals
    ]
    if normalized != signals:
        out["key_signals"] = normalized


def _normalize_closed_enum_aliases(out: dict[str, Any]) -> None:
    """Canonicalize exact schema aliases without inferring market semantics."""
    summaries = out.get("bar_by_bar_summary")
    if isinstance(summaries, list):
        for item in summaries:
            if not isinstance(item, dict):
                continue
            value = item.get("context_effect")
            if isinstance(value, str) and value in _CONTEXT_EFFECT_ALIASES:
                item["context_effect"] = _CONTEXT_EFFECT_ALIASES[value]


def _normalize_spike_analysis_alias(out: dict[str, Any]) -> None:
    """Canonicalize one exact provider alias without changing PA semantics."""
    evidence = out.get("pa_evidence")
    spike = evidence.get("spike_analysis") if isinstance(evidence, dict) else None
    if not isinstance(spike, dict):
        return
    alias = "first_pullback_bar"
    canonical = "first_pause_or_pullback_bar"
    if alias in spike and canonical not in spike:
        spike[canonical] = spike.pop(alias)


def _normalize_structure_level_order(out: dict[str, Any]) -> None:
    """Canonicalize valid level order without changing membership or prices."""
    for field, reverse in (
        ("support_levels", True),
        ("resistance_levels", False),
    ):
        levels = out.get(field)
        if not isinstance(levels, list) or len(levels) < 2:
            continue
        midpoints = [price_level_midpoint(item) for item in levels]
        if any(value is None for value in midpoints):
            continue
        out[field] = [
            level
            for _midpoint, level in sorted(
                zip(midpoints, levels, strict=True),
                key=lambda item: item[0],
                reverse=reverse,
            )
        ]


def _attach_incremental_facts(
    out: dict[str, Any],
    *,
    new_bar_count: int,
    previous_stage1: dict[str, Any] | None,
) -> None:
    if new_bar_count <= 0:
        return
    delta = out.get("incremental_delta")
    if not isinstance(delta, dict):
        delta = {}
        out["incremental_delta"] = delta
    delta["new_closed_bars"] = [f"K{seq}" for seq in range(1, new_bar_count + 1)]
    if isinstance(previous_stage1, dict):
        delta["changed_fields"] = [
            key
            for key in _INCREMENTAL_TRACKED_FIELDS
            if out.get(key) != previous_stage1.get(key)
        ]


def _max_bar_seq(frame: Any) -> int | None:
    bars = getattr(frame, "bars", None) if frame is not None else None
    if not bars:
        return None
    seqs = [int(bar.seq) for bar in bars if getattr(bar, "seq", None)]
    return max(seqs) if seqs else None
