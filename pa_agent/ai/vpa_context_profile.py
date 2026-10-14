"""Typed prompt-budget profile derived only from frozen PA semantics.

This module selects reference knowledge.  It does not classify Wyckoff events,
route a strategy, alter PA fields, or decide a trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pa_agent.ai.pattern_routing import normalize_detected_patterns

_TREND_CYCLES = frozenset(
    {
        "spike",
        "micro_channel",
        "tight_channel",
        "normal_channel",
        "broad_channel",
        "trending_tr",
    }
)
_RANGE_CYCLES = frozenset({"trading_range", "trending_tr", "extreme_tr"})
_TREND_PATTERN_KEYS = frozenset(
    {"h1", "h2", "l1", "l2", "always_in", "ail", "ais", "20gb", "gap_bar"}
)
_BREAKOUT_PATTERN_KEYS = frozenset(
    {"breakout_test", "breakout_pullback", "breakout_failure", "failed_breakout"}
)


@dataclass(frozen=True)
class VpaKnowledgeContext:
    cycles: frozenset[str]
    detected_patterns: frozenset[str]
    module_names: tuple[str, ...]


def build_vpa_knowledge_context(
    stage1: dict[str, Any] | None,
    *,
    include_core: bool = True,
) -> VpaKnowledgeContext:
    """Select modules from explicit PA fields without inferring missing labels."""
    modules = ["core"] if include_core else []
    if not isinstance(stage1, dict):
        return VpaKnowledgeContext(frozenset(), frozenset(), tuple(modules))

    hypotheses = stage1.get("market_hypotheses")
    alternatives = hypotheses.get("alternatives") if isinstance(hypotheses, dict) else None
    cycle_values = [stage1.get("cycle_position")]
    if isinstance(alternatives, list):
        cycle_values.extend(item.get("cycle_position") for item in alternatives if isinstance(item, dict))
    else:
        cycle_values.append(stage1.get("alternative_cycle_position"))
    cycles = frozenset(str(value).strip().lower() for value in cycle_values if value)
    patterns = frozenset(normalize_detected_patterns(stage1.get("detected_patterns")))

    if cycles & _TREND_CYCLES or patterns & _TREND_PATTERN_KEYS:
        modules.append("trend_pullback")
    if patterns & _BREAKOUT_PATTERN_KEYS or cycles & (_RANGE_CYCLES | {"spike", "broad_channel"}):
        modules.append("breakout_retest")
    if cycles & (_RANGE_CYCLES | {"broad_channel"}) or patterns & {
        "ascending_triangle", "descending_triangle", "symmetrical_triangle",
        "expanding_triangle", "double_top_bottom",
    }:
        modules.append("trading_range_events")
    if str(stage1.get("climax_risk") or "").lower() in {"warning", "triggered"} or str(
        stage1.get("spike_stage") or ""
    ).lower() in {"ending", "transitioning"} or patterns & {
        "wedge", "final_flag", "mtr", "reversal_attempt", "shrinking_stairs",
    }:
        modules.append("climax_absorption_exhaustion")

    return VpaKnowledgeContext(cycles, patterns, tuple(dict.fromkeys(modules)))
