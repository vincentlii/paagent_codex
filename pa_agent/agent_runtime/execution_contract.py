"""Role selection and transport only; never a second decision materializer."""
from typing import Any, Mapping

RUNTIME_CONTRACT = "analysis_runtime_v2"
ANALYSIS_EXECUTION_POLICY = "analysis_execution_v1"
INSTRUCTION_DELIVERY_POLICY = "single_frozen_agents_v1"
BASELINE_RECEIPT_POLICY = "baseline_receipt_v1"
SEMANTIC_PARITY_CONTEXT_POLICY = "semantic_parity_v1"
BOUNDED_CONTEXT_POLICY = "bounded_context_v2"
INLINE_MANDATORY_KNOWLEDGE_POLICY = "inline_mandatory_knowledge_v1"
LAYERED_STAGE2_RESEARCH_POLICY = "layered_stage2_research_v1"
JOINT_PROTOCOL = "joint_stage2_agent_protocol_v1"
STAGE2_HOST_GATED_DRAFT_POLICY = "stage2_host_gated_draft_v1"
STAGE_SUBAGENT_ROLES = {
    "off": frozenset(),
    "stage1_challenge_v1": frozenset({"stage1"}),
    "vpa_challenge_v1": frozenset({"vpa_evidence"}),
    "stage2_challenge_v1": frozenset({"stage2"}),
    "single_challenge_v1": frozenset({"stage1", "vpa_evidence", "stage2"}),
}
STAGE_SUBAGENT_POLICIES = frozenset(STAGE_SUBAGENT_ROLES)
STAGE_CHALLENGE_PROTOCOL = "host_stage_challenge_v1"
STAGE_CHALLENGE_LIMITS = {"max_evidence_calls": 8, "max_evidence_response_bytes": 65536,
                        "max_evidence_bytes": 262144, "max_output_bytes": 16384}
SKILL_NAMES = {
    "stage1": "pa-stage1-diagnosis",
    "stage2": "pa-stage2-decision",
    "joint_stage2": "pa-vpa-stage2-decision",
    "vpa_evidence": "vpa-evidence-analysis",
    "joint_arbiter": "joint-arbiter-research",
    "draft_pointer_repair": "draft-pointer-repair",
}


def skill_key(role: str, context: Mapping[str, Any]) -> str:
    protocol = context.get("role_protocol") or {}
    if role == "stage2" and protocol.get("contract_version") == JOINT_PROTOCOL:
        return "joint_stage2"
    return role


def uses_result_envelope(job: Any) -> bool:
    return (
        job.core_context.get("runtime_contract_version") == RUNTIME_CONTRACT
        and job.manifest.role in {"stage1", "stage2", "vpa_evidence"}
        and not job.repair_findings
    )


def stage2_host_gated_draft(job: Any) -> bool:
    """New Stage 2 jobs delegate delivery failures to Host, not model stops."""
    return (
        uses_result_envelope(job)
        and job.manifest.role == "stage2"
        and job.core_context.get("analysis_execution_policy") == ANALYSIS_EXECUTION_POLICY
        and job.core_context.get("context_policy") == SEMANTIC_PARITY_CONTEXT_POLICY
        and job.core_context.get("result_policy") == STAGE2_HOST_GATED_DRAFT_POLICY
    )
