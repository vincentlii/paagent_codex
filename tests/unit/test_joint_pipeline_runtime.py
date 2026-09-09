"""Offline acceptance of the explicit VPA-before-Stage2 pipeline."""

import copy
import json
import pytest

from pa_agent.ai.json_validator import JsonValidator, ValidationError
from pa_agent.ai.pa_provider_contracts import schema_for_joint_stage2_draft
from tests.integration.conftest import VALID_STAGE2, make_frame
from tests.unit.test_pa_provider_contracts import _trace_draft


def joint_draft():
    draft = copy.deepcopy(VALID_STAGE2)
    draft.update(protocol_version="pa_vpa_stage2_draft_v1", decision_intent="trade")
    for key in ("terminal", "next_cycle_prediction"):
        draft.pop(key)
    for key in ("diagnosis_confidence", "diagnosis_confidence_reasoning"):
        draft["decision"].pop(key)
    draft["decision_trace"] = [
        item for item in _trace_draft(draft["decision_trace"]) if item["node_id"] != "10.3"
    ]
    draft["joint_analysis"] = {
        key: "Frozen evidence reasoning"
        for key in (
            "support",
            "challenge",
            "selection_vs_wait",
            "entry_reachability",
            "target_barriers",
            "invalidation",
        )
    }
    draft["joint_analysis"]["vpa_evidence_refs"] = []
    return draft


def test_joint_validator_requires_accepted_upstream_and_keeps_legacy_schema():
    validator = JsonValidator()
    assert (
        validator.schema_for("stage2")["properties"]["protocol_version"]["const"] == "pa_draft_v2"
    )
    result = validator.validate_joint_stage2(
        json.dumps(joint_draft()),
        kline_frame=make_frame(),
        stage1_json={},
        vpa_report=None,
        vpa_catalog={},
    )
    assert isinstance(result, ValidationError)
    assert result.code == "joint_upstream_invalid"
    assert not result.contract_issues  # Identity is not model-repairable.
    assert (
        schema_for_joint_stage2_draft()["properties"]["protocol_version"]["const"]
        == "pa_vpa_stage2_draft_v1"
    )


