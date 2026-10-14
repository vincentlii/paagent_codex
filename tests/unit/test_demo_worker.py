from decimal import Decimal as D

import pytest

from tests.unit.test_demo_plans import intent, contract

NOW = 1800003600500


class Exchange:
    def __init__(self):
        self.calls = []
        self.orders = {}
        self.algos = {}
        self.position = "0"
        self.fail_entry = False
        self.fail_protection = False

    def snapshot(self, **kwargs):
        from pa_agent.execution.demo_contracts import DemoSnapshot

        return DemoSnapshot(
            observed_at_ms=NOW,
            server_time_ms=NOW,
            account_key="private-account",
            account_mode="2",
            position_mode="net_mode",
            equity="10000",
            available="10000",
            contracts=(contract(),),
            leverage={"BTC-USDT-SWAP": "3"},
            quotes={"BTC-USDT-SWAP": {"askPx": "50100", "bidPx": "50099", "ts": str(NOW)}},
            orders=tuple(
                o for o in self.orders.values() if o["state"] in {"live", "partially_filled"}
            ),
            algos=tuple(o for o in self.algos.values() if o["state"] == "live"),
            positions=(
                (
                    {
                        "instId": "BTC-USDT-SWAP",
                        "pos": self.position,
                        "posSide": "net",
                        "mgnMode": "isolated",
                        "lever": "3",
                        "upl": "0",
                        "imr": "100",
                    },
                )
                if self.position != "0"
                else ()
            ),
            coverage={
                k: True
                for k in ("account", "orders", "algos", "positions", "fills", "bills", "contracts")
            },
        )

    def place_limit(self, request):
        from pa_agent.execution.okx_demo import DemoRequestUnknown

        self.calls.append(("entry", request))
        self.orders[request.client_id] = dict(
            instId=request.inst_id,
            clOrdId=request.client_id,
            ordId="entry-1",
            state="live",
            accFillSz="0",
            side=request.side,
        )
        if self.fail_entry:
            raise DemoRequestUnknown("entry")
        return {"ordId": "entry-1"}

    def get_order(self, inst, cid=None, *, order_id=None):
        if order_id is not None:
            return next((row for row in self.orders.values() if row.get("ordId") == order_id), {})
        return self.orders.get(cid, {})

    def cancel_order(self, inst, cid):
        self.calls.append(("cancel", cid))
        return {"ordId": "entry-1"}  # Deliberately not a final state.

    def place_oco(self, **params):
        from pa_agent.execution.okx_demo import DemoRequestRejected

        self.calls.append(("oco", params))
        if self.fail_protection:
            raise DemoRequestRejected("51000")
        self.algos[params["client_id"]] = dict(
            algoId="a" + str(len(self.algos)),
            algoClOrdId=params["client_id"],
            instId=params["inst_id"],
            state="live",
            ordType="oco",
            slTriggerPx=str(params["stop"]),
            tpTriggerPx=str(params["tp1"]),
            closeFraction="1",
            reduceOnly="true",
            posSide="net",
        )
        return {"algoId": self.algos[params["client_id"]]["algoId"]}

    def get_algo(self, inst, cid):
        return self.algos.get(cid, {})

    def cancel_algo(self, inst, algo_id):
        self.calls.append(("cancel_algo", algo_id))
        return {"algoId": algo_id}

    def close_position(self, **params):
        self.calls.append(("emergency", params))
        return {"ordId": "emergency-1"}

    def close(self):
        pass


def setup(tmp_path, **config_changes):
    from pa_agent.execution.demo_contracts import DemoConfig, DemoPaths
    from pa_agent.execution.demo_spool import DemoSpool, atomic_json
    from pa_agent.execution.demo_store import DemoStore
    from pa_agent.execution.demo_worker import DemoWorker

    config = DemoConfig(
        enabled=True,
        entry_enabled=True,
        execution_target="okx_demo",
        account_key="private-account",
        cutover_id="epoch-1",
        cutover_started_at_ms=NOW - 1,
        accepted_symbols=("BTC-USDT-SWAP",),
        **config_changes
    )
    paths = DemoPaths(tmp_path)
    spool = DemoSpool(paths)
    atomic_json(paths.control, {"cutover_id": "epoch-1", "generated_at_ms": NOW, "running": True})
    store = DemoStore(paths.state_db)
    exchange = Exchange()
    worker = DemoWorker(config, paths, exchange, store, spool, now_ms=lambda: NOW)
    spool.publish(intent())
    return worker, exchange, store, spool


