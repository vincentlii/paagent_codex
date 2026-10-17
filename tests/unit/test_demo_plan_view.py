import json
from decimal import Decimal as D

import pytest

from tests.unit.test_demo_plans import intent, bind
from tests.unit.test_demo_worker import NOW


def view(
    *,
    side="buy",
    entered="1",
    exited="0",
    state="filled",
    trade="active",
    entry_price="50000",
    exit_price="51000",
    quote=None,
    fees="-0.1",
    unknown=False,
):
    from pa_agent.operations.demo_plan_view import plan_view

    i = intent(
        side=side,
        stop="49000" if side == "buy" else "51000",
        tp1="52000" if side == "buy" else "48000",
    )
    entry = dict(
        client_id="entry-client",
        leg="entry",
        status="observation_unknown" if unknown else state,
        response=json.dumps(dict(ordId="entry-id", accFillSz=entered, state=state)),
    )
    legs = [entry]
    children = []
    fills = []
    if D(entered):
        fills.append(
            dict(
                instId=i.inst_id,
                ordId="entry-id",
                tradeId="e",
                side=side,
                fillSz=entered,
                fillPx=entry_price,
                fee=fees,
                feeCcy="USDT",
            )
        )
    if D(exited):
        legs.append(dict(client_id="oco", leg="oco", status="effective", response="{}"))
        children.append(
            dict(
                parent_client_id="oco",
                order_id="exit-id",
                state="filled",
                body=json.dumps(dict(ordId="exit-id", accFillSz=exited, state="filled")),
            )
        )
        fills.append(
            dict(
                instId=i.inst_id,
                ordId="exit-id",
                tradeId="x",
                side="sell" if side == "buy" else "buy",
                fillSz=exited,
                fillPx=exit_price,
                fee=fees,
                feeCcy="USDT",
            )
        )
    row = dict(filled=entered, entry_state=state, trade_state=trade, protection="none")
    return plan_view(
        i,
        bind(),
        row,
        legs,
        children,
        fills,
        quote if quote is not None else dict(bidPx="50500", askPx="50502", ts=str(NOW)),
        NOW,
    )


def test_confirmed_zero_fill_cancel_is_cancelled_and_zero():
    result = view(entered="0", state="canceled", trade="closed", quote={})
    assert result["display_status"] == "cancelled"
    assert D(result["pnl"]) == 0


@pytest.mark.parametrize("side,expected", [("buy", "4.9"), ("sell", "-5.12")])
def test_open_plan_uses_own_fills_and_executable_quote(side, expected):
    result = view(side=side)
    assert result["display_status"] == "open"
    assert D(result["pnl"]) == D(expected)


def test_partial_fill_cancel_is_a_position_not_zero_cancelled():
    result = view(entered="0.4", state="canceled")
    assert result["display_status"] == "open"
    assert D(result["pnl"]) == D("1.9")


def test_closed_plan_uses_own_exit_and_deducts_fees_once():
    result = view(exited="1", trade="closed", quote={})
    assert result["display_status"] == "closed"
    assert D(result["pnl"]) == D("9.8")


def test_partial_exit_combines_realized_and_remaining_value():
    result = view(exited="0.4")
    assert result["display_status"] == "open"
    assert D(result["pnl"]) == D("6.8")


@pytest.mark.parametrize(
    "quote", [{}, {"bidPx": "50500", "askPx": "50502", "ts": str(NOW - 31000)}]
)
def test_missing_or_stale_quote_is_not_zero(quote):
    result = view(quote=quote)
    assert result["pnl"] is None and result["sync_required"]


def test_unknown_entry_query_does_not_fake_zero_or_closed():
    result = view(entered="0", state="live", unknown=True)
    assert result["pnl"] is None and result["sync_required"]
    assert result["display_status"] != "closed"


def test_cancelled_zero_observation_contradicted_by_owned_fill_is_not_zero():
    from pa_agent.operations.demo_plan_view import plan_view

    i = intent()
    entry = dict(
        client_id="cid",
        leg="entry",
        status="canceled",
        response=json.dumps(dict(ordId="entry", accFillSz="0", state="canceled")),
    )
    row = dict(filled="0", entry_state="canceled", trade_state="closed", protection="none")
    fills = [
        dict(
            instId=i.inst_id,
            ordId="entry",
            tradeId="e",
            side="buy",
            fillSz="1",
            fillPx="50000",
            fee="-0.1",
            feeCcy="USDT",
        )
    ]
    result = plan_view(i, bind(), row, [entry], [], fills, {}, NOW)
    assert result["pnl"] is None and result["sync_required"]


