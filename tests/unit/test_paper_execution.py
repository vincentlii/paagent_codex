from __future__ import annotations

from pathlib import Path

import pytest

from pa_agent.data.base import KlineBar
from pa_agent.execution.paper import (
    InstrumentSpec,
    PaperBroker,
    PaperTradePlan,
    RiskPolicy,
    size_for_structural_stop,
)


def _spec() -> InstrumentSpec:
    return InstrumentSpec(
        provider="binance_usdm",
        venue="BINANCE",
        symbol="XAUUSDT",
        instrument_type="tradifi_perpetual",
        tick_size=0.01,
        quantity_step=0.001,
        minimum_quantity=0.001,
        maximum_quantity=1000.0,
        minimum_notional=5.0,
    )


def _contract_spec() -> InstrumentSpec:
    return InstrumentSpec(
        provider="okx_swap",
        venue="OKX",
        symbol="XAU-USDT-SWAP",
        instrument_type="linear_swap",
        tick_size=0.1,
        quantity_step=1.0,
        minimum_quantity=1.0,
        maximum_quantity=100_000.0,
        minimum_notional=0.0,
        contract_multiplier=0.01,
    )


def _plan(*, run_id: str = "run-1") -> PaperTradePlan:
    return PaperTradePlan(
        run_id=run_id,
        arm="pa_official",
        symbol="XAUUSDT",
        timeframe="1h",
        side="long",
        order_type="limit",
        entry_price=2000.0,
        stop_price=1990.0,
        take_profit_price=2020.0,
        take_profit_price_2=2030.0,
        created_bar_ts=1000,
    )


def _contract_plan(*, run_id: str = "run-1") -> PaperTradePlan:
    return PaperTradePlan(
        **{
            **_plan(run_id=run_id).__dict__,
            "symbol": "XAU-USDT-SWAP",
        }
    )


def _bar(ts: int, *, open_: float, high: float, low: float, close: float) -> KlineBar:
    return KlineBar(
        seq=1,
        ts_open=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        closed=True,
    )


def test_position_size_uses_stop_risk_then_caps_leverage() -> None:
    sized = size_for_structural_stop(
        equity=10_000.0,
        entry_price=2000.0,
        stop_price=1990.0,
        spec=_spec(),
        policy=RiskPolicy(risk_fraction=0.01, max_leverage=3.0),
    )

    assert sized.risk_budget == pytest.approx(100.0)
    assert sized.quantity == pytest.approx(10.0)
    assert sized.notional == pytest.approx(20_000.0)
    assert sized.leverage == pytest.approx(2.0)


def test_position_size_converts_contracts_to_base_exposure() -> None:
    sized = size_for_structural_stop(
        equity=10_000.0,
        entry_price=2000.0,
        stop_price=1990.0,
        spec=_contract_spec(),
        policy=RiskPolicy(risk_fraction=0.01, max_leverage=3.0),
    )

    assert sized.quantity == pytest.approx(1000.0)
    assert sized.structural_risk == pytest.approx(100.0)
    assert sized.notional == pytest.approx(20_000.0)
    assert sized.leverage == pytest.approx(2.0)


def test_contract_multiplier_round_trips_through_instrument_spec() -> None:
    restored = InstrumentSpec.from_value(_contract_spec().to_dict())

    assert restored == _contract_spec()
    assert InstrumentSpec.from_value(_spec().to_dict()).contract_multiplier == 1.0


def test_paper_broker_applies_contract_multiplier_to_exposure_and_pnl(
    tmp_path: Path,
) -> None:
    broker = PaperBroker(
        tmp_path / "paper.sqlite3",
        account_id="pa-official",
        initial_equity=10_000.0,
        risk_policy=RiskPolicy(
            risk_fraction=0.01,
            max_portfolio_risk_fraction=0.03,
            max_leverage=10.0,
        ),
        fee_bps=0.0,
        slippage_bps=0.0,
    )

    first = broker.submit(_contract_plan(run_id="run-1"), _contract_spec())
    second = broker.submit(_contract_plan(run_id="run-2"), _contract_spec())
    assert first.quantity == pytest.approx(1000.0)
    assert second.quantity == pytest.approx(1000.0)

    broker.observe_market(
        _contract_spec(),
        "1h",
        [_bar(2000, open_=2000.0, high=2021.0, low=1999.0, close=2020.0)],
    )

    assert broker.account_snapshot()["equity"] == pytest.approx(10_400.0)