def run_joint(
    *,
    vpa_failure=False,
    stop_role=None,
    stage1_override=None,
    stage2_drafts=None,
    retry=False,
    codex=False,
    frame=None,
):
    from unittest.mock import MagicMock
    from pa_agent.config.settings import Settings, ValidationSettings
    from pa_agent.config.analysis_pipeline import AnalysisPipelineIdentity
    from pa_agent.ai.prompt_assembler import PromptAssembler
    from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
    from pa_agent.util.threading import CancelToken
    from tests.integration.conftest import VALID_STAGE1
    from tests.unit.test_pa_vpa_joint_orchestrator import (
        _vpa_report,
        make_reply,
        PROMPT_DIR,
        route_strategy_files,
    )

    settings = Settings(
        analysis_pipeline=AnalysisPipelineIdentity(pipeline="pa_vpa_joint"),
        validation=ValidationSettings(retry_enabled=retry),
    )
    client = MagicMock()
    vpa_client = MagicMock()
    calls = []
    from pa_agent.ai.role_contracts import AnalysisRoleStop

    stop = AnalysisRoleStop(
        role=stop_role or "stage2", status="review_required", reason="Frozen upstream needs review"
    ).model_dump(mode="json")
    replies = [
        make_reply(
            stop if stop_role == "stage1" else copy.deepcopy(stage1_override or VALID_STAGE1)
        )
    ]
    replies.extend(
        make_reply(draft)
        for draft in (stage2_drafts or [stop if stop_role == "stage2" else joint_draft()])
    )

    def pa_call(*args, **kwargs):
        calls.append("stage1" if not calls else "stage2")
        return replies.pop(0)

    def vpa_call(*args, **kwargs):
        calls.append("vpa_evidence")
        return make_reply(
            stop if stop_role == "vpa_evidence" else {} if vpa_failure else _vpa_report()
        )

    client.stream_chat.side_effect = pa_call
    client.clone_with_provider.return_value = vpa_client
    vpa_client.stream_chat.side_effect = vpa_call
    writer = MagicMock()
    writer.save_full.return_value.ok = True
    writer.save_full.return_value.audit_ok = True
    writer.save_partial.return_value.ok = True
    writer.save_partial.return_value.audit_ok = True
    backend = None
    if codex:
        from pa_agent.ai.analysis_role_backend import AnalysisRoleResult

        backend = MagicMock()
        backend.backend_id = "codex_agent"
        backend.identity.return_value = {"backend": "codex_agent"}
        role_backend = MagicMock()
        backend.bind_role.return_value = role_backend

        def check_agent_stop(role):
            if stop_role == role:
                from pa_agent.ai.provider_failures import ModelCallFailure, ProviderFailure
                from pa_agent.agent_runtime.codex_worker import CodexWorkerStopped

                calls.append(role)
                raise ModelCallFailure(
                    ProviderFailure(kind="invalid_request", message="role stopped"),
                    original=CodexWorkerStopped(AnalysisRoleStop.model_validate(stop)),
                )

        def agent_call(request, **kwargs):
            check_agent_stop(request.role)
            assert request.snapshot_hash
            if request.role == "stage2":
                assert (
                    request.agent_context["role_protocol"]["contract_version"]
                    == "joint_stage2_agent_protocol_v1"
                )
                assert request.agent_context["pa_hypotheses"]["setup_candidates"] == []
                assert request.agent_context["vpa_evidence"]
            return AnalysisRoleResult(reply=pa_call(), backend_id="codex_agent", runtime={})

        backend.run.side_effect = agent_call

        def agent_vpa_call(request, **kwargs):
            check_agent_stop(request.role)
            return AnalysisRoleResult(reply=vpa_call(), backend_id="codex_agent", runtime={})

        role_backend.run.side_effect = agent_vpa_call
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=PromptAssembler(PROMPT_DIR, prompt_settings=settings.prompt),
        router=route_strategy_files,
        validator=JsonValidator(),
        pending_writer=writer,
        exp_reader=MagicMock(),
        settings=settings,
        analysis_backend=backend,
    )
    record = orchestrator.submit(frame or make_frame(), CancelToken(), lambda event: None)
    return record, calls, writer


def test_joint_pipeline_calls_vpa_before_only_stage2():
    record, calls, writer = run_joint()
    assert record.exception is None, record.exception
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    assert record.pa_hypotheses["setup_candidates"] == []
    assert record.pa_baseline_stage2 is None
    assert record.joint_decision is None
    assert record.run_outcome["official_path"] == "joint"
    assert (
        record.stage2_decision["analysis_basis"]["win_rate_provenance"]
        == "llm_joint_subjective_prior"
    )
    writer.save_full.assert_called_once()


def test_joint_vpa_failure_stops_without_a_no_trade():
    record, calls, writer = run_joint(vpa_failure=True)
    assert calls == ["stage1", "vpa_evidence"]
    assert record.role_stop.status == "incomplete"
    assert record.run_outcome["official_path"] == "none"
    assert record.stage2_decision is None
    writer.save_full.assert_not_called()
    writer.save_partial.assert_called_once()


@pytest.mark.parametrize("role", ["stage1", "vpa_evidence", "stage2"])
@pytest.mark.parametrize("codex", [False, True])
def test_review_stop_is_persisted_without_rerun(role, codex):
    record, calls, writer = run_joint(stop_role=role, codex=codex)
    assert record.role_stop.status == "review_required"
    assert record.role_stop.role == role
    assert (
        calls
        == ["stage1", "vpa_evidence", "stage2"][
            : ["stage1", "vpa_evidence", "stage2"].index(role) + 1
        ]
    )
    assert record.stage2_decision is None
    writer.save_partial.assert_called_once()
    writer.save_full.assert_not_called()


