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


def test_finished_callback_can_recover_matching_record_sidecar() -> None:
    stage2 = {"decision": {"order_type": "不下单"}}
    sidecar = {"stage1_context": {"summary": "live VPA summary"}}
    record = SimpleNamespace(stage2_decision=stage2, vpa_sidecar=sidecar)

    assert sidecar_for_stage2_record(record, stage2) is sidecar
    assert sidecar_for_stage2_record(record, {"decision": {"order_type": "限价单"}}) is None