def test_position_size_rejects_untradable_minimum_notional() -> None:
    spec = _spec().__class__(**{**_spec().__dict__, "minimum_notional": 50_000.0})

    with pytest.raises(ValueError, match="minimum notional"):
        size_for_structural_stop(
            equity=100.0,
            entry_price=2000.0,
            stop_price=1990.0,
            spec=spec,
            policy=RiskPolicy(risk_fraction=0.01, max_leverage=1.0),
        )


def test_paper_broker_persists_limit_fill_and_tp1_full_exit(tmp_path: Path) -> None:
    db = tmp_path / "paper.sqlite3"
    broker = PaperBroker(
        db,
        account_id="pa-official",
        initial_equity=10_000.0,
        risk_policy=RiskPolicy(risk_fraction=0.01, max_leverage=3.0),
        fee_bps=5.0,
        slippage_bps=0.0,
    )
    order = broker.submit(_plan(), _spec())

    broker.observe_market(
        _spec(),
        "1h",
        [
        _bar(2000, open_=2005.0, high=2008.0, low=1998.0, close=2004.0),
        ],
    )
    broker.observe_market(
        _spec(),
        "1h",
        [
        _bar(3000, open_=2004.0, high=2021.0, low=2001.0, close=2018.0),
        ],
    )

    snapshot = broker.account_snapshot()
    saved = broker.order(order.order_id)
    assert saved is not None
    assert saved["status"] == "closed"
    assert saved["exit_reason"] == "take_profit_1"
    assert snapshot["open_position_count"] == 0
    assert snapshot["equity"] > 10_000.0
    assert len(broker.events(order.order_id)) >= 4

    restarted = PaperBroker(
        db,
        account_id="pa-official",
        initial_equity=1.0,
        risk_policy=RiskPolicy(risk_fraction=0.01, max_leverage=3.0),
    )
    assert restarted.account_snapshot()["equity"] == pytest.approx(snapshot["equity"])


def test_intrabar_limit_entry_and_exit_touch_is_ambiguous_without_pnl(
    tmp_path: Path,
) -> None:
    broker = PaperBroker(
        tmp_path / "paper.sqlite3",
        account_id="pa-official",
        initial_equity=10_000.0,
        risk_policy=RiskPolicy(risk_fraction=0.01, max_leverage=3.0),
        fee_bps=0.0,
        slippage_bps=0.0,
    )
    order = broker.submit(_plan(), _spec())
    broker.observe_market(
        _spec(),
        "1h",
        [_bar(2000, open_=2005.0, high=2025.0, low=1998.0, close=2005.0)],
    )

    saved = broker.order(order.order_id)
    assert saved is not None
    assert saved["simulation_version"] == "paper_ohlc_v2"
    assert saved["status"] == "ambiguous"
    assert saved["exit_reason"] == "ambiguous_entry_bar"
    assert saved["realized_pnl"] is None
    assert broker.account_snapshot()["equity"] == pytest.approx(10_000.0)
    assert broker.account_snapshot()["ambiguous_order_count"] == 1
    assert broker.account_snapshot()["orders_by_simulation_version"] == {
        "paper_ohlc_v2": 1
    }


def test_open_position_same_bar_stop_and_target_uses_conservative_stop_first(
    tmp_path: Path,
) -> None:
    broker = PaperBroker(
        tmp_path / "paper.sqlite3",
        account_id="pa-official",
        initial_equity=10_000.0,
        risk_policy=RiskPolicy(risk_fraction=0.01, max_leverage=3.0),
        fee_bps=0.0,
        slippage_bps=0.0,
    )
    order = broker.submit(_plan(), _spec())
    broker.observe_market(
        _spec(),
        "1h",
        [_bar(2000, open_=2005.0, high=2008.0, low=1998.0, close=2004.0)],
    )
    broker.observe_market(
        _spec(),
        "1h",
        [_bar(3000, open_=2001.0, high=2025.0, low=1985.0, close=2005.0)],
    )

    saved = broker.order(order.order_id)
    assert saved is not None
    assert saved["status"] == "closed"
    assert saved["exit_reason"] == "protective_stop_ambiguous_intrabar"
    assert broker.account_snapshot()["equity"] < 10_000.0


