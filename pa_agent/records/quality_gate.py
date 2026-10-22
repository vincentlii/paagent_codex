"""Sample-aware engineering gates for true PA/VPA A/A/B/B experiments.

These gates assess protocol integrity, grounding, repeatability, and runtime
quality. They do not prove profitability and never enable Soft automatically.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pa_agent.ai.vpa_measurements import evidence_has_relative_volume_comparison
from pa_agent.experiments.pa_vpa_aabb import (
    classify_first_pass,
    decision_signature,
    first_pass_clean,
    validate_aabb_records,
)


@dataclass(frozen=True)
class QualityThresholds:
    min_unique_snapshots: int
    min_repeatability_snapshots: int
    max_call_p50_latency_ms: float
    max_call_p95_latency_ms: float
    max_run_p50_latency_ms: float
    max_run_p95_latency_ms: float
    max_run_p50_tokens: int
    max_run_p95_tokens: int
    max_b_to_a_latency_ratio_p95: float
    max_b_to_a_token_ratio_p95: float
    max_a_disagreement_rate: float
    max_b_disagreement_rate: float

    def __post_init__(self) -> None:
        if self.min_unique_snapshots < 1:
            raise ValueError("min_unique_snapshots must be positive")
        if self.min_repeatability_snapshots < 1:
            raise ValueError("min_repeatability_snapshots must be positive")
        for name, value in asdict(self).items():
            if name.startswith("min_"):
                continue
            if float(value) <= 0:
                raise ValueError(f"{name} must be positive")


def default_quality_thresholds() -> QualityThresholds:
    """Load release-gate thresholds from the repository quality contract."""
    from pa_agent.quality.contract import load_quality_contract

    root = Path(__file__).resolve().parents[2]
    contract = load_quality_contract(root / "config" / "quality_contract.toml")
    performance = contract.performance
    research = contract.research
    return QualityThresholds(
        min_unique_snapshots=int(research["minimum_independent_snapshots"]),
        min_repeatability_snapshots=int(research["minimum_repeatability_snapshots"]),
        max_call_p50_latency_ms=float(performance["max_call_p50_latency_ms"]),
        max_call_p95_latency_ms=float(performance["max_call_p95_latency_ms"]),
        max_run_p50_latency_ms=float(performance["max_run_p50_latency_ms"]),
        max_run_p95_latency_ms=float(performance["max_run_p95_latency_ms"]),
        max_run_p50_tokens=int(performance["max_run_p50_tokens"]),
        max_run_p95_tokens=int(performance["max_run_p95_tokens"]),
        max_b_to_a_latency_ratio_p95=float(
            performance["max_b_to_a_latency_ratio_p95"]
        ),
        max_b_to_a_token_ratio_p95=float(
            performance["max_b_to_a_token_ratio_p95"]
        ),
        max_a_disagreement_rate=float(research["max_a_disagreement_rate"]),
        max_b_disagreement_rate=float(research["max_b_disagreement_rate"]),
    )


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


def _final_call_statuses(
    calls: list[dict[str, Any]],
) -> dict[tuple[str, str], str]:
    final: dict[tuple[str, str], tuple[int, str]] = {}
    for call in calls:
        key = (str(call.get("arm") or ""), str(call.get("stage") or ""))
        attempt = int(call.get("attempt") or 0)
        previous = final.get(key)
        if previous is None or attempt >= previous[0]:
            final[key] = (attempt, str(call.get("status") or ""))
    return {key: value[1] for key, value in final.items()}


def _semantic_roles(
    events: list[dict[str, Any]],
    calls: list[dict[str, Any]],
) -> tuple[bool, bool]:
    """Classify only unrepaired final semantic failures by role."""
    pa_error = False
    vpa_error = False
    final_statuses = _final_call_statuses(calls)
    for event in events:
        severity = str(event.get("severity") or "")
        category = str(event.get("category") or "")
        if severity in {"warning", "normalization"} or category in {
            "warning",
            "normalization",
        }:
            continue
        owner = str(event.get("owner") or event.get("stage") or "")
        stage = str(event.get("stage") or owner)
        matching_statuses = [
            status
            for (_arm, call_stage), status in final_statuses.items()
            if call_stage == stage
        ]
        if matching_statuses and all(
            status == "validated" for status in matching_statuses
        ):
            continue
        if owner in {"vpa_evidence", "joint_arbiter"}:
            vpa_error = True
        elif owner in {"stage1", "stage2"}:
            pa_error = True
    return pa_error, vpa_error


def _has_pa_grounding(record: Any) -> bool:
    stage1 = _value(record, "stage1_diagnosis", {}) or {}
    evidence = stage1.get("pa_evidence") if isinstance(stage1, dict) else None
    return bool(
        isinstance(evidence, dict) and evidence.get("fact_refs") and evidence.get("theory_rule_ids")
    )


def _has_vpa_information_increment(record: Any) -> bool:
    context = _value(record, "vpa_evidence", None)
    joint = _value(record, "joint_decision", None)
    arbitration = (
        joint.get("joint_arbitration")
        if isinstance(joint, dict) and isinstance(joint.get("joint_arbitration"), dict)
        else {}
    )
    if not isinstance(context, dict):
        return False
    if context.get("data_status") == "unavailable":
        return (
            context.get("background_relation") == "insufficient_data"
            and (context.get("entry_timing_relation") == "insufficient_data"
                 or (context.get("report_scope") == "predecision_vpa_v2"
                     and context.get("entry_timing_applicability") == "not_applicable"))
            and arbitration.get("decision_effect") in {None, "keep_brooks_baseline"}
        )
    substantive = {
        "effort_result",
        "sequence_comparison",
        "acceptance_rejection",
        "entry_timing",
    }
    catalog = _value(record, "vpa_catalog", {}) or {}
    evidence = (
        {
            str(item.get("evidence_id")): item
            for item in catalog.get("evidence", [])
            if isinstance(item, dict) and item.get("evidence_id")
        }
        if isinstance(catalog, dict)
        else {}
    )
    return any(
        isinstance(item, dict)
        and item.get("finding_kind") in substantive
        and any(
            evidence_has_relative_volume_comparison(evidence.get(str(evidence_id)))
            for evidence_id in item.get("evidence_ids", [])
        )
        for item in context.get("observations", [])
    )


def _group_records(records: list[Any]) -> tuple[dict[str, list[Any]], list[str]]:
    groups: dict[str, list[Any]] = {}
    unassigned: list[str] = []
    for index, record in enumerate(records):
        context = _value(record, "experiment_context", {}) or {}
        experiment_id = str(context.get("experiment_id") if isinstance(context, dict) else "")
        if not experiment_id:
            unassigned.append(_run_id(record, index))
            continue
        groups.setdefault(experiment_id, []).append(record)
    return groups, unassigned


def evaluate_analysis_quality(
    records: Iterable[Any],
    *,
    thresholds: QualityThresholds | None = None,
) -> dict[str, Any]:
    """Evaluate one or more complete offline A/A/B/B experiments."""
    limits = thresholds or default_quality_thresholds()
    candidates = list(records)
    experiments, unassigned = _group_records(candidates)

    invalid_experiments: dict[str, list[str]] = {}
    first_pass_failed: list[str] = []
    first_pass_classifications: dict[str, dict[str, Any]] = {}
    pa_missing: list[str] = []
    pa_semantic: list[str] = []
    vpa_increment_missing: list[str] = []
    vpa_semantic: list[str] = []
    authority_failed: list[str] = []
    call_status_failed: list[str] = []
    missing_latency_runs: list[str] = []
    missing_token_usage_runs: list[str] = []
    call_latencies: list[float] = []
    run_latencies: list[float] = []
    run_tokens: list[float] = []
    latency_ratios: list[float] = []
    token_ratios: list[float] = []
    a_disagreements_by_snapshot: dict[str, list[bool]] = {}
    b_disagreements_by_snapshot: dict[str, list[bool]] = {}
    valid_experiment_count = 0
    cohort_ids: set[str] = set()
    missing_cohort_experiments: list[str] = []

    for experiment_id, experiment_records in experiments.items():
        experiment_cohorts = {
            str((_value(record, "experiment_context", {}) or {}).get("cohort_id") or "")
            for record in experiment_records
        }
        experiment_cohorts.discard("")
        if not experiment_cohorts:
            missing_cohort_experiments.append(experiment_id)
        cohort_ids.update(experiment_cohorts)
        protocol = validate_aabb_records(experiment_records)
        if not protocol["valid"]:
            invalid_experiments[experiment_id] = list(protocol["errors"])
            continue
        valid_experiment_count += 1
        market_snapshot_hash = str(
            (_value(experiment_records[0], "experiment_context", {}) or {}).get(
                "market_snapshot_hash"
            )
            or ""
        )
        by_arm = {
            str((_value(record, "experiment_context", {}) or {}).get("arm_id") or ""): record
            for record in experiment_records
        }
        if {"A1", "A2"}.issubset(by_arm):
            a_disagreements_by_snapshot.setdefault(market_snapshot_hash, []).append(
                decision_signature(by_arm["A1"]) != decision_signature(by_arm["A2"])
            )
        if {"B1", "B2"}.issubset(by_arm):
            b_disagreements_by_snapshot.setdefault(market_snapshot_hash, []).append(
                decision_signature(by_arm["B1"]) != decision_signature(by_arm["B2"])
            )

        totals_by_group: dict[str, list[tuple[float, float]]] = {"A": [], "B": []}
        for index, record in enumerate(experiment_records):
            run_id = _run_id(record, index)
            context = _value(record, "experiment_context", {}) or {}
            group = str(context.get("group") or "")
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
            classification = classify_first_pass(record)
            first_pass_classifications[run_id] = classification
            if not first_pass_clean(record):
                first_pass_failed.append(run_id)
            pa_event, vpa_event = _semantic_roles(events, calls)
            if not _has_pa_grounding(record):
                pa_missing.append(run_id)
            if pa_event:
                pa_semantic.append(run_id)
            if group == "B":
                if not _has_vpa_information_increment(record):
                    vpa_increment_missing.append(run_id)
                if vpa_event:
                    vpa_semantic.append(run_id)
                role_execution = _value(record, "role_runtime", {}) or {}
                if not isinstance(role_execution, dict) or role_execution.get(
                    "authority_violations"
                ):
                    authority_failed.append(run_id)

            final_statuses = _final_call_statuses(calls)
            if not final_statuses or any(
                status != "validated" for status in final_statuses.values()
            ):
                call_status_failed.append(run_id)
            if any(call.get("latency_ms") is None for call in calls):
                missing_latency_runs.append(run_id)
            if any(int(call.get("total_tokens") or 0) <= 0 for call in calls):
                missing_token_usage_runs.append(run_id)
            latencies = [
                max(0.0, float(call["latency_ms"]))
                for call in calls
                if call.get("latency_ms") is not None
            ]
            tokens = [max(0, int(call.get("total_tokens") or 0)) for call in calls]
            call_latencies.extend(latencies)
            run_latency = sum(latencies)
            run_token = float(sum(tokens))
            if calls:
                run_latencies.append(run_latency)
                run_tokens.append(run_token)
            if group in totals_by_group:
                totals_by_group[group].append((run_latency, run_token))
        if totals_by_group["A"] and totals_by_group["B"]:
            a_latency = sum(value[0] for value in totals_by_group["A"]) / len(totals_by_group["A"])
            b_latency = sum(value[0] for value in totals_by_group["B"]) / len(totals_by_group["B"])
            a_tokens = sum(value[1] for value in totals_by_group["A"]) / len(totals_by_group["A"])
            b_tokens = sum(value[1] for value in totals_by_group["B"]) / len(totals_by_group["B"])
            if a_latency > 0:
                latency_ratios.append(b_latency / a_latency)
            if a_tokens > 0:
                token_ratios.append(b_tokens / a_tokens)

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
        "b_to_a_latency_ratio": {"p95": _percentile(latency_ratios, 0.95)},
        "b_to_a_token_ratio": {"p95": _percentile(token_ratios, 0.95)},
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
            "b_to_a_latency_ratio.p95",
            metrics["b_to_a_latency_ratio"]["p95"],
            limits.max_b_to_a_latency_ratio_p95,
        ),
        (
            "b_to_a_token_ratio.p95",
            metrics["b_to_a_token_ratio"]["p95"],
            limits.max_b_to_a_token_ratio_p95,
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

    experiment_count = len(experiments)
    valid_snapshot_hashes = set(a_disagreements_by_snapshot) | set(b_disagreements_by_snapshot)
    unique_valid_snapshots = len(valid_snapshot_hashes)
    cohort_consistent = not missing_cohort_experiments and len(cohort_ids) <= 1
    repeatability_reviewable = (
        cohort_consistent and unique_valid_snapshots >= limits.min_repeatability_snapshots
    )
    a_snapshot_rates = [
        sum(values) / len(values) for values in a_disagreements_by_snapshot.values()
    ]
    b_snapshot_rates = [
        sum(values) / len(values) for values in b_disagreements_by_snapshot.values()
    ]
    a_rate = sum(a_snapshot_rates) / len(a_snapshot_rates) if a_snapshot_rates else None
    b_rate = sum(b_snapshot_rates) / len(b_snapshot_rates) if b_snapshot_rates else None
    repeatability_passed = not repeatability_reviewable or bool(
        a_rate is not None
        and b_rate is not None
        and a_rate <= limits.max_a_disagreement_rate
        and b_rate <= limits.max_b_disagreement_rate
    )
    axes = {
        "aabb_protocol": {
            "passed": not invalid_experiments and not unassigned,
            "invalid_experiments": invalid_experiments,
            "unassigned_runs": sorted(set(unassigned)),
        },
        "cohort_consistency": {
            "passed": cohort_consistent,
            "cohort_count": len(cohort_ids),
            "cohort_ids": sorted(cohort_ids),
            "missing_cohort_experiments": sorted(set(missing_cohort_experiments)),
            "requirement": "quality metrics must use one analysis cohort",
        },
        "first_pass_clean": {
            "passed": not first_pass_failed,
            "failed_runs": sorted(set(first_pass_failed)),
            "warnings_are_accepted": True,
            "classifications": first_pass_classifications,
        },
        "pa_grounding": {
            "passed": not pa_missing and not pa_semantic,
            "missing_evidence_runs": sorted(set(pa_missing)),
            "semantic_error_runs": sorted(set(pa_semantic)),
        },
        "vpa_grounding": {
            "passed": not vpa_increment_missing and not vpa_semantic and not authority_failed,
            "missing_information_increment_runs": sorted(set(vpa_increment_missing)),
            "semantic_error_runs": sorted(set(vpa_semantic)),
            "authority_violation_runs": sorted(set(authority_failed)),
        },
        "repeatability": {
            "passed": repeatability_passed,
            "statistically_reviewable": repeatability_reviewable,
            "experiments": valid_experiment_count,
            "unique_market_snapshots": unique_valid_snapshots,
            "a_disagreement_rate": round(a_rate, 4) if a_rate is not None else None,
            "b_disagreement_rate": round(b_rate, 4) if b_rate is not None else None,
        },
        "performance": {
            "passed": not performance_violations,
            "metrics": metrics,
            "violations": performance_violations,
            "failed_call_status_runs": sorted(set(call_status_failed)),
            "missing_latency_runs": sorted(set(missing_latency_runs)),
            "missing_token_usage_runs": sorted(set(missing_token_usage_runs)),
        },
    }
    enough = unique_valid_snapshots >= limits.min_unique_snapshots
    axes_pass = all(bool(axis["passed"]) for axis in axes.values())
    if not candidates:
        status = "insufficient_sample"
    elif not axes_pass:
        status = "fail"
    elif not enough:
        status = "insufficient_sample"
    else:
        status = "pass"
    return {
        "status": status,
        "quality_gate_passed": status == "pass",
        "ready_for_soft": False,
        "sample": {
            "provided_runs": len(candidates),
            "experiment_groups": experiment_count,
            "completed_protocol_experiments": valid_experiment_count,
            "unique_market_snapshots": unique_valid_snapshots,
            "minimum_unique_snapshots": limits.min_unique_snapshots,
        },
        "thresholds": asdict(limits),
        "axes": axes,
        "promotion_requirements_not_evaluated": [
            "matched counterfactual outcomes",
            "trade-frequency stability",
            "independent out-of-sample profitability after costs and slippage",
        ],
    }
