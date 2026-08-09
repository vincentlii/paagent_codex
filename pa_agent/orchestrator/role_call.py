"""One bounded structured-output loop for VPA and joint roles."""
from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pa_agent.ai.json_validator import coalesce_model_json_text
from pa_agent.ai.role_validator import RoleValidationResult
from pa_agent.orchestrator.warning_events import group_warning_messages

_MISSING = object()
_STABLE_ROLE_PATHS: dict[str, tuple[tuple[str | int, ...], ...]] = {
    "vpa_evidence": (
        ("vpa_report", "background_relation"),
        ("vpa_report", "entry_timing_relation"),
        ("vpa_report", "next_confirmation", "state"),
    ),
    "joint_arbiter": (
        ("joint_arbitration", "selected_candidate_id"),
        ("joint_arbitration", "decision_effect"),
        ("decision_patch", "trade_confidence"),
        ("decision_patch", "watch_points"),
        ("decision_patch", "risk_assessment"),
        ("decision_patch", "invalidation_condition"),
    ),
}


@dataclass(frozen=True)
class StructuredRoleResult:
    """Result of one role call plus at most one targeted contract repair."""

    obj: dict[str, Any] | None
    initial_messages: list[dict[str, Any]]
    final_messages: list[dict[str, Any]]
    replies: tuple[Any, ...]
    validation_events: tuple[dict[str, Any], ...]
    hard_errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.obj is not None and not self.hard_errors


def run_structured_role(
    *,
    role: str,
    messages: list[dict[str, Any]],
    call_api: Callable[[list[dict[str, Any]]], Any],
    validate: Callable[[str], RoleValidationResult],
    max_repairs: int = 1,
) -> StructuredRoleResult:
    """Call, validate, and perform one narrow rewrite when required."""
    initial = [dict(item) for item in messages]
    current = [dict(item) for item in messages]
    replies: list[Any] = []
    events: list[dict[str, Any]] = []
    repairs = 0
    stable_source: dict[str, Any] | None = None

    while True:
        reply = call_api(current)
        replies.append(reply)
        raw = coalesce_model_json_text(
            str(getattr(reply, "content", "") or ""),
            str(getattr(reply, "reasoning_content", "") or ""),
        )
        stability_restores: list[dict[str, Any]] = []
        if repairs > 0 and stable_source is not None:
            candidate = _parse_object(raw)
            if candidate is not None:
                candidate, stability_restores = _restore_stable_role_fields(
                    role,
                    stable_source,
                    candidate,
                )
                raw = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
        result = validate(raw)
        attempt = len(replies)
        for item in stability_restores:
            events.append(
                {
                    "attempt": attempt,
                    "category": "normalization",
                    "code": "repair_stability_restore",
                    "severity": "normalization",
                    "owner": role,
                    "retry_policy": "none",
                    "role": role,
                    "normalization": item,
                }
            )
        for item in result.normalizations:
            events.append(
                {
                    "attempt": attempt,
                    "category": "normalization",
                    "code": "unambiguous_contract_normalization",
                    "severity": "normalization",
                    "owner": role,
                    "retry_policy": "none",
                    "role": role,
                    "normalization": dict(item),
                }
            )
        for warning in group_warning_messages(
            ("professional_prose_warning", item) for item in result.warnings
        ):
            events.append(
                {
                    "attempt": attempt,
                    "category": "warning",
                    "code": warning["code"],
                    "severity": "warning",
                    "owner": role,
                    "retry_policy": "none",
                    "role": role,
                    "message": warning["message"],
                    "occurrence_count": warning["occurrence_count"],
                    "findings": warning["findings"],
                }
            )
        if result.ok:
            return StructuredRoleResult(
                obj=result.obj,
                initial_messages=initial,
                final_messages=current,
                replies=tuple(replies),
                validation_events=tuple(events),
                hard_errors=(),
            )

        errors = tuple(result.hard_errors)
        authority_violation = bool(result.authority_violations)
        repair_allowed = (
            not authority_violation and repairs < max(0, int(max_repairs))
        )
        events.append(
            {
                "attempt": attempt,
                "category": "hard_error",
                "code": (
                    "authority_violation"
                    if result.authority_violations
                    else "role_contract_violation"
                ),
                "severity": "hard_error",
                "owner": role,
                "retry_policy": (
                    "bounded_repair" if repair_allowed else "none"
                ),
                "role": role,
                "message": "; ".join(errors),
                "invalid_fields": list(errors),
                "authority_violations": list(result.authority_violations),
            }
        )
        if not repair_allowed:
            return StructuredRoleResult(
                obj=None,
                initial_messages=initial,
                final_messages=current,
                replies=tuple(replies),
                validation_events=tuple(events),
                hard_errors=errors,
            )
        if stable_source is None:
            stable_source = _parse_object(raw)
        repairs += 1
        feedback = _repair_feedback(role, errors)
        events[-1]["retry_feedback"] = feedback
        current = [
            *current,
            {
                "role": "assistant",
                "content": raw,
            },
            {"role": "user", "content": feedback},
        ]


