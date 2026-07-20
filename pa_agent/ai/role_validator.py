"""Validation for the PA/VPA/joint role hand-offs.

Only reproducible facts, schema shape, citations, authority, and order safety
are hard failures.  Professional VPA interpretation remains an LLM judgment.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from pa_agent.ai.json_validator import (
    JsonValidator,
    Ok,
    ValidationError,
    format_model_json_for_context,
)
from pa_agent.ai.role_contracts import (
    JOINT_DECISION_SCHEMA,
    VPA_EVIDENCE_SCHEMA,
    JointArbitration,
    JointDecisionProposal,
    PaHypothesisSet,
    VpaEvidenceReport,
)
from pa_agent.ai.vpa_measurements import evidence_has_relative_volume_comparison
from pa_agent.ai.vpa_sanitizer import materialize_vpa_observation_ranges
from pa_agent.config.settings import ValidationSettings

_EXECUTABLE_ORDER_TYPES = frozenset(
    {"限价单", "突破单", "市价单", "limit", "breakout", "market"}
)
_TIMING_FIELDS = frozenset(
    {
        "order_type",
        "entry_price",
        "entry_basis_bar",
        "entry_basis_extreme",
        "entry_rule",
        "trade_confidence",
        "trade_confidence_reasoning",
        "reasoning",
        "key_factors",
        "watch_points",
        "risk_assessment",
        "invalidation_condition",
    }
)


@dataclass(frozen=True)
class RoleValidationResult:
    """Parsed role output and its non-overlapping validation classes."""

    obj: dict[str, Any] | None
    hard_errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    normalizations: tuple[dict[str, Any], ...] = ()

    @property
    def ok(self) -> bool:
        return self.obj is not None and not self.hard_errors


def _parse_json_object(raw_text: str) -> tuple[dict[str, Any] | None, list[str]]:
    rendered = format_model_json_for_context(raw_text)
    if not rendered:
        return None, ["response must contain one JSON object"]
    try:
        obj = json.loads(rendered)
    except json.JSONDecodeError as exc:
        return None, [f"invalid JSON at {exc.lineno}:{exc.colno}: {exc.msg}"]
    if not isinstance(obj, dict):
        return None, ["top-level JSON value must be an object"]
    return obj, []


def _schema_errors(schema: dict[str, Any], obj: dict[str, Any]) -> list[str]:
    try:
        import jsonschema  # type: ignore[import]
    except ImportError:
        return ["required validator dependency jsonschema is not installed"]
    result: list[str] = []
    for error in jsonschema.Draft7Validator(schema).iter_errors(obj):
        path = ".".join(str(item) for item in error.absolute_path) or "<root>"
        result.append(f"{path}: {error.message}")
    return result


def validate_vpa_evidence(
    raw_text: str,
    *,
    catalog: dict[str, Any],
    pa_hypotheses: PaHypothesisSet,
) -> RoleValidationResult:
    """Validate an independent VPA report against immutable evidence."""
    outer, parse_errors = _parse_json_object(raw_text)
    if outer is None:
        return RoleValidationResult(None, tuple(parse_errors))

    normalizations: list[dict[str, Any]] = []
    if "vpa_report" not in outer and {
        "catalog_id",
        "data_status",
        "observations",
        "background_relation",
        "entry_timing_relation",
    }.issubset(outer):
        outer = {"vpa_report": outer}
        normalizations.append(
            {
                "path": "<root>",
                "before": "unwrapped_vpa_report",
                "after": "vpa_report",
            }
        )

    report_obj = outer.get("vpa_report")
    evidence_items = {
        str(item.get("evidence_id")): item
        for item in catalog.get("evidence", [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    known_ids = set(evidence_items)
    cited_before_materialization = {
        str(evidence_id)
        for section in (
            (
                report_obj.get("observations", [])
                if isinstance(report_obj, dict)
                else []
            ),
            (
                report_obj.get("hypothesis_assessments", [])
                if isinstance(report_obj, dict)
                else []
            ),
        )
        for item in section
        if isinstance(item, dict)
        for evidence_id in item.get("evidence_ids", [])
    }
    pre_schema_errors: list[str] = []
    unknown_before_materialization = sorted(
        cited_before_materialization - known_ids
    )
    if unknown_before_materialization:
        pre_schema_errors.append(
            "unknown evidence IDs: "
            + ", ".join(unknown_before_materialization)
        )
    raw_assessments = (
        report_obj.get("hypothesis_assessments", [])
        if isinstance(report_obj, dict)
        else []
    )
    raw_assessed_hypotheses = {
        str(item.get("hypothesis_id"))
        for item in raw_assessments
        if isinstance(item, dict) and item.get("hypothesis_id")
    }
    if raw_assessed_hypotheses != pa_hypotheses.hypothesis_ids:
        pre_schema_errors.append(
            "hypothesis_assessments must cover exactly: "
            + ", ".join(sorted(pa_hypotheses.hypothesis_ids))
        )
    if isinstance(report_obj, dict):
        materialized = materialize_vpa_observation_ranges(
            {"vpa_context": report_obj},
            catalog,
        )
        report_obj = materialized.get("vpa_context")
        outer = {"vpa_report": report_obj}

    errors = [
        *pre_schema_errors,
        *_schema_errors(VPA_EVIDENCE_SCHEMA, outer),
    ]
    if errors:
        return RoleValidationResult(
            None,
            tuple(errors),
            normalizations=tuple(normalizations),
        )

    try:
        report = VpaEvidenceReport.model_validate(report_obj)
    except PydanticValidationError as exc:
        return RoleValidationResult(
            None,
            tuple(
                f"{'.'.join(str(item) for item in item['loc'])}: {item['msg']}"
                for item in exc.errors()
            ),
            normalizations=tuple(normalizations),
        )

    unknown_ids = sorted(report.evidence_ids - known_ids)
    if unknown_ids:
        errors.append("unknown evidence IDs: " + ", ".join(unknown_ids))
    if report.catalog_id != str(catalog.get("catalog_id") or ""):
        errors.append("vpa_report.catalog_id does not match the frozen catalog")
    if report.data_status != str(catalog.get("data_status") or ""):
        errors.append("vpa_report.data_status does not match the frozen catalog")

    expected_hypotheses = pa_hypotheses.hypothesis_ids
    assessed_hypotheses = {
        item.hypothesis_id for item in report.hypothesis_assessments
    }
    if assessed_hypotheses != expected_hypotheses:
        errors.append(
            "hypothesis_assessments must cover exactly: "
            + ", ".join(sorted(expected_hypotheses))
        )

    relative_ids = {
        evidence_id
        for evidence_id, item in evidence_items.items()
        if evidence_has_relative_volume_comparison(item)
    }
    if (
        report.background_relation in {"supports", "challenges"}
        or report.entry_timing_relation in {"supports", "challenges"}
    ) and not (report.evidence_ids & relative_ids):
        errors.append(
            "supports/challenges requires cited program relative-volume evidence"
        )
    for index, assessment in enumerate(report.hypothesis_assessments):
        if assessment.relation in {"supports", "challenges"}:
            if not assessment.evidence_ids:
                errors.append(
                    f"hypothesis_assessments.{index} requires evidence_ids"
                )
            elif not (set(assessment.evidence_ids) & relative_ids):
                errors.append(
                    f"hypothesis_assessments.{index} requires relative-volume evidence"
                )

    if report.data_status == "unavailable":
        if report.background_relation != "insufficient_data":
            errors.append(
                "unavailable VPA data requires background_relation=insufficient_data"
            )
        if report.entry_timing_relation != "insufficient_data":
            errors.append(
                "unavailable VPA data requires entry_timing_relation=insufficient_data"
            )
        if any(
            item.relation != "insufficient_data"
            for item in report.hypothesis_assessments
        ):
            errors.append(
                "unavailable VPA data requires insufficient_data assessments"
            )

    warnings: list[str] = []
    if not report.observations and report.data_status != "unavailable":
        warnings.append("usable VPA data produced no contextual observations")
    if (
        report.background_relation == "neutral"
        and report.entry_timing_relation == "neutral"
    ):
        warnings.append("VPA report is neutral; no decision change is expected")

    return RoleValidationResult(
        outer if not errors else None,
        tuple(errors),
        tuple(warnings),
        tuple(normalizations),
    )


def validate_joint_decision(
    raw_text: str,
    *,
    baseline_stage2: dict[str, Any],
    stage1: dict[str, Any],
    pa_hypotheses: PaHypothesisSet,
    vpa_report: VpaEvidenceReport,
    catalog: dict[str, Any],
    kline_frame: Any,
    skip_next_bar: bool,
) -> RoleValidationResult:
    """Validate that joint arbitration stays inside PA and VPA authority."""
    outer, parse_errors = _parse_json_object(raw_text)
    if outer is None:
        return RoleValidationResult(None, tuple(parse_errors))
    errors = _schema_errors(JOINT_DECISION_SCHEMA, outer)
    if errors:
        return RoleValidationResult(None, tuple(errors))

    try:
        envelope = JointDecisionProposal.model_validate(outer)
        arbitration = JointArbitration.model_validate(envelope.joint_arbitration)
    except PydanticValidationError as exc:
        return RoleValidationResult(
            None,
            tuple(
                f"{'.'.join(str(item) for item in item['loc'])}: {item['msg']}"
                for item in exc.errors()
            ),
        )

    stage2 = envelope.stage2_decision
    validator = JsonValidator(ValidationSettings())
    stage2_result = validator.validate(
        "stage2",
        json.dumps(stage2, ensure_ascii=False),
        kline_frame=kline_frame,
        stage1_json=stage1,
        skip_next_bar=skip_next_bar,
    )
    warnings: list[str] = []
    if isinstance(stage2_result, ValidationError):
        errors.extend(
            [
                stage2_result.message,
                *[f"missing:{item}" for item in stage2_result.missing_fields],
                *[f"invalid:{item}" for item in stage2_result.invalid_fields],
            ]
        )
    else:
        assert isinstance(stage2_result, Ok)
        stage2 = stage2_result.obj
        outer["stage2_decision"] = stage2
        warnings.extend(item.message for item in stage2_result.warnings)

    baseline_decision = baseline_stage2.get("decision")
    joint_decision = stage2.get("decision")
    if not isinstance(baseline_decision, dict) or not isinstance(joint_decision, dict):
        errors.append("baseline and joint decisions must both be objects")
        return RoleValidationResult(None, tuple(errors), tuple(warnings))

    baseline_order = str(baseline_decision.get("order_type") or "")
    joint_order = str(joint_decision.get("order_type") or "")
    baseline_executable = baseline_order in _EXECUTABLE_ORDER_TYPES
    joint_executable = joint_order in _EXECUTABLE_ORDER_TYPES
    if not baseline_executable and joint_executable:
        errors.append("joint arbiter cannot create a trade from a PA no-order baseline")
    if joint_decision.get("diagnosis_confidence") != baseline_decision.get(
        "diagnosis_confidence"
    ):
        errors.append("joint arbiter cannot change diagnosis_confidence")
    if joint_decision.get("estimated_win_rate") != baseline_decision.get(
        "estimated_win_rate"
    ):
        errors.append("joint arbiter cannot change estimated_win_rate")
    if (
        baseline_executable
        and joint_executable
        and joint_decision.get("order_direction")
        != baseline_decision.get("order_direction")
    ):
        errors.append("joint arbiter cannot reverse the PA order direction")

    if arbitration.selected_candidate_id is not None:
        if arbitration.selected_candidate_id not in pa_hypotheses.candidate_ids:
            errors.append("joint_arbitration selected an unknown PA candidate")
    elif joint_executable:
        errors.append("an executable joint decision requires selected_candidate_id")

    used = set(arbitration.evidence_used)
    if not used.issubset(vpa_report.evidence_ids):
        errors.append("joint_arbitration cited evidence absent from vpa_report")
    evidence_items = {
        str(item.get("evidence_id")): item
        for item in catalog.get("evidence", [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    relative_used = any(
        evidence_has_relative_volume_comparison(evidence_items.get(item))
        for item in used
    )
    effect = arbitration.decision_effect
    if effect != "keep_brooks_baseline" and not used:
        errors.append("a non-baseline decision_effect requires VPA evidence")
    if effect != "keep_brooks_baseline" and not relative_used:
        errors.append(
            "a non-baseline decision_effect requires relative-volume evidence"
        )
    if (
        vpa_report.entry_timing_relation in {"neutral", "insufficient_data"}
        and effect != "keep_brooks_baseline"
    ):
        errors.append(
            "neutral/insufficient VPA timing must keep the PA baseline"
        )
    if not baseline_executable and effect != "keep_brooks_baseline":
        errors.append("VPA cannot alter a PA no-order baseline")

    baseline_clean = copy.deepcopy(baseline_stage2)
    baseline_clean.pop("confidence_threshold", None)
    if effect == "keep_brooks_baseline":
        if stage2 != baseline_clean:
            errors.append(
                "keep_brooks_baseline requires an unchanged Stage 2 decision"
            )
    else:
        if stage2 == baseline_clean:
            errors.append("non-baseline decision_effect did not change timing")
        changed_decision_fields = {
            key
            for key in set(baseline_decision) | set(joint_decision)
            if baseline_decision.get(key) != joint_decision.get(key)
        }
        forbidden_changes = changed_decision_fields - _TIMING_FIELDS
        if forbidden_changes:
            errors.append(
                "joint arbiter changed non-timing decision fields: "
                + ", ".join(sorted(forbidden_changes))
            )
        if effect == "lower_confidence":
            try:
                lowered = int(joint_decision.get("trade_confidence"))
                baseline_confidence = int(
                    baseline_decision.get("trade_confidence")
                )
            except (TypeError, ValueError):
                lowered = baseline_confidence = -1
            if lowered < 0 or lowered >= baseline_confidence:
                errors.append(
                    "lower_confidence requires a strictly lower trade_confidence"
                )
        if effect == "reject_current_attempt" and joint_executable:
            errors.append("reject_current_attempt requires a no-order decision")
        if effect in {"require_confirmation", "prefer_retest"} and not (
            changed_decision_fields
            & {
                "order_type",
                "entry_price",
                "entry_basis_bar",
                "entry_basis_extreme",
                "entry_rule",
                "watch_points",
                "invalidation_condition",
            }
        ):
            errors.append(
                f"{effect} must change a trigger, entry, or watch condition"
            )

    return RoleValidationResult(
        outer if not errors else None,
        tuple(errors),
        tuple(warnings),
    )
