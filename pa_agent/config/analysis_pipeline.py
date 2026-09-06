"""Versioned analysis authority, independent of the selected model backend.

The joint contract is not a runtime feature switch. Callers must explicitly
wire its staged execution before exposing it in settings or a dashboard.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from pa_agent.config.vpa import resolve_production_vpa_mode, resolve_vpa_mode

AnalysisPipeline = Literal["pa_only", "pa_shadow", "pa_vpa_joint"]


class AnalysisPipelineIdentity(BaseModel):
    """Frozen authority identity; never inferred from a backend or model name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal["analysis_pipeline_v1"] = "analysis_pipeline_v1"
    pipeline: AnalysisPipeline

    @property
    def roles(self) -> tuple[str, ...]:
        """Nominal order; the validated Stage 1 Gate can stop downstream roles."""
        return {
            "pa_only": ("stage1", "stage2"),
            "pa_shadow": ("stage1", "stage2", "vpa_evidence"),
            "pa_vpa_joint": ("stage1", "vpa_evidence", "stage2"),
        }[self.pipeline]


def pipeline_from_legacy(
    prompt_settings: object | None, *, research: bool = False,
) -> AnalysisPipelineIdentity:
    """Adapt old production config, never relabel the old research treatment."""
    if research and resolve_vpa_mode(prompt_settings) == "soft":
        raise ValueError("legacy research soft is not analysis_pipeline_v1 joint")
    mode = resolve_production_vpa_mode(prompt_settings)
    return AnalysisPipelineIdentity(pipeline="pa_only" if mode == "off" else "pa_shadow")
