"""Frozen limit exits keep their own budget until actual exit, never a touch."""

import json
from decimal import Decimal as D

import httpx
import pytest

from pa_agent.ai.trade_execution_context import TradeExecutionContext
from tests.unit.test_demo_worker import NOW
from tests.unit.test_independent_demo import plan
from tests.unit.test_independent_demo import runtime as runtime
from tests.unit.test_okx_demo_client import client


def limit_context(**changes):
    return TradeExecutionContext.model_validate(
        dict(
            inst_id="BTC-USDT-SWAP",
            source_close_ms=1800003600000,
            limit_validity_hours=3,
            expires_at_ms=1800014400000,
            frozen_at_ms=NOW,
            fee_schedule=dict(
                inst_id="BTC-USDT-SWAP", maker="-0.0002", taker="-0.0005", observed_at_ms=NOW
            ),
            exit_order_type="limit",
        )
        | changes
    )


def limit_plan(name="a", **changes):
    return plan(
        name,
        trade_execution_context=limit_context().model_dump(mode="json"),
        demo_execution_policy=dict(notional_usdt="500", limit_validity_hours=3),
        **changes,
    )


def setup_limit(runtime):
    worker, exchange, store, spool = runtime
    original_snapshot, original_oco = exchange.snapshot, exchange.place_oco
    exchange.snapshot = lambda **kw: original_snapshot(**kw).model_copy(
        update={"fee_schedules": (limit_context().fee_schedule,)}
    )

    def oco(**params):
        result = original_oco(**params)
        if params.get("exit_order_type") == "limit":
            exchange.algos[params["client_id"]].update(
                slOrdPx=str(params["stop"]), tpOrdPx=str(params["tp1"])
            )
        return result

    exchange.place_oco = oco
    return worker, exchange, store, spool


def test_context_preserves_legacy_bytes_and_binds_new_execution_type():
    new = limit_context()
    old = limit_context(exit_order_type=None)
    assert "exit_order_type" not in old.model_dump(mode="json")
    assert "exit_order_type" not in old.identity()
    assert new.identity() != old.identity()
    assert new.treatment() != old.treatment()
    decision = dict(
        entry_price=50000, stop_loss_price=49000, take_profit_price=52000, estimated_win_rate=60
    )
    assert "limit_exit_nonfill" in new.commission_check(decision)["uncovered"]
    assert old.commission_check(decision)["uncovered"] == ["slippage", "funding"]
    assert "限价" in new.guidance() and "未成交" in new.guidance()


def test_new_host_context_freezes_limit_exit_but_old_intent_is_not_upgraded(runtime):
    from dataclasses import replace

    from pa_agent.execution.demo_dispatch import OfficialExecutionDispatcher
    from pa_agent.execution.demo_spool import atomic_json
    from tests.integration.conftest import make_frame

    worker, exchange, store, spool = setup_limit(runtime)
    projection = worker.tick()
    atomic_json(spool.paths.projection, projection)
    frame = replace(make_frame(), symbol="BTCUSDT")
    close = int(frame.bars[0].ts_open) + 3600000
    dispatcher = OfficialExecutionDispatcher(None, spool, worker.config, now_ms=lambda: close + 500)
    # Fee availability is unrelated to the immutable choice of exit type.
    frozen = dispatcher.trade_execution_context(frame)
    assert frozen["exit_order_type"] == "limit"
    assert plan().trade_execution_context is None
    assert "trade_execution_context" not in plan().digest_payload()


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_http_oco_and_emergency_use_original_limit_prices(side):
    calls = []
    c = client(
        lambda req: (
            calls.append(json.loads(req.content))
            or httpx.Response(
                200, json={"code": "0", "data": [{"sCode": "0", "algoId": "1", "ordId": "2"}]}
            )
        )
    )
    try:
        c.place_oco(
            inst_id="BTC-USDT-SWAP",
            side=side,
            stop=D("49000"),
            tp1=D("52000"),
            client_id="a",
            quantity=D(".4"),
            exit_order_type="limit",
        )
        c.close_position(
            inst_id="BTC-USDT-SWAP",
            side=side,
            quantity=D(".4"),
            client_id="b",
            limit_price=D("49000"),
        )
        assert calls[0]["slOrdPx"] == "49000" and calls[0]["tpOrdPx"] == "52000"
        assert calls[0]["slTriggerPxType"] == calls[0]["tpTriggerPxType"] == "last"
        assert calls[1]["ordType"] == "limit" and calls[1]["px"] == "49000"
        assert all(c["reduceOnly"] is True and D(c["sz"]) == D(".4") for c in calls)
    finally:
        c.close()


