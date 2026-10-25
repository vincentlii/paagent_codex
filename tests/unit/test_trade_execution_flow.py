"""Frozen context survives Host delivery; fee faults affect only new entries."""
import copy
import hashlib
import json
from dataclasses import replace

import pytest

from pa_agent.ai.execution_policy import apply_confidence_execution_policy
from pa_agent.ai.trade_execution_context import FeeSchedule, TradeExecutionContext
from pa_agent.execution.demo_contracts import DemoIntent, DemoPaths
from pa_agent.execution.demo_plans import compile_demo_intent
from pa_agent.execution.demo_spool import DemoSpool, atomic_json
from pa_agent.execution.demo_store import DemoStore
from pa_agent.execution.demo_worker import DemoWorker
from pa_agent.operations.demo_settings import DemoExecutionPolicy
from tests.unit.test_demo_execution_flow import config, source_record
from tests.unit.test_demo_worker import NOW
from tests.unit.test_independent_demo import NetExchange


def marked_record(*, unavailable=False, narrow=False):
    record, spec = source_record()
    policy = DemoExecutionPolicy(limit_validity_hours=5)
    fee = FeeSchedule(inst_id="BTC-USDT-SWAP", maker="-0.0002", taker="-0.0005",
                      observed_at_ms=NOW-500)
    context = TradeExecutionContext(inst_id=fee.inst_id, source_close_ms=NOW-500,
        frozen_at_ms=NOW, limit_validity_hours=5, expires_at_ms=NOW-500+5*3_600_000,
        fee_schedule=None if unavailable else fee)
    record.role_runtime = {"trade_execution_context": context.model_dump(mode="json")}
    record.stage2_decision["decision"].update(estimated_win_rate=60, trade_confidence=60, take_profit_price_2=None)
    if narrow:
        record.stage2_decision["decision"].update(stop_loss_price=49990, take_profit_price=50015)
    record.stage2_decision["execution_disposition"] = {"trade_execution_context": context.model_dump(mode="json")}
    record.stage2_decision = apply_confidence_execution_policy(record.stage2_decision, 40)
    return record, spec, policy, fee


@pytest.mark.parametrize("case", ["eligible", "unavailable", "narrow", "increased", "missing_current"])
def test_cost_projection_delivery_worker_replay_and_read_only_journal(tmp_path, case):
    record, spec, policy, fee = marked_record(unavailable=case == "unavailable", narrow=case == "narrow")
    before = copy.deepcopy(record.stage2_decision["decision"])
    intent = compile_demo_intent(record, spec, task_id="fee-plan", config=config(),
        frozen_execution_epoch="epoch-1", now_ms=NOW, market_snapshot_hash="a"*64,
        demo_execution_policy=policy.model_dump(mode="json"))
    assert record.stage2_decision["decision"] == before
    assert intent.expires_at_ms == NOW-500+5*3_600_000
    assert (intent.disposition == "limit") == (case not in {"unavailable", "narrow"})
    if case == "increased":
        fee = fee.model_copy(update={"taker": fee.taker*2})
    exchange = NetExchange()
    original = exchange.snapshot
    exchange.snapshot = lambda **kwargs: original(**kwargs).model_copy(update={
        "fee_schedules": () if case == "missing_current" else (fee,)})
    paths = DemoPaths(tmp_path)
    spool = DemoSpool(paths)
    spool.publish(intent)
    atomic_json(paths.control, dict(cutover_id="epoch-1", generated_at_ms=NOW, running=True))
    for _ in range(2):
        worker = DemoWorker(config(), paths, exchange, DemoStore(paths.state_db), spool, now_ms=lambda: NOW)
        try:
            projection = worker.tick()
        finally:
            worker.close()
    assert len([c for c in exchange.calls if c[0] == "entry"]) == (1 if case == "eligible" else 0)
    row = projection["orders"][0]
    from pa_agent.operations.demo_trading import safe_demo_projection
    assert projection["fee_scope"] == hashlib.sha256(b"fee:private-account").hexdigest()
    assert "fee_scope" not in safe_demo_projection(projection)
    if case != "eligible":
        assert row["display_status"] == "unsubmitted"
        assert row["reason"] == {
            "narrow": "commission_net_equation_failed", "unavailable": "commission_unavailable",
            "missing_current": "commission_unavailable", "increased": "commission_rate_increased"}[case]


