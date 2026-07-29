"""Deterministic representation normalization for Stage-2 model output.

The model owns every trading interpretation, probability distribution, and
order field. Program-derivable summaries and notation are canonicalized here;
invalid payloads and trading contradictions remain validator errors.
"""
from __future__ import annotations

import copy
from typing import Any

from pa_agent.ai.cycle_enums import CYCLE_ORDER
from pa_agent.ai.trace_normalize import normalize_stage2_traces
from pa_agent.ai.vpa_sanitizer import strip_vpa_fields


def normalize_stage2(
    obj: dict[str, Any],
    *,
    kline_frame: Any = None,
    skip_next_bar: bool = False,
) -> dict[str, Any]:
    """Return a copy with notation-only changes and feature-flag filtering."""
    out = strip_vpa_fields(copy.deepcopy(obj))
    normalize_stage2_traces(out, default_max_seq=_max_bar_seq(kline_frame))
    if not skip_next_bar:
        out["next_bar_prediction"] = _canonicalize_auxiliary_prediction(
            out.get("next_bar_prediction"),
            summary_field="direction",
            order=("bullish", "bearish", "neutral"),
        )
    out["next_cycle_prediction"] = _canonicalize_auxiliary_prediction(
        out.get("next_cycle_prediction"),
        summary_field="cycle",
        order=CYCLE_ORDER,
    )
    _canonicalize_state_dependent_nulls(out)
    if skip_next_bar:
        out.pop("next_bar_prediction", None)
    return out


def _canonicalize_auxiliary_prediction(
    prediction: Any,
    *,
    summary_field: str,
    order: tuple[str, ...],
) -> dict[str, Any]:
    """Canonicalize non-trading forecasts without failing the PA decision."""
    if not isinstance(prediction, dict) or prediction.get("unpredictable") is not False:
        return _unpredictable_prediction(prediction, summary_field=summary_field)
    probabilities = prediction.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != set(order):
        return _unpredictable_prediction(prediction, summary_field=summary_field)
    values = [probabilities[key] for key in order]
    if any(
        isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100
        for value in values
    ):
        return _unpredictable_prediction(prediction, summary_field=summary_field)
    if not 99 <= sum(values) <= 101:
        return _unpredictable_prediction(prediction, summary_field=summary_field)
    maximum = max(values)
    winners = [key for key in order if probabilities[key] == maximum]
    if prediction.get(summary_field) not in winners:
        prediction[summary_field] = winners[0]
    return prediction


def _unpredictable_prediction(
    prediction: Any,
    *,
    summary_field: str,
) -> dict[str, Any]:
    source = prediction if isinstance(prediction, dict) else {}
    reasoning = str(source.get("reasoning") or "").strip()
    if not reasoning:
        reasoning = "Auxiliary prediction unavailable from this model response."
    features = source.get("features_used")
    features = (
        [str(item) for item in features if str(item).strip()]
        if isinstance(features, list)
        else []
    )
    result: dict[str, Any] = {
        summary_field: None,
        "probabilities": None,
        "reasoning": reasoning,
        "unpredictable": True,
        "features_used": features,
    }
    if summary_field == "cycle":
        result["direction"] = None
    return result


def _canonicalize_state_dependent_nulls(stage2: dict[str, Any]) -> None:
    """Clear values whose absence follows uniquely from the declared state."""
    decision = stage2.get("decision")
    if isinstance(decision, dict) and decision.get("order_type") == "不下单":
        for key in (
            "trade_confidence",
            "estimated_win_rate",
            "estimated_win_rate_reasoning",
        ):
            if key in decision:
                decision[key] = None

    bar_analysis = stage2.get("bar_analysis")
    entry_bar = bar_analysis.get("entry_bar") if isinstance(bar_analysis, dict) else None
    if not isinstance(entry_bar, dict):
        return
    if entry_bar.get("bar") is None:
        entry_bar["bar"] = None
        entry_bar["strength"] = "not_triggered"
        entry_bar["follow_through"] = None
        entry_bar["still_valid"] = None


def _max_bar_seq(frame: Any) -> int | None:
    bars = getattr(frame, "bars", None) if frame is not None else None
    if not bars:
        return None
    seqs = [int(bar.seq) for bar in bars if getattr(bar, "seq", None)]
    return max(seqs) if seqs else None
