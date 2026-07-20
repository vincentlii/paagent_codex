from __future__ import annotations

from pa_agent.orchestrator.two_stage import _apply_confidence_threshold


def test_confidence_threshold_is_persisted_as_an_execution_policy() -> None:
    stage2 = {
        "decision": {
            "order_type": "限价单",
            "order_direction": "做多",
            "entry_price": 100.0,
            "take_profit_price": 110.0,
            "take_profit_price_2": 120.0,
            "stop_loss_price": 95.0,
            "entry_rule": "test",
            "entry_basis_bar": "K1",
            "entry_basis_extreme": "low",
            "trade_confidence": 65,
            "estimated_win_rate": 54,
            "estimated_win_rate_reasoning": "PA baseline",
            "reasoning": "PA baseline",
        },
        "decision_trace": [],
        "terminal": {"node_id": "11.2", "outcome": "trade", "label": "PA"},
    }

    out = _apply_confidence_threshold(stage2, 70)

    assert out["decision"]["order_type"] == "不下单"
    assert out["decision"]["estimated_win_rate"] is None
    assert out["terminal"]["node_id"] == "confidence_threshold"
    assert out["decision_trace"][-1]["answer"] == "否"
    assert out["confidence_threshold"]["result"] == "rejected"
