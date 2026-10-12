from dataclasses import replace

import pytest

from pa_agent.execution.paper import PaperBroker, RiskPolicy
from tests.unit.test_paper_execution import _plan, _spec, _bar

T = 1_800_000_000_000
H = 3_600_000


def broker(tmp_path):
    return PaperBroker(
        tmp_path / "paper.sqlite3",
        account_id="pipeline-pa_vpa_joint",
        initial_equity=10000,
        risk_policy=RiskPolicy(),
        slippage_bps=10,
    )


def plan(**kwargs):
    return replace(
        _plan(), created_bar_ts=T, available_at_ms=T + H, expires_at_ms=T + 2 * H, **kwargs
    )


def test_latency_plan_has_explicit_delivery_fields():
    assert hasattr(_plan(), "available_at_ms")


def test_v3_limit_fill_never_worse_than_limit(tmp_path):
    b = broker(tmp_path)
    order = b.submit(plan(), _spec())
    b.observe_market(_spec(), "1h", [_bar(T + H, open_=2005, high=2010, low=2000, close=2007)])
    row = b.order(order.order_id)
    assert row["fill_price"] == 2000
    assert row["simulation_version"] == "paper_latency_v3"


def test_midbar_delivery_cannot_retroactively_fill(tmp_path):
    b = broker(tmp_path)
    p = replace(plan(), available_at_ms=T + H + 100)
    order = b.submit(p, _spec())
    b.observe_market(_spec(), "1h", [_bar(T + H, open_=2005, high=2010, low=2000, close=2007)])
    row = b.order(order.order_id)
    assert row["status"] == "ambiguous"
    assert row["fill_price"] is None
    assert row["realized_pnl"] is None


def test_untouched_pending_expires_at_next_close(tmp_path):
    b = broker(tmp_path)
    order = b.submit(plan(), _spec())
    b.observe_market(_spec(), "1h", [_bar(T + H, open_=2005, high=2010, low=2003, close=2007)])
    row = b.order(order.order_id)
    assert row["status"] == "cancelled"
    assert row["exit_reason"] == "decision_expired"


def test_v3_expired_unfilled_history_conflict_allows_only_independent_future_plan(tmp_path):
    b = broker(tmp_path)
    old = b.submit(plan(), _spec())
    bar = _bar(T + H, open_=2005, high=2010, low=2003, close=2007)
    b.observe_market(_spec(), "1h", [bar], observed_at_ms=T + 2 * H)
    before = b.order(old.order_id), b.events(old.order_id), b.account_snapshot()
    assert before[0]["status"] == "cancelled" and before[0]["fill_price"] is None
    revised = replace(bar, close=2007.01)
    restarted = broker(tmp_path)
    restarted.observe_market(_spec(), "1h", [revised], observed_at_ms=T + 3 * H)
    restarted.observe_market(_spec(), "1h", [revised], observed_at_ms=T + 4 * H)
    assert (restarted.order(old.order_id), restarted.events(old.order_id), restarted.account_snapshot()) == before
    with pytest.raises(ValueError, match="quarantined"):
        restarted.submit(replace(plan(), run_id="late"), _spec())
    future_plan = replace(plan(), run_id="future", created_bar_ts=T + 3 * H,
                          available_at_ms=T + 4 * H, expires_at_ms=T + 5 * H)
    future = restarted.submit(future_plan, _spec())
    assert future.status == "pending"
    restarted.observe_market(_spec(), "1h", [revised, _bar(T + 4 * H, open_=2000, high=2008, low=1998, close=2005)],
                             observed_at_ms=T + 5 * H)
    assert restarted.order(future.order_id)["status"] == "open"
    assert restarted.order(future.order_id)["fill_price"] == 2000


def test_stale_or_incomplete_delivery_is_rejected(tmp_path):
    b = broker(tmp_path)
    with pytest.raises(ValueError, match="expired"):
        b.submit(replace(plan(), available_at_ms=T + 2 * H), _spec())
    with pytest.raises(ValueError, match="timing"):
        b.submit(replace(plan(), expires_at_ms=None), _spec())


def test_legacy_plan_keeps_old_simulation_version(tmp_path):
    b = broker(tmp_path)
    order = b.submit(_plan(), _spec())
    assert b.order(order.order_id)["simulation_version"] == "paper_ohlc_v2"


def test_v3_intrabar_entry_and_exit_remain_ambiguous(tmp_path):
    b = broker(tmp_path)
    order = b.submit(plan(), _spec())
    b.observe_market(_spec(), "1h", [_bar(T + H, open_=2005, high=2030, low=1980, close=2007)])
    row = b.order(order.order_id)
    assert row["status"] == "ambiguous"
    assert row["realized_pnl"] is None


