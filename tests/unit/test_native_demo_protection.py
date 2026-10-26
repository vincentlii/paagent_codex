"""One entry POST delegates frozen limit protection to the exchange, not the poller."""

import json
from decimal import Decimal as D

import httpx
import pytest

from tests.unit.test_independent_demo import runtime as runtime
from tests.unit.test_limit_demo_exits import limit_context, limit_plan, setup_limit
from tests.unit.test_okx_demo_client import client


def native_plan(name="a", **changes):
    from pa_agent.execution.demo_contracts import DemoIntent

    p = limit_plan(name, **changes)
    values = p.digest_payload()
    values["trade_execution_context"] = limit_context(
        protection_binding="entry_attached"
    ).model_dump(mode="json")
    return DemoIntent.seal(values)


def setup_native(runtime):
    w, ex, store, spool = setup_limit(runtime)
    w.config = w.config.model_copy(update={"attached_accepted_symbols": ("BTC-USDT-SWAP",)})
    original_fill = ex.fill

    def fill(store, p, quantity="1"):
        original_fill(store, p, quantity)
        req = json.loads(store.row(p.content_digest)["request"])
        aid = req["attached_algo_client_id"]
        ex.algos[aid] = dict(
            algoId="native-" + aid,
            algoClOrdId=aid,
            instId=p.inst_id,
            state="live",
            ordType="oco",
            side="sell" if p.side == "buy" else "buy",
            posSide="net",
            reduceOnly="true",
            closeFraction="",
            sz=quantity,
            slTriggerPx=str(p.stop),
            tpTriggerPx=str(p.tp1),
            slOrdPx=str(p.stop),
            tpOrdPx=str(p.tp1),
            slTriggerPxType="last",
            tpTriggerPxType="last",
        )

    ex.fill = fill
    return w, ex, store, spool


def test_native_context_and_request_reject_unbound_or_non_limit_attachment():
    from pa_agent.execution.demo_plans import bind_demo_limit
    from tests.unit.test_demo_plans import contract

    p = native_plan()
    req = bind_demo_limit(p, contract(), account_key="private-account", available_margin=D(10000))
    assert req.attached_algo_client_id
    with pytest.raises(ValueError):
        limit_context(protection_binding="entry_attached", exit_order_type=None)


def test_native_entry_http_attaches_one_frozen_condition_pair_without_extra_oco():
    from tests.unit.test_demo_plans import bind

    calls = []
    c = client(
        lambda request: (
            calls.append(json.loads(request.content))
            or httpx.Response(200, json={"code": "0", "data": [{"sCode": "0", "ordId": "1"}]})
        )
    )
    request = bind(native_plan())
    try:
        c.place_limit(request)
        assert len(calls) == 1
        attachment = calls[0]["attachAlgoOrds"]
        assert len(attachment) == 1 and attachment[0]["tpOrdKind"] == "condition"
        assert attachment[0]["attachAlgoClOrdId"] == request.attached_algo_client_id
        assert D(attachment[0]["slOrdPx"]) == request.stop
        assert D(attachment[0]["tpOrdPx"]) == request.tp1
    finally:
        c.close()


@pytest.mark.parametrize("code", ["1", "2"])
def test_top_level_batch_code_keeps_the_single_safe_subcode(code):
    from pa_agent.execution.okx_demo import DemoRequestRejected

    c = client(
        lambda request: httpx.Response(
            200,
            json={"code": code, "data": [{"sCode": "51006", "sMsg": "secret-pass", "ordId": ""}]},
        )
    )
    try:
        with pytest.raises(DemoRequestRejected) as caught:
            c.place_limit(__import__("tests.unit.test_demo_plans", fromlist=["bind"]).bind())
        assert caught.value.code == "51006"
        assert "secret" not in str(caught.value)
    finally:
        c.close()


def test_parent_attachment_projection_keeps_only_safe_bounded_facts():
    from pa_agent.execution.okx_demo import safe_row

    raw = dict(
        ordId="1",
        attachAlgoOrds=[
            dict(
                attachAlgoClOrdId="pad123",
                failCode="51006",
                slTriggerPx="49000",
                slOrdPx="49000",
                tpTriggerPx="52000",
                tpOrdPx="52000",
                secret="do-not-persist",
                sMsg="secret-pass",
            )
        ],
    )
    clean = safe_row(raw)
    assert json.loads(clean["attached_algo_status"])[0]["failCode"] == "51006"
    assert "secret" not in json.dumps(clean)


