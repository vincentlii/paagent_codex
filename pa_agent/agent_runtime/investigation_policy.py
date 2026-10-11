"""Shared Stage 1 investigation flow; no market interpretation or decisions."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from pa_agent.agent_runtime.execution_contract import BOUNDED_CONTEXT_POLICY

STAGE1_INVESTIGATION_POLICY = "stage1_investigation_v1"
OPTIONAL_INVESTIGATION_CATEGORIES = {
    "list_fact_ids": "fact", "get_measurements": "fact", "get_pa_evidence": "fact",
    "get_facts": "fact", "get_bars": "fact", "measure_price_window": "fact",
    "project_price_leg": "fact", "search_allowed_knowledge": "knowledge",
    "read_allowed_knowledge": "knowledge",
}
OPTIONAL_INVESTIGATION_OPERATIONS = frozenset(OPTIONAL_INVESTIGATION_CATEGORIES)
STAGE1_INVESTIGATION_LIMITS = {
    "max_optional_calls": 2, "max_fact_batches": 1, "max_knowledge_batches": 1,
}
STAGE1_INVESTIGATION_TOOLS = frozenset({
    "get_stage1_decision_packet", "get_pa_evidence", "read_allowed_knowledge",
    "measure_price_window", "project_price_leg",
})


class InvestigationPolicyViolation(ValueError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def stage1_investigation_enabled(job: Any, *, actor: str = "parent") -> bool:
    """Absent markers retain history; unknown explicit versions fail closed."""
    policy = job.core_context.get("stage1_investigation_policy")
    if "stage1_investigation_policy" not in job.core_context:
        return False
    if policy != STAGE1_INVESTIGATION_POLICY:
        raise InvestigationPolicyViolation(
            "unknown_stage1_investigation_policy", "unknown frozen Stage 1 investigation policy",
        )
    return (actor == "parent" and not job.repair_findings
            and job.manifest.role == "stage1"
            and job.core_context.get("context_policy") == BOUNDED_CONTEXT_POLICY)


def validate_stage1_investigation(
    events: Sequence[Mapping[str, Any]], *, require_packet: bool = True,
    reject_denied: bool = True,
) -> None:
    """Count every attempt, requiring a successfully delivered packet first."""
    packets = 0
    packet_ready = False
    counts = {"fact": 0, "knowledge": 0}
    for event in events:
        operation = str(event.get("operation") or "")
        if operation == "get_stage1_decision_packet":
            packets += 1
            if packets > 1:
                raise InvestigationPolicyViolation(
                    "duplicate_stage1_decision_packet", "Stage 1 must read exactly one decision packet",
                )
            packet_ready = event.get("status") == "ok"
        else:
            category = OPTIONAL_INVESTIGATION_CATEGORIES.get(operation)
            if category is None or operation not in STAGE1_INVESTIGATION_TOOLS:
                raise InvestigationPolicyViolation(
                    "stage1_operation_not_available", "operation is outside the frozen Stage 1 investigation policy",
                )
            if not packet_ready:
                raise InvestigationPolicyViolation(
                    "stage1_baseline_required", "Stage 1 optional investigation requires a successfully read decision packet",
                )
            counts[category] += 1
            if counts[category] > STAGE1_INVESTIGATION_LIMITS[f"max_{category}_batches"]:
                raise InvestigationPolicyViolation(
                    f"stage1_{category}_budget_exceeded",
                    f"Stage 1 {category} investigation attempt budget exceeded",
                )
            if sum(counts.values()) > STAGE1_INVESTIGATION_LIMITS["max_optional_calls"]:
                raise InvestigationPolicyViolation(
                    "stage1_optional_budget_exceeded", "Stage 1 optional investigation attempt budget exceeded",
                )
        if reject_denied and event.get("status") == "denied":
            raise InvestigationPolicyViolation(
                str(event.get("reason") or "stage1_evidence_access_denied"),
                "Stage 1 evidence was denied; a successful Draft cannot hide that failure",
            )
    if require_packet and not packet_ready:
        raise InvestigationPolicyViolation(
            "stage1_baseline_required", "Stage 1 must successfully read exactly one decision packet",
        )
