"""Execution price grids are deterministic and leave model proposals intact."""
import copy
import json
from dataclasses import replace
from decimal import Decimal

import pytest

from pa_agent.ai.execution_price_policy import ExecutionPricePolicy
from pa_agent.ai.execution_policy import apply_confidence_execution_policy, effective_decision
from pa_agent.ai.fact_catalog import build_analysis_fact_catalog
from pa_agent.ai.pa_provider_contracts import materialize_stage2_draft
from pa_agent.ai.take_profit_policy import FixedRiskRewardPolicy
from tests.integration.conftest import VALID_STAGE1, make_frame
from tests.unit.test_fixed_risk_reward import trade_draft


def policy(tick="0.1"):
    return ExecutionPricePolicy(inst_id="XAU-USDT-SWAP", tick_size=tick)


@pytest.mark.parametrize("side,entry,stop,tp,expected", [
    ("做空", "4101.32", "4109.69", "4088.8", ("4101.4", "4109.7", "4088.8")),
    ("做多", "4101.38", "4092.31", "4115.37", ("4101.3", "4092.3", "4115.3")),
    ("做多", "100.25", "99.75", "102.75", ("100.25", "99.75", "102.75")),
])
def test_conservative_rounding_uses_actual_tick_multiples(side, entry, stop, tp, expected):
    p = policy("0.25" if entry == "100.25" else "0.1")
    decision = dict(order_type="限价单", order_direction=side, entry_price=entry,
                    stop_loss_price=stop, take_profit_price=tp, take_profit_price_2=None)
    before = copy.deepcopy(decision)
    result = p.prices(decision)
    assert tuple(Decimal(result[k]) for k in ("entry_price", "stop_loss_price", "take_profit_price")) == tuple(map(Decimal, expected))
    assert decision == before


def test_actual_xau_fixed_rr_recomputes_target_from_effective_entry_and_stop():
    draft = trade_draft()
    draft["decision"].update(order_type="限价单", order_direction="做空", entry_price=4101.32,
                             stop_loss_price=4109.69, estimated_win_rate=46, trade_confidence=51)
    before = copy.deepcopy(draft)
    canonical = materialize_stage2_draft(draft, stage1=VALID_STAGE1,
        catalog=build_analysis_fact_catalog(make_frame()),
        take_profit_policy=FixedRiskRewardPolicy(tick_size="0.1"), execution_price_policy=policy())
    assert draft == before
    assert canonical["decision"]["entry_price"] == 4101.32
    assert canonical["decision"]["stop_loss_price"] == 4109.69
    effective = effective_decision(canonical)
    assert Decimal(str(effective["entry_price"])) == Decimal("4101.4")
    assert Decimal(str(effective["stop_loss_price"])) == Decimal("4109.7")
    assert Decimal(str(effective["take_profit_price"])) == Decimal("4089")
    checked = apply_confidence_execution_policy(canonical, 40)
    assert checked["execution_disposition"]["status"] == "eligible"
    assert effective_decision(checked) == effective
    assert checked["decision"]["estimated_win_rate"] == 46
    assert checked["decision"]["trade_confidence"] == 51
    assert canonical["terminal"]["outcome"] == "trade"


def test_projection_is_recomputed_not_trusted_when_consumed():
    draft = trade_draft()
    draft["decision"]["order_type"] = "限价单"
    canonical = materialize_stage2_draft(draft, stage1=VALID_STAGE1,
        catalog=build_analysis_fact_catalog(make_frame()),
        take_profit_policy=FixedRiskRewardPolicy(tick_size="0.1"), execution_price_policy=policy())
    canonical["execution_disposition"]["price_adjustment"]["effective_prices"]["entry_price"] = "1"
    with pytest.raises(ValueError, match="price.*mismatch"):
        effective_decision(canonical)


def test_collapsed_grid_is_suppressed_without_adding_ticks_or_changing_original():
    draft = trade_draft()
    draft["decision"].update(order_type="限价单", order_direction="做空", entry_price=100.01,
                             stop_loss_price=100.09, estimated_win_rate=90)
    result = materialize_stage2_draft(draft, stage1=VALID_STAGE1,
        catalog=build_analysis_fact_catalog(make_frame()),
        take_profit_policy=FixedRiskRewardPolicy(tick_size="0.1"), execution_price_policy=policy())
    assert result["execution_disposition"]["status"] == "suppressed"
    assert result["terminal"]["outcome"] == "reject"
    assert result["decision"]["entry_price"] == 100.01