def test_joint_gate_wait_does_not_call_vpa_or_stage2():
    from tests.integration.conftest import VALID_STAGE1

    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1.update(
        cycle_position="unknown",
        gate_result="wait",
        gate_trace=[
            {
                "node_id": "1.2",
                "question": "是否能识别市场周期？",
                "answer": "否",
                "action": "等待",
                "reason": "多个合理周期假设。",
                "bar_range": "K5-K1",
            }
        ],
    )
    record, calls, writer = run_joint(stage1_override=stage1)
    assert calls == ["stage1"]
    assert record.role_stop is None
    assert record.stage2_decision["decision"]["order_type"] == "不下单"
    assert record.vpa_evidence is None
    writer.save_full.assert_called_once()


def test_joint_reference_repair_uses_joint_contract_without_second_pa_decision():
    bad = joint_draft()
    bad["joint_analysis"]["vpa_evidence_refs"] = ["UNPUBLISHED.EVIDENCE"]
    record, calls, writer = run_joint(stage2_drafts=[bad, joint_draft()], retry=True)
    assert record.exception is None, record.exception
    assert calls == ["stage1", "vpa_evidence", "stage2", "stage2"]
    feedbacks = [entry.get("retry_feedback") or "" for entry in record.validation_audit]
    assert any("pa_vpa_stage2_draft_v1" in text for text in feedbacks)
    assert record.pa_baseline_stage2 is None
    writer.save_full.assert_called_once()


def test_codex_joint_uses_independent_vpa_backend_and_joint_stage2_protocol():
    record, calls, writer = run_joint(codex=True)
    assert record.exception is None, record.exception
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    assert record.run_outcome["official_path"] == "joint"
    writer.save_full.assert_called_once()


def test_explicit_shadow_keeps_legacy_order_and_does_not_recurse():
    from tests.unit.test_pa_vpa_joint_orchestrator import _orchestrator, _vpa_report, make_reply
    from pa_agent.config.analysis_pipeline import AnalysisPipelineIdentity
    from pa_agent.util.threading import CancelToken

    orchestrator, _, writer = _orchestrator(
        "shadow", vpa_replies=[make_reply(_vpa_report())], joint_replies=[]
    )
    orchestrator._settings.analysis_pipeline = AnalysisPipelineIdentity(pipeline="pa_shadow")
    record = orchestrator.submit(make_frame(), CancelToken(), lambda event: None)
    assert record.exception is None
    assert record.analysis_pipeline.pipeline == "pa_shadow"
    assert record.run_outcome["official_path"] == "pa_only"
    assert record.stage2_decision
    assert record.vpa_evidence
    writer.save_full.assert_called_once()


def test_settings_pipeline_is_explicit_and_roundtrips():
    from pa_agent.config.settings import Settings

    assert Settings().analysis_pipeline is None
    settings = Settings(analysis_pipeline={"pipeline": "pa_vpa_joint"})
    assert (
        Settings.model_validate_json(settings.model_dump_json()).analysis_pipeline
        == settings.analysis_pipeline
    )


def test_direct_vpa_stop_transport_preserves_root_definitions():
    from unittest.mock import MagicMock
    import jsonschema
    from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
    from pa_agent.config.settings import Settings
    from pa_agent.ai.role_contracts import VPA_EVIDENCE_SCHEMA
    from tests.unit.test_pa_vpa_joint_orchestrator import _vpa_report

    owner = MagicMock()
    owner._settings = Settings(analysis_pipeline={"pipeline": "pa_vpa_joint"})
    owner._analysis_backend.backend_id = "direct"
    schema = TwoStageOrchestrator._joint_direct_contract(
        owner, "vpa_evidence", VPA_EVIDENCE_SCHEMA, []
    )
    assert "$defs" in schema
    # Resolves every nested reference, regardless of the fixture's canonical-only fields.
    list(jsonschema.Draft7Validator(schema).iter_errors(_vpa_report()))
    assert "$defs" in VPA_EVIDENCE_SCHEMA  # No global schema mutation.


