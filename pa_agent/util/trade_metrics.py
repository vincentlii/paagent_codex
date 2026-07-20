"""Risk/reward and estimated win-rate helpers for trading decisions."""
from __future__ import annotations

from typing import Any


def is_long_direction(direction: object) -> bool | None:
    """Return True for long, False for short, None if unknown."""
    text = str(direction or "").strip().lower()
    if not text:
        return None
    if "多" in text or text in ("long", "buy", "bull"):
        return True
    if "空" in text or text in ("short", "sell", "bear"):
        return False
    return None


def compute_risk_reward(
    entry: object,
    take_profit: object,
    stop_loss: object,
    direction: object,
) -> dict[str, float | str] | None:
    """Compute risk/reward distances and reward:risk ratio (盈亏比).

    Returns None when prices are invalid or risk is zero.
    """
    try:
        e = float(entry)
        tp = float(take_profit)
        sl = float(stop_loss)
    except (TypeError, ValueError):
        return None

    long = is_long_direction(direction)
    if long is True:
        risk = e - sl
        reward = tp - e
    elif long is False:
        risk = sl - e
        reward = e - tp
    else:
        if tp > e and sl < e:
            risk = e - sl
            reward = tp - e
        elif tp < e and sl > e:
            risk = sl - e
            reward = e - tp
        else:
            return None

    if risk <= 0 or reward <= 0:
        return None

    ratio = reward / risk
    return {
        "risk": risk,
        "reward": reward,
        "ratio": ratio,
        "ratio_text": f"{ratio:.2f} : 1",
    }


def format_estimated_win_rate(decision: dict[str, Any]) -> str | None:
    """Format model-provided estimated_win_rate (0–100) for display."""
    value = decision.get("estimated_win_rate")
    if value is None or value == "":
        return None
    try:
        pct = max(0, min(100, int(float(str(value).strip()))))
    except (ValueError, TypeError):
        return None
    return f"{pct}%"


def format_estimated_win_rate_reasoning(decision: dict[str, Any]) -> str:
    return str(decision.get("estimated_win_rate_reasoning", "") or "").strip()


# Lower cap: reward must be at least equal to risk (1:1) for any stance.
MIN_RISK_REWARD_RATIO = 1.0


def min_risk_reward_ratio() -> float:
    """Minimum reward:risk ratio required by the execution policy."""
    return MIN_RISK_REWARD_RATIO


def passes_trader_equation(
    win_rate_pct: float,
    risk: float,
    reward: float,
) -> bool:
    """Brooks equation: win_rate × reward > (1 - win_rate) × risk."""
    if risk <= 0 or reward <= 0:
        return False
    p = max(0.0, min(100.0, float(win_rate_pct))) / 100.0
    return p * reward > (1.0 - p) * risk


def _parse_win_rate(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return max(0.0, min(100.0, float(str(value).strip())))
    except (TypeError, ValueError):
        return None


def validate_take_profit_2_geometry(
    decision: dict[str, Any],
) -> list[str]:
    """Ensure TP2 is beyond TP1 in the profit direction (no RR cap on TP2)."""
    entry = decision.get("entry_price")
    tp1 = decision.get("take_profit_price")
    tp2 = decision.get("take_profit_price_2")
    sl = decision.get("stop_loss_price")
    direction = decision.get("order_direction")

    try:
        e = float(entry)
        t1 = float(tp1)
        t2 = float(tp2)
        s = float(sl)
    except (TypeError, ValueError):
        return ["decision.take_profit_price_2: required finite number when placing an order"]

    long = is_long_direction(direction)
    if long is True:
        if not (s < e < t1 < t2):
            return [
                "decision.take_profit_price_2: long plan requires "
                "stop < entry < take_profit_price < take_profit_price_2"
            ]
    elif long is False:
        if not (t2 < t1 < e < s):
            return [
                "decision.take_profit_price_2: short plan requires "
                "take_profit_price_2 < take_profit_price < entry < stop"
            ]
    else:
        if t1 > e and t2 <= t1:
            return [
                "decision.take_profit_price_2: must be above take_profit_price for long geometry"
            ]
        if t1 < e and t2 >= t1:
            return [
                "decision.take_profit_price_2: must be below take_profit_price for short geometry"
            ]

    return []


def validate_order_trade_metrics(
    decision: dict[str, Any],
) -> list[str]:
    """Validate entry/TP/SL geometry, RR floor, and trader equation for live orders."""
    order_type = decision.get("order_type")
    if order_type not in ("限价单", "突破单", "市价单"):
        return []

    entry = decision.get("entry_price")
    tp = decision.get("take_profit_price")
    sl = decision.get("stop_loss_price")
    direction = decision.get("order_direction")
    rr = compute_risk_reward(entry, tp, sl, direction)
    if rr is None:
        return [
            "decision prices: entry/stop/target must form a valid long (sl<entry<tp) "
            "or short (tp<entry<sl) trade with positive risk and reward"
        ]

    errors: list[str] = []
    ratio = float(rr["ratio"])
    risk = float(rr["risk"])
    reward = float(rr["reward"])
    min_rr = min_risk_reward_ratio()

    if ratio < min_rr:
        errors.append(
            f"decision prices: risk_reward {rr['ratio_text']} is below minimum "
            f"{min_rr:.2f}:1; choose another structurally valid plan or set "
            "order_type=不下单 with 10.3=否"
        )

    win_rate = _parse_win_rate(decision.get("estimated_win_rate"))
    if win_rate is None:
        errors.append(
            "decision.estimated_win_rate: required integer 0–100 when placing an order"
        )
    elif not passes_trader_equation(win_rate, risk, reward):
        ev = win_rate / 100.0 * reward - (1.0 - win_rate / 100.0) * risk
        errors.append(
            f"decision prices: trader equation fails at {win_rate:.0f}% win rate "
            f"(risk={risk:.4g}, reward={reward:.4g}, expectancy≈{ev:.4g}); "
            "10.3 must be 否 and order_type=不下单 unless prices are fixed"
        )

    errors.extend(validate_take_profit_2_geometry(decision))

    return errors
