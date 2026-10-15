from __future__ import annotations

import copy
import json

from pa_agent.ai.pa_handoff import compile_pa_handoff
from pa_agent.ai.role_contracts import PaHypothesisSet, VpaEvidenceReport
from pa_agent.ai.role_validator import (
    validate_joint_decision,
    validate_vpa_evidence,
)
from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog
from tests.integration.conftest import VALID_STAGE1, VALID_STAGE2, make_frame


def _hypotheses() -> PaHypothesisSet:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["alternative_cycle_position"] = "trending_tr"
    return compile_pa_handoff(stage1, VALID_STAGE2, make_frame())


def _vpa_payload(
    catalog: dict,
    hypotheses: PaHypothesisSet,
    *,
    relation: str = "supports",
) -> dict:
    return {
        "vpa_report": {
            "summary": "Relative activity adds independent timing evidence.",
            "observations": [
                {
                    "evidence_ids": ["vpa-e001"],
                    "finding_kind": "effort_result",
                    "interpretation": (
                        "vpa-e001 shows effort versus result in the current "
                        "PA location; acceptance still needs a closed bar."
                    ),
                }
            ],
            "hypothesis_assessments": [
                {
                    "hypothesis_id": hypothesis_id,
                    "relation": relation,
                    "evidence_ids": ["vpa-e001"],
                    "reason": "vpa-e001 is relevant to this PA hypothesis.",
                }
                for hypothesis_id in sorted(hypotheses.hypothesis_ids)
            ],
            "background_relation": relation,
            "entry_timing_relation": relation,
            "next_confirmation": {
                "state": "pending_closed_bar",
                "trigger": "Wait for the next closed bar to test acceptance.",
            },
        }
    }


def test_unknown_vpa_references_produce_bounded_repair_pointers() -> None:
    from types import SimpleNamespace
    from pa_agent.agent_runtime.repair_contract import extract_repair_context
    from pa_agent.orchestrator.role_call import run_structured_role

    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    valid = _vpa_payload(catalog, hypotheses)
    invalid = copy.deepcopy(valid)
    invalid["vpa_report"]["observations"][0]["evidence_ids"] = ["vpa-unknown"]
    invalid["vpa_report"]["hypothesis_assessments"][0]["evidence_ids"] = ["vpa-unknown"]
    calls = []

    def call(messages):
        calls.append(messages)
        if len(calls) == 1:
            return SimpleNamespace(content=json.dumps(invalid))
        repair = extract_repair_context(messages)
        assert repair is not None
        assert set(repair["editable_pointers"]) == {
            "/vpa_report/observations/0/evidence_ids",
            "/vpa_report/hypothesis_assessments/0/evidence_ids",
        }
        return SimpleNamespace(content=json.dumps(valid))

    result = run_structured_role(
        role="vpa_evidence", messages=[], call_api=call,
        validate=lambda raw: validate_vpa_evidence(raw, catalog=catalog, pa_hypotheses=hypotheses),
    )
    assert result.ok
    assert len(calls) == 2


def test_pa_hypothesis_set_delegates_stage1_without_new_direction() -> None:
    hypotheses = _hypotheses()

    assert hypotheses.primary.direction == VALID_STAGE1["direction"]
    assert hypotheses.primary.cycle_position == VALID_STAGE1["cycle_position"]
    assert hypotheses.alternative is not None
    assert hypotheses.alternative.cycle_position == "trending_tr"
    assert hypotheses.setup_candidates[0].candidate_id == "pa-setup-1"
    assert hypotheses.setup_candidates[0].timing_state == "eligible"


def test_vpa_reports_all_reference_findings_before_bounded_repair() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    observation = payload["vpa_report"]["observations"][0]
    observation["evidence_ids"].append("vpa-w999")
    observation["interpretation"] = "vpa-w999 supports the response at K999; acceptance is uncertain."
    result = validate_vpa_evidence(json.dumps(payload), catalog=catalog, pa_hypotheses=hypotheses)
    assert not result.ok
    assert any("observations[0].evidence_ids" in error for error in result.hard_errors)
    assert any("observations[0].interpretation" in error and "vpa-w999" in error
               for error in result.hard_errors)
    assert any("references K999 outside" in error for error in result.hard_errors)


