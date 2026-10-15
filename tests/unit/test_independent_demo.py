"""Independent plans share net exposure, never each other's exit quantity."""

import json
from decimal import Decimal as D

import pytest

from tests.unit.test_demo_plans import intent
from tests.unit.test_demo_worker import Exchange, NOW


def plan(name="a", **changes):
    if "expires_at_ms" in changes:
        changes.setdefault("fresh_until_ms", changes["expires_at_ms"])
    return intent(schema_version="okx_demo_intent_v2", task_id=name, run_id=name, **changes)


class NetExchange(Exchange):
    def __init__(self):
        super().__init__()
        self.now = NOW
        self.unknown_oco = False

    def snapshot(self, **kwargs):
        s = super().snapshot(**kwargs)
        return s.model_copy(
            update={
                "observed_at_ms": self.now,
                "server_time_ms": self.now,
                "quotes": {
                    "BTC-USDT-SWAP": {"askPx": "50100", "bidPx": "50099", "ts": str(self.now)}
                },
            }
        )

    def place_limit(self, request):
        result = super().place_limit(request)
        self.orders[request.client_id]["ordId"] = request.client_id
        return {"ordId": request.client_id}

    def place_oco(self, **params):
        result = super().place_oco(**params)
        self.algos[params["client_id"]].update(
            closeFraction="",
            sz=str(params["quantity"]),
            side=params["side"],
            slOrdPx="-1",
            tpOrdPx="-1",
            slTriggerPxType="last",
            tpTriggerPxType="last",
        )
        if self.unknown_oco:
            from pa_agent.execution.okx_demo import DemoRequestUnknown

            raise DemoRequestUnknown("oco")
        return result

    def close_position(self, **params):
        self.calls.append(("emergency", params))
        cid = params["client_id"]
        self.orders[cid] = dict(
            instId=params["inst_id"],
            clOrdId=cid,
            ordId=cid,
            state="filled",
            accFillSz=str(params["quantity"]),
        )
        sign = D(1) if params["side"] == "sell" else D(-1)
        self.position = str(D(self.position) - sign * params["quantity"])
        return {"ordId": cid}

    def fill(self, store, p, quantity="1"):
        leg = next(x for x in store.legs(p.content_digest) if x["leg"] == "entry")
        row = self.orders[leg["client_id"]]
        delta = D(quantity) - D(row["accFillSz"])
        self.position = str(D(self.position) + delta * (1 if p.side == "buy" else -1))
        row.update(state="filled" if D(quantity) == 1 else "partially_filled", accFillSz=quantity)

    def trigger(self, store, p):
        leg = next(x for x in store.legs(p.content_digest) if x["leg"] == "oco")
        algo = self.algos[leg["client_id"]]
        qty = D(algo["sz"])
        oid = "exit-" + leg["client_id"]
        self.orders[oid] = dict(instId=p.inst_id, ordId=oid, state="filled", accFillSz=str(qty))
        algo.update(state="effective", child_order_ids=json.dumps([oid]))
        self.position = str(D(self.position) - qty * (1 if p.side == "buy" else -1))


@pytest.fixture
def runtime(tmp_path):
    from pa_agent.execution.demo_contracts import DemoConfig, DemoPaths
    from pa_agent.execution.demo_spool import DemoSpool, atomic_json
    from pa_agent.execution.demo_store import DemoStore
    from pa_agent.execution.demo_worker import DemoWorker

    paths = DemoPaths(tmp_path / "execution")
    cfg = DemoConfig(
        enabled=True,
        entry_enabled=True,
        execution_target="okx_demo",
        account_key="private-account",
        cutover_id="epoch-1",
        cutover_started_at_ms=NOW - 1,
        accepted_symbols=("BTC-USDT-SWAP",),
        independent_accepted_symbols=("BTC-USDT-SWAP",),
    )
    spool = DemoSpool(paths)
    atomic_json(paths.control, {"cutover_id": "epoch-1", "generated_at_ms": NOW, "running": True})
    store, exchange = DemoStore(paths.state_db), NetExchange()
    worker = DemoWorker(cfg, paths, exchange, store, spool, now_ms=lambda: exchange.now)
    yield worker, exchange, store, spool
    worker.close()


