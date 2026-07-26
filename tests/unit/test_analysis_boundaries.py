from __future__ import annotations

import copy
from types import SimpleNamespace

from pa_agent.ai.kline_features import compute_kline_geometry_features
from pa_agent.ai.market_features import compute_simple_market_features
from pa_agent.ai.stage1_normalizer import normalize_stage1
from pa_agent.ai.stage2_normalizer import normalize_stage2
from pa_agent.orchestrator.two_stage import _accumulate_usage
from tests.integration.conftest import make_frame


def test_program_features_are_numeric_geometry_not_market_verdicts() -> None:
    frame = make_frame()
    bar_fact = compute_kline_geometry_features(frame, limit=1)[0]
    market_facts = compute_simple_market_features(frame)

    assert bar_fact.seq == 1
    assert bar_fact.body_size == abs(frame.bars[0].close - frame.bars[0].open)
    assert not hasattr(bar_fact, "bar_type")
    assert not hasattr(bar_fact, "signal")
    assert not hasattr(market_facts, "trend")
    assert not hasattr(market_facts, "breakout")
    assert not hasattr(market_facts, "support_levels")
def test_stage1_normalizer_does_not_replace_llm_market_interpretation() -> None:
    raw = {
        "cycle_position": "trading_range",
        "direction": "bearish",
        "entry_setup": "upper-boundary failed breakout",
        "support_levels": ["K20 low"],
        "resistance_levels": ["K5 high"],
        "detected_patterns": ["WEDGE", "wedge"],
        "gate_trace": [],
    }
    before = copy.deepcopy(raw)

    out = normalize_stage1(raw)

    assert raw == before
    assert out["cycle_position"] == "trading_range"
    assert out["direction"] == "bearish"
    assert out["entry_setup"] == raw["entry_setup"]
    assert out["support_levels"] == raw["support_levels"]
    assert out["resistance_levels"] == raw["resistance_levels"]
    assert out["detected_patterns"] == ["wedge"]


def test_stage2_normalizer_does_not_repair_or_reprice_unrecognized_order() -> None:
    raw = {
        "decision": {
            "order_type": "buy_limit",
            "order_direction": "long",
            "entry_price": 123.456,
            "stop_loss_price": 120.0,
            "take_profit_price": 130.0,
        },
        "decision_trace": [],
    }
    before = copy.deepcopy(raw)

    out = normalize_stage2(raw, skip_next_bar=False)

    assert raw == before
    assert out["decision"] == raw["decision"]
    assert out["next_bar_prediction"]["unpredictable"] is True


def test_model_runtime_metrics_are_accumulated_with_token_usage() -> None:
    reply = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=100,
            cached_prompt_tokens=60,
            completion_tokens=20,
            total_tokens=120,
        ),
        latency_ms=123.6,
    )

    first = _accumulate_usage({}, reply)
    total = _accumulate_usage(first, reply)

    assert total == {
        "prompt_tokens": 200,
        "cached_prompt_tokens": 120,
        "completion_tokens": 40,
        "total_tokens": 240,
        "api_calls": 2,
        "model_latency_ms": 248,
    }