def test_triggered_unfilled_then_partial_limit_never_reposts_or_hands_off(runtime):
    worker, exchange, store, spool = setup_limit(runtime)
    p = limit_plan()
    spool.publish(p)
    worker.tick()
    exchange.fill(store, p)
    worker.tick()
    leg = next(l for l in store.legs(p.content_digest) if l["leg"] == "oco")
    assert json.loads(leg["params"])["exit_order_type"] == "limit"
    algo = exchange.algos[leg["client_id"]]
    assert algo["slOrdPx"] == str(p.stop) and algo["tpOrdPx"] == str(p.tp1)
    oid = "exit-limit"
    algo.update(state="effective", child_order_ids=json.dumps([oid]))
    exchange.orders[oid] = dict(
        instId=p.inst_id,
        ordId=oid,
        state="live",
        accFillSz="0",
        sz="1",
        side="sell",
        ordType="limit",
        px=str(p.stop),
        reduceOnly="true",
    )
    for _ in range(3):
        worker.tick()
    exposure = store.plan_exposure(p.content_digest)
    assert exposure.remaining == exposure.reserved_exit == D(1)
    exchange.orders[oid].update(state="partially_filled", accFillSz=".4")
    exchange.position = ".6"
    worker.request_shutdown()
    worker.tick()
    assert store.plan_exposure(p.content_digest).remaining == D(".6")
    assert store.plan_exposure(p.content_digest).reserved_exit == D(".6")
    assert not worker.can_handoff()
    assert len([c for c in exchange.calls if c[0] == "oco"]) == 1
    assert not any(c[0] == "emergency" for c in exchange.calls)


def test_limit_exit_is_frozen_across_codec_and_changes_treatment_not_market():
    from dataclasses import replace

    from pa_agent.config.settings import Settings
    from pa_agent.monitoring.frozen_task_codec import decode_task, encode_task
    from pa_agent.monitoring.task_factory import create_frozen_analysis_task
    from tests.integration.conftest import make_frame

    frame = replace(make_frame(), symbol="BTCUSDT")
    close = int(frame.bars[0].ts_open) + 3600000
    context = limit_context(
        source_close_ms=close,
        expires_at_ms=close + 3 * 3600000,
        frozen_at_ms=close + 500,
        fee_schedule=None,
    )
    tasks = [
        create_frozen_analysis_task(
            frame,
            {},
            Settings(),
            source="binance_usdm",
            analysis_fingerprint="a" * 64,
            trade_execution_context=c.model_dump(mode="json"),
        )
        for c in (context, context.model_copy(update={"exit_order_type": None}))
    ]
    restored = decode_task(*encode_task(tasks[0]))
    assert (
        restored.payload.frame.data_metadata["trade_execution_context"]["exit_order_type"]
        == "limit"
    )
    assert tasks[0].task_id != tasks[1].task_id
    assert tasks[0].cohort_id != tasks[1].cohort_id


def test_unknown_limit_fallback_keeps_budget_across_restart_and_only_adds_late_delta(runtime):
    from pa_agent.execution.demo_worker import DemoWorker
    from pa_agent.execution.okx_demo import DemoRequestUnknown

    worker, exchange, store, spool = setup_limit(runtime)
    exchange.fail_protection = True

    def unknown_close(**params):
        exchange.calls.append(("emergency", params))
        raise DemoRequestUnknown("emergency")

    exchange.close_position = unknown_close
    p = limit_plan()
    spool.publish(p)
    worker.tick()
    exchange.fill(store, p, ".4")
    worker.tick()
    worker.close()
    restored = DemoWorker(
        worker.config, worker.paths, exchange, store, spool, now_ms=lambda: exchange.now
    )
    try:
        for _ in range(3):
            restored.tick()
        assert len([c for c in exchange.calls if c[0] == "emergency"]) == 1
        exchange.fill(store, p, ".6")
        restored.tick()
        assert [v["quantity"] for k, v in exchange.calls if k == "emergency"] == [D(".4"), D(".2")]
        assert all(v["limit_price"] == p.stop for k, v in exchange.calls if k == "emergency")
        assert store.plan_exposure(p.content_digest).reserved_exit == D(".6")
    finally:
        restored.close()