def test_new_plan_and_no_trade_do_not_cancel_old_entry(runtime):
    worker, exchange, store, spool = runtime
    a, b = plan(), plan("b", source_bar_ms=1800000000100)
    spool.publish(a)
    worker.tick()
    spool.publish(b)
    worker.tick()
    spool.publish(plan("wait", disposition="no_trade", side=None, entry=None, stop=None, tp1=None))
    worker.tick()
    assert len([c for c in exchange.calls if c[0] == "entry"]) == 2
    assert not any(c[0] == "cancel" for c in exchange.calls)
    assert store.row(a.content_digest)["trade_state"] == "active"


def test_each_plan_protects_its_quantity_and_one_exit_keeps_the_other(runtime):
    worker, exchange, store, spool = runtime
    a, b = plan(), plan("b", stop="48000", tp1="53000")
    for p in (a, b):
        spool.publish(p)
    worker.tick()
    for p in (a, b):
        exchange.fill(store, p)
    worker.tick()
    protections = [c[1] for c in exchange.calls if c[0] == "oco"]
    assert len(protections) == 2 and all(p["quantity"] == 1 for p in protections)
    assert {p["stop"] for p in protections} == {D(49000), D(48000)}
    exchange.trigger(store, a)
    worker.tick()
    exchange.now += 10
    worker.tick()
    assert D(exchange.position) == 1
    assert store.row(a.content_digest)["trade_state"] == "closed"
    assert store.row(b.content_digest)["trade_state"] == "active"
    assert not any(c[0] == "emergency" for c in exchange.calls)


def test_opposite_pending_entry_rejected_even_before_a_position_exists(runtime):
    worker, exchange, store, spool = runtime
    a = plan()
    spool.publish(a)
    worker.tick()
    b = plan("b", side="sell", stop="51000", tp1="48000")
    spool.publish(b)
    worker.tick()
    assert len([c for c in exchange.calls if c[0] == "entry"]) == 1
    assert not any(c[0] == "cancel" for c in exchange.calls)
    assert store.row(b.content_digest)["trade_state"] == "rejected"


def test_unknown_oco_never_adds_emergency_that_can_close_another_plan(runtime):
    worker, exchange, store, spool = runtime
    a, b = plan(), plan("b")
    for p in (a, b):
        spool.publish(p)
    worker.tick()
    for p in (a, b):
        exchange.fill(store, p)
    exchange.unknown_oco = True
    worker.tick()
    assert not any(c[0] == "emergency" for c in exchange.calls)
    worker.tick()
    assert len([c for c in exchange.calls if c[0] == "oco"]) == 2
    assert D(exchange.position) == 2


def test_each_expiry_only_cancels_its_own_unfilled_order(runtime):
    worker, exchange, store, spool = runtime
    a, b = plan(expires_at_ms=NOW + 100), plan("b", expires_at_ms=NOW + 1000)
    for p in (a, b):
        spool.publish(p)
    worker.tick()
    exchange.now += 200
    worker.tick()
    a_entry = next(l for l in store.legs(a.content_digest) if l["leg"] == "entry")
    assert [c[1] for c in exchange.calls if c[0] == "cancel"] == [a_entry["client_id"]]


def test_same_batch_opposite_is_rejected_in_fifo_order(runtime):
    worker, exchange, store, spool = runtime
    a, b = plan(), plan("b", side="sell", stop="51000", tp1="48000")
    spool.publish(a)
    spool.publish(b)
    worker.tick()
    # Spool ordering is its deterministic arrival sequence, not task lexicographic order.
    assert len([c for c in exchange.calls if c[0] == "entry"]) == 1
    active = store.pending_intents()
    assert len([p for p in active if store.row(p.content_digest)["request"]]) == 1


def test_partial_and_late_fill_adds_only_unprotected_delta(runtime):
    worker, exchange, store, spool = runtime
    a = plan()
    spool.publish(a)
    worker.tick()
    exchange.fill(store, a, ".4")
    worker.tick()
    exchange.fill(store, a, ".6")
    worker.tick()
    quantities = [p["quantity"] for k, p in exchange.calls if k == "oco"]
    assert quantities == [D(".4"), D(".2")]
    assert store.plan_exposure(a.content_digest).protected == D(".6")


