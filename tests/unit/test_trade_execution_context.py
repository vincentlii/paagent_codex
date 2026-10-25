import copy
from dataclasses import replace
from decimal import Decimal

import pytest

from pa_agent.ai.execution_policy import apply_confidence_execution_policy
from pa_agent.ai.trade_execution_context import TradeExecutionContext


def context(**changes):
    values = dict(inst_id="XAU-USDT-SWAP", source_close_ms=1800003600000,
                  limit_validity_hours=5, expires_at_ms=1800021600000,
                  frozen_at_ms=1800003600500,
                  fee_schedule=dict(inst_id="XAU-USDT-SWAP", maker="-0.0002", taker="-0.0005",
                                    observed_at_ms=1800003600000))
    return TradeExecutionContext.model_validate(values | changes)


def test_gross_pass_but_commission_net_failure_preserves_model_candidate():
    stage2 = dict(decision=dict(order_type="限价单", order_direction="做多", entry_price=100,
                               stop_loss_price=99, take_profit_price=101.5,
                               take_profit_price_2=None, estimated_win_rate=41, trade_confidence=60),
                  execution_disposition={"trade_execution_context": context().model_dump(mode="json")})
    before = copy.deepcopy(stage2["decision"])
    result = apply_confidence_execution_policy(stage2, 40)
    assert result["decision"] == before
    disposition = result["execution_disposition"]
    assert disposition["checks"]["trade_metrics"]["passed"] is True
    assert disposition["checks"]["commission_net"]["net_expectancy"] == "-0.0750125"
    assert disposition["status"] == "suppressed"
    assert "commission_net_equation_failed" in disposition["reason_codes"]


def test_context_is_frozen_through_task_and_expiry_is_not_extended_on_restore():
    from pa_agent.config.settings import Settings
    from pa_agent.monitoring.frozen_task_codec import decode_task, encode_task
    from pa_agent.monitoring.task_factory import create_frozen_analysis_task
    from tests.integration.conftest import make_frame
    frame = replace(make_frame(), symbol="XAUUSDT")
    close = int(frame.bars[0].ts_open) + 3_600_000
    policy = context(source_close_ms=close, expires_at_ms=close + 5*3_600_000,
                     frozen_at_ms=close+500, fee_schedule=None)
    original = copy.deepcopy(frame.data_metadata)
    task = create_frozen_analysis_task(frame, {}, Settings(), source="binance_usdm",
        analysis_fingerprint="a"*64, trade_execution_context=policy.model_dump(mode="json"))
    restored = decode_task(*encode_task(task))
    assert restored.payload.frame.data_metadata["trade_execution_context"] == policy.model_dump(mode="json")
    assert frame.data_metadata == original


@pytest.mark.parametrize("side,stop,target", [("做多",99,101.5),("做空",101,98.5)])
def test_two_exit_outcomes_and_rebates_do_not_create_fictitious_advantage(side,stop,target):
    c=context(fee_schedule=dict(inst_id="XAU-USDT-SWAP",maker="0.0001",taker="-0.0005",
                               observed_at_ms=1800003600000))
    result=c.commission_check(dict(entry_price=100,stop_loss_price=stop,take_profit_price=target,
                                   order_direction=side,estimated_win_rate=60))
    assert Decimal(result["rate"]) == Decimal("0.0005")
    assert Decimal(result["win_fee"]) == (Decimal("0.10075") if side=="做多" else Decimal("0.09925"))
    assert result["uncovered"] == ["slippage","funding"]
    assert result["passed"]


@pytest.mark.parametrize("codex", [False, True])
def test_joint_chain_delivers_context_only_to_stage2_and_checks_commission(codex):
    import json

    from tests.integration.conftest import make_frame
    from tests.unit.test_joint_pipeline_runtime import joint_draft, run_joint
    frame = replace(make_frame(), symbol="XAUUSDT")
    close = int(frame.bars[0].ts_open) + 3_600_000
    c = context(source_close_ms=close, expires_at_ms=close+5*3_600_000,
                frozen_at_ms=close+500, fee_schedule=None)
    frame = replace(frame, data_metadata={**frame.data_metadata,
                    "trade_execution_context": c.model_dump(mode="json")})
    requests = []
    record, calls, _ = run_joint(codex=codex, frame=frame, captured_requests=requests,
                                 stage2_drafts=[joint_draft()])
    assert record.exception is None, record.exception
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    assert record.role_runtime["trade_execution_context"] == c.model_dump(mode="json")
    assert record.stage2_decision["execution_disposition"]["reason_codes"] == ["commission_unavailable"]
    if codex:
        for request in requests:
            assert ("trade_execution_context_v1" in json.dumps(request.agent_context)) == (request.role == "stage2")
    else:
        assert "trade_execution_context_v1" in json.dumps(record.stage2_messages)
        assert "trade_execution_context_v1" not in json.dumps(record.stage1_messages)


@pytest.mark.parametrize("changes", [
    {"limit_validity_hours": True}, {"expires_at_ms": 1800021600001},
    {"frozen_at_ms": 1800003599999},
    {"fee_schedule": {"inst_id": "ETH-USDT-SWAP", "maker": "0", "taker": "0", "observed_at_ms": 1800003600000}},
    {"fee_schedule": {"inst_id": "XAU-USDT-SWAP", "maker": "NaN", "taker": "0", "observed_at_ms": 1800003600000}},
    {"fee_schedule": {"inst_id": "XAU-USDT-SWAP", "maker": True, "taker": "0", "observed_at_ms": 1800003600000}},
    {"fee_schedule": {"inst_id": "XAU-USDT-SWAP", "maker": "0", "taker": "0", "observed_at_ms": 1800003600501}},
    {"fee_schedule": {"inst_id": "XAU-USDT-SWAP", "maker": "0", "taker": "0", "observed_at_ms": 1800003300499}},
])
def test_invalid_or_stale_facts_never_turn_into_zero_cost(changes):
    with pytest.raises(ValueError):
        context(**changes)


