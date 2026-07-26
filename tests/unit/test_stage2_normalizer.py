from __future__ import annotations

import copy

from pa_agent.ai.stage2_normalizer import normalize_stage2


def test_stage2_normalizer_only_canonicalizes_trace_notation() -> None:
    raw = {
        "decision": {"order_type": "buy_limit", "entry_price": 123.456},
        "decision_trace": [
            {
                "node_id": "§10.3",
                "answer": "partial",
                "bar_range": "K1-K5",
            }
        ],
    }
    before = copy.deepcopy(raw)

    out = normalize_stage2(raw)

    assert raw == before
    assert out["decision"] == before["decision"]
    assert out["decision_trace"][0]["node_id"] == "10.3"
    assert out["decision_trace"][0]["bar_range"] == "K5-K1"
    assert out["decision_trace"][0]["answer"] == "partial"


def test_stage2_feature_flag_removes_next_bar_and_keeps_cycle_as_auxiliary_stub() -> None:
    raw = {
        "decision": {},
        "decision_trace": [],
        "next_bar_prediction": {"direction": "bullish"},
        "next_cycle_prediction": {"cycle": "normal_channel"},
    }

    out = normalize_stage2(raw, skip_next_bar=True)

    assert "next_bar_prediction" not in out
    assert out["next_cycle_prediction"]["unpredictable"] is True
    assert out["next_cycle_prediction"]["cycle"] is None


def test_stage2_vpa_off_strips_vpa_without_touching_order() -> None:
    raw = {
        "decision": {"order_type": "不下单"},
        "decision_trace": [],
        "vpa_impact": {
            "background_relation": "supports",
            "entry_timing_relation": "supports",
        },
        "wyckoff_signal": "spring",
    }

    out = normalize_stage2(raw)

    assert out["decision"] == raw["decision"]
    assert "vpa_impact" not in out
    assert "wyckoff_signal" not in out


def test_stage2_normalizer_derives_probability_summary_labels() -> None:
    raw = {
        "decision": {},
        "decision_trace": [],
        "next_bar_prediction": {
            "unpredictable": False,
            "direction": "neutral",
            "probabilities": {"bullish": 60, "bearish": 30, "neutral": 10},
        },
        "next_cycle_prediction": {
            "unpredictable": False,
            "cycle": "trading_range",
            "probabilities": {
                "spike": 5,
                "micro_channel": 5,
                "tight_channel": 5,
                "normal_channel": 10,
                "broad_channel": 40,
                "trending_tr": 15,
                "trading_range": 15,
                "extreme_tr": 5,
            },
        },
    }

    out = normalize_stage2(raw)

    assert out["next_bar_prediction"]["direction"] == "bullish"
    assert out["next_cycle_prediction"]["cycle"] == "broad_channel"
    assert raw["next_bar_prediction"]["direction"] == "neutral"


def test_stage2_normalizer_degrades_invalid_auxiliary_prediction() -> None:
    raw = {
        "decision_trace": [],
        "next_cycle_prediction": {
            "unpredictable": False,
            "cycle": "trading_range",
            "probabilities": {"trading_range": 100},
        },
    }

    prediction = normalize_stage2(raw)["next_cycle_prediction"]

    assert prediction["unpredictable"] is True
    assert prediction["cycle"] is None
    assert prediction["direction"] is None
    assert prediction["probabilities"] is None


def test_stage2_normalizer_clears_values_implied_absent_by_no_order() -> None:
    raw = {
        "decision": {
            "order_type": "不下单",
            "order_direction": "做多",
            "entry_price": 100.0,
            "trade_confidence": 30,
            "estimated_win_rate": 45,
            "estimated_win_rate_reasoning": "placeholder",
        },
        "decision_trace": [],
    }

    decision = normalize_stage2(raw)["decision"]

    assert decision["order_direction"] == "做多"
    assert decision["entry_price"] == 100.0
    assert decision["trade_confidence"] is None
    assert decision["estimated_win_rate"] is None
    assert decision["estimated_win_rate_reasoning"] is None


def test_stage2_normalizer_canonicalizes_untriggered_entry_bar_state() -> None:
    raw = {
        "decision": {},
        "decision_trace": [],
        "bar_analysis": {
            "entry_bar": {
                "bar": None,
                "strength": "strong",
                "follow_through": True,
                "still_valid": False,
                "freshness": "pending",
            }
        },
    }

    entry_bar = normalize_stage2(raw)["bar_analysis"]["entry_bar"]

    assert entry_bar == {
        "bar": None,
        "strength": "not_triggered",
        "follow_through": None,
        "still_valid": None,
        "freshness": "pending",
    }