def test_entry_reserves_native_identity_before_post_and_full_fill_never_posts_oco(runtime):
    w, ex, store, spool = setup_native(runtime)
    original = ex.place_limit
    p = native_plan()

    def post(req):
        leg = next(l for l in store.legs(p.content_digest) if l["leg"] == "oco")
        assert leg["client_id"] == req.attached_algo_client_id
        assert json.loads(leg["params"])["quantity"] == str(req.sz)
        return original(req)

    ex.place_limit = post
    spool.publish(p)
    w.tick()
    ex.fill(store, p)
    w.tick()
    assert store.plan_exposure(p.content_digest).protected == 1
    assert not any(k in {"oco", "emergency"} for k, _ in ex.calls)


def test_partial_cancel_and_late_terminal_quantity_use_same_immutable_native_leg(runtime):
    w, ex, store, spool = setup_native(runtime)
    p = native_plan()
    spool.publish(p)
    w.tick()
    ex.fill(store, p, ".4")
    entry = next(l for l in store.legs(p.content_digest) if l["leg"] == "entry")
    ex.orders[entry["client_id"]].update(state="canceled")
    w.tick()
    leg = next(l for l in store.legs(p.content_digest) if l["leg"] == "oco")
    params = leg["params"]
    assert store.plan_exposure(p.content_digest).protected == D(".4")
    ex.fill(store, p, ".6")
    ex.orders[entry["client_id"]].update(state="canceled")
    w.tick()
    assert store.plan_exposure(p.content_digest).protected == D(".6")
    assert next(l for l in store.legs(p.content_digest) if l["leg"] == "oco")["params"] == params
    assert not any(k in {"oco", "emergency"} for k, _ in ex.calls)


def test_partial_live_remainder_is_cancelled_without_posting_second_protection(runtime):
    w, ex, store, spool = setup_native(runtime)
    p = native_plan()
    spool.publish(p)
    w.tick()
    ex.fill(store, p, ".4")
    ex.algos.clear()
    w.tick()
    assert any(k == "cancel" for k, _ in ex.calls)
    assert store.plan_exposure(p.content_digest).reserved_exit == D(".4")
    assert not any(k in {"oco", "emergency"} for k, _ in ex.calls)


def test_cancelled_zero_fill_attachment_ends_without_infinite_handoff(runtime):
    w, ex, store, spool = setup_native(runtime)
    p = native_plan()
    spool.publish(p)
    w.tick()
    entry = next(l for l in store.legs(p.content_digest) if l["leg"] == "entry")
    ex.orders[entry["client_id"]].update(state="canceled")
    w.tick()
    ex.now += 2
    w.tick()
    assert store.row(p.content_digest)["trade_state"] == "closed"
    assert not store.plan_exposure(p.content_digest).uncertain
    w.request_shutdown()
    assert w.can_handoff()


def test_native_can_exit_before_host_first_observes_entry(runtime):
    w, ex, store, spool = setup_native(runtime)
    p = native_plan()
    spool.publish(p)
    w.tick()
    ex.fill(store, p)
    leg = next(l for l in store.legs(p.content_digest) if l["leg"] == "oco")
    ex.algos[leg["client_id"]].update(state="effective", child_order_ids='["native-exit"]')
    ex.orders["native-exit"] = dict(
        instId=p.inst_id,
        ordId="native-exit",
        state="filled",
        accFillSz="1",
        sz="1",
        ordType="limit",
        px=str(p.tp1),
        side="sell",
        reduceOnly="true",
    )
    ex.position = "0"
    w.tick()
    ex.now += 2
    w.tick()
    assert store.plan_exposure(p.content_digest).remaining == 0
    assert store.row(p.content_digest)["trade_state"] == "closed"
    assert not any(k in {"oco", "emergency"} for k, _ in ex.calls)


def test_unaccepted_native_protocol_never_releases_entry(runtime):
    w, ex, store, spool = setup_limit(runtime)
    p = native_plan()
    spool.publish(p)
    w.tick()
    assert store.row(p.content_digest)["reason"] == "attached_protection_not_live_accepted"
    assert not ex.calls