def test_joint_bad_reference_cannot_become_a_formal_decision():
    bad = joint_draft()
    bad["joint_analysis"]["vpa_evidence_refs"] = ["UNPUBLISHED.EVIDENCE"]
    record, calls, writer = run_joint(stage2_drafts=[bad])
    assert record.role_stop.status == "incomplete"
    assert record.stage2_decision is None
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    writer.save_full.assert_not_called()


def test_joint_stage2_contract_is_single_and_preserves_mature_rules():
    record, _, _ = run_joint()
    task = next(
        message["content"]
        for message in record.stage2_messages
        if "## 阶段二任务" in message.get("content", "")
    )
    from pa_agent.ai.pa_provider_contracts import render_provider_draft_contract, provider_draft_schema_digest

    assert render_provider_draft_contract("stage2", joint_stage2=True) in task
    assert render_provider_draft_contract("stage2") not in task
    assert "9.0P" in task
    assert "结构" in task
    assert record.strategy_files_used
    assert record.role_runtime["provider_contract_digests"]["stage2"] == provider_draft_schema_digest("stage2", joint_stage2=True)


def test_joint_history_cannot_leak_vpa_through_ordinary_decision_fields():
    from pa_agent.ai.vpa_sanitizer import price_only_history

    prior = {
        "decision": {"reasoning": "VPA joint judgment"},
        "analysis_basis": {"protocol_version": "pa_vpa_stage2_draft_v1"},
    }
    assert price_only_history(prior) == {}
    assert prior["decision"]["reasoning"] == "VPA joint judgment"
    assert price_only_history({"direction": "bull"}) == {"direction": "bull"}


def test_joint_preflight_failure_is_incomplete_before_any_model_call():
    from dataclasses import replace

    frame = make_frame()
    record, calls, writer = run_joint(frame=replace(frame, bars=frame.bars[:19]))
    assert calls == []
    assert record.role_stop.status == "incomplete"
    assert record.role_stop.role == "stage1"
    assert record.exception["stage"] == "preflight"
    assert record.stage2_decision is None
    writer.save_partial.assert_called_once()


def test_joint_availability_fallback_restarts_same_frozen_protocol():
    from unittest.mock import MagicMock
    from pa_agent.ai.codex_analysis_backend import FullChainFallbackRequired
    from pa_agent.ai.provider_failures import ProviderFailure
    from pa_agent.config.analysis_pipeline import AnalysisPipelineIdentity
    from pa_agent.orchestrator.backend_fallback import FullChainFallbackOrchestrator

    primary, cold = MagicMock(), MagicMock()
    primary.submit.side_effect = FullChainFallbackRequired(
        ProviderFailure(kind="unavailable", message="Unavailable")
    )
    cold_record, calls, _ = run_joint()
    cold.submit.return_value = cold_record
    pipeline = AnalysisPipelineIdentity(pipeline="pa_vpa_joint")
    wrapper = FullChainFallbackOrchestrator(
        primary=primary, cold_fallback=cold, pipeline_identity=pipeline
    )
    frame = make_frame()
    frozen_catalog = {"opaque": "same frozen input"}
    result = wrapper.submit(frame, object(), lambda event: None, frozen_vpa_catalog=frozen_catalog)
    assert cold.submit.call_args.args[0] is frame
    assert cold.submit.call_args.kwargs["frozen_vpa_catalog"] is frozen_catalog
    assert result.analysis_pipeline == pipeline
    assert result.role_runtime["backend_fallback"]["restart_scope"] == "full_analysis_pipeline"
    assert calls == ["stage1", "vpa_evidence", "stage2"]