def test_legacy_materialization_does_not_apply_new_execution_rounding():
    draft = trade_draft()
    result = materialize_stage2_draft(draft, stage1=VALID_STAGE1,
        catalog=build_analysis_fact_catalog(make_frame()), take_profit_policy=FixedRiskRewardPolicy(tick_size="0.1"))
    assert "price_adjustment" not in result.get("execution_disposition", {})


@pytest.mark.parametrize("codex", [False, True])
def test_real_joint_validator_and_role_protocol_use_the_same_frozen_price_policy(codex):
    from tests.unit.test_joint_pipeline_runtime import run_joint, joint_draft
    draft = joint_draft()
    draft["decision"].pop("take_profit_price")
    draft["decision"].pop("take_profit_price_2")
    draft["decision"].update(order_type="限价单", entry_price=2350.03, stop_loss_price=2340.09,
                             estimated_win_rate=60)
    # Keep fixture's bullish support context and original entry evidence.
    frame = make_frame()
    frame = replace(frame, data_metadata={**frame.data_metadata,
        "execution_price_policy": policy().model_dump(mode="json")})
    requests = []
    record, calls, _ = run_joint(codex=codex, frame=frame, captured_requests=requests,
        stage2_drafts=[draft], take_profit_policy=FixedRiskRewardPolicy(tick_size="0.1"))
    assert record.exception is None, record.exception
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    assert record.stage2_decision["decision"]["entry_price"] == 2350.03
    assert Decimal(str(effective_decision(record.stage2_decision)["entry_price"])) == Decimal("2350")
    assert record.role_runtime["execution_price_policy"] == policy().model_dump(mode="json")
    if codex:
        stage2 = next(r for r in requests if r.role == "stage2")
        assert "execution_price_grid_v1" in json.dumps(stage2.agent_context)
    else:
        assert "execution_price_grid_v1" in json.dumps(record.stage2_messages)


def test_new_task_policy_is_frozen_and_tampering_fails_recovery():
    from pa_agent.config.settings import Settings
    from pa_agent.monitoring.task_factory import create_frozen_analysis_task
    from pa_agent.monitoring.frozen_task_codec import encode_task, decode_task
    import hashlib
    frame = make_frame()
    # Fixture symbol is XAUUSD, whereas this interface accepts the production mapping.
    frame = replace(frame, symbol="XAUUSDT")
    task = create_frozen_analysis_task(frame, {}, Settings(), source="binance_usdm",
        analysis_fingerprint="a" * 64, execution_price_policy=policy().model_dump(mode="json"))
    restored = decode_task(*encode_task(task))
    assert restored.payload.frame.data_metadata["execution_price_policy"] == policy().model_dump(mode="json")
    raw = json.loads(encode_task(task)[0])
    raw["payload"]["frame"]["data_metadata"]["execution_price_policy"]["tick_size"] = "1"
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match="snapshot mismatch"):
        decode_task(encoded, hashlib.sha256(encoded.encode()).hexdigest())


def test_real_validator_reports_collapsed_execution_as_suppressed_not_model_failure():
    from tests.unit.test_joint_pipeline_runtime import run_joint, joint_draft
    draft = joint_draft()
    draft["decision"].pop("take_profit_price")
    draft["decision"].pop("take_profit_price_2")
    draft["decision"].update(order_type="限价单", entry_price=2350.09, stop_loss_price=2350.01,
                             estimated_win_rate=90)
    frame = make_frame()
    frame = replace(frame, data_metadata={**frame.data_metadata,
        "execution_price_policy": policy().model_dump(mode="json")})
    record, calls, _ = run_joint(frame=frame, stage2_drafts=[draft],
        take_profit_policy=FixedRiskRewardPolicy(tick_size="0.1"))
    assert record.exception is None, record.exception
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    assert record.stage2_decision["terminal"]["outcome"] == "reject"
    assert record.stage2_decision["execution_disposition"]["status"] == "suppressed"