def test_vpa_rejects_unknown_narrative_id_even_with_valid_declared_ids() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    payload["vpa_report"]["observations"][0]["interpretation"] = "vpa-w999 supports acceptance."
    result = validate_vpa_evidence(json.dumps(payload), catalog=catalog, pa_hypotheses=hypotheses)
    assert not result.ok
    assert any("interpretation" in error and "vpa-w999" in error for error in result.hard_errors)


def test_stage2_plan_creates_candidate_without_inventing_a_setup_name() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["entry_setup"] = ""

    hypotheses = compile_pa_handoff(stage1, VALID_STAGE2, make_frame())

    assert len(hypotheses.setup_candidates) == 1
    candidate = hypotheses.setup_candidates[0]
    assert candidate.trigger == VALID_STAGE2["decision"]["entry_rule"]
    assert candidate.invalidation == VALID_STAGE2["decision"]["invalidation_condition"]
    assert hypotheses.primary.setup is None
    assert "No current PA setup" not in hypotheses.primary.thesis


def test_vpa_report_requires_exact_hypothesis_coverage_and_known_evidence() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)

    result = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert result.ok
    assert result.obj is not None
    assert result.normalizations == ()
    observation = result.obj["vpa_report"]["observations"][0]
    assert observation["bar_range"] == "K1"

    payload["vpa_report"]["hypothesis_assessments"].pop()
    payload["vpa_report"]["observations"][0]["evidence_ids"] = ["vpa-unknown"]
    invalid = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    assert not invalid.ok
    assert any("unknown evidence IDs" in item for item in invalid.hard_errors)
    assert any(
        "must cover exactly" in item for item in invalid.hard_errors
    )


def test_vpa_report_rejects_duplicate_hypothesis_assessments() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    payload["vpa_report"]["hypothesis_assessments"].append(
        copy.deepcopy(payload["vpa_report"]["hypothesis_assessments"][0])
    )

    result = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert not result.ok
    assert any("exactly one assessment" in item for item in result.hard_errors)


def test_vpa_program_fields_are_materialized_instead_of_copied_by_model() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    payload["vpa_report"]["catalog_id"] = "model-invented"
    payload["vpa_report"]["data_status"] = "unavailable"
    payload["vpa_report"]["next_confirmation"]["requires_closed_bar"] = False

    result = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert result.ok
    report = result.obj["vpa_report"]
    assert report["catalog_id"] == catalog["catalog_id"]
    assert report["data_status"] == catalog["data_status"]
    assert report["next_confirmation"]["requires_closed_bar"] is True


def test_vpa_draft_drops_harmless_extra_fields_but_rejects_authority_fields() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    observation = payload["vpa_report"]["observations"][0]
    observation["remarks"] = "Non-authoritative provider prose."

    normalized = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert normalized.ok
    assert "remarks" not in normalized.obj["vpa_report"]["observations"][0]
    assert any(
        item["path"] == "vpa_report.observations[0].remarks"
        for item in normalized.normalizations
    )

    payload["vpa_report"]["observations"][0]["metadata"] = {
        "trade_plan": {"order_type": "market"}
    }
    forbidden = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert not forbidden.ok
    assert forbidden.authority_violations == (
        "vpa_forbidden_field:vpa_report.observations[0].metadata.trade_plan.order_type",
    )