def test_request_and_preregistered_attachment_must_bind_same_context_and_parent(runtime):
    from tests.unit.test_demo_plans import bind

    w, ex, store, spool = setup_native(runtime)
    p = native_plan()
    store.register_intent(p)
    req = bind(p)
    with pytest.raises(ValueError, match="attached_protection_binding_mismatch"):
        store.save_prepared(p, req.model_copy(update={"attached_algo_client_id": None}))
    legacy = limit_plan("legacy")
    store.register_intent(legacy)
    with pytest.raises(ValueError, match="attached_protection_binding_mismatch"):
        store.save_prepared(
            legacy,
            bind(legacy).model_copy(
                update={"attached_algo_client_id": req.attached_algo_client_id}
            ),
        )
    spool.publish(p)
    w.tick()
    leg = next(l for l in store.legs(p.content_digest) if l["leg"] == "oco")
    params = json.loads(leg["params"])
    with pytest.raises(ValueError):
        store.prepare_leg(
            p.content_digest, "oco", params | {"attached_entry_client_id": "wrong"}, generation=2
        )


def test_two_pending_native_plans_then_same_direction_fills_remain_independent(runtime):
    w, ex, store, spool = setup_native(runtime)
    a, b = native_plan(), native_plan("b")
    spool.publish(a)
    w.tick()
    spool.publish(b)
    w.tick()
    assert len([k for k, _ in ex.calls if k == "entry"]) == 2
    ex.fill(store, a, ".4")
    ex.fill(store, b, ".6")
    w.tick()
    assert store.plan_exposure(a.content_digest).protected == D(".4")
    assert store.plan_exposure(b.content_digest).protected == D(".6")
    assert D(ex.position) == 1
    assert not any(k in {"oco", "emergency"} for k, _ in ex.calls)


def test_missing_native_survives_restart_without_duplicate_exit(runtime):
    from pa_agent.execution.demo_worker import DemoWorker

    w, ex, store, spool = setup_native(runtime)
    p = native_plan()
    spool.publish(p)
    w.tick()
    ex.fill(store, p)
    ex.algos.clear()
    w.tick()
    assert store.plan_exposure(p.content_digest).reserved_exit == 1
    w.close()
    w2 = DemoWorker(w.config, w.paths, ex, store, spool, now_ms=lambda: ex.now)
    try:
        w2.tick()
        w2.request_shutdown()
        assert not w2.can_handoff()
        assert store.plan_exposure(p.content_digest).reserved_exit == 1
        assert not any(k in {"oco", "emergency"} for k, _ in ex.calls)
    finally:
        w2.close()


def test_mismatched_native_is_retained_never_cancelled_or_replaced(runtime):
    w, ex, store, spool = setup_native(runtime)
    p = native_plan()
    spool.publish(p)
    w.tick()
    ex.fill(store, p)
    for algo in ex.algos.values():
        algo["sz"] = "2"
    projection = w.tick()
    assert projection["reason"] == "protection_parameters_mismatch"
    assert store.plan_exposure(p.content_digest).reserved_exit == 1
    assert not any(k in {"oco", "emergency", "cancel_algo"} for k, _ in ex.calls)


def test_native_certain_failure_uses_only_owned_original_stop_limit(runtime):
    w, ex, store, spool = setup_native(runtime)
    p = native_plan()
    spool.publish(p)
    w.tick()
    ex.fill(store, p, ".4")
    ex.algos.clear()
    entry = next(l for l in store.legs(p.content_digest) if l["leg"] == "entry")
    request = json.loads(store.row(p.content_digest)["request"])
    ex.orders[entry["client_id"]].update(
        state="canceled",
        attached_algo_status=json.dumps(
            [dict(attachAlgoClOrdId=request["attached_algo_client_id"], failCode="51006")]
        ),
    )

    def close(**params):
        ex.calls.append(("emergency", params))
        raise __import__(
            "pa_agent.execution.okx_demo", fromlist=["DemoRequestUnknown"]
        ).DemoRequestUnknown("limit")

    ex.close_position = close
    w.tick()
    emergency = [params for k, params in ex.calls if k == "emergency"]
    assert len(emergency) == 1 and emergency[0]["quantity"] == D(".4")
    assert emergency[0]["limit_price"] == p.stop
    ex.fill(store, p, ".6")
    ex.algos.clear()
    ex.orders[entry["client_id"]].update(state="canceled")
    w.tick()
    emergency = [params for k, params in ex.calls if k == "emergency"]
    assert [x["quantity"] for x in emergency] == [D(".4"), D(".2")]
    assert not any(k == "oco" for k, _ in ex.calls)


def test_parent_failure_cannot_release_a_known_native_or_triggered_child(runtime):
    w, ex, store, spool = setup_native(runtime)
    p = native_plan()
    spool.publish(p)
    w.tick()
    ex.fill(store, p)
    w.tick()
    entry = next(l for l in store.legs(p.content_digest) if l["leg"] == "entry")
    leg = next(l for l in store.legs(p.content_digest) if l["leg"] == "oco")
    ex.orders[entry["client_id"]]["attached_algo_status"] = json.dumps(
        [dict(attachAlgoClOrdId=leg["client_id"], failCode="51006")]
    )
    assert w.tick()["reason"] == "attached_protection_observation_conflict"
    assert store.plan_exposure(p.content_digest).reserved_exit == 1
    assert not any(k in {"oco", "emergency"} for k, _ in ex.calls)


