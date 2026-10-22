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


def test_old_ledger_derives_recovery_scope_without_rewriting_cursors(tmp_path):
    from pa_agent.execution.demo_store import DemoStore
    from tests.unit.test_demo_worker import Exchange

    store = DemoStore(tmp_path / "demo.sqlite3")
    old = Exchange().snapshot().model_copy(update={
        "fills": ({"instId": "BTC-USDT-SWAP", "tradeId": "7", "billId": "99", "ts": "1799000000000"},),
    })
    store.apply_snapshot(old)
    state = DemoStore(store.path).sync_state()
    assert state["fills"]["account_key"] == old.account_key
    assert state["fills"]["last_id"] == "99"
    assert state["fills"]["through_ms"] == 0
    assert state["fills"]["origin_ms"] == 1799000000000
    assert store.sync_cursors() == (None, {"SWAP": "99"})


@pytest.mark.parametrize("field,new_value", [("fillPx", "50001"), ("fillSz", "0.2"), ("fee", "-0.02"),
                                               ("ordId", "another-order"), ("billId", "100")])
def test_conflicting_fill_is_audited_without_overwrite_or_losing_live_snapshot(tmp_path, field, new_value):
    from pa_agent.execution.demo_store import DemoStore
    from tests.unit.test_demo_worker import Exchange
    from tests.unit.test_okx_demo_client import _event

    store = DemoStore(tmp_path / "demo.sqlite3")
    first = Exchange().snapshot().model_copy(update={"fills": (_event(99, 1800003600000),)})
    store.apply_snapshot(first)
    changed = first.model_copy(update={"fills": (first.fills[0] | {field: new_value},),
                                       "positions": ({"instId": "BTC-USDT-SWAP", "pos": "0.1"},)})
    accepted = store.apply_snapshot(changed)
    assert accepted.coverage["fills"] is False
    assert accepted.positions == changed.positions
    with store.connect() as db:
        import json
        original = json.loads(db.execute("SELECT body FROM fills").fetchone()[0])
        assert original[field] == first.fills[0][field]
        assert db.execute("SELECT COUNT(*) FROM events WHERE kind='exchange_event_conflict'").fetchone()[0] == 1
    assert store.sync_cursors() == (None, {"SWAP": "99"})
    assert store.apply_snapshot(first).coverage["fills"] is False


def test_sync_coverage_is_persisted_with_events_and_resumed_after_restart(tmp_path):
    from pa_agent.execution.demo_store import DemoStore
    from tests.unit.test_okx_demo_client import _event, _event_client, DAY_MS

    clock = [1800003600500]
    events = {"/api/v5/trade/fills-history": [_event(i, clock[0] - 4 * DAY_MS + i) for i in range(1, 452)]}
    c, _ = _event_client(events, clock)
    store = DemoStore(tmp_path / "demo.sqlite3")
    try:
        first = c.snapshot(since_fill_ids={"SWAP": "1"})
        store.apply_snapshot(first)
        store = DemoStore(store.path)
        assert store.sync_state()["fills"]["pending"]["after"] == "52"
        clock[0] += 20_000
        events["/api/v5/trade/fills"] = [_event(452, clock[0] - 1)]
        second = c.snapshot(sync_state=store.sync_state())
        assert second.coverage["fills"]
        store.apply_snapshot(second)
        assert store.sync_cursors() == (None, {"SWAP": "452"})
        assert store.sync_state()["fills"]["through_ms"] == clock[0]
        with store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 452
            assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        clock[0] += 4 * DAY_MS
        after_idle = c.snapshot(sync_state=DemoStore(store.path).sync_state())
        assert after_idle.coverage["fills"]
    finally:
        c.close()