def _parse_object(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _restore_stable_role_fields(
    role: str,
    source: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Keep first-attempt structured judgments stable during contract repair."""
    paths = list(_STABLE_ROLE_PATHS.get(role, ()))
    if role == "vpa_evidence":
        report = source.get("vpa_report")
        if isinstance(report, dict):
            for index, assessment in enumerate(
                report.get("hypothesis_assessments", [])
            ):
                if isinstance(assessment, dict):
                    paths.extend(
                        (
                            ("vpa_report", "hypothesis_assessments", index, "hypothesis_id"),
                            ("vpa_report", "hypothesis_assessments", index, "relation"),
                        )
                    )
            for index, observation in enumerate(report.get("observations", [])):
                if isinstance(observation, dict):
                    paths.extend(
                        (
                            ("vpa_report", "observations", index, "finding_kind"),
                            ("vpa_report", "observations", index, "interpretation_label"),
                            ("vpa_report", "observations", index, "epistemic_status"),
                        )
                    )
                    for claim_index, claim in enumerate(
                        observation.get("sequence_claims", [])
                    ):
                        if isinstance(claim, dict):
                            paths.append(
                                (
                                    "vpa_report",
                                    "observations",
                                    index,
                                    "sequence_claims",
                                    claim_index,
                                    "relation",
                                )
                            )

    out = copy.deepcopy(candidate)
    events: list[dict[str, Any]] = []
    for path in paths:
        original = _get_nested(source, path)
        current = _get_nested(out, path)
        if original is _MISSING or original == current:
            continue
        if not _set_nested(out, path, copy.deepcopy(original)):
            continue
        events.append(
            {
                "path": ".".join(str(part) for part in path),
                "before": (
                    "missing_from_repair" if current is _MISSING else copy.deepcopy(current)
                ),
                "after": copy.deepcopy(original),
                "reason": "preserve_unrelated_structured_judgment_during_repair",
            }
        )
    return out, events


def _get_nested(value: Any, path: tuple[str | int, ...]) -> Any:
    current = value
    for part in path:
        if isinstance(part, int):
            if not isinstance(current, list) or not 0 <= part < len(current):
                return _MISSING
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                return _MISSING
            current = current[part]
    return current


def _set_nested(value: Any, path: tuple[str | int, ...], replacement: Any) -> bool:
    current = value
    for part in path[:-1]:
        if isinstance(part, int):
            if not isinstance(current, list) or not 0 <= part < len(current):
                return False
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
    leaf = path[-1]
    if isinstance(leaf, int):
        if not isinstance(current, list) or not 0 <= leaf < len(current):
            return False
        current[leaf] = replacement
    else:
        if not isinstance(current, dict):
            return False
        current[leaf] = replacement
    return True


def _repair_feedback(role: str, errors: tuple[str, ...]) -> str:
    listed = "\n".join(f"- {item}" for item in errors[:20])
    return (
        f"{role} 输出未通过确定性契约。请返回修正后的完整裸 JSON object；"
        "聚焦修复下列字段或引用，并优先保持未涉及的专业判断稳定；"
        "无需为了措辞逐字一致而牺牲完整、自然的最终结论。"
        "不要解释校验过程，也不要输出 Markdown：\n"
        f"{listed}"
    )
