from __future__ import annotations

from pa_agent.records.counterfactual import evaluate_trade_plan


def _plan(order_type: str = "限价单") -> dict:
    return {
        "order_type": order_type,
        "order_direction": "做多",
        "entry_price": 100.0,
        "stop_loss_price": 95.0,
        "take_profit_price": 110.0,
    }


def _bar(ts: int, *, open_: float, high: float, low: float, close: float) -> dict:
    return {
        "ts_open": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "closed": True,
    }


def test_limit_plan_is_filled_then_wins_on_a_later_closed_bar() -> None:
    result = evaluate_trade_plan(
        _plan(),
        [
            _bar(2, open_=103, high=104, low=99, close=102),
            _bar(3, open_=102, high=111, low=101, close=109),
        ],
        horizon_bars=10,
    )

    assert result.entry_status == "filled"
    assert result.outcome == "win"
    assert result.entry_bar_ts == 2
    assert result.exit_bar_ts == 3
    assert result.realized_return_r == 2.0


def test_same_bar_entry_and_exit_touch_is_ambiguous() -> None:
    result = evaluate_trade_plan(
        _plan(),
        [_bar(2, open_=103, high=111, low=94, close=102)],
        horizon_bars=10,
    )

    assert result.entry_status == "filled"
    assert result.outcome == "ambiguous"
    assert result.status == "ambiguous_intrabar_order"


def test_unfilled_plan_expires_at_the_configured_horizon() -> None:
    result = evaluate_trade_plan(
        _plan(),
        [
            _bar(2, open_=105, high=108, low=101, close=106),
            _bar(3, open_=106, high=109, low=101, close=107),
        ],
        horizon_bars=2,
    )

    assert result.entry_status == "expired"
    assert result.outcome == "not_applicable"
    assert result.status == "entry_not_triggered"


def test_market_plan_is_not_backfilled_without_a_real_fill_price() -> None:
    result = evaluate_trade_plan(
        _plan("市价单"),
        [_bar(2, open_=101, high=105, low=99, close=104)],
        horizon_bars=10,
    )

    assert result.status == "unsupported_market_fill"
    assert result.outcome == "not_applicable"


def test_breakout_gap_does_not_assume_the_planned_trigger_fill() -> None:
    result = evaluate_trade_plan(
        _plan("突破单"),
        [_bar(2, open_=104, high=108, low=103, close=107)],
        horizon_bars=10,
    )

    assert result.status == "ambiguous_gap_fill"
    assert result.outcome == "ambiguous"


def test_conflicting_duplicate_bar_is_not_replayed() -> None:
    result = evaluate_trade_plan(
        _plan(),
        [
            _bar(2, open_=103, high=104, low=99, close=102),
            _bar(2, open_=103, high=105, low=99, close=104),
        ],
        horizon_bars=10,
    )

    assert result.status == "invalid_bar_history"
    assert result.outcome == "unresolved"
