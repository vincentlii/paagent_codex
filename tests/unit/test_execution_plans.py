from __future__ import annotations

from types import SimpleNamespace

import pytest

from pa_agent.data.instrument import InstrumentSpec
from pa_agent.execution.plans import market_observed_at_ms, official_paper_plan, research_arm_plan
from pa_agent.experiments.pa_vpa_aabb import AABB_PROTOCOL_VERSION


def _spec() -> InstrumentSpec:
    return InstrumentSpec(
        provider="binance_usdm",
        venue="BINANCE",
        symbol="XAUUSDT",
        instrument_type="tradifi_perpetual",
        tick_size=0.01,
        quantity_step=0.001,
        minimum_quantity=0.001,
        maximum_quantity=1000.0,
        minimum_notional=5.0,
    )


def _record(
    *,
    path: str = "pa_only",
    mode: str = "shadow",
    arm: str | None = None,
    order_type: str = "\u9650\u4ef7\u5355",
):
    experiment = None
    if arm is not None:
        experiment = {
            "protocol_version": "pa_vpa_aabb_v1",
            "arm_id": arm,
            "group": arm[0],
        }
    return SimpleNamespace(
        meta=SimpleNamespace(run_id=f"run-{arm or 'official'}", symbol="XAUUSDT", timeframe="1h"),
        exception=None,
        run_outcome={
            "status": "completed",
            "official_path": path,
            "effective_vpa_mode": mode,
        },
        experiment_context=experiment,
        stage1_diagnosis={"gate_result": "proceed"},
        joint_stage2=None,
        joint_decision=None,
        snapshot_context={
            "source_identity": {
                "provider": "binance_usdm",
                "requested_symbol": "XAUUSDT",
                "resolved_symbol": "XAUUSDT",
                "venue": "BINANCE",
                "instrument_type": "tradifi_perpetual",
            },
            "instrument_spec": _spec().to_dict(),
        },
        kline_data=[{
            "seq": 1,
            "ts_open": 2000,
            "open": 2005.0,
            "high": 2008.0,
            "low": 1998.0,
            "close": 2004.0,
            "volume": 100.0,
            "amount": 200400.0,
            "closed": True,
        }],
        stage2_decision={
            "decision": {
                "order_type": order_type,
                "order_direction": "\u505a\u591a",
                "entry_price": 2000.0,
                "stop_loss_price": 1990.0,
                "take_profit_price": 2020.0,
                "take_profit_price_2": 2030.0,
            }
        },
    )


def test_official_plan_uses_only_validated_pa_fields() -> None:
    plan = official_paper_plan(_record(), _spec(), dedupe_key="task-1")

    assert plan is not None
    assert plan.arm == "pa_official"
    assert plan.side == "long"
    assert plan.order_type == "limit"
    assert plan.created_bar_ts == 2000
    assert plan.dedupe_key == "task-1"


def test_market_observation_time_uses_frozen_time_even_when_zero() -> None:
    record = _record()
    record.meta.timestamp_local_ms = 3000
    record.role_runtime = {"analysis_as_of_ms": 2000}
    record.analysis_timing = SimpleNamespace(analysis_as_of_ms=0)
    assert market_observed_at_ms(record) == 0
    record.analysis_timing = None
    assert market_observed_at_ms(record) == 2000
    record.role_runtime = {}
    assert market_observed_at_ms(record) == 3000


def test_official_plan_keeps_tp2_optional() -> None:
    record = _record()
    record.stage2_decision["decision"]["take_profit_price_2"] = None

    plan = official_paper_plan(record, _spec(), dedupe_key="task-optional-tp2")

    assert plan is not None
    assert plan.take_profit_price_2 is None


def test_official_plan_respects_program_execution_disposition() -> None:
    record = _record()
    record.stage2_decision["execution_disposition"] = {
        "status": "suppressed",
        "candidate_order_type": record.stage2_decision["decision"]["order_type"],
        "effective_order_type": "不下单",
        "reason_codes": ["confidence_below_threshold"],
        "checks": {},
    }

    assert official_paper_plan(record, _spec(), dedupe_key="task-1") is None


def test_official_plan_rejects_joint_or_experiment_authority() -> None:
    with pytest.raises(ValueError, match="PA-only"):
        official_paper_plan(
            _record(path="joint", mode="soft", arm="B1"),
            _spec(),
            dedupe_key="task-1",
        )


def test_research_plan_enforces_assigned_a_and_b_paths() -> None:
    a = research_arm_plan(
        _record(path="pa_only", mode="off", arm="A1"),
        _spec(),
        market_snapshot_hash="a" * 64,
    )
    b = research_arm_plan(
        _record(path="joint", mode="soft", arm="B1"),
        _spec(),
        market_snapshot_hash="a" * 64,
    )

    assert a is not None and a.arm == "A1"
    assert b is not None and b.arm == "B1"


def test_research_b_fallback_keeps_itt_pa_decision() -> None:
    fallback = research_arm_plan(
        _record(path="pa_only", mode="soft", arm="B2"),
        _spec(),
        market_snapshot_hash="b" * 64,
    )

    assert fallback is not None
    assert fallback.arm == "B2"


def test_research_plan_accepts_current_aabb_protocol() -> None:
    record = _record(path="joint", mode="soft", arm="B1")
    record.experiment_context["protocol_version"] = AABB_PROTOCOL_VERSION

    plan = research_arm_plan(
        record,
        _spec(),
        market_snapshot_hash="c" * 64,
    )

    assert plan is not None and plan.arm == "B1"


def test_plan_rejects_cross_contract_execution() -> None:
    other = _spec().__class__(**{**_spec().__dict__, "instrument_type": "spot"})

    with pytest.raises(ValueError, match="identity"):
        official_paper_plan(_record(), other, dedupe_key="task-1")
