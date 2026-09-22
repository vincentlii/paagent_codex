"""Role selection and transport only; never a second decision materializer."""
from typing import Any, Mapping

RUNTIME_CONTRACT = "analysis_runtime_v2"
ANALYSIS_EXECUTION_POLICY = "analysis_execution_v1"
INSTRUCTION_DELIVERY_POLICY = "single_frozen_agents_v1"
SEMANTIC_PARITY_CONTEXT_POLICY = "semantic_parity_v1"
BOUNDED_CONTEXT_POLICY = "bounded_context_v2"
JOINT_PROTOCOL = "joint_stage2_agent_protocol_v1"
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
