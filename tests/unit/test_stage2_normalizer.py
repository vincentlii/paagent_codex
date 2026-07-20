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


def test_stage2_feature_flag_removes_only_next_bar_prediction() -> None:
    raw = {
        "decision": {},
        "decision_trace": [],
        "next_bar_prediction": {"direction": "bullish"},
        "next_cycle_prediction": {"cycle": "normal_channel"},
    }

    out = normalize_stage2(raw, skip_next_bar=True)

    assert "next_bar_prediction" not in out
    assert out["next_cycle_prediction"] == raw["next_cycle_prediction"]


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

    out = normalize_stage2(raw, vpa_enabled=False)

    assert out["decision"] == raw["decision"]
    assert "vpa_impact" not in out
    assert "wyckoff_signal" not in out