def test_vpa_sequence_claim_ids_are_materialized_into_parent_observation() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    payload["vpa_report"]["observations"][0]["sequence_claims"] = [
        {
            "earlier_evidence_id": "vpa-e002",
            "later_evidence_id": "vpa-e001",
            "relation": "response",
        }
    ]

    result = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert result.ok
    observation = result.obj["vpa_report"]["observations"][0]
    assert observation["evidence_ids"] == ["vpa-e001"]
    assert observation["auto_bound_evidence_ids"] == ["vpa-e002"]
    assert any(
        item["path"] == "vpa_report.observations[0].auto_bound_evidence_ids"
        and item["after"] == ["vpa-e002"]
        for item in result.reference_bindings
    )


def test_vpa_sequence_claim_still_rejects_unknown_evidence() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    payload["vpa_report"]["observations"][0]["sequence_claims"] = [
        {
            "earlier_evidence_id": "vpa-unknown",
            "later_evidence_id": "vpa-e001",
            "relation": "response",
        }
    ]

    result = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert not result.ok
    assert any("unknown evidence IDs" in item for item in result.hard_errors)


def test_vpa_rejects_false_absence_of_already_closed_follow_through_bars() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    observation = payload["vpa_report"]["observations"][0]
    observation["evidence_ids"] = ["vpa-e008", "vpa-e007"]
    observation["interpretation"] = (
        "K8与K7完成边界互动，但之后尚无下一根已收盘K线确认。"
    )

    result = validate_vpa_evidence(
        json.dumps(payload, ensure_ascii=False),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert not result.ok
    assert any(
        "K7 already has 6 later closed bars" in item
        for item in result.hard_errors
    )


def test_vpa_no_later_bar_claim_is_bound_to_its_subject_not_other_mentions() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    observation = payload["vpa_report"]["observations"][0]
    observation["evidence_ids"] = ["vpa-e002", "vpa-e001"]
    observation["interpretation"] = (
        "K1收盘转上，相对K2活动下降，但K1没有后续已收盘棒。"
        "K1再度上行，其活动低于K2但仍高于自身基线，且没有更晚的收盘棒可供确认。"
    )

    result = validate_vpa_evidence(
        json.dumps(payload, ensure_ascii=False),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert result.ok


def test_vpa_rejects_explicit_false_no_later_bar_claim_after_comparison() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    observation = payload["vpa_report"]["observations"][0]
    observation["evidence_ids"] = ["vpa-e002", "vpa-e001"]
    observation["interpretation"] = "K1反弹，但K2之后没有更晚的已收盘棒。"

    result = validate_vpa_evidence(
        json.dumps(payload, ensure_ascii=False),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert not result.ok
    assert any("K2 already has 1 later closed bars" in item for item in result.hard_errors)


def test_vpa_allows_unconfirmed_result_after_existing_closed_bars() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    observation = payload["vpa_report"]["observations"][0]
    observation["evidence_ids"] = ["vpa-e007"]
    observation["interpretation"] = (
        "K7 already has later closed bars, but none confirms acceptance "
        "outside the boundary."
    )

    result = validate_vpa_evidence(
        json.dumps(payload, ensure_ascii=False),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert result.ok


def test_vpa_overlapping_sequence_claim_is_dropped_without_failing_report() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    observation = payload["vpa_report"]["observations"][0]
    observation["evidence_ids"] = ["vpa-w005", "vpa-e001"]
    observation["sequence_claims"] = [
        {
            "earlier_evidence_id": "vpa-w005",
            "later_evidence_id": "vpa-e001",
            "relation": "follow_through",
        }
    ]

    result = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert result.ok
    assert result.obj["vpa_report"]["observations"][0]["sequence_claims"] == []
    assert any("chronology overlaps" in item for item in result.warnings)
    assert any(
        item["path"] == "vpa_report.observations[0].sequence_claims[0]"
        and item["after"] == "dropped_overlapping_chronology"
        for item in result.normalizations
    )


def test_vpa_role_audits_an_explicit_comparator_contradiction() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())

    numeric = _vpa_payload(catalog, hypotheses)
    numeric["vpa_report"]["summary"] = "Program volume was 100."
    numeric_result = validate_vpa_evidence(
        json.dumps(numeric),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    assert numeric_result.ok
    assert any(
        "numeric market facts" in item for item in numeric_result.warnings
    )

    conflict = _vpa_payload(catalog, hypotheses)
    conflict["vpa_report"]["observations"][0]["interpretation"] = (
        "vpa-e001 is above the causal baseline."
    )
    conflict_result = validate_vpa_evidence(
        json.dumps(conflict),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    assert conflict_result.ok
    assert any(
        "conflicts with program relation" in item
        for item in conflict_result.warnings
    )


def test_vpa_multi_evidence_clause_is_unverified_not_a_false_conflict() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    observation = payload["vpa_report"]["observations"][0]
    observation["evidence_ids"] = ["vpa-e001", "vpa-e002"]
    observation["interpretation"] = (
        "vpa-e002 is above the causal baseline while vpa-e001 is below "
        "the previous bar."
    )

    result = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert result.ok
    assert any("unverified comparator claim: multiple evidence selectors" in item
               for item in result.warnings)
    assert not any("claim for vpa-e001" in item for item in result.warnings)


def test_vpa_role_audits_unsupported_attribution_but_allows_disclaimer() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())

    attributed = _vpa_payload(catalog, hypotheses)
    attributed["vpa_report"]["observations"][0]["interpretation"] = (
        "Institutional buyers absorbed selling pressure at K1."
    )
    attributed_result = validate_vpa_evidence(
        json.dumps(attributed),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    assert attributed_result.ok
    assert any(
        "cannot support actor attribution" in item
        for item in attributed_result.warnings
    )

    disclaimer = _vpa_payload(catalog, hypotheses)
    disclaimer["vpa_report"]["observations"][0]["interpretation"] = (
        "Volume cannot identify institutional buyers; vpa-e001 only supports "
        "a relative-activity interpretation."
    )
    disclaimer_result = validate_vpa_evidence(
        json.dumps(disclaimer),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    assert disclaimer_result.ok
    assert not any(
        "cannot support actor attribution" in item
        for item in disclaimer_result.warnings
    )


def test_vpa_role_rejects_unclosed_confirmation_and_unavailable_k_reference() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())

    unclosed = _vpa_payload(catalog, hypotheses)
    unclosed["vpa_report"]["next_confirmation"]["trigger"] = (
        "Wait for the next closed bar, but use K0 forming bar as confirmation."
    )
    unclosed_result = validate_vpa_evidence(
        json.dumps(unclosed),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    assert not unclosed_result.ok
    assert any("must not use K0" in item for item in unclosed_result.hard_errors)

    absent_anchor = _vpa_payload(catalog, hypotheses)
    absent_anchor["vpa_report"]["next_confirmation"]["trigger"] = (
        "Wait for the next closed bar to accept above K99 high."
    )
    absent_anchor_result = validate_vpa_evidence(
        json.dumps(absent_anchor),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    assert not absent_anchor_result.ok
    assert any(
        "K99 outside its cited evidence ranges" in item
        for item in absent_anchor_result.hard_errors
    )

    uncited = _vpa_payload(catalog, hypotheses)
    uncited["vpa_report"]["observations"][0]["interpretation"] = (
        "K2 provides the relevant effort-versus-result context."
    )
    uncited_result = validate_vpa_evidence(
        json.dumps(uncited),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    assert uncited_result.ok
    normalized_observation = uncited_result.obj["vpa_report"]["observations"][0]
    assert normalized_observation["evidence_ids"] == ["vpa-e001"]
    assert normalized_observation["auto_bound_evidence_ids"] == ["vpa-e002"]
    assert any(
        item["path"] == "vpa_report.observations[0].auto_bound_evidence_ids"
        and item["after"] == ["vpa-e002"]
        for item in uncited_result.reference_bindings
    )
    assert not any("auto_bound_evidence_ids" in item["path"]
                   for item in uncited_result.normalizations)

    unavailable = _vpa_payload(catalog, hypotheses)
    unavailable["vpa_report"]["observations"][0]["interpretation"] = (
        "K99 provides the relevant effort-versus-result context."
    )
    unavailable_result = validate_vpa_evidence(
        json.dumps(unavailable),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    assert not unavailable_result.ok
    assert any(
        "K99 outside its cited evidence ranges" in item
        for item in unavailable_result.hard_errors
    )


def test_vpa_k_reference_already_covered_by_range_is_not_duplicated() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    observation = payload["vpa_report"]["observations"][0]
    observation["evidence_ids"] = ["vpa-w005"]
    observation["interpretation"] = (
        "K5 is already covered by the cited sequence comparison."
    )

    result = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert result.ok
    assert result.obj["vpa_report"]["observations"][0]["evidence_ids"] == [
        "vpa-w005"
    ]
    assert result.obj["vpa_report"]["observations"][0][
        "auto_bound_evidence_ids"
    ] == []


def test_vpa_no_entry_candidate_normalizes_timing_relation_without_retry() -> None:
    hypotheses = compile_pa_handoff(VALID_STAGE1, None, make_frame())
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses, relation="challenges")

    result = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert result.ok
    assert result.obj["vpa_report"]["entry_timing_relation"] == "insufficient_data"
    assert any("no PA setup candidate" in item for item in result.warnings)
    assert any(
        item["path"] == "vpa_report.entry_timing_relation"
        and item["after"] == "insufficient_data"
        for item in result.normalizations
    )


def test_vpa_audits_explicit_sequence_direction_prose_contradiction() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    observation = payload["vpa_report"]["observations"][0]
    observation["evidence_ids"] = ["vpa-w005"]
    observation["interpretation"] = (
        "vpa-w005 shows that the two windows have opposite net progress directions."
    )

    conflict = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert conflict.ok
    assert any(
        "net close progress direction" in item
        and "conflicts with program relation" in item
        for item in conflict.warnings
    )

    payload["vpa_report"]["observations"][0]["interpretation"] = (
        "vpa-w005: the two windows have opposite net close progress directions."
    )
    chinese_conflict = validate_vpa_evidence(
        json.dumps(payload, ensure_ascii=False),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    assert chinese_conflict.ok
    assert any(
        "window direction claim" in item
        or "net close progress direction" in item
        for item in chinese_conflict.warnings
    )

    payload["vpa_report"]["observations"][0]["interpretation"] = (
        "vpa-w005 may contain rebound bars, while both windows retain positive "
        "net progress."
    )
    contextual = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    assert contextual.ok


def test_vpa_rejects_exact_window_sign_transition_that_conflicts_with_frozen_facts() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    windows = {item["evidence_id"]: item for item in catalog["evidence"]}
    windows["vpa-w005"]["facts"].update(
        current_progress_direction="negative", prior_progress_direction="positive",
    )
    payload = _vpa_payload(catalog, hypotheses)
    observation = payload["vpa_report"]["observations"][0]
    observation["evidence_ids"] = ["vpa-w005"]
    observation["interpretation"] = (
        "K5-K1相对K10-K6，呈现方向由负转正的窗口差异。"
    )
    result = validate_vpa_evidence(
        json.dumps(payload, ensure_ascii=False), catalog=catalog, pa_hypotheses=hypotheses,
    )
    assert not result.ok
    assert any("window transition claim for vpa-w005" in item for item in result.hard_errors)


def test_vpa_not_applicable_confirmation_has_no_future_trigger() -> None:
    hypotheses = compile_pa_handoff(VALID_STAGE1, None, make_frame())
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    payload["vpa_report"]["next_confirmation"] = {
        "state": "not_applicable",
        "trigger": None,
        "requires_closed_bar": False,
    }

    result = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert result.ok
    assert result.obj["vpa_report"]["next_confirmation"] == {
        "state": "not_applicable",
        "trigger": None,
        "requires_closed_bar": False,
    }

    candidate_hypotheses = _hypotheses()
    candidate_payload = _vpa_payload(catalog, candidate_hypotheses)
    candidate_payload["vpa_report"]["next_confirmation"] = {
        "state": "not_applicable",
        "trigger": None,
        "requires_closed_bar": False,
    }
    with_candidate = validate_vpa_evidence(
        json.dumps(candidate_payload),
        catalog=catalog,
        pa_hypotheses=candidate_hypotheses,
    )
    assert with_candidate.ok
    assert with_candidate.obj["vpa_report"]["next_confirmation"]["state"] == (
        "not_applicable"
    )


def test_vpa_role_underuse_is_a_warning_not_an_analysis_failure() -> None:
    hypotheses = _hypotheses()
    catalog = build_vpa_evidence_catalog(make_frame())
    payload = _vpa_payload(catalog, hypotheses)
    payload["vpa_report"]["observations"][0]["finding_kind"] = "background"

    result = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )

    assert result.ok
    assert "available VPA report has no independent price-volume finding" in result.warnings


def test_joint_keep_baseline_passes_and_win_rate_change_is_rejected() -> None:
    frame = make_frame()
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["alternative_cycle_position"] = "trending_tr"
    hypotheses = compile_pa_handoff(stage1, VALID_STAGE2, make_frame())
    catalog = build_vpa_evidence_catalog(frame)
    vpa_result = validate_vpa_evidence(
        json.dumps(_vpa_payload(catalog, hypotheses)),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    report = VpaEvidenceReport.model_validate(vpa_result.obj["vpa_report"])
    payload = {
        "joint_arbitration": {
            "selected_candidate_id": "pa-setup-1",
            "decision_effect": "keep_brooks_baseline",
            "evidence_used": ["vpa-e001"],
            "reason": "VPA supports but does not alter the PA entry attempt.",
        },
        "decision_patch": {},
    }

    valid = validate_joint_decision(
        json.dumps(payload, ensure_ascii=False),
        baseline_stage2=VALID_STAGE2,
        stage1=stage1,
        pa_hypotheses=hypotheses,
        vpa_report=report,
        catalog=catalog,
        kline_frame=frame,
        skip_next_bar=False,
    )
    assert valid.ok

    payload["decision_patch"]["estimated_win_rate"] = 99
    invalid = validate_joint_decision(
        json.dumps(payload, ensure_ascii=False),
        baseline_stage2=VALID_STAGE2,
        stage1=stage1,
        pa_hypotheses=hypotheses,
        vpa_report=report,
        catalog=catalog,
        kline_frame=frame,
        skip_next_bar=False,
    )
    assert not invalid.ok
    assert any(
        "estimated_win_rate" in item and "Additional properties" in item
        for item in invalid.hard_errors
    )


def test_joint_cannot_create_trade_from_pa_no_order() -> None:
    frame = make_frame()
    stage1 = copy.deepcopy(VALID_STAGE1)
    hypotheses = compile_pa_handoff(stage1, VALID_STAGE2, make_frame())
    catalog = build_vpa_evidence_catalog(frame)
    vpa_result = validate_vpa_evidence(
        json.dumps(_vpa_payload(catalog, hypotheses)),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    report = VpaEvidenceReport.model_validate(vpa_result.obj["vpa_report"])
    baseline = copy.deepcopy(VALID_STAGE2)
    baseline["decision"].update(
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
    baseline["terminal"] = {
        "node_id": "11.2",
        "outcome": "wait",
        "label": "No PA order",
    }
    payload = {
        "joint_arbitration": {
            "selected_candidate_id": "pa-setup-1",
            "decision_effect": "lower_confidence",
            "evidence_used": ["vpa-e001"],
            "reason": "Attempted illegal creation.",
        },
        "decision_patch": {"trade_confidence": 50},
    }

    result = validate_joint_decision(
        json.dumps(payload, ensure_ascii=False),
        baseline_stage2=baseline,
        stage1=stage1,
        pa_hypotheses=hypotheses,
        vpa_report=report,
        catalog=catalog,
        kline_frame=frame,
        skip_next_bar=False,
    )

    assert not result.ok
    assert any("cannot alter a PA no-order baseline" in item for item in result.hard_errors)


def test_joint_timing_effects_materialize_without_rewriting_pa_baseline() -> None:
    frame = make_frame()
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["alternative_cycle_position"] = "trending_tr"
    hypotheses = compile_pa_handoff(stage1, VALID_STAGE2, make_frame())
    catalog = build_vpa_evidence_catalog(frame)
    vpa_result = validate_vpa_evidence(
        json.dumps(_vpa_payload(catalog, hypotheses)),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    report = VpaEvidenceReport.model_validate(vpa_result.obj["vpa_report"])
    baseline = copy.deepcopy(VALID_STAGE2)
    frozen = copy.deepcopy(baseline)
    patches = {
        "require_confirmation": {
            "watch_points": ["Wait for the next closed bar to confirm the PA setup."],
        },
        "prefer_retest": {
            "watch_points": ["Wait for a closed-bar retest of the PA setup boundary."],
        },
        "reject_current_attempt": {
            "invalidation_condition": (
                "The next closed bar fails to recover the PA setup boundary."
            ),
        },
    }

    for effect, decision_patch in patches.items():
        payload = {
            "joint_arbitration": {
                "selected_candidate_id": "pa-setup-1",
                "decision_effect": effect,
                "evidence_used": ["vpa-e001"],
                "reason": "VPA evidence challenges the timing of this PA attempt.",
            },
            "decision_patch": decision_patch,
        }
        result = validate_joint_decision(
            json.dumps(payload),
            baseline_stage2=baseline,
            stage1=stage1,
            pa_hypotheses=hypotheses,
            vpa_report=report,
            catalog=catalog,
            kline_frame=frame,
            skip_next_bar=False,
        )

        assert result.ok, result.hard_errors
        assert result.obj is not None
        materialized = result.obj["stage2_decision"]
        assert materialized["decision"]["order_type"] == "不下单"
        assert materialized["decision"]["estimated_win_rate"] is None
        assert "PA baseline remains the frozen analytical path" in materialized["decision"]["reasoning"]
        assert "联合裁决" in materialized["terminal"]["label"]
        assert materialized["decision_trace"] == baseline["decision_trace"]
        assert result.obj["joint_arbitration"]["decision_effect"] == effect
        assert baseline == frozen


def test_joint_reason_uses_warning_for_prose_and_hard_error_for_k_bounds() -> None:
    frame = make_frame()
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["alternative_cycle_position"] = "trending_tr"
    hypotheses = compile_pa_handoff(stage1, VALID_STAGE2, make_frame())
    catalog = build_vpa_evidence_catalog(frame)
    vpa_result = validate_vpa_evidence(
        json.dumps(_vpa_payload(catalog, hypotheses)),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    report = VpaEvidenceReport.model_validate(vpa_result.obj["vpa_report"])
    cases = (
        (
            "Institutional buyers prove the PA attempt is valid.",
            True,
            "cannot support actor attribution",
        ),
        (
            "K99 proves the PA attempt is valid.",
            False,
            "frozen frame contains K1-K20",
        ),
    )
    for reason, expected_ok, expected_finding in cases:
        payload = {
            "joint_arbitration": {
                "selected_candidate_id": "pa-setup-1",
                "decision_effect": "keep_brooks_baseline",
                "evidence_used": ["vpa-e001"],
                "reason": reason,
            },
            "decision_patch": {},
        }

        result = validate_joint_decision(
            json.dumps(payload),
            baseline_stage2=VALID_STAGE2,
            stage1=stage1,
            pa_hypotheses=hypotheses,
            vpa_report=report,
            catalog=catalog,
            kline_frame=frame,
            skip_next_bar=False,
        )

        assert result.ok is expected_ok
        findings = result.warnings if expected_ok else result.hard_errors
        assert any(expected_finding in item for item in findings)
