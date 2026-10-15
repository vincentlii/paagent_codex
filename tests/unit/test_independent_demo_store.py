from decimal import Decimal as D

import pytest

from pa_agent.execution.demo_store import DemoStore
from tests.unit.test_demo_plans import bind
from tests.unit.test_independent_demo import plan


def prepared(tmp_path):
    store = DemoStore(tmp_path / "demo.sqlite3")
    p = plan()
    store.register_intent(p)
    request = bind(p)
    store.save_prepared(p, request)
    cid = store.prepare_leg(
        p.content_digest, "entry", request.model_dump(mode="json"), generation=1
    )
    store.update_leg(cid, "partially_filled", {"ordId": "entry", "accFillSz": "1"})
    return store, p, cid


def test_backward_observation_cannot_reduce_accumulated_fill(tmp_path):
    store, p, cid = prepared(tmp_path)
    store.update_leg(cid, "partially_filled", {"accFillSz": ".4"})
    assert store.plan_exposure(p.content_digest).entered == 1


def test_atomic_exit_budget_prevents_overlapping_unknown_or_emergency(tmp_path):
    store, p, _ = prepared(tmp_path)
    params = {"quantity": ".6", "side": "sell", "stop": "49000", "tp1": "52000"}
    cid = store.prepare_leg(p.content_digest, "oco", params, generation=1)
    store.update_leg(cid, "live")
    with pytest.raises(ValueError, match="exit_budget_unavailable"):
        store.prepare_leg(
            p.content_digest, "emergency", {"quantity": ".5", "side": "sell"}, generation=1
        )
    cid2 = store.prepare_leg(p.content_digest, "oco", params | {"quantity": ".4"}, generation=2)
    store.update_leg(cid2, "submission_unknown")
    with pytest.raises(ValueError, match="exit_budget_unavailable"):
        store.prepare_leg(
            p.content_digest, "emergency", {"quantity": ".4", "side": "sell"}, generation=1
        )
    assert store.plan_exposure(p.content_digest).reserved_exit == 1


def test_triggered_fill_is_monotonic_and_schema_unchanged(tmp_path):
    store, p, _ = prepared(tmp_path)
    cid = store.prepare_leg(p.content_digest, "oco", {"quantity": "1"}, generation=1)
    store.update_leg(cid, "effective")
    store.register_triggered(cid, p.inst_id, ["exit"])
    store.observe_triggered(
        cid, "exit", {"state": "partially_filled", "ordId": "exit", "accFillSz": ".4"}
    )
    store.observe_triggered(
        cid, "exit", {"state": "partially_filled", "ordId": "exit", "accFillSz": ".1"}
    )
    assert store.plan_exposure(p.content_digest).exited == D(".4")
    with store.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert len(db.execute("PRAGMA table_info(intents)").fetchall()) == 13


def test_shared_net_position_cannot_release_two_unreflected_reservations(tmp_path):
    from tests.unit.test_independent_demo import NetExchange

    store, p, cid = prepared(tmp_path)
    q = plan("b")
    store.register_intent(q)
    request = bind(q)
    store.save_prepared(q, request)
    store.update(p.content_digest, filled="1")
    store.update(q.content_digest, filled="1")
    exchange = NetExchange()
    exchange.position = "1"
    assert store.unreflected_margin(exchange.snapshot()) == request.margin * 2
