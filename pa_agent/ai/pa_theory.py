"""Canonical Brooks PA theory references used for model-output provenance.

The catalog names principles already explained by the project's PA knowledge
files. It does not classify a market or turn those principles into code gates.
"""
from __future__ import annotations

from typing import Final


PA_THEORY_RULES: Final[dict[str, str]] = {
    "BROOKS.CYCLE.SPIKE_TO_CHANNEL": (
        "A spike stops being an active one-way event after a clear pause or pullback; "
        "the market then transitions toward a channel or range hypothesis."
    ),
    "BROOKS.CYCLE.CHANNEL_TO_RANGE": (
        "Channels tend to broaden and develop two-sided trading before a trading-range "
        "interpretation becomes dominant."
    ),
    "BROOKS.CHANNEL.CONTEXT": (
        "Channel slope, overlap, pullback depth, boundaries and location jointly define "
        "context; a channel label alone is not a setup."
    ),
    "BROOKS.TREND.ALWAYS_IN": (
        "Always-In direction is a contextual reading of recent price strength and failed "
        "opposition, not a mechanical moving-average signal."
    ),
    "BROOKS.TREND.FOLLOW_THROUGH": (
        "A directional attempt gains credibility from later same-direction closes and loses "
        "credibility when follow-through is absent or fails."
    ),
    "BROOKS.TREND.PULLBACK_CONTINUATION": (
        "A pullback is a continuation candidate only in supportive trend context and after "
        "a valid signal/trigger; weakness alone does not create an entry."
    ),
    "BROOKS.BREAKOUT.REQUIRES_FOLLOW_THROUGH": (
        "A breakout bar is an attempt; acceptance, follow-through or a successful retest is "
        "needed before treating the break as established."
    ),
    "BROOKS.BREAKOUT.FAILURE": (
        "A failed breakout requires price to return through the boundary and trap or disappoint "
        "the breakout side; a wick alone is not enough."
    ),
    "BROOKS.RANGE.BREAKOUT_MODE": (
        "Inside a trading range, both breakout directions remain plausible until price earns "
        "acceptance outside a credible boundary."
    ),
    "BROOKS.RANGE.EDGE_OVER_MIDDLE": (
        "Range-edge setups generally offer clearer invalidation and trader-equation geometry "
        "than entries in the middle of congestion."
    ),
    "BROOKS.SIGNAL.SECOND_ENTRY": (
        "A second entry is a renewed attempt after the first attempt and pullback; it must be "
        "identified in sequence and context, not from one isolated bar."
    ),
    "BROOKS.SIGNAL.BAR_CONTEXT": (
        "Signal-bar quality depends on location, direction, surrounding bars and likely follow-"
        "through; candle shape alone is insufficient."
    ),
    "BROOKS.ENTRY.TRIGGER_BEYOND_SIGNAL": (
        "A stop-style PA trigger belongs beyond the signal-bar extreme, with executable tick "
        "size verified by the execution venue."
    ),
    "BROOKS.CLIMAX.NO_CHASE": (
        "Late consecutive expansion and poor follow-through raise climax risk; avoid treating "
        "an extended move as a fresh low-risk entry without a new setup."
    ),
    "BROOKS.MTR.BREAK_AND_TEST": (
        "A major trend reversal normally needs a meaningful trend-line/structure break and a "
        "later test or failed continuation, not one counter-trend bar."
    ),
    "BROOKS.TRADER_EQUATION": (
        "A trade requires coherent probability, reward and risk assumptions; setup quality does "
        "not bypass the trader equation."
    ),
    "BROOKS.RISK.STRUCTURAL_INVALIDATION": (
        "A protective stop belongs beyond the price action that invalidates the current setup. "
        "A signal-bar extreme is one possible anchor, not a universal stop; an unaffordable "
        "structural stop changes sizing, timing, or trade eligibility rather than being tightened "
        "to improve the equation."
    ),
    "BROOKS.PROBABILITY.CONTEXTUAL_PRIOR": (
        "Setup probabilities are context-dependent subjective priors until calibrated on the "
        "same instrument, timeframe, data source and execution protocol. Teaching frequencies "
        "rank hypotheses but must not be copied into the current trade's numeric win rate."
    ),
    "BROOKS.UNCERTAINTY.COMPETING_HYPOTHESES": (
        "When several cycle or direction interpretations remain plausible, preserve alternatives "
        "and reduce certainty instead of forcing one label."
    ),
    "BROOKS.HTF.CONTEXT_NOT_GATE": (
        "Higher-timeframe structure is background context for the current timeframe; it is not "
        "an independent direction or entry gate."
    ),
}

PA_THEORY_RULE_IDS: Final[frozenset[str]] = frozenset(PA_THEORY_RULES)


def render_pa_theory_rule_catalog() -> str:
    """Render stable theory references for citation, not mechanical routing."""
    lines = [
        "## PA_THEORY_RULE_CATALOG",
        "Use only IDs actually applied in this diagnosis. IDs cite theory; they do not prove facts.",
    ]
    lines.extend(f"- {rule_id}: {description}" for rule_id, description in PA_THEORY_RULES.items())
    return "\n".join(lines)