def test_unknown_oco_absent_after_restart_reserves_budget(runtime):
    from pa_agent.execution.demo_worker import DemoWorker

    worker, exchange, store, spool = runtime
    a = plan()
    spool.publish(a)
    worker.tick()
    exchange.fill(store, a)
    exchange.unknown_oco = True
    worker.tick()
    exchange.algos.clear()
    worker.close()
    restored = DemoWorker(
        worker.config, worker.paths, exchange, store, spool, now_ms=lambda: exchange.now
    )
    try:
        restored.tick()
        restored.tick()
        assert len([c for c in exchange.calls if c[0] == "oco"]) == 1
        assert not any(c[0] == "emergency" for c in exchange.calls)
        assert store.plan_exposure(a.content_digest).reserved_exit == 1
    finally:
        restored.close()


def test_stale_net_snapshot_never_infers_exit_or_submits_new_entry(runtime):
    worker, exchange, store, spool = runtime
    a = plan()
    spool.publish(a)
    worker.tick()
    exchange.fill(store, a)
    original = exchange.snapshot
    exchange.snapshot = lambda **kw: original(**kw).model_copy(update={"positions": ()})
    spool.publish(plan("b"))
    result = worker.tick()
    assert len([c for c in exchange.calls if c[0] == "entry"]) == 1
    assert not any(c[0] in {"oco", "emergency"} for c in exchange.calls)
    assert store.row(a.content_digest)["trade_state"] == "active"
    assert result["state"] == "blocked"


def test_old_symbol_acceptance_does_not_enable_independent_orders(runtime):
    worker, exchange, store, spool = runtime
    worker.config = worker.config.model_copy(update={"independent_accepted_symbols": ()})
    p = plan()
    spool.publish(p)
    worker.tick()
    assert not exchange.calls
    assert store.row(p.content_digest)["reason"] == "independent_protection_not_live_accepted"


def test_duplicate_delivery_does_not_post_twice(runtime):
    worker, exchange, store, spool = runtime
    p = plan()
    spool.publish(p)
    worker.tick()
    spool.publish(p)
    worker.tick()
    assert len([c for c in exchange.calls if c[0] == "entry"]) == 1


def test_projection_quantities_and_risk_do_not_double_count_net_position(runtime):
    worker, exchange, store, spool = runtime
    a, b = plan(), plan("b")
    for p in (a, b):
        spool.publish(p)
    worker.tick()
    for p in (a, b):
        exchange.fill(store, p)
    worker.tick()
    exchange.trigger(store, a)
    worker.tick()
    exchange.now += 10
    projection = worker.tick()
    rows = {r["task_id"]: r for r in projection["orders"]}
    assert rows["a"]["exited_quantity"] == "1" and rows["a"]["remaining_quantity"] == "0"
    assert rows["b"]["exited_quantity"] == "0" and rows["b"]["remaining_quantity"] == "1"
    assert len(projection["positions"]) == 1 and D(projection["positions"][0]["pos"]) == 1
    assert D(projection["account"]["planned_stop_loss"]) == 10


def test_legacy_v1_active_plan_blocks_new_v2_on_same_contract(runtime):
    worker, exchange, store, spool = runtime
    config = worker.config
    worker.config = config.model_copy(update={"independent_accepted_symbols": ()})
    old = intent()
    spool.publish(old)
    worker.tick()
    worker.config = config
    p = plan()
    spool.publish(p)
    worker.tick()
    assert len([c for c in exchange.calls if c[0] == "entry"]) == 1
    assert store.row(p.content_digest)["reason"] == "legacy_plan_active"


def test_child_unknown_cannot_release_budget_for_another_exit(runtime):
    worker, exchange, store, spool = runtime
    p = plan()
    spool.publish(p)
    worker.tick()
    exchange.fill(store, p)
    worker.tick()
    exchange.trigger(store, p)
    oid = next(k for k in exchange.orders if k.startswith("exit-"))
    del exchange.orders[oid]
    worker.tick()
    exchange.now += 10
    worker.tick()
    assert store.plan_exposure(p.content_digest).uncertain
    assert store.plan_exposure(p.content_digest).reserved_exit == 1
    assert not any(c[0] == "emergency" for c in exchange.calls)
    assert store.row(p.content_digest)["trade_state"] == "active"


def test_immediate_wrong_oco_is_not_reported_as_verified_protection(runtime):
    worker, exchange, store, spool = runtime
    p = plan()
    spool.publish(p)
    worker.tick()
    exchange.fill(store, p)
    original = exchange.get_algo
    exchange.get_algo = lambda inst, cid: original(inst, cid) | {"sz": "10"}
    worker.tick()
    assert store.row(p.content_digest)["protection"] == "protection_failed"
    assert not any(c[0] == "emergency" for c in exchange.calls)


