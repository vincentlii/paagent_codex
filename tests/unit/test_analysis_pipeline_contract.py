from __future__ import annotations

import pytest
from pydantic import ValidationError

from pa_agent.ai.pa_provider_contracts import (
    schema_for_joint_stage2_draft,
    schema_for_provider_draft,
)
from pa_agent.ai.role_contracts import AnalysisMetric, AnalysisRoleStop
from pa_agent.config.analysis_pipeline import AnalysisPipelineIdentity, pipeline_from_legacy
from pa_agent.config.settings import PromptSettings


@pytest.mark.parametrize("mode, expected", [("off", "pa_only"), ("shadow", "pa_shadow"), ("soft", "pa_shadow")])
def test_legacy_authority_does_not_become_joint(mode, expected):
    assert pipeline_from_legacy(PromptSettings(vpa_mode=mode)).pipeline == expected


def test_legacy_boolean_and_research_identity_are_preserved():
    assert pipeline_from_legacy(PromptSettings(vpa_enabled=True)).pipeline == "pa_shadow"
    assert pipeline_from_legacy(None).pipeline == "pa_only"
    with pytest.raises(ValueError, match="legacy research"):
        pipeline_from_legacy(PromptSettings(vpa_mode="soft"), research=True)


def test_joint_uses_vpa_before_a_single_final_stage2():
    identity = AnalysisPipelineIdentity(pipeline="pa_vpa_joint")
    assert identity.roles == ("stage1", "vpa_evidence", "stage2")
    assert AnalysisPipelineIdentity.model_validate_json(identity.model_dump_json()) == identity
    with pytest.raises(ValidationError):
        AnalysisPipelineIdentity(pipeline="soft")


@pytest.mark.parametrize("status", ["incomplete", "review_required"])
def test_stopped_role_cannot_carry_an_order(status):
    args = dict(status=status, role="stage2", reason="Frozen evidence unavailable")
    assert AnalysisRoleStop(**args).status == status
    with pytest.raises(ValidationError):
        AnalysisRoleStop(**args, decision={"order_type": "限价单"})


@pytest.mark.parametrize("status,value", [("unknown", 0), ("actual", None), ("estimated", None), ("actual", float("nan")), ("actual", float("inf")), ("actual", -1)])
def test_metrics_do_not_fabricate_zero_or_accept_nonfinite_values(status, value):
    with pytest.raises(ValidationError):
        AnalysisMetric(status=status, value=value, unit="tokens", source="provider_usage")


def test_actual_zero_and_missing_are_distinct():
    assert AnalysisMetric(status="actual", value=0, unit="tokens", source="provider_usage").value == 0
    assert AnalysisMetric(status="unknown", unit="tokens", source="provider_usage_absent").value is None


def test_joint_schema_reuses_order_and_pa_trace_without_mutating_legacy():
    import jsonschema

    before = schema_for_provider_draft("stage2")
    joint = schema_for_joint_stage2_draft()
    jsonschema.Draft7Validator.check_schema(joint)
    for key in ("decision", "decision_trace", "bar_analysis"):
        assert joint["properties"][key] == before["properties"][key]
    assert "joint_analysis" not in before["properties"]
    assert "joint_analysis" in joint["required"]
    assert joint["properties"]["decision_trace"]["items"]["properties"]["evidence_refs"]["items"]["pattern"] == r"^PA\."
    joint["properties"]["decision"].clear()
    assert schema_for_provider_draft("stage2") == before
