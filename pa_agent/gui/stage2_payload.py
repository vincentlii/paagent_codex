"""Helpers to merge stage-2 JSON for UI panels."""
from __future__ import annotations

from typing import Any

from pa_agent.records.projection import gui_vpa_view


def sidecar_for_stage2_record(
    record: Any,
    stage2: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return a sidecar only when it belongs to the displayed Stage-2 result."""
    if record is None or not isinstance(stage2, dict):
        return None
    if getattr(record, "stage2_decision", None) != stage2:
        return None
    return gui_vpa_view(record)


def merge_stage2_for_panels(
    s2_full: dict[str, Any] | None,
    *,
    vpa_sidecar: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge top-level prediction fields into the inner decision dict for panels."""
    if not isinstance(s2_full, dict):
        return {}
    inner = s2_full.get("decision")
    payload: dict[str, Any] = dict(inner) if isinstance(inner, dict) else {}
    for key in ("next_bar_prediction", "next_cycle_prediction", "vpa_impact"):
        if key in s2_full:
            payload[key] = s2_full[key]
    sidecar = vpa_sidecar if isinstance(vpa_sidecar, dict) else s2_full.get("vpa_sidecar")
    if isinstance(sidecar, dict):
        payload["vpa_sidecar"] = sidecar
        if isinstance(sidecar.get("stage1_context"), dict):
            payload["vpa_context"] = sidecar["stage1_context"]
        if isinstance(sidecar.get("stage2_impact"), dict):
            payload["vpa_impact"] = sidecar["stage2_impact"]
    return payload


def prepare_stage2_for_ui(
    s2_full: dict[str, Any] | None,
    *,
    stage1_json: dict[str, Any] | None = None,
    skip_next_bar: bool = False,
    vpa_sidecar: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prepare validated fields for display without inventing missing analysis."""
    if not isinstance(s2_full, dict):
        return {}
    del stage1_json
    if not skip_next_bar:
        return merge_stage2_for_panels(s2_full, vpa_sidecar=vpa_sidecar)
    without_next_bar = dict(s2_full)
    without_next_bar.pop("next_bar_prediction", None)
    return merge_stage2_for_panels(without_next_bar, vpa_sidecar=vpa_sidecar)
