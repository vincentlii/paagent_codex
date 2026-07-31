from __future__ import annotations

import copy
import json

from pa_agent.ai.pa_handoff import compile_pa_handoff
from pa_agent.ai.role_contracts import PaHypothesisSet
from pa_agent.ai.role_validator import validate_vpa_evidence
from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog
from pa_agent.orchestrator.two_stage import (
    _pa_joint_diff,
    _role_execution_status,
    _usage_from_call_attempts,
)
from tests.integration.conftest import VALID_STAGE1, VALID_STAGE2, make_frame


def _vpa_payload(catalog: dict, hypotheses: PaHypothesisSet) -> dict:
    return {
        "vpa_report": {
            "summary": "Mixed effort and result near the current PA location.",
            "observations": [
                {
                    "evidence_ids": ["vpa-e004", "vpa-e001"],
                    "finding_kind": "acceptance_rejection",
                    "interpretation_label": "rejection_candidate",
                    "epistemic_status": "candidate",
                    "sequence_claims": [],
                    "interpretation": (
                        "K4 is a rejection candidate; later evidence remains unresolved."
                    ),
                }
            ],
            "hypothesis_assessments": [
                {
                    "hypothesis_id": hypothesis_id,
                    "relation": "neutral",
                    "evidence_ids": ["vpa-e004", "vpa-e001"],
                    "reason": "The evidence is mixed and does not resolve the PA hypothesis.",
                }
                for hypothesis_id in sorted(hypotheses.hypothesis_ids)
            ],
            "background_relation": "neutral",
            "entry_timing_relation": "neutral",
            "next_confirmation": {
                "state": "pending_closed_bar",
                "trigger": "Wait for the next closed bar to test the K4 high.",
            },
        }
    }


def test_usage_is_derived_only_from_response_bearing_call_ledger_entries() -> None:
    calls = [
        {
            "response_hash": "a" * 64,
            "prompt_tokens": 10,
            "cached_prompt_tokens": 2,
            "completion_tokens": 3,
            "total_tokens": 13,
            "latency_ms": 20.4,
        },
        {
            "response_hash": None,
            "prompt_tokens": 0,
            "cached_prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "latency_ms": None,
        },
        {
            "response_hash": "b" * 64,
            "prompt_tokens": 30,
            "cached_prompt_tokens": 4,
            "completion_tokens": 5,
            "total_tokens": 35,
            "latency_ms": 40.6,
        },
    ]

    assert _usage_from_call_attempts(calls) == {
        "prompt_tokens": 40,
        "cached_prompt_tokens": 6,
        "completion_tokens": 8,
        "total_tokens": 48,
        "api_calls": 2,
        "model_latency_ms": 61,
    }


def test_role_status_distinguishes_not_run_not_applicable_repair_and_failure() -> None:
    assert _role_execution_status(None, applicable=False) == "not_applicable"
    assert _role_execution_status(None, applicable=True) == "not_attempted"
    assert _role_execution_status(None, applicable=True, failed=True) == "failed"

    class Result:
        ok = True
        replies = (object(),)
        validation_events = ()

    assert _role_execution_status(Result(), applicable=True) == "validated"
    Result.replies = (object(), object())
    assert _role_execution_status(Result(), applicable=True) == "repaired"


def test_missing_joint_result_has_no_counterfactual_comparison() -> None:
    assert _pa_joint_diff(None, None) is None


def test_pa_candidate_uses_the_validated_structured_stage2_plan() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    assert compile_pa_handoff(stage1, None, make_frame()).setup_candidates == []

    hypotheses = compile_pa_handoff(stage1, VALID_STAGE2, make_frame())
    assert len(hypotheses.setup_candidates) == 1
    candidate = hypotheses.setup_candidates[0]
    assert candidate.trigger == VALID_STAGE2["decision"]["entry_rule"]
    assert candidate.invalidation == VALID_STAGE2["decision"]["invalidation_condition"]

    no_prose_invalidation = copy.deepcopy(VALID_STAGE2)
    no_prose_invalidation["decision"]["invalidation_condition"] = None
    candidate = compile_pa_handoff(
        stage1,
        no_prose_invalidation,
        make_frame(),
    ).setup_candidates[0]
    assert candidate.invalidation is None


def test_interpretive_candidate_label_is_open_but_reversed_time_is_hard_error() -> None:
    frame = make_frame()
    catalog = build_vpa_evidence_catalog(frame)
    hypotheses = compile_pa_handoff(VALID_STAGE1, VALID_STAGE2, frame)
    payload = _vpa_payload(catalog, hypotheses)

    accepted = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    assert accepted.ok
    assert accepted.obj["vpa_report"]["observations"][0][
        "interpretation_label"
    ] == "rejection_candidate"

    payload["vpa_report"]["observations"][0]["sequence_claims"] = [
        {
            "earlier_evidence_id": "vpa-e001",
            "later_evidence_id": "vpa-e004",
            "relation": "response",
        }
    ]
    reversed_result = validate_vpa_evidence(
        json.dumps(payload),
        catalog=catalog,
        pa_hypotheses=hypotheses,
    )
    assert not reversed_result.ok
    assert any("chronology" in item for item in reversed_result.hard_errors)
