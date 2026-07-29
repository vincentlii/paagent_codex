from __future__ import annotations

from types import SimpleNamespace

from pa_agent.gui.stage2_payload import (
    merge_stage2_for_panels,
    sidecar_for_stage2_record,
)


def test_vpa_sidecar_is_exposed_to_decision_panel_payload() -> None:
    payload = merge_stage2_for_panels(
        {
            "decision": {"order_type": "不下单"},
            "vpa_sidecar": {
                "stage1_context": {"summary": "volume context"},
                "stage2_impact": {"decision_effect": "keep_brooks_baseline"},
            },
        }
    )

    assert payload["vpa_context"]["summary"] == "volume context"
    assert payload["vpa_impact"]["decision_effect"] == "keep_brooks_baseline"


def test_top_level_vpa_sidecar_is_not_required_inside_stage2_decision() -> None:
    payload = merge_stage2_for_panels(
        {"decision": {"order_type": "不下单"}},
        vpa_sidecar={
            "stage1_context": {"summary": "volume context"},
            "stage2_impact": {"decision_effect": "keep_brooks_baseline"},
        },
    )

    assert payload["vpa_context"]["summary"] == "volume context"
    assert payload["vpa_impact"]["decision_effect"] == "keep_brooks_baseline"


def test_finished_callback_projects_matching_record_for_the_ui() -> None:
    stage2 = {"decision": {"order_type": "不下单"}}
    record = SimpleNamespace(
        stage2_decision=stage2,
        vpa_catalog={"snapshot_hash": "a" * 64},
        vpa_evidence={
            "summary": "live VPA summary",
            "background_relation": "supports",
            "entry_timing_relation": "neutral",
            "observations": [{"evidence_ids": ["VPA.K1", "VPA.SEQ.K3_K1"]}],
            "hypothesis_assessments": [
                {"evidence_ids": ["VPA.K1", "VPA.WAVE.1"]}
            ],
        },
        joint_decision=None,
        pa_baseline_stage2=stage2,
        joint_stage2=None,
        run_outcome={"status": "completed", "requested_vpa_mode": "shadow"},
        role_runtime={},
        role_statuses={"vpa": "validated", "joint": "not_applicable"},
    )

    view = sidecar_for_stage2_record(record, stage2)
    assert view["stage1_context"]["summary"] == "live VPA summary"
    assert view["stage2_impact"] == {
        "background_relation": "supports",
        "entry_timing_relation": "neutral",
        "evidence_used": ["VPA.K1", "VPA.SEQ.K3_K1", "VPA.WAVE.1"],
        "decision_effect": "shadow_only_no_effect",
        "reason": (
            "VPA evidence was recorded for evaluation only; "
            "the official PA decision was not changed."
        ),
    }
    assert sidecar_for_stage2_record(record, {"decision": {"order_type": "限价单"}}) is None