def test_bill_conflict_is_sticky_and_numeric_format_is_not_a_conflict(tmp_path):
    from pa_agent.execution.demo_store import DemoStore
    from tests.unit.test_demo_worker import Exchange
    from tests.unit.test_okx_demo_client import _event

    store = DemoStore(tmp_path / "demo.sqlite3")
    bill = _event(7, 1800003600000, stream="bills")
    snapshot = Exchange().snapshot().model_copy(update={"bills": (bill,)})
    store.apply_snapshot(snapshot)
    formatted = snapshot.model_copy(update={"bills": (bill | {"fee": "-0.0100"},)})
    assert store.apply_snapshot(formatted).coverage["bills"]
    conflict = snapshot.model_copy(update={"bills": (bill | {"balChg": "-1"},)})
    assert not store.apply_snapshot(conflict).coverage["bills"]
    assert not DemoStore(store.path).apply_snapshot(snapshot).coverage["bills"]
    assert store.sync_cursors()[0] == "7"


def test_legacy_recovery_starts_at_trusted_last_event_not_ancient_ledger_start(tmp_path):
    from pa_agent.execution.demo_store import DemoStore
    from tests.unit.test_demo_worker import Exchange
    from tests.unit.test_okx_demo_client import _event, DAY_MS

    store = DemoStore(tmp_path / "demo.sqlite3")
    snapshot = Exchange().snapshot().model_copy(update={"fills": (_event(99, 1800003600000),)})
    store.apply_snapshot(snapshot)
    with store.connect() as db:
        db.execute("UPDATE metadata SET value=? WHERE key='ledger_start_ms'", (str(1800003600000 - 120 * DAY_MS),))
    assert store.sync_state()["fills"]["origin_ms"] == 1800003600000


def test_completed_backfill_watermark_commits_while_new_head_is_still_paginating(tmp_path):
    from pa_agent.execution.demo_store import DemoStore
    from tests.unit.test_okx_demo_client import _event, _event_client, DAY_MS

    clock = [1800003600500]
    events = {"/api/v5/trade/fills-history": [_event(i, clock[0] - 4 * DAY_MS + i) for i in range(1, 452)]}
    c, _ = _event_client(events, clock)
    store = DemoStore(tmp_path / "demo.sqlite3")
    try:
        first = c.snapshot(since_fill_ids={"SWAP": "1"})
        store.apply_snapshot(first)
        old_end = first.event_sync["fills"].pending.end_ms
        clock[0] += 20_000
        events["/api/v5/trade/fills"] = [_event(i, clock[0] - 1) for i in range(452, 803)]
        second = c.snapshot(sync_state=store.sync_state())
        assert not second.coverage["fills"]
        store.apply_snapshot(second)
        progress = store.sync_state()["fills"]
        assert progress["through_ms"] == old_end
        assert progress["pending"]["end_ms"] == clock[0]
        clock[0] += 20_000
        third = c.snapshot(sync_state=DemoStore(store.path).sync_state())
        assert third.coverage["fills"]
        store.apply_snapshot(third)
        assert store.sync_cursors()[1] == {"SWAP": "802"}
    finally:
        c.close()


@pytest.mark.parametrize("defect", ["account", "stream"])
def test_sync_state_rejects_valid_shape_with_wrong_persisted_identity(tmp_path, defect):
    import json
    from pa_agent.execution.demo_store import DemoStore
    from pa_agent.execution.demo_contracts import DemoEventSync
    from tests.unit.test_demo_worker import Exchange

    store = DemoStore(tmp_path / "demo.sqlite3")
    snapshot = Exchange().snapshot()
    store.apply_snapshot(snapshot)
    state = DemoEventSync(account_key=snapshot.account_key, stream="fills",
                          query_filter={"instType": "SWAP"}).model_dump(mode="json")
    if defect == "account":
        state["account_key"] = "f" * 64
    else:
        state.update(stream="bills", query_filter={})
    raw = json.dumps(state)
    with store.connect() as db:
        db.execute("INSERT INTO metadata VALUES('event_sync:fills',?)", (raw,))
    with pytest.raises(ValueError, match="identity"):
        store.sync_state()
    with store.connect() as db:
        assert db.execute("SELECT value FROM metadata WHERE key='event_sync:fills'").fetchone()[0] == raw
