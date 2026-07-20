from __future__ import annotations

import copy
import json

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
    return PaHypothesisSet.from_stage1(stage1)


def _vpa_payload(
    catalog: dict,
    hypotheses: PaHypothesisSet,
    *,
    relation: str = "supports",
) -> dict:
    return {
        "vpa_report": {
            "catalog_id": catalog["catalog_id"],
            "data_status": catalog["data_status"],
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
                "requires_closed_bar": True,
            },
        }
    }


def test_pa_hypothesis_set_delegates_stage1_without_new_direction() -> None:
    hypotheses = _hypotheses()

    assert hypotheses.primary.direction == VALID_STAGE1["direction"]
    assert hypotheses.primary.cycle_position == VALID_STAGE1["cycle_position"]
    assert hypotheses.alternative is not None
    assert hypotheses.alternative.cycle_position == "trending_tr"
    assert hypotheses.setup_candidates[0].candidate_id == "pa-setup-1"
    assert hypotheses.setup_candidates[0].timing_state == "eligible"


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


def test_vpa_role_audits_narrative_restatement_without_hard_gating() -> None:
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


def test_vpa_role_rejects_unclosed_confirmation_and_uncited_k_reference() -> None:
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
    assert not uncited_result.ok
    assert any(
        "K2 outside its cited evidence ranges" in item
        for item in uncited_result.hard_errors
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
    hypotheses = PaHypothesisSet.from_stage1(stage1)
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
    hypotheses = PaHypothesisSet.from_stage1(stage1)
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
    hypotheses = PaHypothesisSet.from_stage1(stage1)
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
    hypotheses = PaHypothesisSet.from_stage1(stage1)
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