def test_partial_is_protected_before_cancel_and_prices_never_move(tmp_path):
    worker, exchange, store, _ = setup(tmp_path)
    worker.tick()
    entry = exchange.calls[0][1]
    assert entry.notional == 500 and entry.sz == 1
    exchange.orders[entry.client_id].update(
        state="partially_filled", accFillSz="0.4", avgPx="49900"
    )
    exchange.position = "0.4"
    worker.tick()
    assert [c[0] for c in exchange.calls] == ["entry", "oco", "cancel"]
    params = exchange.calls[1][1]
    assert params["stop"] == D("49000") and params["tp1"] == D("52000")
    row = store.row(intent().content_digest)
    assert row["entry_state"] == "cancel_pending" and row["protection"] == "protected"
    assert row["trade_state"] == "active"
    worker.close()


def test_unknown_submit_survives_restart_without_second_post(tmp_path):
    from pa_agent.execution.demo_worker import DemoWorker

    worker, exchange, store, spool = setup(tmp_path)
    exchange.fail_entry = True
    worker.tick()
    worker.close()
    restored = DemoWorker(worker.config, worker.paths, exchange, store, spool, now_ms=lambda: NOW)
    restored.tick()
    assert len([c for c in exchange.calls if c[0] == "entry"]) == 1
    assert store.row(intent().content_digest)["entry_state"] == "live"
    restored.close()


def test_protection_failure_does_not_claim_safe_exit(tmp_path):
    worker, exchange, store, _ = setup(tmp_path)
    worker.tick()
    cid = exchange.calls[0][1].client_id
    exchange.orders[cid].update(state="partially_filled", accFillSz="0.4")
    exchange.position = "0.4"
    exchange.fail_protection = True
    projection = worker.tick()
    assert projection["state"] == "blocked"
    assert [c[0] for c in exchange.calls][-3:] == ["oco", "cancel", "emergency"]
    row = store.row(intent().content_digest)
    assert row["protection"] == "protection_failed" and row["trade_state"] == "active"
    worker.tick()
    assert len([c for c in exchange.calls if c[0] == "emergency"]) == 1
    worker.close()


def test_late_fill_after_consumed_oco_uses_new_generation(tmp_path):
    worker, exchange, store, _ = setup(tmp_path)
    worker.tick()
    cid = exchange.calls[0][1].client_id
    exchange.orders[cid].update(state="partially_filled", accFillSz="0.4")
    exchange.position = "0.4"
    worker.tick()
    first = next(iter(exchange.algos))
    exchange.algos[first]["state"] = "effective"
    exchange.position = "0"
    worker.tick()
    assert store.row(intent().content_digest)["trade_state"] == "active"
    exchange.orders[cid].update(state="canceled", accFillSz="0.6")
    exchange.position = "0.2"
    worker.tick()
    assert len(exchange.algos) == 2 and len(set(exchange.algos)) == 2
    worker.close()


def test_two_workers_refused_and_no_credential_required_for_core(tmp_path):
    from pa_agent.execution.demo_worker import DemoWorker

    worker, exchange, store, spool = setup(tmp_path)
    with pytest.raises(RuntimeError, match="worker_already_running"):
        DemoWorker(worker.config, worker.paths, exchange, store, spool, now_ms=lambda: NOW)
    worker.close()


def test_new_no_trade_in_same_batch_prevents_old_entry_post(tmp_path):
    worker, exchange, store, spool = setup(tmp_path)
    spool.publish(
        intent(
            task_id="new-no-trade",
            source_bar_ms=1800003600000,
            disposition="no_trade",
            side=None,
            entry=None,
            stop=None,
            tp1=None,
        )
    )
    worker.tick()
    assert exchange.calls == []
    assert store.row(intent().content_digest)["reason"] == "superseded_before_submit"
    worker.close()


def test_positions_sample_before_terminal_entry_cannot_close_trade(tmp_path):
    worker, exchange, store, _ = setup(tmp_path)
    worker.tick()
    cid = exchange.calls[0][1].client_id
    exchange.orders[cid].update(state="filled", accFillSz="1")
    exchange.position = "0"  # Position endpoint sampled before latest fill.
    worker.tick()
    assert store.row(intent().content_digest)["trade_state"] == "active"
    exchange.position = "1"
    worker.tick()
    assert any(c[0] == "oco" for c in exchange.calls)
    worker.close()


def test_immediate_oco_trigger_is_bounded_and_never_recurses_posts(tmp_path):
    worker, exchange, store, _ = setup(tmp_path)
    worker.tick()
    cid = exchange.calls[0][1].client_id
    exchange.orders[cid].update(state="filled", accFillSz="1")
    exchange.position = "1"
    original = exchange.place_oco

    def triggered(**params):
        result = original(**params)
        exchange.algos[params["client_id"]]["state"] = "effective"
        return result

    exchange.place_oco = triggered
    worker.tick()
    assert len([c for c in exchange.calls if c[0] == "oco"]) == 1
    assert store.row(intent().content_digest)["trade_state"] == "active"
    worker.close()


