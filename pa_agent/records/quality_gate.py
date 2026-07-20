"""Strict, sample-size-aware acceptance gates for PA/VPA analysis experiments.

The gate evaluates engineering and semantic quality only. It cannot prove
profitability and never unlocks VPA Soft execution by itself.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from pa_agent.ai.vpa_measurements import evidence_has_relative_volume_comparison
from pa_agent.config.vpa import VPA_PAIR_IDENTITY_VERSION


@dataclass(frozen=True)
class QualityThresholds:
    """Review thresholds; callers may override them without changing runtime logic."""

    min_completed_runs: int = 20
    max_call_p50_latency_ms: float = 100_000.0
    max_call_p95_latency_ms: float = 150_000.0
    max_run_p50_latency_ms: float = 220_000.0
    max_run_p95_latency_ms: float = 300_000.0
    max_run_p50_tokens: int = 180_000
    max_run_p95_tokens: int = 250_000
    max_vpa_to_baseline_latency_ratio_p95: float = 2.2

    def __post_init__(self) -> None:
        if self.min_completed_runs < 1:
            raise ValueError("min_completed_runs must be positive")
        for name, value in asdict(self).items():
            if name != "min_completed_runs" and float(value) <= 0:
                raise ValueError(f"{name} must be positive")


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _run_id(record: Any, index: int) -> str:
    meta = _value(record, "meta", {})
    return str(_value(meta, "run_id", "") or f"record-{index + 1}")


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return round(ordered[index], 3)


def _semantic_prefixes(events: list[dict[str, Any]]) -> tuple[bool, bool]:
    pa_error = False
    vpa_error = False
    for event in events:
        category = str(event.get("category") or "")
        if category == "normalization":
            continue
        fields = [
            str(value)
            for key in ("invalid_fields", "missing_fields")
            for value in event.get(key, [])
        ]
        if category == "c" and not fields:
            pa_error = True
        for value in fields:
            is_vpa = value.startswith(
                (
                    "vpa:",
                    "vpa_pair:",
                    "paired_vpa_",
                    "vpa_context",
                    "vpa_impact",
                )
            )
            vpa_error = vpa_error or is_vpa
            pa_error = pa_error or not is_vpa
    return pa_error, vpa_error


def _has_pa_grounding(record: Any) -> bool:
    stage1 = _value(record, "stage1_diagnosis", {}) or {}
    evidence = stage1.get("pa_evidence") if isinstance(stage1, dict) else None
    return bool(
        isinstance(evidence, dict)
        and evidence.get("fact_refs")
        and evidence.get("theory_rule_ids")
    )


def _has_vpa_information_increment(sidecar: dict[str, Any]) -> bool:
    context = sidecar.get("stage1_context")
    impact = sidecar.get("stage2_impact")
    if not isinstance(context, dict) or not isinstance(impact, dict):
        return False
    if context.get("data_status") == "unavailable":
        return (
            context.get("background_relation") == "insufficient_data"
            and context.get("entry_timing_relation") == "insufficient_data"
            and impact.get("decision_effect") == "keep_brooks_baseline"
        )
    substantive = {
        "effort_result",
        "sequence_comparison",
        "acceptance_rejection",
        "entry_timing",
    }
    catalog = sidecar.get("evidence_catalog") or {}
    evidence = (
        {
            str(item.get("evidence_id")): item
            for item in catalog.get("evidence", [])
            if isinstance(item, dict) and item.get("evidence_id")
        }
        if isinstance(catalog, dict)
        else {}
    )
    observations = context.get("observations") or []
    return any(
        isinstance(item, dict)
        and item.get("finding_kind") in substantive
        and any(
            evidence_has_relative_volume_comparison(evidence.get(str(evidence_id)))
            for evidence_id in item.get("evidence_ids", [])
        )
        for item in observations
    )


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _valid_full_prompt_protocol(
    sidecar: dict[str, Any],
    paired: dict[str, Any],
) -> bool:
    pair_input_hash = str(paired.get("pair_input_hash") or "")
    baseline_hash = str(paired.get("baseline_stage1_messages_hash") or "")
    vpa_hash = str(paired.get("vpa_stage1_messages_hash") or "")
    return bool(
        paired.get("experiment_type") == "full_prompt_ab"
        and paired.get("pair_identity_version") == VPA_PAIR_IDENTITY_VERSION
        and paired.get("protocol_valid") is True
        and paired.get("full_prompt_ab_eligible") is True
        and _is_sha256(pair_input_hash)
        and sidecar.get("pair_id") == f"vpa-{pair_input_hash[:12]}"
        and paired.get("official_arm") == "baseline"
        and paired.get("arm_sequence") == ["baseline", "vpa"]
        and paired.get("provider_fallback_allowed") is False
        and paired.get("client_instances_isolated") is True
        and paired.get("same_frozen_input") is True
        and paired.get("baseline_has_vpa_catalog") is False
        and paired.get("vpa_has_vpa_catalog") is True
        and paired.get("intended_prompt_difference_present") is True
        and paired.get("incremental_context_disabled_for_protocol") is True
        and paired.get("stage2_baseline_prefix_reused") is False
        and paired.get("baseline_prompt_price_only") is True
        and _is_sha256(baseline_hash)
        and _is_sha256(vpa_hash)
        and baseline_hash != vpa_hash
    )


def evaluate_analysis_quality(
    records: Iterable[Any],
    *,
    thresholds: QualityThresholds | None = None,
) -> dict[str, Any]:
    """Evaluate full-prompt Shadow runs without confusing quality with PnL."""
    limits = thresholds or QualityThresholds()
    candidates = list(records)
    completed = [record for record in candidates if not _value(record, "exception")]

    first_pass_failed: list[str] = []
    pa_missing: list[str] = []
    pa_semantic: list[str] = []
    vpa_increment_missing: list[str] = []
    vpa_semantic: list[str] = []
    authority_failed: list[str] = []
    ab_invalid: list[str] = []
    call_latencies: list[float] = []
    run_latencies: list[float] = []
    run_tokens: list[float] = []
    arm_ratios: list[float] = []
    call_status_failed: list[str] = []
    missing_latency_runs: list[str] = []
    missing_token_usage_runs: list[str] = []
    missing_arm_runs: list[str] = []

    for index, record in enumerate(completed):
        run_id = _run_id(record, index)
        events = [
            event
            for event in (_value(record, "validation_audit", []) or [])
            if isinstance(event, dict)
        ]
        calls = [
            call
            for call in (_value(record, "call_attempts", []) or [])
            if isinstance(call, dict)
        ]
        if events or any(str(call.get("status")) != "validated" for call in calls):
            first_pass_failed.append(run_id)
        call_arms = {str(call.get("arm") or "") for call in calls}
        if not {"baseline", "vpa"}.issubset(call_arms):
            missing_arm_runs.append(run_id)
        if not calls or any(str(call.get("status")) != "validated" for call in calls):
            call_status_failed.append(run_id)
        if any(call.get("latency_ms") is None for call in calls):
            missing_latency_runs.append(run_id)
        if any(int(call.get("total_tokens") or 0) <= 0 for call in calls):
            missing_token_usage_runs.append(run_id)

        pa_event, vpa_event = _semantic_prefixes(events)
        if not _has_pa_grounding(record):
            pa_missing.append(run_id)
        if pa_event:
            pa_semantic.append(run_id)
        if vpa_event:
            vpa_semantic.append(run_id)

        sidecar = _value(record, "vpa_sidecar", {}) or {}
        sidecar = sidecar if isinstance(sidecar, dict) else {}
        paired = sidecar.get("paired_execution") or {}
        paired = paired if isinstance(paired, dict) else {}
        if not _has_vpa_information_increment(sidecar):
            vpa_increment_missing.append(run_id)
        if paired.get("authority_violations"):
            authority_failed.append(run_id)
        if not _valid_full_prompt_protocol(sidecar, paired):
            ab_invalid.append(run_id)

        usable_calls = [call for call in calls if call.get("latency_ms") is not None]
        latencies = [max(0.0, float(call["latency_ms"])) for call in usable_calls]
        tokens = [max(0, int(call.get("total_tokens") or 0)) for call in calls]
        call_latencies.extend(latencies)
        if calls:
            run_latencies.append(sum(latencies))
            run_tokens.append(float(sum(tokens)))
        arm_latency: dict[str, float] = {}
        for call in usable_calls:
            arm = str(call.get("arm") or "")
            arm_latency[arm] = arm_latency.get(arm, 0.0) + max(
                0.0, float(call["latency_ms"])
            )
        baseline_latency = arm_latency.get("baseline", 0.0)
        vpa_latency = arm_latency.get("vpa", 0.0)
        if baseline_latency > 0 and vpa_latency > 0:
            arm_ratios.append(vpa_latency / baseline_latency)

    metrics = {
        "call_latency_ms": {
            "p50": _percentile(call_latencies, 0.50),
            "p95": _percentile(call_latencies, 0.95),
        },
        "run_latency_ms": {
            "p50": _percentile(run_latencies, 0.50),
            "p95": _percentile(run_latencies, 0.95),
        },
        "run_total_tokens": {
            "p50": _percentile(run_tokens, 0.50),
            "p95": _percentile(run_tokens, 0.95),
        },
        "vpa_to_baseline_latency_ratio": {
            "p95": _percentile(arm_ratios, 0.95),
        },
    }
    performance_violations: list[str] = []
    comparisons = (
        ("call_latency_ms.p50", metrics["call_latency_ms"]["p50"], limits.max_call_p50_latency_ms),
        ("call_latency_ms.p95", metrics["call_latency_ms"]["p95"], limits.max_call_p95_latency_ms),
        ("run_latency_ms.p50", metrics["run_latency_ms"]["p50"], limits.max_run_p50_latency_ms),
        ("run_latency_ms.p95", metrics["run_latency_ms"]["p95"], limits.max_run_p95_latency_ms),
        ("run_total_tokens.p50", metrics["run_total_tokens"]["p50"], limits.max_run_p50_tokens),
        ("run_total_tokens.p95", metrics["run_total_tokens"]["p95"], limits.max_run_p95_tokens),
        (
            "vpa_to_baseline_latency_ratio.p95",
            metrics["vpa_to_baseline_latency_ratio"]["p95"],
            limits.max_vpa_to_baseline_latency_ratio_p95,
        ),
    )
    for name, value, maximum in comparisons:
        if value is None:
            performance_violations.append(f"{name}:missing")
        elif float(value) > float(maximum):
            performance_violations.append(f"{name}:{value}>{maximum}")
    if call_status_failed:
        performance_violations.append("non_validated_or_missing_calls")
    if missing_latency_runs:
        performance_violations.append("incomplete_call_latency")
    if missing_token_usage_runs:
        performance_violations.append("incomplete_token_usage")
    if missing_arm_runs:
        performance_violations.append("incomplete_ab_call_ledger")

    axes = {
        "first_pass_clean": {
            "passed": not first_pass_failed,
            "failed_runs": sorted(set(first_pass_failed)),
        },
        "pa_grounding": {
            "passed": not pa_missing and not pa_semantic,
            "missing_evidence_runs": sorted(set(pa_missing)),
            "semantic_error_runs": sorted(set(pa_semantic)),
        },
        "vpa_grounding": {
            "passed": not vpa_increment_missing
            and not vpa_semantic
            and not authority_failed,
            "missing_information_increment_runs": sorted(set(vpa_increment_missing)),
            "semantic_error_runs": sorted(set(vpa_semantic)),
            "authority_violation_runs": sorted(set(authority_failed)),
        },
        "ab_protocol": {
            "passed": not ab_invalid,
            "invalid_runs": sorted(set(ab_invalid)),
        },
        "performance": {
            "passed": not performance_violations,
            "metrics": metrics,
            "violations": performance_violations,
            "missing_latency_runs": sorted(set(missing_latency_runs)),
            "missing_token_usage_runs": sorted(set(missing_token_usage_runs)),
            "missing_arm_runs": sorted(set(missing_arm_runs)),
        },
    }
    enough = len(completed) >= limits.min_completed_runs
    axes_pass = all(bool(axis["passed"]) for axis in axes.values())
    status = "pass" if enough and axes_pass else "fail" if enough else "insufficient_sample"
    return {
        "status": status,
        "quality_gate_passed": status == "pass",
        "ready_for_soft": False,
        "sample": {
            "provided_runs": len(candidates),
            "completed_runs": len(completed),
            "minimum_completed_runs": limits.min_completed_runs,
        },
        "thresholds": asdict(limits),
        "axes": axes,
        "promotion_requirements_not_evaluated": [
            "matched counterfactual outcomes",
            "trade-frequency stability",
            "out-of-sample profitability after costs and slippage",
        ],
    }