def test_task_identity_changes_for_treatment_but_not_fee_refresh_or_next_bar_cohort():
    from pa_agent.config.settings import Settings
    from pa_agent.monitoring.task_factory import create_frozen_analysis_task
    from tests.integration.conftest import make_frame
    frame = replace(make_frame(), symbol="XAUUSDT")
    close = int(frame.bars[0].ts_open) + 3_600_000
    c = context(source_close_ms=close, expires_at_ms=close+5*3_600_000,
                frozen_at_ms=close+500, fee_schedule=dict(inst_id="XAU-USDT-SWAP",
                maker="-0.0002", taker="-0.0005", observed_at_ms=close))
    def task(value, f=frame):
        return create_frozen_analysis_task(f, {}, Settings(), source="binance_usdm",
            analysis_fingerprint="a"*64, trade_execution_context=value.model_dump(mode="json"))
    first = task(c)
    refreshed = c.model_copy(update={"frozen_at_ms": close+1000,
                                    "fee_schedule": c.fee_schedule.model_copy(update={"observed_at_ms": close+500})})
    assert first.task_id == task(refreshed).task_id
    assert first.cohort_id != task(c.model_copy(update={"limit_validity_hours": 3, "expires_at_ms": close+3*3_600_000})).cohort_id
    assert first.cohort_id != task(c.model_copy(update={"fee_schedule": None})).cohort_id
    next_frame = replace(frame, bars=(replace(frame.bars[0], ts_open=frame.bars[0].ts_open+3_600_000), *frame.bars[1:]))
    shifted = c.model_copy(update={"source_close_ms": close+3_600_000,
        "expires_at_ms": c.expires_at_ms+3_600_000, "frozen_at_ms": c.frozen_at_ms+3_600_000,
        "fee_schedule": c.fee_schedule.model_copy(update={"observed_at_ms": close+3_600_000})})
    assert first.cohort_id == task(shifted, next_frame).cohort_id
    assert first.task_id != task(shifted, next_frame).task_id
    from pa_agent.data.snapshot_bundle import compact_snapshot_context
    marked_frame = replace(frame, data_metadata={**frame.data_metadata,
        "trade_execution_context": c.model_dump(mode="json")})
    assert compact_snapshot_context(marked_frame) == compact_snapshot_context(frame)
    from_metadata = create_frozen_analysis_task(marked_frame, {}, Settings(), source="binance_usdm",
                                               analysis_fingerprint="a"*64)
    assert from_metadata.task_id == first.task_id


def test_extreme_commission_is_suppressed_without_division_by_zero():
    c = context(fee_schedule=dict(inst_id="XAU-USDT-SWAP", maker="-1", taker="-1",
                                observed_at_ms=1800003600000))
    check = c.commission_check(dict(entry_price=100, stop_loss_price=99,
        take_profit_price=101.5, estimated_win_rate=60))
    assert check["passed"] is False
    assert check["breakeven_win_rate"] is None


def test_missing_fee_does_not_change_no_trade_or_legacy_candidate():
    from pa_agent.ai.execution_policy import apply_stage2_execution_policy
    old = dict(decision=dict(order_type="限价单", order_direction="做多", entry_price=100,
                            stop_loss_price=99, take_profit_price=101.5, estimated_win_rate=60))
    assert apply_stage2_execution_policy(old)["execution_disposition"]["status"] == "eligible"
    waiting = {"decision": {"order_type": "不下单"}, "execution_disposition": {
        "trade_execution_context": context(fee_schedule=None).model_dump(mode="json")}}
    result = apply_stage2_execution_policy(waiting)
    assert result["execution_disposition"]["status"] == "not_applicable"
    assert result["execution_disposition"]["reason_codes"] == []


def test_materialization_recomputes_cost_after_tick_and_preserves_structural_prices():
    from pa_agent.ai.execution_policy import effective_order_type
    from pa_agent.ai.execution_price_policy import ExecutionPricePolicy
    from pa_agent.ai.fact_catalog import build_analysis_fact_catalog
    from pa_agent.ai.pa_provider_contracts import materialize_stage2_draft
    from pa_agent.ai.take_profit_policy import FixedRiskRewardPolicy
    from tests.integration.conftest import VALID_STAGE1, make_frame
    from tests.unit.test_fixed_risk_reward import trade_draft
    draft = trade_draft()
    draft["decision"].update(order_type="限价单", order_direction="做空", entry_price=4101.32,
        stop_loss_price=4109.69, estimated_win_rate=46, trade_confidence=51)
    before = copy.deepcopy(draft)
    result = materialize_stage2_draft(draft, stage1=VALID_STAGE1,
        catalog=build_analysis_fact_catalog(make_frame()),
        execution_price_policy=ExecutionPricePolicy(inst_id="XAU-USDT-SWAP", tick_size="0.1"),
        take_profit_policy=FixedRiskRewardPolicy(tick_size="0.1"), trade_execution_context=context())
    assert draft == before
    assert result["decision"]["entry_price"] == 4101.32
    assert result["decision"]["stop_loss_price"] == 4109.69
    assert result["decision"]["estimated_win_rate"] == 46
    d = result["execution_disposition"]
    assert d["checks"]["trade_metrics"]["passed"] is True
    assert Decimal(d["checks"]["commission_net"]["gross_risk"]) == Decimal("8.3")
    assert d["effective_order_type"] == "不下单"
    d["checks"]["commission_net"]["passed"] = True
    with pytest.raises(ValueError, match="commission_projection_mismatch"):
        effective_order_type(result)
