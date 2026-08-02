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

    broker.on_closed_bar(
        "XAUUSDT",
        _bar(2000, open_=2005.0, high=2008.0, low=1998.0, close=2004.0),
    )
    broker.on_closed_bar(
        "XAUUSDT",
        _bar(3000, open_=2004.0, high=2021.0, low=2001.0, close=2018.0),
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


def test_same_bar_stop_and_target_uses_conservative_stop_first(tmp_path: Path) -> None:
    broker = PaperBroker(
        tmp_path / "paper.sqlite3",
        account_id="pa-official",
        initial_equity=10_000.0,
        risk_policy=RiskPolicy(risk_fraction=0.01, max_leverage=3.0),
        fee_bps=0.0,
        slippage_bps=0.0,
    )
    order = broker.submit(_plan(), _spec())
    broker.on_closed_bar(
        "XAUUSDT",
        _bar(2000, open_=2001.0, high=2025.0, low=1985.0, close=2005.0),
    )

    saved = broker.order(order.order_id)
    assert saved is not None
    assert saved["status"] == "closed"
    assert saved["exit_reason"] == "protective_stop_ambiguous_intrabar"
    assert broker.account_snapshot()["equity"] < 10_000.0


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
    broker.on_closed_bar(
        "XAUUSDT",
        _bar(2000, open_=2005.0, high=2008.0, low=2001.0, close=2004.0),
    )
    assert broker.order(order.order_id)["fill_price"] == pytest.approx(2005.0)

    broker.on_closed_bar(
        "XAUUSDT",
        _bar(3000, open_=1980.0, high=1988.0, low=1975.0, close=1982.0),
    )
    saved = broker.order(order.order_id)
    assert saved is not None
    assert saved["exit_price"] == pytest.approx(1980.0)
    assert saved["exit_reason"] == "protective_stop"


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
