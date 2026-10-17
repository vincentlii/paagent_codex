import pytest

from tests.unit.test_demo_plans import intent, bind


def test_durable_idempotency_and_unknown_leg_is_never_reprepared(tmp_path):
    from pa_agent.execution.demo_store import DemoStore

    path = tmp_path / "demo.sqlite3"
    store = DemoStore(path)
    i = intent()
    assert store.register_intent(i) is None
    store.save_prepared(i, bind())
    cid = store.prepare_leg(i.content_digest, "entry", {"px": "50000"}, generation=1)
    again = DemoStore(path)
    assert again.register_intent(i).disposition == "accepted"
    assert again.prepare_leg(i.content_digest, "entry", {"px": "50000"}, generation=1) == cid
    with pytest.raises(ValueError, match="semantics"):
        again.prepare_leg(i.content_digest, "entry", {"px": "49999"}, generation=1)
    with pytest.raises(ValueError, match="identity"):
        again.register_intent(intent(stop="48000"))
    assert store.quick_check()


def test_old_no_trade_does_not_advance_watermark_or_supersede(tmp_path):
    from pa_agent.execution.demo_store import DemoStore

    store = DemoStore(tmp_path / "demo.sqlite3")
    store.register_intent(intent())
    stale = intent(
        task_id="old",
        source_bar_ms=1799996400000,
        disposition="no_trade",
        side=None,
        entry=None,
        stop=None,
        tp1=None,
    )
    assert store.register_intent(stale).reason == "stale_decision"
    assert store.pending_intents() == (intent(),)


def test_cumulative_fill_monotone_and_partial_reservation_not_double_counted(tmp_path):
    from pa_agent.execution.demo_store import DemoStore

    store = DemoStore(tmp_path / "demo.sqlite3")
    i = intent()
    store.register_intent(i)
    store.save_prepared(i, bind())
    store.update(i.content_digest, filled="0.4", entry_state="partially_filled")
    store.update(i.content_digest, filled="0.1", entry_state="live")
    row = store.row(i.content_digest)
    assert row["filled"] == "0.4" and row["entry_state"] == "partially_filled"
    assert abs(float(store.reserved_margin()) - 100) < 0.00001


def test_late_smaller_fill_observation_preserves_paired_average_price(tmp_path):
    from pa_agent.execution.demo_store import DemoStore
    import json

    store = DemoStore(tmp_path / "demo.sqlite3")
    i = intent()
    store.register_intent(i)
    store.save_prepared(i, bind(i))
    cid = store.prepare_leg(i.content_digest, "entry", {}, generation=1)
    store.update_leg(cid, "live", {"accFillSz": "0.4", "avgPx": "50000", "fee": "-0.1"})
    store.update_leg(cid, "live", {"accFillSz": "0.2", "avgPx": "49000", "fee": "-0.05"})
    observation = json.loads(store.legs(i.content_digest)[0]["response"])
    assert observation["accFillSz"] == "0.4"
    assert observation["avgPx"] == "50000"
    assert observation["fee"] == "-0.1"


def test_leg_generation_cannot_reuse_consumed_protection(tmp_path):
    from pa_agent.execution.demo_store import DemoStore

    store = DemoStore(tmp_path / "demo.sqlite3")
    i = intent()
    store.register_intent(i)
    store.save_prepared(i, bind())
    first = store.prepare_leg(i.content_digest, "oco", {"stop": "49000"}, generation=1)
    with pytest.raises(ValueError, match="previous_leg_not_terminal"):
        store.prepare_leg(i.content_digest, "oco", {"stop": "49000"}, generation=2)
    store.update_leg(first, "effective")
    assert store.prepare_leg(i.content_digest, "oco", {"stop": "49000"}, generation=2) != first


def test_foreign_database_is_rejected_without_schema_or_journal_changes(tmp_path):
    import sqlite3
    from pa_agent.execution.demo_store import DemoStore

    path = tmp_path / "original.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE analysis_runs(run_id TEXT)")
        db.execute("INSERT INTO analysis_runs VALUES('original')")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="foreign_database"):
        DemoStore(path)
    assert path.read_bytes() == before


def test_cursors_survive_restart_and_unknown_reservation_needs_balance_snapshot(tmp_path):
    from pa_agent.execution.demo_store import DemoStore
    from tests.unit.test_demo_worker import Exchange

    path = tmp_path / "demo.sqlite3"
    store = DemoStore(path)
    snapshot = (
        Exchange()
        .snapshot()
        .model_copy(
            update={
                "bills": ({"billId": "100"},),
                "fills": ({"instId": "BTC-USDT-SWAP", "tradeId": "7", "billId": "99"},),
            }
        )
    )
    store.apply_snapshot(snapshot)
    assert DemoStore(path).sync_cursors() == ("100", {"SWAP": "99"})
    i, request = intent(), bind()
    store.register_intent(i)
    store.save_prepared(i, request)
    store.prepare_leg(i.content_digest, "entry", {}, generation=1)
    store.update(i.content_digest, entry_state="cancel_pending")
    assert store.unreflected_margin(snapshot) == request.margin
    reflected = snapshot.model_copy(update={"orders": ({"clOrdId": request.client_id},)})
    assert store.unreflected_margin(reflected) == 0