def test_second_target_must_be_strictly_beyond_first_target(tmp_path: Path) -> None:
    broker = PaperBroker(
        tmp_path / "paper.sqlite3",
        account_id="pa-official",
        initial_equity=10_000.0,
        risk_policy=RiskPolicy(risk_fraction=0.01, max_leverage=3.0),
    )
    invalid = _plan().__class__(
        **{**_plan().__dict__, "take_profit_price_2": _plan().take_profit_price}
    )

    with pytest.raises(ValueError, match="strictly beyond"):
        broker.submit(invalid, _spec())


def test_breakout_and_stop_gap_use_open_instead_of_optimistic_trigger_price(
    tmp_path: Path,
) -> None:
    broker = PaperBroker(
        tmp_path / "paper.sqlite3",
        account_id="pa-official",
        initial_equity=10_000.0,
        risk_policy=RiskPolicy(risk_fraction=0.01, max_leverage=3.0),
        fee_bps=0.0,
        slippage_bps=0.0,
    )
    plan = _plan().__class__(**{**_plan().__dict__, "order_type": "breakout"})
    order = broker.submit(plan, _spec())
    broker.observe_market(
        _spec(),
        "1h",
        [
        _bar(2000, open_=2005.0, high=2008.0, low=2001.0, close=2004.0),
        ],
    )
    assert broker.order(order.order_id)["fill_price"] == pytest.approx(2005.0)

    broker.observe_market(
        _spec(),
        "1h",
        [
        _bar(3000, open_=1980.0, high=1988.0, low=1975.0, close=1982.0),
        ],
    )
    saved = broker.order(order.order_id)
    assert saved is not None
    assert saved["exit_price"] == pytest.approx(1980.0)
    assert saved["exit_reason"] == "protective_stop"


def test_gap_fill_reduces_quantity_to_preserve_structural_risk(tmp_path: Path) -> None:
    broker = PaperBroker(
        tmp_path / "paper.sqlite3",
        account_id="pa-official",
        initial_equity=10_000.0,
        risk_policy=RiskPolicy(
            risk_fraction=0.01,
            max_portfolio_risk_fraction=0.02,
            max_leverage=3.0,
        ),
        fee_bps=0.0,
        slippage_bps=0.0,
    )
    plan = _plan().__class__(**{**_plan().__dict__, "order_type": "breakout"})
    order = broker.submit(plan, _spec())

    changed_live_spec = _spec().__class__(
        **{**_spec().__dict__, "maximum_quantity": 1.0}
    )
    broker.observe_market(
        changed_live_spec,
        "1h",
        [_bar(2000, open_=2010.0, high=2012.0, low=2001.0, close=2008.0)],
    )

    saved = broker.order(order.order_id)
    assert saved["status"] == "open"
    assert saved["fill_price"] == pytest.approx(2010.0)
    assert saved["quantity"] == pytest.approx(5.0)
    assert saved["quantity"] * (saved["fill_price"] - saved["stop_price"]) <= 100.0


def test_broker_deduplicates_same_run_and_arm(tmp_path: Path) -> None:
    broker = PaperBroker(
        tmp_path / "paper.sqlite3",
        account_id="pa-official",
        initial_equity=10_000.0,
        risk_policy=RiskPolicy(risk_fraction=0.01, max_leverage=3.0),
    )
    first = broker.submit(_plan(), _spec())
    second = broker.submit(_plan(), _spec())

    assert second.order_id == first.order_id


def test_broker_caps_aggregate_pending_and_open_risk(tmp_path: Path) -> None:
    broker = PaperBroker(
        tmp_path / "paper.sqlite3",
        account_id="pa-official",
        initial_equity=10_000.0,
        risk_policy=RiskPolicy(
            risk_fraction=0.01,
            max_portfolio_risk_fraction=0.02,
            max_leverage=10.0,
        ),
    )
    broker.submit(_plan(run_id="run-1"), _spec())
    broker.submit(_plan(run_id="run-2"), _spec())

    with pytest.raises(ValueError, match="minimum quantity"):
        broker.submit(_plan(run_id="run-3"), _spec())


