"""Compile validated PA outputs into the frozen VPA/Joint handoff.

This module only copies model-owned PA semantics and derives reproducible
order math, identity, and numeric window positions. It never names a setup,
classifies market location, or changes the PA decision.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from pa_agent.ai.fact_catalog import AnalysisFactCatalog, build_analysis_fact_catalog
from pa_agent.ai.role_contracts import (
    PaHypothesis,
    PaHypothesisSet,
    PaSetupCandidate,
    PaTradeMath,
    PaWindowContext,
)
from pa_agent.data.base import KlineFrame
from pa_agent.util.trade_metrics import compute_risk_reward, passes_trader_equation

_EXECUTABLE_ORDER_TYPES = frozenset({"限价单", "突破单", "市价单"})
_ORDER_DIRECTIONS = frozenset({"做多", "做空"})
_EXECUTION_MODE_BY_ORDER_TYPE = {
    "限价单": "passive_limit",
    "突破单": "price_triggered_stop",
    "市价单": "immediate_market",
}


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fraction_from_low(value: float, low: float, high: float) -> float:
    width = high - low
    if width <= 0:
        return 0.0
    return round((value - low) / width, 8)


def _window_context(
    *,
    fact_refs: list[str],
    catalog: AnalysisFactCatalog,
    decision: dict[str, Any],
) -> list[PaWindowContext]:
    try:
        current_close = float(catalog.fact("PA.BAR.K1").value["close"])
    except (KeyError, TypeError, ValueError):
        return []
    prices: dict[str, float | None] = {
        "entry": float(decision["entry_price"]),
        "stop": float(decision["stop_loss_price"]),
        "tp1": float(decision["take_profit_price"]),
        "tp2": _finite(decision.get("take_profit_price_2")),
    }
    result: list[PaWindowContext] = []
    seen: set[str] = set()
    for fact_id in fact_refs:
        if fact_id in seen or not fact_id.startswith("PA.WINDOW.K"):
            continue
        seen.add(fact_id)
        try:
            fact = catalog.fact(fact_id)
        except KeyError:
            continue
        if fact.fact_type != "rolling_price_window" or not isinstance(
            fact.value,
            dict,
        ):
            continue
        high = _finite(fact.value.get("high"))
        low = _finite(fact.value.get("low"))
        if high is None or low is None or high <= low:
            continue
        result.append(
            PaWindowContext(
                fact_id=fact.fact_id,
                bar_range=fact.bar_range,
                high=high,
                high_bars=[str(item) for item in fact.value.get("high_bars", [])],
                low=low,
                low_bars=[str(item) for item in fact.value.get("low_bars", [])],
                current_close_fraction_from_low=_fraction_from_low(
                    current_close,
                    low,
                    high,
                ),
                entry_fraction_from_low=_fraction_from_low(
                    prices["entry"],
                    low,
                    high,
                ),
                stop_fraction_from_low=_fraction_from_low(
                    prices["stop"],
                    low,
                    high,
                ),
                tp1_fraction_from_low=_fraction_from_low(
                    prices["tp1"],
                    low,
                    high,
                ),
                tp2_fraction_from_low=(
                    _fraction_from_low(prices["tp2"], low, high)
                    if prices["tp2"] is not None
                    else None
                ),
            )
        )
    return result


def _trade_math(decision: dict[str, Any], frame: KlineFrame) -> PaTradeMath:
    rr = compute_risk_reward(
        decision.get("entry_price"),
        decision.get("take_profit_price"),
        decision.get("stop_loss_price"),
        decision.get("order_direction"),
    )
    win_rate_raw = decision.get("estimated_win_rate")
    if rr is None or isinstance(win_rate_raw, bool) or not isinstance(
        win_rate_raw,
        int,
    ):
        raise ValueError(
            "validated executable PA plan has incomplete trade mathematics"
        )
    risk = float(rr["risk"])
    reward = float(rr["reward"])
    entry = float(decision["entry_price"])
    tp1 = float(decision["take_profit_price"])
    tp2 = _finite(decision.get("take_profit_price_2"))
    reward_tp2 = (
        tp2 - entry if tp1 > entry else entry - tp2
    ) if tp2 is not None else None
    atr = _finite(frame.indicators.atr14[0]) if frame.indicators.atr14 else None
    current_close = _finite(frame.bars[0].close) if frame.bars else None
    win_rate = int(win_rate_raw)
    probability = win_rate / 100.0
    return PaTradeMath(
        risk=risk,
        reward_to_tp1=reward,
        reward_risk_ratio=float(rr["ratio"]),
        reward_to_tp2=reward_tp2,
        reward_risk_ratio_tp2=(reward_tp2 / risk if reward_tp2 is not None else None),
        risk_atr=risk / atr if atr is not None and atr > 0 else None,
        entry_distance_from_current_close_atr=(
            abs(entry - current_close) / atr
            if current_close is not None and atr is not None and atr > 0
            else None
        ),
        risk_pct_of_entry=(100.0 * risk / abs(entry) if entry != 0 else None),
        break_even_win_rate_pct=100.0 * risk / (risk + reward),
        pa_estimated_win_rate_pct=win_rate,
        estimated_expectancy=probability * reward - (1.0 - probability) * risk,
        equation_passes=passes_trader_equation(win_rate, risk, reward),
        win_rate_provenance="llm_subjective_prior",
        calibration_id=None,
    )


def _candidate(
    *,
    decision: dict[str, Any],
    stage2: dict[str, Any],
    setup: str | None,
    fact_refs: list[str],
    theory_ids: list[str],
    catalog: AnalysisFactCatalog,
    frame: KlineFrame,
) -> PaSetupCandidate | None:
    order_type = str(decision.get("order_type") or "")
    if order_type not in _EXECUTABLE_ORDER_TYPES:
        return None
    order_direction = str(decision.get("order_direction") or "")
    numeric_fields = (
        "entry_price",
        "stop_loss_price",
        "take_profit_price",
    )
    if order_direction not in _ORDER_DIRECTIONS or any(
        _finite(decision.get(field)) is None for field in numeric_fields
    ):
        raise ValueError(
            "validated executable PA plan is missing structured order fields"
        )
    trigger = str(decision.get("entry_rule") or "").strip() or None
    basis_bar = str(decision.get("entry_basis_bar") or "").strip() or None
    basis_extreme = decision.get("entry_basis_extreme")
    if order_type == "突破单" and (
        trigger is None
        or basis_bar is None
        or basis_extreme not in {"high", "low"}
    ):
        raise ValueError(
            "validated executable PA plan is missing breakout basis fields"
        )
    if basis_extreme not in {"high", "low"}:
        basis_extreme = None
    invalidation = (
        str(decision.get("invalidation_condition") or "").strip() or None
    )
    bar_analysis = stage2.get("bar_analysis")
    entry_bar = (
        bar_analysis.get("entry_bar")
        if isinstance(bar_analysis, dict)
        and isinstance(bar_analysis.get("entry_bar"), dict)
        else {}
    )
    timing_state = (
        "waiting"
        if entry_bar.get("bar") is None
        or str(entry_bar.get("freshness") or "") == "pending"
        else "eligible"
    )
    candidate_direction = "bullish" if order_direction == "做多" else "bearish"
    return PaSetupCandidate(
        candidate_id="pa-setup-1",
        hypothesis_id="pa-primary",
        direction=candidate_direction,
        setup=setup,
        timing_state=timing_state,
        execution_mode=_EXECUTION_MODE_BY_ORDER_TYPE[order_type],
        order_type=order_type,
        order_direction=order_direction,
        entry_price=float(decision["entry_price"]),
        entry_basis_bar=basis_bar,
        entry_basis_extreme=basis_extreme,
        trigger=trigger,
        stop_loss_price=float(decision["stop_loss_price"]),
        take_profit_price=float(decision["take_profit_price"]),
        take_profit_price_2=_finite(decision.get("take_profit_price_2")),
        invalidation=invalidation,
        trade_math=_trade_math(decision, frame),
        window_context=_window_context(
            fact_refs=fact_refs,
            catalog=catalog,
            decision=decision,
        ),
        fact_refs=fact_refs,
        theory_rule_ids=theory_ids,
    )


def compile_pa_handoff(
    stage1: dict[str, Any],
    stage2: dict[str, Any] | None,
    frame: KlineFrame,
    *,
    catalog: AnalysisFactCatalog | None = None,
) -> PaHypothesisSet:
    """Compile validated PA outputs without inventing missing PA semantics."""
    evidence = stage1.get("pa_evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    fact_refs = [str(item) for item in evidence.get("fact_refs", [])]
    theory_ids = [str(item) for item in evidence.get("theory_rule_ids", [])]
    cycle = str(stage1.get("cycle_position") or "unknown")
    direction = str(stage1.get("direction") or "neutral")
    if direction not in {"bullish", "bearish", "neutral"}:
        direction = "neutral"
    gate = str(stage1.get("gate_result") or "unknown")
    if gate not in {"proceed", "wait", "unknown"}:
        gate = "unknown"
    setup = str(stage1.get("entry_setup") or "").strip() or None
    hypotheses = stage1.get("market_hypotheses")
    hypotheses = hypotheses if isinstance(hypotheses, dict) else {}
    primary_source = hypotheses.get("primary")
    primary_source = primary_source if isinstance(primary_source, dict) else {}
    primary_thesis = str(primary_source.get("thesis") or f"{cycle} / {direction}. Gate={gate}.")
    primary = PaHypothesis(
        hypothesis_id="pa-primary",
        role="primary",
        cycle_position=cycle,
        direction=direction,
        setup=setup,
        thesis=primary_thesis,
        fact_refs=[str(item) for item in primary_source.get("supporting_fact_refs", fact_refs)],
        challenging_fact_refs=[
            str(item) for item in primary_source.get("challenging_fact_refs", [])
        ],
        theory_rule_ids=[str(item) for item in primary_source.get("theory_rule_ids", theory_ids)],
        confirmation_condition=str(primary_source.get("confirmation_condition") or "") or None,
        disconfirmation_condition=str(primary_source.get("disconfirmation_condition") or "") or None,
    )
    alternative_sources = hypotheses.get("alternatives")
    alternative_sources = alternative_sources if isinstance(alternative_sources, list) else []
    compiled_alternatives: list[PaHypothesis] = []
    for index, source in enumerate(alternative_sources, start=1):
        if not isinstance(source, dict):
            continue
        alternative_cycle = str(source.get("cycle_position") or "unknown")
        alternative_direction = str(source.get("direction") or "neutral")
        if alternative_direction not in {"bullish", "bearish", "neutral"}:
            alternative_direction = "neutral"
        compiled_alternatives.append(
            PaHypothesis(
                hypothesis_id=str(source.get("hypothesis_id") or f"pa-alternative-{index}"),
                role="alternative",
                cycle_position=alternative_cycle,
                direction=alternative_direction,
                setup=str(source.get("setup_hint") or "").strip() or None,
                thesis=str(source.get("thesis") or ""),
                fact_refs=[str(item) for item in source.get("supporting_fact_refs", [])],
                challenging_fact_refs=[
                    str(item) for item in source.get("challenging_fact_refs", [])
                ],
                theory_rule_ids=[str(item) for item in source.get("theory_rule_ids", [])],
                confirmation_condition=str(source.get("confirmation_condition") or "") or None,
                disconfirmation_condition=str(source.get("disconfirmation_condition") or "") or None,
            )
        )
    if not compiled_alternatives:
        legacy_cycle = stage1.get("alternative_cycle_position")
        if legacy_cycle and str(legacy_cycle) != cycle:
            compiled_alternatives.append(
                PaHypothesis(
                    hypothesis_id="pa-alternative",
                    role="alternative",
                    cycle_position=str(legacy_cycle),
                    direction=direction,
                    setup=setup,
                    thesis=f"Competing PA framework: {legacy_cycle}.",
                    fact_refs=fact_refs,
                    theory_rule_ids=theory_ids,
                )
            )
    alternative = compiled_alternatives[0] if compiled_alternatives else None
    stage2_obj = stage2 if isinstance(stage2, dict) else {}
    decision = (
        stage2_obj.get("decision")
        if isinstance(stage2_obj.get("decision"), dict)
        else {}
    )
    catalog = catalog or build_analysis_fact_catalog(frame)
    candidate = (
        _candidate(
            decision=decision,
            stage2=stage2_obj,
            setup=setup,
            fact_refs=fact_refs,
            theory_ids=theory_ids,
            catalog=catalog,
            frame=frame,
        )
        if gate == "proceed"
        else None
    )
    candidates = [candidate] if candidate is not None else []
    source_payload = {
        "primary": primary.model_dump(mode="python"),
        "alternative": (
            alternative.model_dump(mode="python")
            if alternative is not None
            else None
        ),
        "alternatives": [
            item.model_dump(mode="python") for item in compiled_alternatives
        ],
        "setup_candidates": [
            item.model_dump(mode="python") for item in candidates
        ],
        "gate_result": gate,
        "price_snapshot_hash": catalog.snapshot_hash,
    }
    source_digest = hashlib.sha256(
        json.dumps(
            source_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return PaHypothesisSet(
        primary=primary,
        alternative=alternative,
        alternatives=compiled_alternatives,
        setup_candidates=candidates,
        gate_result=gate,
        price_snapshot_hash=catalog.snapshot_hash,
        source_digest=source_digest,
    )