def test_old_bars_before_availability_do_not_fill(tmp_path):
    b = broker(tmp_path)
    # Complete an earlier bar before the plan was available; no retroactive fill.
    b.observe_market(_spec(), "1h", [_bar(T + H, open_=2005, high=2010, low=2000, close=2007)])
    p = replace(plan(), available_at_ms=T + 2 * H, expires_at_ms=T + 3 * H)
    order = b.submit(p, _spec())
    assert b.order(order.order_id)["fill_price"] is None


def test_missing_closed_bar_at_expiry_is_censored_not_a_missed_trade(tmp_path):
    b = broker(tmp_path)
    order = b.submit(plan(), _spec())
    assert hasattr(b, "expire_pending")
    b.expire_pending(_spec(), "1h", now_ms=T + 2 * H)
    row = b.order(order.order_id)
    assert row["status"] == "ambiguous"
    assert row["exit_reason"] == "missing_delivery_window_bars"


def test_short_limit_and_open_position_survive_pending_expiry(tmp_path):
    b = broker(tmp_path)
    p = plan(side="short", stop_price=2010, take_profit_price=1980, take_profit_price_2=None)
    order = b.submit(p, _spec())
    b.observe_market(_spec(), "1h", [_bar(T + H, open_=1995, high=2000, low=1992, close=1996)])
    assert b.order(order.order_id)["fill_price"] == 2000
    b.expire_pending(_spec(), "1h", now_ms=T + 2 * H)
    assert b.order(order.order_id)["status"] == "open"


@pytest.mark.parametrize("side", ["long", "short"])
def test_v3_open_position_missing_bar_is_censored_not_later_profit(tmp_path, side):
    b = broker(tmp_path)
    overrides = {} if side == "long" else dict(stop_price=2010, take_profit_price=1980)
    order = b.submit(plan(side=side, take_profit_price_2=None, **overrides), _spec())
    b.observe_market(_spec(), "1h", [_bar(T + H, open_=2000, high=2005, low=1995, close=2000)])
    assert b.order(order.order_id)["status"] == "open"
    # T+2H could already have stopped/closed the position. It is not observed.
    b.observe_market(_spec(), "1h", [_bar(T + 3 * H, open_=2000, high=2025, low=1975, close=2000)])
    row = b.order(order.order_id)
    assert row["status"] == "ambiguous"
    assert row["exit_reason"] == "missing_open_position_bars"
    assert row["realized_pnl"] is None
    assert row["fill_price"] == 2000
    assert b.account_snapshot()["equity"] == 10000


def test_v3_batch_replay_of_complete_bars_is_not_a_gap(tmp_path):
    b = broker(tmp_path)
    order = b.submit(plan(), _spec())
    b.observe_market(_spec(), "1h", [
        _bar(T + 3 * H, open_=2000, high=2025, low=1995, close=2020),
        _bar(T + H, open_=2000, high=2005, low=1995, close=2000),
        _bar(T + 2 * H, open_=2000, high=2005, low=1995, close=2000),
    ])
    assert b.order(order.order_id)["status"] == "closed"
    assert b.order(order.order_id)["realized_pnl"] is not None


def test_market_midbar_is_unknown_not_filled_at_prior_open(tmp_path):
    b = broker(tmp_path)
    order = b.submit(replace(plan(order_type="market"), available_at_ms=T + H + 100), _spec())
    b.observe_market(_spec(), "1h", [_bar(T + H, open_=2005, high=2010, low=2000, close=2007)])
    assert b.order(order.order_id)["fill_price"] is None
    assert b.order(order.order_id)["status"] == "ambiguous"


def test_monthly_timeframe_is_not_misread_as_minutes(tmp_path):
    with pytest.raises(ValueError, match="fixed-duration"):
        broker(tmp_path).submit(plan(timeframe="1M"), _spec())


def test_activation_rechecks_time_inside_write_transaction(tmp_path, monkeypatch):
    from tests.unit.test_pipeline_paper_coordinator import clock

    b = broker(tmp_path)
    clock(monkeypatch, T + 2 * H)
    with pytest.raises(ValueError, match="expired"):
        b.submit(plan(), _spec(), activate_now=True)


def test_additive_migration_does_not_rewrite_legacy_order(tmp_path):
    import sqlite3

    b = broker(tmp_path)
    order = b.submit(_plan(), _spec())
    before = b.order(order.order_id)
    with sqlite3.connect(tmp_path / "paper.sqlite3") as c:
        c.execute("ALTER TABLE paper_orders DROP COLUMN available_at_ms")
        c.execute("ALTER TABLE paper_orders DROP COLUMN expires_at_ms")
    after = broker(tmp_path).order(order.order_id)
    assert before == after
    assert after["available_at_ms"] is None and after["expires_at_ms"] is None
