"""Deterministic execution safety applied after semantic normalization."""
from __future__ import annotations

import copy
from typing import Any

from pa_agent.util.trade_metrics import validate_order_trade_metrics


def apply_stage2_execution_policy(stage2: dict[str, Any]) -> dict[str, Any]:
    """Block an unsafe order while preserving the model response in audit data."""
    out = copy.deepcopy(stage2)
    decision = out.get("decision")
    trace = out.get("decision_trace")
    if not isinstance(decision, dict) or not isinstance(trace, list):
        return out
    errors = validate_order_trade_metrics(decision)
    if not errors:
        return out
    equation_index = next(
        (
            index
            for index, item in enumerate(trace)
            if isinstance(item, dict)
            and str(item.get("node_id") or "") == "10.3"
        ),
        None,
    )
    if equation_index is None:
        return out

    reason = f"Program execution policy rejected this candidate: {errors[0]}"
    original_order_type = decision.get("order_type")
    decision["order_type"] = "不下单"
    for key in (
        "order_direction",
        "entry_price",
        "entry_basis_bar",
        "entry_basis_extreme",
        "entry_rule",
        "take_profit_price",
        "take_profit_price_2",
        "stop_loss_price",
        "trade_confidence",
        "estimated_win_rate",
        "estimated_win_rate_reasoning",
        "invalidation_condition",
    ):
        decision[key] = None
    decision["reasoning"] = reason[:280]
    decision["trade_confidence_reasoning"] = (
        f"Candidate {original_order_type} was blocked by deterministic "
        "execution policy."
    )
    decision["risk_assessment"] = reason
    watch_points = [str(item) for item in decision.get("watch_points", [])]
    policy_watch = "Reassess the candidate on a new closed-bar snapshot."
    if policy_watch not in watch_points:
        watch_points.append(policy_watch)
    decision["watch_points"] = watch_points

    equation_node = copy.deepcopy(trace[equation_index])
    equation_node["answer"] = "否"
    equation_node["reason"] = reason
    out["decision_trace"] = [*trace[:equation_index], equation_node]
    out["terminal"] = {
        "node_id": "10.3",
        "outcome": "reject",
        "label": "Program execution policy rejected the candidate",
    }
    return out
