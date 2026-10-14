"""Freeze current role instructions; never interpret market evidence."""
import tomllib
from typing import Any, Mapping

from pa_agent.agent_runtime.execution_contract import (
    LAYERED_STAGE2_RESEARCH_POLICY, STAGE_SUBAGENT_ROLES,
)

RUNTIME_GUIDANCE_POLICY = "role_guidance_v1"


def _role_modes(role: str, *, repair: bool) -> tuple[str, str]:
    output_mode = ("patch_envelope" if repair else "result_draft_only" if role == "stage2"
                   else "result_draft_or_stop" if role in {"stage1", "vpa_evidence"} else "research_draft")
    entrypoint = ("get_repair_findings" if repair else "get_stage1_decision_packet"
                  if role == "stage1" else "get_role_baseline")
    return entrypoint, output_mode


def _extension_keys(role: str, context: Mapping[str, Any], *, repair: bool) -> list[str]:
    challenge_allowed = (not repair and context.get("investigation_policy") == "agentic"
                         and role in STAGE_SUBAGENT_ROLES.get(context.get("stage_subagents", "off"), ()))
    keys = ["stage_challenge"] if challenge_allowed else []
    if not repair and context.get("knowledge_delivery_policy") == LAYERED_STAGE2_RESEARCH_POLICY:
        keys.append("layered_knowledge")
    return keys


def freeze_runtime_guidance(role: str, context: Mapping[str, Any], *, repair: bool,
                            documents: Mapping[str, str]) -> dict[str, Any]:
    if context.get("runtime_guidance_policy") not in {None, RUNTIME_GUIDANCE_POLICY}:
        raise ValueError("unknown frozen runtime guidance policy")
    entrypoint, output_mode = _role_modes(role, repair=repair)
    try:
        templates = tomllib.loads(documents["role_contracts"])
        failure_instruction = templates["output"][output_mode]
        investigation = templates["investigation"]["repair" if repair else context.get("investigation_policy", "agentic")]
    except (KeyError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("runtime guidance role contracts are unavailable") from exc
    guidance: dict[str, Any] = {
        "role": role,
        "entrypoint": entrypoint,
        "output_mode": output_mode,
        "failure_instruction": failure_instruction,
        "investigation_instruction": investigation,
    }
    for key in _extension_keys(role, context, repair=repair):
        text = documents.get(key)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"runtime guidance missing required extension: {key}")
        guidance[key] = text
    return guidance


def validate_runtime_guidance(job: Any) -> None:
    """Check only explicitly marked new Jobs; legacy frozen text is unchanged."""
    policy = job.core_context.get("runtime_guidance_policy")
    if policy is None:
        return
    if policy != RUNTIME_GUIDANCE_POLICY:
        raise ValueError("unknown frozen runtime guidance policy")
    protocol = job.core_context.get("role_protocol") or {}
    actual = protocol.get("runtime_guidance")
    entrypoint, output_mode = _role_modes(job.manifest.role, repair=bool(job.repair_findings))
    required_text = {"failure_instruction", "investigation_instruction", *_extension_keys(
        job.manifest.role, job.core_context, repair=bool(job.repair_findings),
    )}
    if (not isinstance(actual, dict)
            or set(actual) != {"role", "entrypoint", "output_mode", *required_text}
            or actual.get("role") != job.manifest.role or actual.get("entrypoint") != entrypoint
            or actual.get("output_mode") != output_mode
            or any(not isinstance(actual.get(key), str) or not actual[key].strip() for key in required_text)):
        raise ValueError("runtime guidance does not match frozen role and mode")
    if job.repair_findings and job.repair_findings.get("runtime_guidance") != actual:
        raise ValueError("runtime guidance is missing from frozen repair delivery")
    packet = job.core_context.get("stage1_decision_packet")
    if not job.repair_findings and isinstance(packet, dict):
        if (packet.get("role_contract") or {}).get("runtime_guidance") != actual:
            raise ValueError("runtime guidance does not match frozen packet delivery")