def test_definitive_rejection_without_order_id_is_cancelled_zero():
    from pa_agent.operations.demo_plan_view import plan_view

    entry = dict(client_id="cid", leg="entry", status="rejected", response="{}")
    row = dict(filled="0", entry_state="rejected", trade_state="rejected", protection="none")
    result = plan_view(intent(), bind(), row, [entry], [], [], {}, NOW)
    assert result["display_status"] == "cancelled" and result["pnl"] == "0"
    unknown_exit = dict(client_id="oco", leg="oco", status="observation_unknown", response="{}")
    result = plan_view(intent(), bind(), row, [entry, unknown_exit], [], [], {}, NOW)
    assert result["pnl"] is None and result["sync_required"]


def test_fee_rebate_keeps_positive_sign():
    assert D(view(exited="1", trade="closed", fees="0.1")["pnl"]) == D("10.2")


def test_order_fills_not_limited_to_recent_150_or_other_plans(tmp_path):
    from pa_agent.execution.demo_store import DemoStore
    from tests.unit.test_demo_worker import Exchange

    store = DemoStore(tmp_path / "execution.sqlite3")
    rows = tuple(
        dict(
            instId="BTC-USDT-SWAP",
            ordId="own" if n == 0 else "other",
            tradeId=str(n),
            fillSz="1",
            fillPx="50000",
            ts=str(NOW + n),
        )
        for n in range(200)
    )
    store.apply_snapshot(Exchange().snapshot().model_copy(update={"fills": rows}))
    assert len(store.order_fills("BTC-USDT-SWAP", {"own"})) == 1
    assert store.order_fills("ETH-USDT-SWAP", {"own"}) == []


def test_fills_projection_selects_latest_exchange_time_not_insert_order(tmp_path):
    from pa_agent.execution.demo_store import DemoStore
    from tests.unit.test_demo_worker import Exchange

    store = DemoStore(tmp_path / "execution.sqlite3")
    rows = tuple(
        dict(instId="BTC-USDT-SWAP", ordId="o", tradeId=str(n), ts=str(NOW + n))
        for n in range(200, 0, -1)
    )
    store.apply_snapshot(Exchange().snapshot().model_copy(update={"fills": rows}))
    projected = store.projection_rows(now_ms=NOW + 201)["fills"]
    assert len(projected) == 150
    assert min(int(x["ts"]) for x in projected) == NOW + 51
    assert int(projected[0]["ts"]) == NOW + 200


def test_plan_projection_keeps_different_entry_basis_under_shared_position(tmp_path):
    from pa_agent.execution.demo_store import DemoStore
    from pa_agent.operations.demo_trading import build_demo_projection
    from tests.unit.test_demo_worker import Exchange

    store = DemoStore(tmp_path / "execution.sqlite3")
    fills = []
    for name, price, quantity in [("a", "50000", "1"), ("b", "51000", "0.98")]:
        i = intent(task_id=name, run_id=name, entry=price, tp1="53000")
        store.register_intent(i)
        store.save_prepared(i, bind(i))
        cid = store.prepare_leg(i.content_digest, "entry", {}, generation=1)
        store.update_leg(
            cid, "filled", dict(ordId=name, state="filled", accFillSz=quantity, avgPx=price)
        )
        store.update(i.content_digest, filled=quantity, entry_state="filled")
        fills.append(
            dict(
                instId=i.inst_id,
                ordId=name,
                tradeId=name,
                side="buy",
                fillSz=quantity,
                fillPx=price,
                fee="-0.1",
                feeCcy="USDT",
            )
        )
    snapshot = (
        Exchange()
        .snapshot()
        .model_copy(
            update={
                "fills": tuple(fills),
                "positions": (
                    {"instId": "BTC-USDT-SWAP", "pos": "1.98", "avgPx": "50500", "upl": "9999"},
                ),
                "quotes": {"BTC-USDT-SWAP": {"bidPx": "52000", "askPx": "52001", "ts": str(NOW)}},
            }
        )
    )
    store.apply_snapshot(snapshot)
    p = build_demo_projection(store, snapshot, now_ms=NOW)
    values = {r["run_id"]: D(r["pnl"]) for r in p["orders"]}
    assert values == {"a": D("19.9"), "b": D("9.7")}


def test_no_trade_history_does_not_push_order_plans_out_of_projection(tmp_path):
    from pa_agent.execution.demo_store import DemoStore

    store = DemoStore(tmp_path / "execution.sqlite3")
    i = intent()
    store.register_intent(i)
    store.save_prepared(i, bind(i))
    for n in range(155):
        store.register_intent(
            intent(
                schema_version="okx_demo_intent_v2",
                task_id=str(n),
                disposition="no_trade",
                side=None,
                entry=None,
                stop=None,
                tp1=None,
            )
        )
    rows = store.projection_rows(now_ms=NOW)
    assert [r["id"] for r in rows["intents"]] == [i.content_digest]
    assert len(rows["decisions"]) == 150
