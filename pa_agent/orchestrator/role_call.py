"""One bounded structured-output loop for VPA and joint roles."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pa_agent.ai.json_validator import coalesce_model_json_text
from pa_agent.ai.role_validator import RoleValidationResult


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

    while True:
        reply = call_api(current)
        replies.append(reply)
        raw = coalesce_model_json_text(
            str(getattr(reply, "content", "") or ""),
            str(getattr(reply, "reasoning_content", "") or ""),
        )
        result = validate(raw)
        attempt = len(replies)
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
        for warning in result.warnings:
            events.append(
                {
                    "attempt": attempt,
                    "category": "warning",
                    "code": "professional_prose_warning",
                    "severity": "warning",
                    "owner": role,
                    "retry_policy": "none",
                    "role": role,
                    "message": warning,
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
                    "none" if authority_violation else "bounded_repair"
                ),
                "role": role,
                "message": "; ".join(errors),
                "invalid_fields": list(errors),
                "authority_violations": list(result.authority_violations),
            }
        )
        if repairs >= max(0, int(max_repairs)):
            return StructuredRoleResult(
                obj=None,
                initial_messages=initial,
                final_messages=current,
                replies=tuple(replies),
                validation_events=tuple(events),
                hard_errors=errors,
            )
        if authority_violation:
            return StructuredRoleResult(
                obj=None,
                initial_messages=initial,
                final_messages=current,
                replies=tuple(replies),
                validation_events=tuple(events),
                hard_errors=errors,
            )

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


def _repair_feedback(role: str, errors: tuple[str, ...]) -> str:
    listed = "\n".join(f"- {item}" for item in errors[:20])
    return (
        f"{role} 输出未通过确定性契约。请返回修正后的完整裸 JSON object；"
        "聚焦修复下列字段或引用，并优先保持未涉及的专业判断稳定；"
        "无需为了措辞逐字一致而牺牲完整、自然的最终结论。"
        "不要解释校验过程，也不要输出 Markdown：\n"
        f"{listed}"
    )