def test_stale_zero_terminal_cannot_erase_known_partial_or_release_native_budget(runtime):
    w, ex, store, spool = setup_native(runtime)
    p = native_plan()
    spool.publish(p)
    w.tick()
    ex.fill(store, p, ".4")
    ex.algos.clear()
    w.tick()
    entry = next(l for l in store.legs(p.content_digest) if l["leg"] == "entry")
    ex.orders[entry["client_id"]].update(state="canceled", accFillSz="0")
    w.tick()
    assert D(store.row(p.content_digest)["filled"]) == D(".4")
    exposure = store.plan_exposure(p.content_digest)
    assert exposure.entered == exposure.reserved_exit == D(".4")
    assert exposure.uncertain
    assert not any(k in {"oco", "emergency"} for k, _ in ex.calls)


def test_unactivated_attachment_exception_ends_at_first_real_native_observation(runtime):
    w, ex, store, spool = setup_native(runtime)
    p = native_plan()
    spool.publish(p)
    w.tick()
    native = next(l for l in store.legs(p.content_digest) if l["leg"] == "oco")
    cid = native["client_id"]
    store.update_leg(cid, "canceled", {"_unactivated_attachment": True})
    store.update_leg(cid, "live", {"algoId": "real-native"})
    store.update_leg(cid, "effective", {"child_order_ids": '["real-child"]'})
    store.update_leg(cid, "live", {"algoId": "real-native"})
    latest = next(l for l in store.legs(p.content_digest) if l["client_id"] == cid)
    assert latest["status"] == "effective"
    assert "_unactivated_attachment" not in json.loads(latest["response"])


def test_native_child_before_parent_fill_is_retained_as_inconsistent_not_zero_closed(runtime):
    w, ex, store, spool = setup_native(runtime)
    p = native_plan()
    spool.publish(p)
    w.tick()
    ex.fill(store, p)
    native = next(l for l in store.legs(p.content_digest) if l["leg"] == "oco")
    entry = next(l for l in store.legs(p.content_digest) if l["leg"] == "entry")
    ex.orders[entry["client_id"]].update(state="canceled", accFillSz="0")
    ex.algos[native["client_id"]].update(state="effective", child_order_ids='["early-child"]')
    ex.orders["early-child"] = dict(
        instId=p.inst_id,
        ordId="early-child",
        state="filled",
        accFillSz="1",
        sz="1",
        ordType="limit",
        px=str(p.tp1),
        side="sell",
        reduceOnly="true",
    )
    ex.position = "0"
    w.tick()
    assert store.plan_exposure(p.content_digest).inconsistent
    assert store.row(p.content_digest)["trade_state"] == "active"
    spool.publish(native_plan("second"))
    w.tick()
    assert len([k for k, _ in ex.calls if k == "entry"]) == 1
    assert not any(k in {"oco", "emergency"} for k, _ in ex.calls)
    ex.orders[entry["client_id"]].update(state="filled", accFillSz="1")
    ex.now += 2
    w.tick()
    ex.now += 2
    w.tick()
    assert not store.plan_exposure(p.content_digest).inconsistent
    assert store.plan_exposure(p.content_digest).remaining == 0
    assert store.row(p.content_digest)["trade_state"] == "closed"
    assert len([l for l in store.legs(p.content_digest) if l["leg"] == "oco"]) == 1
    assert not any(k in {"oco", "emergency"} for k, _ in ex.calls)


def test_native_internal_error_keeps_reservation_instead_of_claiming_certain_rejection(runtime):
    w, ex, store, spool = setup_native(runtime)
    p = native_plan()
    spool.publish(p)
    w.tick()
    ex.fill(store, p)
    ex.algos.clear()
    entry = next(l for l in store.legs(p.content_digest) if l["leg"] == "entry")
    native = next(l for l in store.legs(p.content_digest) if l["leg"] == "oco")
    ex.orders[entry["client_id"]]["attached_algo_status"] = json.dumps(
        [dict(attachAlgoClOrdId=native["client_id"], failCode="50004")]
    )
    w.tick()
    assert store.plan_exposure(p.content_digest).reserved_exit == 1
    assert not any(k in {"oco", "emergency"} for k, _ in ex.calls)