@pytest.mark.parametrize(
    "field,value", [("px", "48000"), ("ordType", "market"), ("reduceOnly", "false"), ("sz", "2")]
)
def test_restarted_limit_fallback_never_accepts_changed_exchange_parameters(runtime, field, value):
    from pa_agent.execution.demo_worker import DemoWorker

    worker, exchange, store, spool = setup_limit(runtime)
    p = limit_plan()
    spool.publish(p)
    worker.tick()
    exchange.fill(store, p)
    # A durable pending limit exit is owned by this plan, even when its GET is contradictory.
    entry = next(l for l in store.legs(p.content_digest) if l["leg"] == "entry")
    worker._leg_query(p, entry)
    cid = store.prepare_leg(
        p.content_digest,
        "emergency",
        dict(quantity="1", side="sell", limit_price="49000"),
        generation=1,
    )
    store.update_leg(cid, "submission_unknown")
    exchange.orders[cid] = dict(
        instId=p.inst_id,
        clOrdId=cid,
        ordId="exit-limit",
        state="live",
        ordType="limit",
        px="49000",
        sz="1",
        accFillSz="0",
        side="sell",
        reduceOnly="true",
    ) | {field: value}
    worker.close()
    restored = DemoWorker(
        worker.config, worker.paths, exchange, store, spool, now_ms=lambda: exchange.now
    )
    try:
        restored.tick()
        assert restored.error == "limit_exit_parameters_mismatch"
        assert store.plan_exposure(p.content_digest).reserved_exit == 1
        assert not any(c[0] in {"oco", "emergency"} for c in exchange.calls)
    finally:
        restored.close()


@pytest.mark.parametrize("reject_close", [False, True])
def test_protection_rejection_records_code_and_limit_fallback_never_loops(runtime, reject_close):
    from pa_agent.execution.okx_demo import DemoRequestRejected

    worker, exchange, store, spool = setup_limit(runtime)
    exchange.fail_protection = True

    def close(**params):
        exchange.calls.append(("emergency", params))
        assert params["limit_price"] == D("49000")
        if reject_close:
            raise DemoRequestRejected("51008")
        exchange.orders[params["client_id"]] = dict(
            instId=params["inst_id"],
            clOrdId=params["client_id"],
            ordId="emergency-limit",
            state="live",
            accFillSz="0",
            sz=str(params["quantity"]),
            side=params["side"],
            ordType="limit",
            px=str(params["limit_price"]),
            reduceOnly="true",
        )
        return {"ordId": "emergency-limit"}

    exchange.close_position = close
    p = limit_plan()
    spool.publish(p)
    worker.tick()
    exchange.fill(store, p)
    for _ in range(4):
        worker.tick()
    legs = store.legs(p.content_digest)
    oco = next(l for l in legs if l["leg"] == "oco")
    assert json.loads(oco["response"])["sCode"] == "51000"
    emergency = next(l for l in legs if l["leg"] == "emergency")
    assert json.loads(emergency["params"])["limit_price"] == "49000"
    assert len([c for c in exchange.calls if c[0] == "emergency"]) == 1
    assert len([c for c in exchange.calls if c[0] == "oco"]) == 1
    assert store.row(p.content_digest)["trade_state"] == "active"
    assert not worker.can_handoff()
    if reject_close:
        assert json.loads(emergency["response"])["sCode"] == "51008"
        assert worker.error == "limit_exit_rejected"


