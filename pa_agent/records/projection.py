"""Deterministic views derived from one canonical analysis record."""
from __future__ import annotations

import copy
from typing import Any


def analysis_model_identity(record: Any) -> dict[str, str | None]:
    """Project observed call identity, retaining mixed models and unknown service."""
    calls = [call for call in record.call_attempts if isinstance(call, dict)]
    runtime = record.role_runtime or {}
    backend = runtime.get("analysis_backend_identity") or {}
    requested = list(dict.fromkeys(
        str(call["requested_model"]) for call in calls if call.get("requested_model")
    ))
    effective = []
    for call in calls:
        model = call.get("effective_model")
        if "effective_model" not in call and call.get("response_hash"):
            model = call.get("model")  # Legacy successful receipts.
        if model and str(model) not in effective:
            effective.append(str(model))
    codex = runtime.get("analysis_backend") == "codex_agent"
    backend_model = (
        backend.get("model") or backend.get("codex_model")
        if codex else
        backend.get("deepseek_model")
    )
    configured_model = (
        runtime.get("requested_model") or record.meta.ai_provider.get("model")
        if not codex else None
    )
    requested_model = (
        " / ".join(requested) if requested else
        backend_model or configured_model
    )
    return {
        "requested_model": requested_model,
        "effective_model": " / ".join(effective) if effective else None,
    }


def usage_from_call_attempts(calls: list[dict[str, Any]]) -> dict[str, int]:
    response_calls = [
        call
        for call in calls
        if isinstance(call, dict) and call.get("response_hash")
    ]
    return {
        "prompt_tokens": sum(max(0, int(call.get("prompt_tokens") or 0)) for call in response_calls),
        "cached_prompt_tokens": sum(
            max(0, int(call.get("cached_prompt_tokens") or 0))
            for call in response_calls
        ),
        "completion_tokens": sum(
            max(0, int(call.get("completion_tokens") or 0))
            for call in response_calls
        ),
        "total_tokens": sum(max(0, int(call.get("total_tokens") or 0)) for call in response_calls),
        "api_calls": len(response_calls),
        "model_latency_ms": sum(
            max(0, round(float(call.get("latency_ms") or 0)))
            for call in response_calls
        ),
    }


def vpa_impact(record: Any) -> dict[str, Any] | None:
    report = record.vpa_evidence if isinstance(record.vpa_evidence, dict) else None
    if report is None:
        return None
    if report.get("report_scope") == "predecision_vpa_v2":
        pipeline = getattr(record, "analysis_pipeline", None)
        pipeline = pipeline.get("pipeline") if isinstance(pipeline, dict) else getattr(pipeline, "pipeline", None)
        stage2 = getattr(record, "stage2_decision", None) if pipeline == "pa_vpa_joint" else None
        stage2 = stage2 if isinstance(stage2, dict) else {}
        analysis = stage2.get("joint_analysis") or {}
        analysis = analysis if isinstance(analysis, dict) else {}
        return {
            "report_scope": report["report_scope"],
            "entry_timing_applicability": report.get("entry_timing_applicability"),
            "background_relation": report.get("background_relation"),
            "evidence_used": copy.deepcopy(analysis.get("vpa_evidence_refs") or []),
            "decision_effect": "joint_stage2_evidence" if stage2 else "predecision_evidence_only",
            "reason": "Validated predecision VPA evidence; no causal decision-change claim.",
        }
    joint = record.joint_decision if isinstance(record.joint_decision, dict) else None
    arbitration = (
        joint.get("joint_arbitration")
        if isinstance(joint, dict) and isinstance(joint.get("joint_arbitration"), dict)
        else None
    )
    if arbitration is None:
        evidence_used: list[str] = []
        for collection_name in ("observations", "hypothesis_assessments"):
            collection = report.get(collection_name)
            if not isinstance(collection, list):
                continue
            for item in collection:
                if not isinstance(item, dict):
                    continue
                for evidence_id in item.get("evidence_ids") or []:
                    if isinstance(evidence_id, str) and evidence_id not in evidence_used:
                        evidence_used.append(evidence_id)
        return {
            "background_relation": report.get("background_relation"),
            "entry_timing_relation": report.get("entry_timing_relation"),
            "evidence_used": evidence_used,
            "decision_effect": "shadow_only_no_effect",
            "reason": (
                "VPA evidence was recorded for evaluation only; "
                "the official PA decision was not changed."
            ),
        }
    return {
        "background_relation": report.get("background_relation"),
        "entry_timing_relation": report.get("entry_timing_relation"),
        "evidence_used": copy.deepcopy(arbitration.get("evidence_used") or []),
        "decision_effect": arbitration.get("decision_effect"),
        "reason": arbitration.get("reason"),
    }


def pa_joint_comparison(record: Any) -> dict[str, Any] | None:
    baseline = (
        record.pa_baseline_stage2
        if isinstance(record.pa_baseline_stage2, dict)
        else None
    )
    joint = record.joint_stage2 if isinstance(record.joint_stage2, dict) else None
    if baseline is None or joint is None or record.joint_decision is None:
        return None
    base_decision = baseline.get("decision")
    joint_decision = joint.get("decision")
    if not isinstance(base_decision, dict) or not isinstance(joint_decision, dict):
        return None
    keys = (
        "order_type",
        "order_direction",
        "entry_price",
        "stop_loss_price",
        "take_profit_price",
        "take_profit_price_2",
        "trade_confidence",
        "estimated_win_rate",
    )
    diff = {
        key: {"baseline": base_decision.get(key), "joint": joint_decision.get(key)}
        for key in keys
        if base_decision.get(key) != joint_decision.get(key)
    }
    base_trade = bool(base_decision.get("order_direction"))
    joint_trade = bool(joint_decision.get("order_direction"))
    if base_trade and not joint_trade:
        classification = "joint_reduced_entry_attempt"
    elif not base_trade and joint_trade:
        classification = "joint_added_entry_attempt"
    elif base_trade and joint_trade and diff:
        classification = "joint_modified_entry_attempt"
    elif diff:
        classification = "joint_timing_changed_without_trade_rate_effect"
    else:
        classification = "keep_baseline"
    return {
        "stage2": diff,
        "observed_effect": {
            "classification": classification,
            "comparison_valid": True,
            "baseline_has_executable_order": base_trade,
            "joint_has_executable_order": joint_trade,
            "stage2_changed": bool(diff),
        },
    }


def gui_vpa_view(record: Any) -> dict[str, Any] | None:
    """Build the legacy-shaped UI view without persisting duplicate data."""
    if not isinstance(record.vpa_catalog, dict):
        return None
    outcome = record.run_outcome if isinstance(record.run_outcome, dict) else {}
    requested_mode = outcome.get("requested_vpa_mode")
    effective_mode = outcome.get("effective_vpa_mode") or requested_mode
    view = {
        "mode": effective_mode,
        "requested_mode": requested_mode,
        "effective_mode": effective_mode,
        "status": outcome.get("status"),
        "snapshot_hash": record.vpa_catalog.get("snapshot_hash"),
        "evidence_catalog": record.vpa_catalog,
        "comparison": pa_joint_comparison(record),
        "role_execution": {
            **copy.deepcopy(record.role_runtime or {}),
            **copy.deepcopy(record.role_statuses or {}),
        },
        "stage1_context": record.vpa_evidence,
        "stage2_impact": vpa_impact(record),
    }
    if outcome.get("degradation_stage"):
        view["degradation"] = {
            "stage": outcome.get("degradation_stage"),
            "reason": outcome.get("reason"),
        }
    return view
