"""One bounded structured-output loop for VPA and joint roles."""
from __future__ import annotations

import copy
import json
import re
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
    repair_editable_paths: tuple[tuple[str | int, ...], ...] = ()

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
                    editable_paths=repair_editable_paths,
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
        repair_editable_paths = _error_paths(errors)
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
    *,
    editable_paths: tuple[tuple[str | int, ...], ...] = (),
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Keep first-attempt structured judgments stable during contract repair."""
    path_pairs = [(path, path) for path in _STABLE_ROLE_PATHS.get(role, ())]
    if role == "vpa_evidence":
        path_pairs.extend(_vpa_identity_path_pairs(source, candidate))

    out = copy.deepcopy(candidate)
    events: list[dict[str, Any]] = []
    for source_path, candidate_path in path_pairs:
        if _overlaps_any(candidate_path, editable_paths):
            continue
        original = _get_nested(source, source_path)
        current = _get_nested(out, candidate_path)
        if original is _MISSING or original == current:
            continue
        if not _set_nested(out, candidate_path, copy.deepcopy(original)):
            continue
        events.append(
            {
                "path": ".".join(str(part) for part in candidate_path),
                "before": (
                    "missing_from_repair" if current is _MISSING else copy.deepcopy(current)
                ),
                "after": copy.deepcopy(original),
                "reason": "preserve_unrelated_structured_judgment_during_repair",
            }
        )
    return out, events


_PATH_PART = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _error_paths(errors: tuple[str, ...]) -> tuple[tuple[str | int, ...], ...]:
    """Extract validator path prefixes; prose-only errors grant no edit scope."""
    result: list[tuple[str | int, ...]] = []
    for error in errors:
        prefix, separator, _ = str(error).partition(":")
        if not separator or prefix in {"", "<root>"} or " " in prefix:
            continue
        parts: list[str | int] = []
        for match in _PATH_PART.finditer(prefix):
            name, index = match.groups()
            numeric = index if index is not None else name if name.isdigit() else None
            parts.append(int(numeric) if numeric is not None else name)
        if parts:
            result.append(tuple(parts))
    return tuple(dict.fromkeys(result))


def _overlaps_any(
    path: tuple[str | int, ...],
    editable_paths: tuple[tuple[str | int, ...], ...],
) -> bool:
    return any(
        path[: len(editable)] == editable or editable[: len(path)] == path
        for editable in editable_paths
    )


def _unique_index_by(items: Any, identity: Callable[[dict[str, Any]], Any]) -> dict[Any, int]:
    if not isinstance(items, list):
        return {}
    indexes: dict[Any, int] = {}
    duplicates: set[Any] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        key = identity(item)
        if key is None or key in indexes:
            duplicates.add(key)
            continue
        indexes[key] = index
    for key in duplicates:
        indexes.pop(key, None)
    return indexes


def _vpa_identity_path_pairs(
    source: dict[str, Any], candidate: dict[str, Any]
) -> list[tuple[tuple[str | int, ...], tuple[str | int, ...]]]:
    """Map stable VPA judgments by semantic identity, never array position."""
    source_report = source.get("vpa_report")
    candidate_report = candidate.get("vpa_report")
    if not isinstance(source_report, dict) or not isinstance(candidate_report, dict):
        return []
    pairs: list[tuple[tuple[str | int, ...], tuple[str | int, ...]]] = []

    def assessment_id(item: dict[str, Any]) -> Any:
        return item.get("hypothesis_id")

    source_assessments = _unique_index_by(
        source_report.get("hypothesis_assessments"), assessment_id
    )
    candidate_assessments = _unique_index_by(
        candidate_report.get("hypothesis_assessments"), assessment_id
    )
    for identity in source_assessments.keys() & candidate_assessments.keys():
        pairs.append(
            (
                ("vpa_report", "hypothesis_assessments", source_assessments[identity], "relation"),
                ("vpa_report", "hypothesis_assessments", candidate_assessments[identity], "relation"),
            )
        )

    def observation_id(item: dict[str, Any]) -> Any:
        return tuple(sorted(map(str, item.get("evidence_ids", [])))) or None

    source_observations = _unique_index_by(source_report.get("observations"), observation_id)
    candidate_observations = _unique_index_by(
        candidate_report.get("observations"), observation_id
    )
    for identity in source_observations.keys() & candidate_observations.keys():
        source_index = source_observations[identity]
        candidate_index = candidate_observations[identity]
        source_prefix = ("vpa_report", "observations", source_index)
        candidate_prefix = ("vpa_report", "observations", candidate_index)
        pairs.extend(
            ((source_prefix + (field,), candidate_prefix + (field,)))
            for field in ("finding_kind", "interpretation_label", "epistemic_status")
        )
        source_item = source_report["observations"][source_index]
        candidate_item = candidate_report["observations"][candidate_index]
        def claim_id(item: dict[str, Any]) -> Any:
            return (
                item.get("earlier_evidence_id"),
                item.get("later_evidence_id"),
            )
        source_claims = _unique_index_by(source_item.get("sequence_claims"), claim_id)
        candidate_claims = _unique_index_by(candidate_item.get("sequence_claims"), claim_id)
        for claim_identity in source_claims.keys() & candidate_claims.keys():
            pairs.append(
                (
                    source_prefix
                    + ("sequence_claims", source_claims[claim_identity], "relation"),
                    candidate_prefix
                    + ("sequence_claims", candidate_claims[claim_identity], "relation"),
                )
            )
    return pairs


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
