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
        "stage2_decision": copy.deepcopy(VALID_STAGE2),
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

    payload["stage2_decision"]["decision"]["estimated_win_rate"] += 1
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
        "cannot change estimated_win_rate" in item
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
            "decision_effect": "keep_brooks_baseline",
            "evidence_used": ["vpa-e001"],
            "reason": "Attempted illegal creation.",
        },
        "stage2_decision": copy.deepcopy(VALID_STAGE2),
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
    assert any("cannot create a trade" in item for item in result.hard_errors)