def test_market_observation_is_persistent_idempotent_and_timeframe_scoped(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper.sqlite3"
    broker = PaperBroker(
        db,
        account_id="pa-official",
        initial_equity=10_000.0,
        risk_policy=RiskPolicy(risk_fraction=0.01, max_leverage=3.0),
    )
    order = broker.submit(_plan(), _spec())
    bars = [_bar(2000, open_=2005.0, high=2008.0, low=1998.0, close=2004.0)]

    assert broker.observe_market(_spec(), "4h", bars) == 1
    assert broker.order(order.order_id)["status"] == "pending"
    assert broker.observe_market(_spec(), "1h", bars) == 1
    assert broker.order(order.order_id)["status"] == "open"
    assert broker.observe_market(_spec(), "1h", bars) == 0

    restarted = PaperBroker(
        db,
        account_id="pa-official",
        initial_equity=1.0,
        risk_policy=RiskPolicy(risk_fraction=0.01, max_leverage=3.0),
    )
    assert restarted.observe_market(_spec(), "1h", bars) == 0


def test_late_decision_replays_already_observed_closed_bars_once(tmp_path: Path) -> None:
    broker = PaperBroker(
        tmp_path / "paper.sqlite3",
        account_id="pa-official",
        initial_equity=10_000.0,
        risk_policy=RiskPolicy(risk_fraction=0.01, max_leverage=3.0),
    )
    future = _bar(2000, open_=2005.0, high=2008.0, low=1998.0, close=2004.0)
    assert broker.observe_market(_spec(), "1h", [future]) == 1

    order = broker.submit(_plan(), _spec())

    assert broker.order(order.order_id)["status"] == "open"
    event_count = len(broker.events(order.order_id))
    assert broker.observe_market(_spec(), "1h", [future]) == 0
    assert len(broker.events(order.order_id)) == event_count


def test_same_market_timestamp_with_different_facts_is_rejected(tmp_path: Path) -> None:
    broker = PaperBroker(
        tmp_path / "paper.sqlite3",
        account_id="pa-official",
        initial_equity=10_000.0,
        risk_policy=RiskPolicy(risk_fraction=0.01, max_leverage=3.0),
    )
    first = _bar(2000, open_=2005.0, high=2008.0, low=1998.0, close=2004.0)
    revised = _bar(2000, open_=2005.0, high=2009.0, low=1998.0, close=2004.0)
    broker.observe_market(_spec(), "1h", [first])

    with pytest.raises(ValueError, match="conflicting closed bar"):
        broker.observe_market(_spec(), "1h", [revised])


def test_new_analysis_supersedes_only_unfilled_older_plan(tmp_path: Path) -> None:
    broker = PaperBroker(
        tmp_path / "paper.sqlite3",
        account_id="pa-official",
        initial_equity=10_000.0,
        risk_policy=RiskPolicy(risk_fraction=0.01, max_leverage=3.0),
    )
    stale = _plan().__class__(
        **{**_plan().__dict__, "entry_price": 1900.0, "stop_price": 1890.0,
           "take_profit_price": 1920.0, "take_profit_price_2": 1930.0}
    )
    stale_order = broker.submit(stale, _spec())
    broker.observe_market(
        _spec(),
        "1h",
        [_bar(2000, open_=2005.0, high=2008.0, low=1998.0, close=2004.0)],
    )

    cancelled = broker.supersede_pending(
        _spec(),
        "1h",
        as_of_bar_ts=2000,
        excluding_dedupe_key="new-task",
    )

    assert cancelled == 1
    assert broker.order(stale_order.order_id)["status"] == "cancelled"


def test_paper_plan_rejects_price_off_venue_tick(tmp_path: Path) -> None:
    broker = PaperBroker(
        tmp_path / "paper.sqlite3",
        account_id="pa-official",
        initial_equity=10_000.0,
        risk_policy=RiskPolicy(risk_fraction=0.01, max_leverage=3.0),
    )
    invalid = _plan().__class__(**{**_plan().__dict__, "entry_price": 2000.005})

    with pytest.raises(ValueError, match="tick size"):
        broker.submit(invalid, _spec())
