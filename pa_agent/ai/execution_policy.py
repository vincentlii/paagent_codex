"""Deterministic execution eligibility projected from an immutable Stage 2 candidate."""
from __future__ import annotations

import copy
from typing import Any

from pa_agent.util.trade_metrics import validate_order_trade_metrics
from pa_agent.ai.execution_price_policy import adjusted_decision

_EXECUTABLE_ORDER_TYPES = frozenset({"限价单", "突破单", "市价单"})
_NO_ORDER = "不下单"


def apply_stage2_execution_policy(stage2: dict[str, Any]) -> dict[str, Any]:
    """Attach deterministic execution eligibility without rewriting model analysis."""
    out = copy.deepcopy(stage2)
    decision = out.get("decision")
    if not isinstance(decision, dict):
        return out

    order_type = str(decision.get("order_type") or "")
    if order_type not in _EXECUTABLE_ORDER_TYPES:
        out["execution_disposition"] = {
            "status": "not_applicable",
            "candidate_order_type": order_type or None,
            "effective_order_type": _NO_ORDER,
            "reason_codes": [],
            "checks": {},
        }
        return out

    adjustment = copy.deepcopy((out.get("execution_disposition") or {}).get("price_adjustment"))
    trade_context = copy.deepcopy((out.get("execution_disposition") or {}).get("trade_execution_context"))
    errors = validate_order_trade_metrics(adjusted_decision(out))
    out["execution_disposition"] = {
        "status": "suppressed" if errors else "eligible",
        "candidate_order_type": order_type,
        "effective_order_type": _NO_ORDER if errors else order_type,
        "reason_codes": _metric_reason_codes(errors),
        "checks": {
            "trade_metrics": {
                "passed": not errors,
                "errors": errors,
            }
        },
    }
    if adjustment is not None:
        out["execution_disposition"]["price_adjustment"] = adjustment
    if trade_context is not None:
        from pa_agent.ai.trade_execution_context import TradeExecutionContext
        context = TradeExecutionContext.model_validate(trade_context)
        disposition = out["execution_disposition"]
        disposition["trade_execution_context"] = context.model_dump(mode="json")
        if not errors:
            check = context.commission_check(adjusted_decision(out))
            disposition["checks"]["commission_net"] = check
            if not check["passed"]:
                disposition.update(status="suppressed", effective_order_type=_NO_ORDER)
                disposition["reason_codes"].append(
                    "commission_net_equation_failed" if check["status"] == "available" else "commission_unavailable")
    return out


def apply_confidence_execution_policy(
    stage2: dict[str, Any],
    threshold: int,
) -> dict[str, Any]:
    """Add the configured confidence check to the execution projection."""
    out = apply_stage2_execution_policy(stage2)
    decision = out.get("decision")
    disposition = out.get("execution_disposition")
    if (
        not isinstance(decision, dict)
        or not isinstance(disposition, dict)
        or threshold <= 0
        or disposition.get("candidate_order_type") not in _EXECUTABLE_ORDER_TYPES
    ):
        return out

    try:
        confidence = int(decision.get("trade_confidence"))
    except (TypeError, ValueError):
        confidence = -1
    passed = confidence >= threshold
    checks = copy.deepcopy(disposition.get("checks") or {})
    checks["confidence_threshold"] = {
        "configured": int(threshold),
        "observed": confidence,
        "passed": passed,
    }
    disposition["checks"] = checks
    if not passed:
        reasons = list(disposition.get("reason_codes") or [])
        if "confidence_below_threshold" not in reasons:
            reasons.append("confidence_below_threshold")
        disposition.update(
            {
                "status": "suppressed",
                "effective_order_type": _NO_ORDER,
                "reason_codes": reasons,
            }
        )
    out["execution_disposition"] = disposition
    return out


def effective_order_type(stage2: Any) -> str:
    """Return the program-authorized order type for execution consumers."""
    if not isinstance(stage2, dict):
        return ""
    decision = stage2.get("decision")
    if not isinstance(decision, dict):
        return ""
    candidate = str(decision.get("order_type") or "")
    disposition = stage2.get("execution_disposition")
    if not isinstance(disposition, dict):
        return candidate
    effective = str(disposition.get("effective_order_type") or "")
    if disposition.get("candidate_order_type") != candidate:
        raise ValueError("execution disposition does not match the candidate decision")
    verify_commission_projection(stage2)
    return effective


def verify_commission_projection(stage2: dict) -> None:
    disposition = stage2.get("execution_disposition") or {}
    if disposition.get("trade_execution_context") is None:
        return
    expected = apply_stage2_execution_policy(stage2)["execution_disposition"]
    if expected["checks"].get("commission_net") != disposition.get("checks", {}).get("commission_net"):
        raise ValueError("commission_projection_mismatch")
    if expected["status"] == "suppressed" and disposition.get("effective_order_type") != _NO_ORDER:
        raise ValueError("commission_eligibility_mismatch")


def effective_decision(stage2: dict) -> dict:
    return adjusted_decision(stage2)


def _metric_reason_codes(errors: list[str]) -> list[str]:
    codes: list[str] = []
    for error in errors:
        if "trader equation fails" in error:
            code = "trader_equation_failed"
        elif "estimated_win_rate" in error:
            code = "estimated_win_rate_unavailable"
        elif "take_profit_price_2" in error:
            code = "invalid_take_profit_2"
        else:
            code = "invalid_order_geometry"
        if code not in codes:
            codes.append(code)
    return codes
