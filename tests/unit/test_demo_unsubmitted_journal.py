"""Unsubmitted plans remain visible without inventing exchange execution."""
import pytest

from pa_agent.execution.demo_store import DemoStore
from pa_agent.operations.demo_trading import build_demo_projection, safe_demo_projection
from tests.unit.test_demo_plans import bind, intent
from tests.unit.test_demo_worker import Exchange, NOW


@pytest.mark.parametrize("reason", ["target_tick_mismatch", "opposite_plan_active", "insufficient_margin"])
def test_rejected_before_request_is_visible_with_zero_pnl_and_original_identity(tmp_path, reason):
    store = DemoStore(tmp_path / "execution.sqlite3")
    plan = intent(schema_version="okx_demo_intent_v2")
    store.register_intent(plan)
    store.update(plan.content_digest, trade_state="rejected", reason=reason)
    before = store.row(plan.content_digest)
    projection = safe_demo_projection(build_demo_projection(store, Exchange().snapshot(), now_ms=NOW))
    assert len(projection["orders"]) == 1
    row = projection["orders"][0]
    assert row["display_status"] == "unsubmitted"
    assert row["reason"] == reason
    assert row["pnl"] == "0" and row["sync_required"] is False
    assert row["quantity"] is None and row["notional"] is None
    assert row["entry"] == "50000" and row["stop"] == "49000"
    assert row["market_digest"] == plan.market_digest
    assert row["source_symbol"] == plan.source_identity["symbol"]
    assert row["task_id"] == plan.task_id and row["run_id"] == plan.run_id
    assert store.row(plan.content_digest) == before
    assert projection["total_orders"] == 0 and projection["total_decisions"] == 1
    assert projection["account"]["planned_stop_loss"] == "0"


def test_unprepared_active_plan_is_not_pending_exchange_order_or_zero_pnl(tmp_path):
    store = DemoStore(tmp_path / "execution.sqlite3")
    plan = intent(schema_version="okx_demo_intent_v2")
    store.register_intent(plan)
    row, = build_demo_projection(store, Exchange().snapshot(), now_ms=NOW)["orders"]
    assert row["display_status"] == "unsubmitted"
    assert row["reason"] == "awaiting_submission"
    assert row["pnl"] is None and row["sync_required"] is True


def test_conflicting_fill_state_never_turns_unprepared_rejection_into_zero(tmp_path):
    store = DemoStore(tmp_path / "execution.sqlite3")
    plan = intent(schema_version="okx_demo_intent_v2")
    store.register_intent(plan)
    store.update(plan.content_digest, trade_state="rejected", reason="target_tick_mismatch", filled="1")
    row, = build_demo_projection(store, Exchange().snapshot(), now_ms=NOW)["orders"]
    assert row["display_status"] == "unsubmitted"
    assert row["pnl"] is None and row["sync_required"] is True


def test_owned_fill_without_request_keeps_rejected_amount_unknown(tmp_path):
    from pa_agent.execution.demo_contracts import client_id

    store = DemoStore(tmp_path / "execution.sqlite3")
    plan = intent(schema_version="okx_demo_intent_v2")
    store.register_intent(plan)
    store.update(plan.content_digest, trade_state="rejected", reason="target_tick_mismatch")
    snapshot = Exchange().snapshot()
    store.apply_snapshot(snapshot.model_copy(update={"fills": ({
        "instId": plan.inst_id, "tradeId": "contradiction", "ordId": "order",
        "clOrdId": client_id(snapshot.account_key, plan, "entry", 1),
        "side": "buy", "fillSz": "1", "fillPx": "50000",
    },)}))
    row, = build_demo_projection(store, snapshot, now_ms=NOW)["orders"]
    assert row["pnl"] is None and row["sync_required"] is True


def test_non_limit_rejection_has_no_invented_prices_or_direction(tmp_path):
    store = DemoStore(tmp_path / "execution.sqlite3")
    plan = intent(schema_version="okx_demo_intent_v2", disposition="rejected",
                  side=None, entry=None, stop=None, tp1=None, source_reason="unsupported_order_type")
    store.register_intent(plan)
    store.update(plan.content_digest, trade_state="rejected", reason=plan.source_reason)
    row, = safe_demo_projection(build_demo_projection(store, Exchange().snapshot(), now_ms=NOW))["orders"]
    assert row["display_status"] == "unsubmitted"
    assert row["side"] is None and row["entry"] is None
    assert row["reason"] == "unsupported_order_type"


def test_no_trade_does_not_hide_unsubmitted_plan_or_change_prepared_order_statistics(tmp_path):
    store = DemoStore(tmp_path / "execution.sqlite3")
    prepared = intent(schema_version="okx_demo_intent_v2", task_id="prepared")
    hidden = intent(schema_version="okx_demo_intent_v2", task_id="unsubmitted")
    for plan in (prepared, hidden):
        store.register_intent(plan)
    store.save_prepared(prepared, bind(prepared))
    store.update(hidden.content_digest, trade_state="rejected", reason="target_tick_mismatch")
    for number in range(155):
        store.register_intent(intent(schema_version="okx_demo_intent_v2", task_id=f"wait-{number}",
                                     disposition="no_trade", side=None, entry=None, stop=None, tp1=None))
    projection = build_demo_projection(store, Exchange().snapshot(), now_ms=NOW)
    assert {row["task_id"] for row in projection["orders"]} == {"prepared", "unsubmitted"}
    assert projection["total_orders"] == 1
    assert projection["total_decisions"] == 157
