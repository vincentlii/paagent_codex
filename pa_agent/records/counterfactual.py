"""Deterministic stop/target replay for analysis plans.

This module evaluates a hypothetical plan from closed OHLC bars. It never
claims that an order was actually submitted or filled. Intrabar paths that
cannot be reconstructed from OHLC are reported as ambiguous rather than being
forced into a win or loss.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

_LIMIT_TYPES = frozenset({"限价单", "limit"})
_BREAKOUT_TYPES = frozenset({"突破单", "breakout", "stop"})
_MARKET_TYPES = frozenset({"市价单", "market"})
_NO_ORDER_TYPES = frozenset({"", "不下单", "no_order", "none"})
_LONG_DIRECTIONS = frozenset({"做多", "long", "buy"})
_SHORT_DIRECTIONS = frozenset({"做空", "short", "sell"})


@dataclass(frozen=True)
class CounterfactualResult:
    status: str
    entry_status: str
    outcome: str
    horizon_bars: int
    bars_observed: int
    entry_bar_ts: int | None = None
    exit_bar_ts: int | None = None
    realized_return_r: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    evaluated_through_ts: int | None = None
    notes: str = ""


def evaluate_trade_plan(
    plan: Mapping[str, Any] | None,
    future_bars: Iterable[Mapping[str, Any]],
    *,
    horizon_bars: int,
) -> CounterfactualResult:
    """Replay one planned order against later closed bars.

    TP1 is the winning target because the live Trader Equation also uses TP1.
    The entry bar is never used to decide an exit: OHLC cannot reveal whether
    entry or stop/target occurred first inside that bar.
    """
    horizon = max(1, int(horizon_bars))
    candidate = dict(plan or {})
    order_type = str(candidate.get("order_type") or "").strip().lower()
    if order_type in _NO_ORDER_TYPES:
        return CounterfactualResult(
            status="not_applicable",
            entry_status="not_traded",
            outcome="not_applicable",
            horizon_bars=horizon,
            bars_observed=0,
        )
    if order_type in _MARKET_TYPES:
        return CounterfactualResult(
            status="unsupported_market_fill",
            entry_status="not_traded",
            outcome="not_applicable",
            horizon_bars=horizon,
            bars_observed=0,
            notes="Market orders require an actual execution price; snapshot OHLC is insufficient.",
        )
    if order_type not in _LIMIT_TYPES | _BREAKOUT_TYPES:
        return _invalid(horizon, "unsupported order type")

    direction = str(candidate.get("order_direction") or "").strip().lower()
    if direction in _LONG_DIRECTIONS:
        is_long = True
    elif direction in _SHORT_DIRECTIONS:
        is_long = False
    else:
        return _invalid(horizon, "unsupported order direction")

    entry = _finite(candidate.get("entry_price"))
    stop = _finite(candidate.get("stop_loss_price"))
    target = _finite(candidate.get("take_profit_price"))
    if entry is None or stop is None or target is None:
        return _invalid(horizon, "missing entry, stop, or TP1")
    risk = entry - stop if is_long else stop - entry
    reward = target - entry if is_long else entry - target
    if risk <= 0 or reward <= 0:
        return _invalid(horizon, "invalid price ordering")

    bars_by_ts: dict[int, dict[str, float | int]] = {}
    for item in future_bars:
        normalized = _normalize_bar(item)
        if normalized is None:
            continue
        ts_open = int(normalized["ts_open"])
        previous = bars_by_ts.get(ts_open)
        if previous is not None and previous != normalized:
            return CounterfactualResult(
                status="invalid_bar_history",
                entry_status="pending",
                outcome="unresolved",
                horizon_bars=horizon,
                bars_observed=len(bars_by_ts),
                evaluated_through_ts=max(bars_by_ts) if bars_by_ts else None,
                notes="Conflicting closed OHLC values share the same bar timestamp.",
            )
        bars_by_ts[ts_open] = normalized
    usable = sorted(bars_by_ts.values(), key=lambda item: item["ts_open"])[:horizon]
    observed_through = usable[-1]["ts_open"] if usable else None
    entry_index: int | None = None
    for index, bar in enumerate(usable):
        if _entry_touched(order_type, is_long, entry, bar):
            entry_index = index
            break

    if entry_index is None:
        expired = len(usable) >= horizon
        return CounterfactualResult(
            status="entry_not_triggered" if expired else "pending_entry",
            entry_status="expired" if expired else "pending",
            outcome="not_applicable" if expired else "pending",
            horizon_bars=horizon,
            bars_observed=len(usable),
            evaluated_through_ts=observed_through,
        )

    entry_bar = usable[entry_index]
    if order_type in _BREAKOUT_TYPES and (
        (is_long and float(entry_bar["open"]) > entry)
        or (not is_long and float(entry_bar["open"]) < entry)
    ):
        return CounterfactualResult(
            status="ambiguous_gap_fill",
            entry_status="filled",
            outcome="ambiguous",
            horizon_bars=horizon,
            bars_observed=len(usable),
            entry_bar_ts=entry_bar["ts_open"],
            evaluated_through_ts=observed_through,
            notes="Breakout bar opened beyond the trigger; planned entry price is not a valid fill assumption.",
        )
    if _stop_touched(is_long, stop, entry_bar) or _target_touched(
        is_long, target, entry_bar
    ):
        return CounterfactualResult(
            status="ambiguous_intrabar_order",
            entry_status="filled",
            outcome="ambiguous",
            horizon_bars=horizon,
            bars_observed=len(usable),
            entry_bar_ts=entry_bar["ts_open"],
            evaluated_through_ts=observed_through,
            notes="Entry and an exit level touched in the same OHLC bar; event order is unknowable.",
        )

    mfe = 0.0
    mae = 0.0
    for bar in usable[entry_index + 1 :]:
        favourable = bar["high"] - entry if is_long else entry - bar["low"]
        adverse = entry - bar["low"] if is_long else bar["high"] - entry
        mfe = max(mfe, favourable / risk)
        mae = max(mae, adverse / risk)
        stop_hit = _stop_touched(is_long, stop, bar)
        target_hit = _target_touched(is_long, target, bar)
        if stop_hit and target_hit:
            return CounterfactualResult(
                status="ambiguous_intrabar_exit",
                entry_status="filled",
                outcome="ambiguous",
                horizon_bars=horizon,
                bars_observed=len(usable),
                entry_bar_ts=entry_bar["ts_open"],
                exit_bar_ts=bar["ts_open"],
                mfe_r=round(mfe, 6),
                mae_r=round(mae, 6),
                evaluated_through_ts=observed_through,
                notes="Stop and TP1 touched in the same closed bar; event order is unknowable.",
            )
        if target_hit or stop_hit:
            return CounterfactualResult(
                status="completed",
                entry_status="filled",
                outcome="win" if target_hit else "loss",
                horizon_bars=horizon,
                bars_observed=len(usable),
                entry_bar_ts=entry_bar["ts_open"],
                exit_bar_ts=bar["ts_open"],
                realized_return_r=round(reward / risk, 6) if target_hit else -1.0,
                mfe_r=round(mfe, 6),
                mae_r=round(mae, 6),
                evaluated_through_ts=observed_through,
            )

    horizon_reached = len(usable) >= horizon
    return CounterfactualResult(
        status="horizon_reached" if horizon_reached else "pending_exit",
        entry_status="filled",
        outcome="unresolved" if horizon_reached else "pending",
        horizon_bars=horizon,
        bars_observed=len(usable),
        entry_bar_ts=entry_bar["ts_open"],
        mfe_r=round(mfe, 6),
        mae_r=round(mae, 6),
        evaluated_through_ts=observed_through,
    )


def _invalid(horizon: int, notes: str) -> CounterfactualResult:
    return CounterfactualResult(
        status="invalid_plan",
        entry_status="not_traded",
        outcome="not_applicable",
        horizon_bars=horizon,
        bars_observed=0,
        notes=notes,
    )


def _normalize_bar(item: Mapping[str, Any]) -> dict[str, float | int] | None:
    if item.get("closed") is False:
        return None
    ts_open = _integer(item.get("ts_open"))
    open_ = _finite(item.get("open"))
    high = _finite(item.get("high"))
    low = _finite(item.get("low"))
    close = _finite(item.get("close"))
    if ts_open is None or None in {open_, high, low, close}:
        return None
    assert open_ is not None and high is not None and low is not None and close is not None
    if high < max(open_, close) or low > min(open_, close) or high < low:
        return None
    return {"ts_open": ts_open, "open": open_, "high": high, "low": low, "close": close}


def _entry_touched(order_type: str, is_long: bool, entry: float, bar: Mapping[str, Any]) -> bool:
    if order_type in _LIMIT_TYPES:
        return float(bar["low"]) <= entry if is_long else float(bar["high"]) >= entry
    return float(bar["high"]) >= entry if is_long else float(bar["low"]) <= entry


def _stop_touched(is_long: bool, stop: float, bar: Mapping[str, Any]) -> bool:
    return float(bar["low"]) <= stop if is_long else float(bar["high"]) >= stop


def _target_touched(is_long: bool, target: float, bar: Mapping[str, Any]) -> bool:
    return float(bar["high"]) >= target if is_long else float(bar["low"]) <= target


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
