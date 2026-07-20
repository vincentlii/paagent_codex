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

from pa_agent.ai.factual_grounding import (
    validate_future_confirmation_narrative,
    validate_inference_only_narrative,
    validate_k_reference_bounds,
    validate_vpa_capability_narrative,
    validate_vpa_comparator_narrative,
    validate_vpa_k_references,
)
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
from pa_agent.ai.vpa_sanitizer import materialize_vpa_report_observation_ranges
from pa_agent.config.settings import ValidationSettings

_EXECUTABLE_ORDER_TYPES = frozenset(
    {"限价单", "突破单", "市价单", "limit", "breakout", "market"}
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


def _vpa_capability_context(
    catalog: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    capabilities = catalog.get("capabilities")
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    comparators = catalog.get("comparators")
    comparators = comparators if isinstance(comparators, dict) else {}
    seasonality = comparators.get("seasonality")
    seasonality = seasonality if isinstance(seasonality, dict) else {}
    market_session = str(seasonality.get("market_session") or "unclassified")
    return capabilities, market_session


def _validate_vpa_report_grounding(
    report: VpaEvidenceReport,
    *,
    evidence_items: dict[str, dict[str, Any]],
    catalog: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Validate only reproducible narrative facts and capability boundaries."""
    errors: list[str] = []
    warnings: list[str] = []
    capabilities, market_session = _vpa_capability_context(catalog)

    def validate_inference(
        text: object,
        *,
        path: str,
        cited_ids: set[str],
        check_k_references: bool,
    ) -> None:
        cited = [
            evidence_items[evidence_id]
            for evidence_id in sorted(cited_ids)
            if evidence_id in evidence_items
        ]
        warnings.extend(validate_inference_only_narrative(text, path=path))
        warnings.extend(
            validate_vpa_capability_narrative(
                text,
                path=path,
                capabilities=capabilities,
                market_session=market_session,
            )
        )
        warnings.extend(
            validate_vpa_comparator_narrative(
                text,
                evidence_items=cited,
                path=path,
            )
        )
        if check_k_references:
            errors.extend(
                validate_vpa_k_references(
                    text,
                    evidence_items=cited,
                    path=path,
                )
            )

    validate_inference(
        report.summary,
        path="vpa_report.summary",
        cited_ids=report.evidence_ids,
        check_k_references=True,
    )
    for index, observation in enumerate(report.observations):
        validate_inference(
            observation.interpretation,
            path=f"vpa_report.observations[{index}].interpretation",
            cited_ids=set(observation.evidence_ids),
            check_k_references=True,
        )
    for index, assessment in enumerate(report.hypothesis_assessments):
        validate_inference(
            assessment.reason,
            path=f"vpa_report.hypothesis_assessments[{index}].reason",
            cited_ids=set(assessment.evidence_ids),
            check_k_references=True,
        )

    confirmation = report.next_confirmation
    confirmation_path = "vpa_report.next_confirmation.trigger"
    if confirmation.state == "pending_closed_bar":
        confirmation_findings = validate_future_confirmation_narrative(
            confirmation.trigger,
            path=confirmation_path,
        )
        errors.extend(
            item for item in confirmation_findings if "must not use K0" in item
        )
        warnings.extend(
            item for item in confirmation_findings if "must not use K0" not in item
        )
    else:
        warnings.extend(
            validate_inference_only_narrative(
                confirmation.trigger,
                path=confirmation_path,
            )
        )
    warnings.extend(
        validate_vpa_capability_narrative(
            confirmation.trigger,
            path=confirmation_path,
            capabilities=capabilities,
            market_session=market_session,
        )
    )
    errors.extend(
        validate_vpa_k_references(
            confirmation.trigger,
            evidence_items=evidence_items.values(),
            path=confirmation_path,
        )
    )

    substantive = {
        "effort_result",
        "sequence_comparison",
        "acceptance_rejection",
        "entry_timing",
    }
    if report.data_status != "unavailable" and not any(
        observation.finding_kind in substantive
        and any(
            evidence_has_relative_volume_comparison(
                evidence_items.get(evidence_id)
            )
            for evidence_id in observation.evidence_ids
        )
        for observation in report.observations
    ):
        warnings.append(
            "available VPA report has no independent price-volume finding"
        )
    return errors, warnings


def _validate_joint_narratives(
    arbitration: JointArbitration,
    *,
    patch: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    catalog: dict[str, Any],
    maximum_bar_seq: int | None,
) -> tuple[list[str], list[str]]:
    """Ground the joint role's prose without interpreting its trade judgment."""
    errors: list[str] = []
    warnings: list[str] = []
    capabilities, market_session = _vpa_capability_context(catalog)

    def validate_common(text: object, *, path: str) -> None:
        warnings.extend(validate_inference_only_narrative(text, path=path))
        warnings.extend(
            validate_vpa_capability_narrative(
                text,
                path=path,
                capabilities=capabilities,
                market_session=market_session,
            )
        )
        errors.extend(
            validate_k_reference_bounds(
                text,
                maximum=maximum_bar_seq,
                path=path,
            )
        )

    validate_common(arbitration.reason, path="joint_arbitration.reason")
    warnings.extend(
        validate_vpa_comparator_narrative(
            arbitration.reason,
            evidence_items=evidence_items,
            path="joint_arbitration.reason",
        )
    )
    for field in (
        "trade_confidence_reasoning",
        "risk_assessment",
        "invalidation_condition",
    ):
        value = patch.get(field)
        if value:
            path = f"decision_patch.{field}"
            validate_common(value, path=path)
            if field != "invalidation_condition":
                warnings.extend(
                    validate_vpa_comparator_narrative(
                        value,
                        evidence_items=evidence_items,
                        path=path,
                    )
                )
    for index, watch_point in enumerate(patch.get("watch_points") or []):
        validate_common(
            watch_point,
            path=f"decision_patch.watch_points[{index}]",
        )
    return errors, warnings


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
        report_obj = materialize_vpa_report_observation_ranges(
            report_obj,
            catalog,
        )
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
    grounding_errors, grounding_warnings = _validate_vpa_report_grounding(
        report,
        evidence_items=evidence_items,
        catalog=catalog,
    )
    errors.extend(grounding_errors)
    warnings.extend(grounding_warnings)
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
    """Validate and materialize a bounded joint decision patch.

    PA's validated Stage 2 result remains the source of prices, direction,
    setup, trader-equation inputs, and predictions.  The joint arbiter emits
    only its semantic effect and a small patch; this function deterministically
    applies no-order invariants when the arbiter delays or rejects the current
    attempt.
    """
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

    normalizations: list[dict[str, Any]] = []
    raw_patch = envelope.decision_patch.model_dump(exclude_unset=True)
    patch = {key: value for key, value in raw_patch.items() if value is not None}
    if patch != raw_patch:
        normalizations.append(
            {
                "path": "decision_patch",
                "before": raw_patch,
                "after": patch,
                "reason": "explicit null is equivalent to an omitted optional patch field",
            }
        )

    baseline_clean = copy.deepcopy(baseline_stage2)
    baseline_clean.pop("confidence_threshold", None)
    baseline_decision = baseline_clean.get("decision")
    if not isinstance(baseline_decision, dict):
        return RoleValidationResult(
            None,
            ("baseline Stage 2 decision must be an object",),
        )

    baseline_order = str(baseline_decision.get("order_type") or "")
    baseline_executable = baseline_order in _EXECUTABLE_ORDER_TYPES
    effect = arbitration.decision_effect

    warnings: list[str] = []

    if arbitration.selected_candidate_id is not None:
        if arbitration.selected_candidate_id not in pa_hypotheses.candidate_ids:
            errors.append("joint_arbitration selected an unknown PA candidate")
    elif baseline_executable:
        errors.append(
            "an executable PA baseline requires selected_candidate_id, even when delayed"
        )

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
    joint_grounding_errors, joint_grounding_warnings = _validate_joint_narratives(
        arbitration,
        patch=patch,
        evidence_items=[
            evidence_items[evidence_id]
            for evidence_id in sorted(used)
            if evidence_id in evidence_items
        ],
        catalog=catalog,
        maximum_bar_seq=max(
            (int(bar.seq) for bar in getattr(kline_frame, "bars", ())),
            default=None,
        ),
    )
    errors.extend(joint_grounding_errors)
    warnings.extend(joint_grounding_warnings)
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

    if effect == "keep_brooks_baseline":
        if patch:
            errors.append("keep_brooks_baseline requires an empty decision_patch")
    elif effect == "lower_confidence":
        try:
            lowered = int(patch.get("trade_confidence"))
            baseline_confidence = int(baseline_decision.get("trade_confidence"))
        except (TypeError, ValueError):
            lowered = baseline_confidence = -1
        if lowered < 0 or lowered >= baseline_confidence:
            errors.append(
                "lower_confidence requires decision_patch.trade_confidence "
                "to be strictly lower than the PA baseline"
            )
    elif effect in {
        "require_confirmation",
        "prefer_retest",
        "reject_current_attempt",
    } and not patch.get("watch_points") and not patch.get(
        "invalidation_condition"
    ):
        errors.append(
            f"{effect} requires an actionable watch point or invalidation condition"
        )

    if errors:
        return RoleValidationResult(
            None,
            tuple(errors),
            tuple(warnings),
            tuple(normalizations),
        )

    stage2 = copy.deepcopy(baseline_clean)
    joint_decision = stage2["decision"]
    for key, value in patch.items():
        joint_decision[key] = copy.deepcopy(value)

    if effect != "keep_brooks_baseline":
        overlay_reason = (
            "PA baseline remains the frozen analytical path; Joint PA/VPA "
            f"overlay: {arbitration.reason}"
        )
        joint_decision["reasoning"] = overlay_reason
        if not patch.get("trade_confidence_reasoning"):
            joint_decision["trade_confidence_reasoning"] = overlay_reason
        if not patch.get("risk_assessment"):
            joint_decision["risk_assessment"] = overlay_reason

    if effect in {
        "require_confirmation",
        "prefer_retest",
        "reject_current_attempt",
    }:
        joint_decision.update(
            {
                "order_direction": None,
                "order_type": "不下单",
                "entry_price": None,
                "entry_basis_bar": None,
                "entry_basis_extreme": None,
                "entry_rule": None,
                "take_profit_price": None,
                "take_profit_price_2": None,
                "stop_loss_price": None,
                "estimated_win_rate": None,
                "estimated_win_rate_reasoning": None,
            }
        )
        terminal = stage2.get("terminal")
        if isinstance(terminal, dict):
            terminal["outcome"] = (
                "reject" if effect == "reject_current_attempt" else "wait"
            )
            terminal["label"] = {
                "require_confirmation": "PA基线已完成: 联合裁决要求下一根已收盘K线确认",
                "prefer_retest": "PA基线已完成: 联合裁决偏好等待回测",
                "reject_current_attempt": "PA基线已完成: 联合裁决拒绝当前入场尝试",
            }[effect]

    validator = JsonValidator(ValidationSettings())
    stage2_result = validator.validate(
        "stage2",
        json.dumps(stage2, ensure_ascii=False),
        kline_frame=kline_frame,
        stage1_json=stage1,
        skip_next_bar=skip_next_bar,
    )
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
        warnings.extend(item.message for item in stage2_result.warnings)

    if errors:
        return RoleValidationResult(
            None,
            tuple(errors),
            tuple(warnings),
            tuple(normalizations),
        )

    outer["decision_patch"] = patch
    outer["stage2_decision"] = stage2
    normalizations.append(
        {
            "path": "stage2_decision",
            "before": "frozen_pa_baseline",
            "after": f"materialized:{effect}",
            "reason": "program-applied bounded joint decision patch",
        }
    )

    return RoleValidationResult(
        outer,
        (),
        tuple(warnings),
        tuple(normalizations),
    )