def test_two_entries_cannot_spend_same_unreflected_margin(runtime):
    worker, exchange, store, spool = runtime
    original = exchange.snapshot
    exchange.snapshot = lambda **kw: original(**kw).model_copy(update={"available": D("200")})
    a, b = plan(), plan("b")
    spool.publish(a)
    spool.publish(b)
    worker.tick()
    assert len([c for c in exchange.calls if c[0] == "entry"]) == 1
    assert store.row(b.content_digest)["trade_state"] == "rejected"


def test_newly_observed_full_fill_cannot_release_stale_balance_reservation(runtime):
    worker, exchange, store, spool = runtime
    original = exchange.snapshot
    exchange.snapshot = lambda **kw: original(**kw).model_copy(update={"available": D("200")})
    a, b = plan(), plan("b")
    spool.publish(a)
    worker.tick()
    exchange.fill(store, a)
    spool.publish(b)
    worker.tick()
    assert len([c for c in exchange.calls if c[0] == "entry"]) == 1


def test_unknown_exit_of_a_does_not_leave_confirmed_b_unprotected(runtime):
    worker, exchange, store, spool = runtime
    a, b = plan(), plan("b")
    for p in (a, b):
        spool.publish(p)
    worker.tick()
    for p in (a, b):
        exchange.fill(store, p)
    exchange.unknown_oco = True
    worker.tick()
    assert len([c for c in exchange.calls if c[0] == "oco"]) == 2
    exchange.algos.clear()
    worker.tick()
    assert len([c for c in exchange.calls if c[0] == "oco"]) == 2
    assert not any(c[0] == "emergency" for c in exchange.calls)


def test_late_fill_delta_can_be_protected_without_reusing_unknown_exit_budget(runtime):
    worker, exchange, store, spool = runtime
    p = plan()
    spool.publish(p)
    worker.tick()
    exchange.fill(store, p, ".4")
    exchange.unknown_oco = True
    worker.tick()
    exchange.algos.clear()
    exchange.fill(store, p, ".6")
    worker.tick()
    assert [v["quantity"] for k, v in exchange.calls if k == "oco"] == [D(".4"), D(".2")]
    assert store.plan_exposure(p.content_digest).reserved_exit == D(".6")
    assert not any(c[0] == "emergency" for c in exchange.calls)


def test_late_fill_emergency_uses_only_new_unreserved_quantity(runtime):
    from pa_agent.execution.okx_demo import DemoRequestUnknown

    worker, exchange, store, spool = runtime

    def unknown_close(**params):
        exchange.calls.append(("emergency", params))
        raise DemoRequestUnknown("emergency")

    exchange.close_position = unknown_close
    exchange.fail_protection = True
    p = plan()
    spool.publish(p)
    worker.tick()
    exchange.fill(store, p, ".4")
    worker.tick()
    exchange.fill(store, p, ".6")
    worker.tick()
    assert [v["quantity"] for k, v in exchange.calls if k == "emergency"] == [D(".4"), D(".2")]
    assert store.plan_exposure(p.content_digest).reserved_exit == D(".6")


def test_live_protection_query_unknown_blocks_entry_across_restart(runtime):
    from pa_agent.execution.demo_worker import DemoWorker

    worker, exchange, store, spool = runtime
    a, b = plan(), plan("b")
    spool.publish(a)
    worker.tick()
    exchange.fill(store, a)
    worker.tick()
    assert store.row(a.content_digest)["protection"] == "protected"
    saved = dict(exchange.algos)
    exchange.algos.clear()
    spool.publish(b)
    projection = worker.tick()
    assert store.row(a.content_digest)["protection"] == "protection_unknown"
    assert store.plan_exposure(a.content_digest).reserved_exit == 1
    assert len([c for c in exchange.calls if c[0] == "entry"]) == 1
    assert projection["state"] == "blocked"
    worker.close()
    restored = DemoWorker(
        worker.config, worker.paths, exchange, store, spool, now_ms=lambda: exchange.now
    )
    try:
        restored.tick()
        assert len([c for c in exchange.calls if c[0] == "entry"]) == 1
        assert len([c for c in exchange.calls if c[0] == "oco"]) == 1
        assert not any(c[0] == "emergency" for c in exchange.calls)
        exchange.algos.update(saved)
        restored.tick()
        assert store.row(a.content_digest)["protection"] == "protected"
        assert len([c for c in exchange.calls if c[0] == "entry"]) == 2
    finally:
        restored.close()
