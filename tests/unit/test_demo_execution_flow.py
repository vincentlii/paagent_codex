from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.unit.test_pipeline_paper_coordinator import record, T, H
from tests.unit.test_execution_plans import _spec
from tests.unit.test_demo_worker import Exchange, NOW


def source_record():
    r = record()
    spec = replace(_spec(), symbol="BTCUSDT", instrument_type="perpetual", tick_size=0.1)
    r.meta.symbol = "BTCUSDT"
    r.snapshot_context["instrument_spec"] = spec.to_dict()
    r.snapshot_context["source_identity"].update(
        requested_symbol="BTCUSDT", resolved_symbol="BTCUSDT", instrument_type="perpetual"
    )
    r.stage2_decision["decision"].update(
        entry_price=50000, stop_loss_price=49000, take_profit_price=52000
    )
    r.kline_data[0].update(open=50100, high=50500, low=49800, close=50100)
    return r, spec


def config():
    from pa_agent.execution.demo_contracts import DemoConfig

    return DemoConfig(
        enabled=True,
        entry_enabled=True,
        execution_target="okx_demo",
        cutover_id="epoch-1",
        cutover_started_at_ms=T + H,
        account_key="private-account",
        accepted_symbols=("BTC-USDT-SWAP",),
        independent_accepted_symbols=("BTC-USDT-SWAP",),
    )


def test_frozen_source_to_demo_ledger_projection_and_restart(tmp_path):
    from pa_agent.execution.demo_contracts import DemoPaths
    from pa_agent.execution.demo_dispatch import OfficialExecutionDispatcher
    from pa_agent.execution.demo_spool import DemoSpool
    from pa_agent.execution.demo_store import DemoStore
    from pa_agent.execution.demo_worker import DemoWorker
    from pa_agent.monitoring.analysis_runner import FrozenAnalysisPayload
    from pa_agent.operations.demo_trading import compose_trading_projection
    from pa_agent.operations.trading import safe_trading_projection

    cfg, paths = config(), DemoPaths(tmp_path)
    spool, exchange = DemoSpool(paths), Exchange()
    dispatcher = OfficialExecutionDispatcher(
        SimpleNamespace(enabled=True), spool, cfg, now_ms=lambda: NOW
    )
    dispatcher.publish_control(running=True)
    r, spec = source_record()
    before = deepcopy(vars(r))
    payload = FrozenAnalysisPayload(
        frame=None, execution_target="okx_demo", execution_epoch="epoch-1"
    )
    result = dispatcher.submit_official(
        r, spec, task_id="task", execution_payload=payload, execution_snapshot_hash="a" * 64
    )
    assert result.status == "queued", result.reason
    assert spool.pending()[0].market_digest == "a" * 64
    store = DemoStore(paths.state_db)
    worker = DemoWorker(cfg, paths, exchange, store, spool, now_ms=lambda: NOW)
    projection = worker.tick()
    assert projection["orders"][0]["notional"] == "500.00"
    assert exchange.calls[0][1].px == Decimal("50000")
    assert vars(r) == before
    worker.close()
    worker = DemoWorker(cfg, paths, exchange, store, spool, now_ms=lambda: NOW)
    worker.tick()
    dispatcher.submit_official(
        r, spec, task_id="task", execution_payload=payload, execution_snapshot_hash="a" * 64
    )
    worker.tick()
    assert len([c for c in exchange.calls if c[0] == "entry"]) == 1
    combined = safe_trading_projection(
        compose_trading_projection(
            {"schema_version": 1, "account": {"settled_balance": 99999}},
            projection,
            selected_backend="okx_demo",
            now_ms=NOW,
        )
    )
    assert combined["demo"]["account"]["equity"] == "10000"
    assert combined["selected_backend"] == "okx_demo"
    worker.close()


@pytest.mark.parametrize(
    "change,reason",
    [
        ("failed", "failed analysis"),
        ("research", "experiment arm"),
        ("source", "source_identity"),
        ("old_epoch", "skipped_cutover"),
        ("expired", "expired"),
    ],
)
def test_source_compiler_reuses_original_official_qualification(change, reason):
    from pa_agent.execution.demo_plans import compile_demo_intent

    r, spec = source_record()
    epoch, now = "epoch-1", NOW
    if change == "failed":
        r.exception = {"error": "failed"}
    elif change == "research":
        r.experiment_context = {"arm_id": "A1"}
    elif change == "source":
        r.snapshot_context["source_identity"]["resolved_symbol"] = "ETHUSDT"
    elif change == "old_epoch":
        epoch = "old"
    elif change == "expired":
        now = T + 2 * H
    with pytest.raises(ValueError, match=reason):
        compile_demo_intent(
            r,
            spec,
            task_id="task",
            config=config(),
            frozen_execution_epoch=epoch,
            now_ms=now,
            market_snapshot_hash="a" * 64,
        )


@pytest.mark.parametrize(
    "order,disposition", [("市价单", "rejected"), ("突破单", "rejected"), ("不下单", "no_trade")]
)
def test_unsupported_orders_not_converted_and_no_trade_is_delivered(order, disposition):
    from pa_agent.execution.demo_plans import compile_demo_intent

    r, spec = source_record()
    r.stage2_decision["decision"]["order_type"] = order
    i = compile_demo_intent(
        r,
        spec,
        task_id="task",
        config=config(),
        frozen_execution_epoch="epoch-1",
        now_ms=NOW,
        market_snapshot_hash="a" * 64,
    )
    assert i.disposition == disposition and i.entry is None and i.stop is None and i.tp1 is None