def test_compiled_demo_intent_uses_effective_prices_and_verifies_new_metadata():
    from tests.unit.test_demo_plans import intent, contract
    from pa_agent.execution.demo_contracts import DemoIntent
    from pa_agent.execution.demo_plans import bind_demo_limit
    raw = dict(order_type="限价单", order_direction="做空", entry_price=4101.32,
               stop_loss_price=4109.69, take_profit_price=4088.8, take_profit_price_2=None)
    fixed = FixedRiskRewardPolicy(tick_size="0.1")
    adjustment = policy().adjustment(raw, fixed)
    xau = intent(inst_id="XAU-USDT-SWAP", source_identity={"provider": "binance_usdm", "venue": "BINANCE",
        "symbol": "XAUUSDT", "instrument_type": "tradifi_perpetual"}, side="sell", entry="4101.4",
        stop="4109.7", tp1="4089.0", take_profit_policy=fixed, price_adjustment=adjustment)
    request = bind_demo_limit(xau, contract(inst_id="XAU-USDT-SWAP"),
        account_key="offline", available_margin=Decimal("10000"))
    assert request.px == xau.entry and request.stop == xau.stop and request.tp1 == xau.tp1
    assert DemoIntent.model_validate_json(xau.model_dump_json()) == xau
    with pytest.raises(ValueError, match="execution_price_tick_changed"):
        bind_demo_limit(xau, contract(inst_id="XAU-USDT-SWAP", tick="0.01"),
            account_key="offline", available_margin=Decimal("10000"))
    changed = xau.model_dump(mode="json")
    changed["price_adjustment"]["original_prices"]["stop_loss_price"] = 4110
    with pytest.raises(ValueError, match="price.*mismatch"):
        DemoIntent.seal(changed)


def test_pa_mode_rechecks_equation_after_grid_without_changing_model_target():
    draft = trade_draft()
    draft["decision"].update(order_type="限价单", order_direction="做多", entry_price=100.01,
        stop_loss_price=99.09, take_profit_price=101.09, take_profit_price_2=None, estimated_win_rate=48)
    result = materialize_stage2_draft(draft, stage1=VALID_STAGE1,
        catalog=build_analysis_fact_catalog(make_frame()), execution_price_policy=policy())
    assert result["decision"]["take_profit_price"] == 101.09
    assert result["terminal"]["outcome"] == "reject"
    assert result["execution_disposition"]["reason_codes"] == ["trader_equation_failed"]
    assert result["decision"]["estimated_win_rate"] == 48


@pytest.mark.parametrize("suppressed", [False, True])
def test_host_compiler_and_worker_replay_execute_only_normalized_plan(tmp_path, suppressed):
    from tests.unit.test_demo_execution_flow import source_record, config, Exchange, NOW
    from pa_agent.execution.demo_plans import compile_demo_intent
    from pa_agent.execution.demo_contracts import DemoPaths
    from pa_agent.execution.demo_spool import DemoSpool
    from pa_agent.execution.demo_store import DemoStore
    from pa_agent.execution.demo_worker import DemoWorker
    from pa_agent.ai.execution_policy import apply_stage2_execution_policy
    from pa_agent.operations.demo_trading import safe_demo_projection
    r, spec = source_record()
    p = ExecutionPricePolicy(inst_id="BTC-USDT-SWAP", tick_size="0.1")
    r.stage2_decision["decision"].update(entry_price=50000.03, stop_loss_price=49000.09,
        take_profit_price=52000.07, take_profit_price_2=None, estimated_win_rate=60)
    r.role_runtime = {"execution_price_policy": p.model_dump(mode="json")}
    adjustment = p.adjustment(r.stage2_decision["decision"])
    r.stage2_decision["execution_disposition"] = {"price_adjustment": adjustment}
    r.stage2_decision = apply_stage2_execution_policy(r.stage2_decision)
    if suppressed:
        r.stage2_decision["decision"]["estimated_win_rate"] = 1
        r.stage2_decision = apply_stage2_execution_policy(r.stage2_decision)
    i = compile_demo_intent(r, spec, task_id="normalized", config=config(), frozen_execution_epoch="epoch-1",
        now_ms=NOW, market_snapshot_hash="a" * 64)
    if suppressed:
        assert i.disposition == "rejected"
        assert i.source_reason == "trader_equation_failed"
    else:
        assert i.entry == Decimal("50000") and i.stop == Decimal("49000") and i.tp1 == Decimal("52000")
        assert i.price_adjustment == adjustment
    paths = DemoPaths(tmp_path)
    spool = DemoSpool(paths)
    spool.publish(i)
    from pa_agent.execution.demo_spool import atomic_json
    atomic_json(paths.control, {"cutover_id": "epoch-1", "generated_at_ms": NOW, "running": True})
    exchange = Exchange()
    for _ in range(2):
        worker = DemoWorker(config(), paths, exchange, DemoStore(paths.state_db), spool, now_ms=lambda: NOW)
        try:
            projection = safe_demo_projection(worker.tick())
        finally:
            worker.close()
    entries = [call for call in exchange.calls if call[0] == "entry"]
    assert len(entries) == (0 if suppressed else 1)
    if suppressed:
        assert projection["orders"][0]["display_status"] == "unsubmitted"
    else:
        assert entries[0][1].px == i.entry
        assert projection["orders"][0]["original_entry"] == 50000.03
        assert projection["orders"][0]["entry"] == "50000.0"