@pytest.mark.parametrize(
    "side,entry,stop,tp1,ask,bid",
    [
        ("buy", "50000", "49000", "52000", "48000", "47999"),
        ("sell", "50000", "51000", "48000", "52001", "52000"),
    ],
)
def test_target_price_past_original_stop_rejected_without_repricing(
    tmp_path, side, entry, stop, tp1, ask, bid
):
    worker, exchange, store, spool = setup(tmp_path)
    original = exchange.snapshot
    exchange.snapshot = lambda **kwargs: original(**kwargs).model_copy(
        update={"quotes": {"BTC-USDT-SWAP": {"askPx": ask, "bidPx": bid, "ts": str(NOW)}}}
    )
    # Replace the initial unconsumed fixture with this case's independent task.
    for path in spool.paths.intents.glob("intent-*.json"):
        path.unlink()
    candidate = intent(side=side, entry=entry, stop=stop, tp1=tp1)
    spool.publish(candidate)
    worker.tick()
    assert exchange.calls == []
    assert store.row(candidate.content_digest)["reason"] == "target_price_beyond_original_stop"
    worker.close()


def test_db_failure_after_accepted_post_recovers_by_query_without_resend(tmp_path):
    import sqlite3
    from pa_agent.execution.demo_worker import DemoWorker

    worker, exchange, store, spool = setup(tmp_path)
    original = store.update_leg

    def fail_after_ack(cid, status, response=None):
        if status == "accepted":
            raise sqlite3.OperationalError("injected disk failure")
        return original(cid, status, response)

    store.update_leg = fail_after_ack
    with pytest.raises(sqlite3.OperationalError):
        worker.tick()
    worker.close()
    store.update_leg = original
    restored = DemoWorker(worker.config, worker.paths, exchange, store, spool, now_ms=lambda: NOW)
    restored.tick()
    assert len([c for c in exchange.calls if c[0] == "entry"]) == 1
    restored.close()


def test_db_failure_before_prepare_never_sends_entry(tmp_path):
    import sqlite3

    worker, exchange, store, _ = setup(tmp_path)

    def unavailable(*args):
        raise sqlite3.OperationalError("injected disk failure")

    store.save_prepared = unavailable
    with pytest.raises(sqlite3.OperationalError):
        worker.tick()
    assert exchange.calls == []
    worker.close()


def test_pause_still_protects_existing_partial_fill(tmp_path):
    from pa_agent.execution.demo_spool import atomic_json

    worker, exchange, store, spool = setup(tmp_path)
    worker.tick()
    request = exchange.calls[0][1]
    exchange.orders[request.client_id].update(state="partially_filled", accFillSz="0.4")
    exchange.position = "0.4"
    atomic_json(
        spool.paths.control, {"running": False, "cutover_id": "epoch-1", "generated_at_ms": NOW}
    )
    worker.tick()
    assert [c[0] for c in exchange.calls] == ["entry", "oco", "cancel"]
    worker.close()


def test_effective_algo_does_not_close_until_triggered_child_is_terminal(tmp_path):
    from pa_agent.execution.demo_worker import DemoWorker

    worker, exchange, store, spool = setup(tmp_path)
    worker.tick()
    cid = exchange.calls[0][1].client_id
    exchange.orders[cid].update(state="filled", accFillSz="1")
    exchange.position = "1"
    worker.tick()
    algo = next(iter(exchange.algos.values()))
    algo.update(state="effective", actualOrdId="child")
    exchange.orders["child"] = {
        "instId": "BTC-USDT-SWAP",
        "ordId": "child",
        "state": "live",
        "accFillSz": "0",
    }
    exchange.position = "0"
    # A fresh position sample after the parent terminal barrier, but child still live.
    snapshot = exchange.snapshot
    exchange.snapshot = lambda **kwargs: snapshot(**kwargs).model_copy(
        update={"observed_at_ms": NOW + 1}
    )
    worker.now_ms = lambda: NOW + 1
    worker.tick()
    assert store.row(intent().content_digest)["trade_state"] == "active"
    worker.close()
    restored = DemoWorker(
        worker.config, worker.paths, exchange, store, spool, now_ms=lambda: NOW + 2
    )
    restored.tick()
    assert store.row(intent().content_digest)["trade_state"] == "active"
    exchange.orders["child"].update(state="filled", accFillSz="1")
    restored.tick()
    assert store.row(intent().content_digest)["trade_state"] == "closed"
    restored.close()