def test_fee_outage_does_not_interrupt_existing_filled_plan_protection(tmp_path):
    record, spec, policy, fee = marked_record()
    intent = compile_demo_intent(record, spec, task_id="protect", config=config(),
        frozen_execution_epoch="epoch-1", now_ms=NOW, market_snapshot_hash="a"*64,
        demo_execution_policy=policy.model_dump(mode="json"))
    exchange = NetExchange()
    original = exchange.snapshot
    fees = [fee]
    exchange.snapshot = lambda **kwargs: original(**kwargs).model_copy(update={"fee_schedules": tuple(fees)})
    paths = DemoPaths(tmp_path)
    spool = DemoSpool(paths)
    store = DemoStore(paths.state_db)
    atomic_json(paths.control, dict(cutover_id="epoch-1", generated_at_ms=NOW, running=True))
    spool.publish(intent)
    worker = DemoWorker(config(), paths, exchange, store, spool, now_ms=lambda: NOW)
    try:
        worker.tick()
        exchange.fill(store, intent)
        fees.clear()
        worker.tick()
        assert len([c for c in exchange.calls if c[0] == "entry"]) == 1
        protection = [c for c in exchange.calls if c[0] == "oco"]
        assert len(protection) == 1
        assert protection[0][1]["stop"] == intent.stop
        assert protection[0][1]["tp1"] == intent.tp1
    finally:
        worker.close()


def test_legacy_intent_shape_and_digest_do_not_gain_null_context():
    from tests.unit.test_demo_plans import intent
    old = intent()
    raw = old.model_dump(mode="json")
    assert "trade_execution_context" not in raw
    assert DemoIntent.model_validate(raw).content_digest == old.content_digest
    assert DemoIntent.seal(old.digest_payload()).model_dump(mode="json") == raw


def test_dispatcher_freezes_verified_account_fee_and_original_task_on_retry(tmp_path):
    from pa_agent.config.settings import Settings
    from pa_agent.execution.demo_dispatch import OfficialExecutionDispatcher
    from pa_agent.monitoring.task_factory import create_frozen_analysis_task
    from tests.integration.conftest import make_frame
    frame = replace(make_frame(), symbol="BTCUSDT")
    close = int(frame.bars[0].ts_open)+3_600_000
    paths = DemoPaths(tmp_path)
    dispatcher = OfficialExecutionDispatcher(None, DemoSpool(paths), config(), now_ms=lambda: close+500)
    dispatcher.apply_execution_settings(DemoExecutionPolicy(limit_validity_hours=5))
    fee = FeeSchedule(inst_id="BTC-USDT-SWAP", maker="-0.0002", taker="-0.0005", observed_at_ms=close)
    projection = dict(environment="okx_demo_v1", schema_version=1, exchange_observed_at_ms=close,
        fee_scope=hashlib.sha256(b"fee:private-account").hexdigest(), fee_schedules=[fee.model_dump(mode="json")])
    atomic_json(paths.projection, projection)
    frozen = dispatcher.trade_execution_context(frame)
    assert frozen["fee_schedule"] == fee.model_dump(mode="json")
    task = create_frozen_analysis_task(frame, {}, Settings(), source="binance_usdm",
        analysis_fingerprint="a"*64, trade_execution_context=frozen)
    bound = dispatcher.bind_task(task)
    dispatcher.task_lookup = lambda _: bound
    dispatcher.apply_execution_settings(DemoExecutionPolicy(limit_validity_hours=3))
    assert dispatcher.bind_task(task).payload == bound.payload
    atomic_json(paths.projection, projection | {"fee_scope": "different"})
    assert dispatcher.trade_execution_context(frame)["fee_schedule"] is None


def test_restore_refuses_ttl_tamper_even_if_envelope_digest_is_resealed():
    from pa_agent.config.settings import Settings
    from pa_agent.execution.demo_dispatch import OfficialExecutionDispatcher
    from pa_agent.monitoring.frozen_task_codec import decode_task, encode_task
    from pa_agent.monitoring.task_factory import create_frozen_analysis_task
    from tests.integration.conftest import make_frame
    record, _, _, _ = marked_record()
    frame = replace(make_frame(), symbol="BTCUSDT")
    close = int(frame.bars[0].ts_open)+3_600_000
    c = TradeExecutionContext.model_validate(record.role_runtime["trade_execution_context"])
    c = c.model_copy(update={"source_close_ms": close, "expires_at_ms": close+5*3_600_000,
        "frozen_at_ms": close+500, "fee_schedule": None})
    task = create_frozen_analysis_task(frame, {}, Settings(), source="binance_usdm",
        analysis_fingerprint="a"*64, trade_execution_context=c.model_dump(mode="json"))
    dispatcher = OfficialExecutionDispatcher(None, None, config(), now_ms=lambda: NOW)
    dispatcher.apply_execution_settings(DemoExecutionPolicy(limit_validity_hours=5))
    encoded, _ = encode_task(dispatcher.bind_task(task))
    raw = json.loads(encoded)
    raw["payload"]["demo_execution_policy"]["limit_validity_hours"] = 3
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match="frozen_trade_context_ttl_mismatch"):
        decode_task(encoded, hashlib.sha256(encoded.encode()).hexdigest())