@pytest.mark.parametrize("exit_kind", ["oco", "emergency"])
def test_live_child_limit_can_be_valued_without_calling_it_closed(exit_kind):
    from pa_agent.operations.demo_plan_view import plan_view
    from tests.unit.test_demo_plans import bind

    p = plan()
    legs = [
        dict(
            client_id="e",
            leg="entry",
            status="filled",
            response=json.dumps(dict(ordId="e", accFillSz="1")),
        ),
        dict(client_id="o", leg="oco", status="effective", response="{}"),
    ]
    children = [
        dict(
            parent_client_id="o",
            order_id="x",
            state="partially_filled",
            body=json.dumps(dict(ordId="x", accFillSz=".4", state="partially_filled")),
        )
    ]
    if exit_kind == "emergency":
        legs[1] = dict(
            client_id="x", leg="emergency", status="partially_filled", response=children[0]["body"]
        )
        children = []
    fills = [
        dict(
            instId=p.inst_id,
            ordId=oid,
            tradeId=oid,
            side=side,
            fillSz=qty,
            fillPx=price,
            fee="-.1",
            feeCcy="USDT",
        )
        for oid, side, qty, price in (("e", "buy", "1", "50000"), ("x", "sell", ".4", "51000"))
    ]
    result = plan_view(
        p,
        bind(),
        dict(filled="1", entry_state="filled"),
        legs,
        children,
        fills,
        dict(bidPx="50500", askPx="50502", ts=str(NOW)),
        NOW,
    )
    assert result["display_status"] == "open"
    assert result["sync_required"] is False and D(result["pnl"]) == D("6.8")


def test_limit_context_is_rejected_for_legacy_whole_position_protocol():
    from tests.unit.test_demo_plans import intent

    with pytest.raises(ValueError, match="limit_exit_requires_independent_plan"):
        intent(
            trade_execution_context=limit_context().model_dump(mode="json"),
            demo_execution_policy=dict(notional_usdt="500", limit_validity_hours=3),
        )


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_two_limit_plans_keep_independent_budgets_across_child_restart(runtime, side):
    from pa_agent.execution.demo_worker import DemoWorker

    worker, exchange, store, spool = setup_limit(runtime)
    changes = dict(
        side=side,
        stop="49000" if side == "buy" else "51000",
        tp1="52000" if side == "buy" else "48000",
    )
    a, b = limit_plan("a", **changes), limit_plan("b", **changes)
    spool.publish(a)
    spool.publish(b)
    worker.tick()
    exchange.fill(store, a)
    exchange.fill(store, b)
    worker.tick()
    a_oco = next(l for l in store.legs(a.content_digest) if l["leg"] == "oco")
    b_oco = next(l for l in store.legs(b.content_digest) if l["leg"] == "oco")
    exchange.algos[a_oco["client_id"]].update(state="effective", child_order_ids='["a-limit-exit"]')
    exchange.orders["a-limit-exit"] = dict(
        instId=a.inst_id,
        ordId="a-limit-exit",
        state="live",
        ordType="limit",
        px=str(a.stop),
        sz="1",
        accFillSz="0",
        reduceOnly="true",
        side="sell" if side == "buy" else "buy",
    )
    worker.close()
    restored = DemoWorker(
        worker.config, worker.paths, exchange, store, spool, now_ms=lambda: exchange.now
    )
    try:
        restored.tick()
        assert store.plan_exposure(a.content_digest).reserved_exit == 1
        assert store.plan_exposure(b.content_digest).protected == 1
        assert exchange.algos[b_oco["client_id"]]["state"] == "live"
        assert len([c for c in exchange.calls if c[0] == "oco"]) == 2
        assert not any(c[0] == "emergency" for c in exchange.calls)
    finally:
        restored.close()


@pytest.mark.parametrize("codex", [False, True])
def test_limit_execution_facts_are_delivered_only_to_stage2(codex):
    from dataclasses import replace

    from tests.integration.conftest import make_frame
    from tests.unit.test_joint_pipeline_runtime import joint_draft, run_joint

    frame = replace(make_frame(), symbol="BTCUSDT")
    close = int(frame.bars[0].ts_open) + 3600000
    context = limit_context(
        source_close_ms=close,
        expires_at_ms=close + 3 * 3600000,
        frozen_at_ms=close + 500,
        fee_schedule=None,
    )
    frame = replace(
        frame,
        data_metadata={
            **frame.data_metadata,
            "trade_execution_context": context.model_dump(mode="json"),
        },
    )
    requests = []
    record, calls, _ = run_joint(
        codex=codex, frame=frame, captured_requests=requests, stage2_drafts=[joint_draft()]
    )
    assert record.exception is None
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    assert record.role_runtime["trade_execution_context"]["exit_order_type"] == "limit"
    if codex:
        for request in requests:
            assert (
                "last触发后原价限价退出" in json.dumps(request.agent_context, ensure_ascii=False)
            ) == (request.role == "stage2")
    else:
        assert "last触发后原价限价退出" in json.dumps(record.stage2_messages, ensure_ascii=False)
        assert '"exit_order_type"' not in json.dumps(record.stage1_messages)
