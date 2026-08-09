from __future__ import annotations

import copy
from unittest.mock import MagicMock

import pytest

from pa_agent.ai.prompt_assembler import PromptAssembler, VpaPromptBuildError
from pa_agent.ai.router import route_strategy_files
from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog
from pa_agent.config.paths import PROMPT_DIR
from pa_agent.config.settings import (
    PromptSettings,
    ReasoningProfileOverride,
    Settings,
    ValidationSettings,
)
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken, OrchestratorEvent
from tests.integration.conftest import (
    VALID_STAGE1,
    VALID_STAGE2,
    make_frame,
    make_reply,
)


def _vpa_report(*, relation: str = "challenges") -> dict:
    catalog = build_vpa_evidence_catalog(make_frame())
    return {
        "vpa_report": {
            "catalog_id": catalog["catalog_id"],
            "data_status": catalog["data_status"],
            "summary": "Relative volume adds independent timing evidence.",
            "observations": [
                {
                    "evidence_ids": ["vpa-e001"],
                    "finding_kind": "entry_timing",
                    "interpretation": (
                        "vpa-e001 challenges immediate timing while the PA "
                        "framework remains unchanged."
                    ),
                }
            ],
            "hypothesis_assessments": [
                {
                    "hypothesis_id": "pa-primary",
                    "relation": relation,
                    "evidence_ids": ["vpa-e001"],
                    "reason": "vpa-e001 is relevant to the primary PA hypothesis.",
                }
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


def _joint_lower_confidence() -> dict:
    return {
        "joint_arbitration": {
            "selected_candidate_id": "pa-setup-1",
            "decision_effect": "lower_confidence",
            "evidence_used": ["vpa-e001"],
            "reason": "VPA challenges immediate timing for the PA candidate.",
        },
        "decision_patch": {
            "trade_confidence": 60,
            "trade_confidence_reasoning": (
                "VPA timing evidence lowers confidence without changing PA."
            ),
        },
    }


def _orchestrator(
    mode: str,
    *,
    vpa_replies: list,
    joint_replies: list,
    baseline_replies: list | None = None,
    vpa_side_effect=None,
    allow_research_joint: bool | None = None,
    reasoning_profiles: dict[str, ReasoningProfileOverride] | None = None,
) -> tuple[TwoStageOrchestrator, MagicMock, MagicMock]:
    settings = Settings(
        prompt=PromptSettings(vpa_mode=mode),
        validation=ValidationSettings(retry_enabled=False),
    )
    for role, profile in (reasoning_profiles or {}).items():
        setattr(settings.provider.reasoning_profiles, role, profile)
    baseline_client = MagicMock()
    baseline_client.stream_chat.side_effect = baseline_replies or [
        make_reply(copy.deepcopy(VALID_STAGE1)),
        make_reply(copy.deepcopy(VALID_STAGE2)),
    ]
    vpa_client = MagicMock()
    vpa_client.stream_chat.side_effect = (
        vpa_side_effect if vpa_side_effect is not None else vpa_replies
    )
    joint_client = MagicMock()
    joint_client.stream_chat.side_effect = joint_replies
    client = MagicMock()
    client.clone_with_provider.side_effect = [
        baseline_client,
        vpa_client,
        joint_client,
    ]
    writer = MagicMock()
    writer.save_full.return_value.ok = True
    writer.save_full.return_value.audit_ok = True
    writer.save_partial.return_value.ok = True
    writer.save_partial.return_value.audit_ok = True
    exp_reader = MagicMock()
    exp_reader.read_for_stage2.return_value = []
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=PromptAssembler(
            PROMPT_DIR,
            prompt_settings=settings.prompt,
        ),
        router=route_strategy_files,
        validator=MagicMock(),
        pending_writer=writer,
        exp_reader=exp_reader,
        settings=settings,
        allow_research_joint=(
            mode == "soft"
            if allow_research_joint is None
            else allow_research_joint
        ),
    )
    return orchestrator, client, writer


@pytest.mark.parametrize(
    ("mode", "official_path", "expected_confidence"),
    [
        ("shadow", "pa_only", 70),
        ("soft", "joint", 60),
    ],
)
def test_shadow_and_soft_have_distinct_official_paths(
    mode: str,
    official_path: str,
    expected_confidence: int,
) -> None:
    orchestrator, client, writer = _orchestrator(
        mode,
        vpa_replies=[make_reply(_vpa_report())],
        joint_replies=[make_reply(_joint_lower_confidence())],
    )
    events: list[OrchestratorEvent] = []

    record = orchestrator.submit(make_frame(), CancelToken(), events.append)

    assert record.exception is None
    assert record.run_outcome["status"] == "completed"
    assert record.run_outcome["official_path"] == official_path
    assert record.run_outcome["effective_vpa_mode"] == mode
    assert record.stage2_decision["decision"]["trade_confidence"] == expected_confidence
    assert record.stage2_decision["decision"]["estimated_win_rate"] == 55
    assert record.pa_hypotheses["primary"]["hypothesis_id"] == "pa-primary"
    assert record.vpa_evidence["catalog_id"].startswith("vpa-catalog-")
    if mode == "soft":
        assert record.joint_decision["joint_arbitration"]["decision_effect"] == (
            "lower_confidence"
        )
    else:
        assert record.joint_decision is None
        assert record.pa_baseline_stage2 is None
    assert record.role_runtime["same_frozen_input"] is True
    assert record.role_runtime["official_path"] == official_path
    assert client.clone_with_provider.call_count == (3 if mode == "soft" else 2)
    expected_calls = [
        ("pa", "stage1", "validated"),
        ("pa", "stage2", "validated"),
        ("vpa_evidence", "vpa_evidence", "validated"),
    ]
    if mode == "soft":
        expected_calls.append(("joint_arbiter", "joint_arbiter", "validated"))
    assert [
        (item["arm"], item["stage"], item["status"])
        for item in record.call_attempts
    ] == expected_calls
    writer.save_full.assert_called_once()
    assert events[-1] == OrchestratorEvent.RecordSaved


def test_each_role_uses_and_records_its_own_reasoning_profile() -> None:
    orchestrator, _client, _writer = _orchestrator(
        "soft",
        vpa_replies=[make_reply(_vpa_report())],
        joint_replies=[make_reply(_joint_lower_confidence())],
        reasoning_profiles={
            "stage1": ReasoningProfileOverride(thinking=True, reasoning_effort="high"),
            "stage2": ReasoningProfileOverride(thinking=False),
            "vpa_evidence": ReasoningProfileOverride(
                thinking=True,
                reasoning_effort="max",
            ),
            "joint_arbiter": ReasoningProfileOverride(thinking=False),
        },
    )

    record = orchestrator.submit(make_frame(), CancelToken(), lambda _event: None)

    controls = {
        item["stage"]: (item["thinking"], item["reasoning_effort"])
        for item in record.call_attempts
    }
    assert controls == {
        "stage1": (True, "high"),
        "stage2": (False, None),
        "vpa_evidence": (True, "max"),
        "joint_arbiter": (False, None),
    }
    assert record.role_runtime["reasoning_profiles"]["stage2"] == {
        "role": "stage2",
        "thinking": False,
        "reasoning_effort": "high",
        "source": "role",
    }


def test_vpa_failure_degrades_to_pa_only_without_failing_analysis() -> None:
    orchestrator, _client, writer = _orchestrator(
        "soft",
        vpa_replies=[
            make_reply({"vpa_report": {"catalog_id": "wrong"}}),
            make_reply({"vpa_report": {"catalog_id": "still-wrong"}}),
        ],
        joint_replies=[],
    )

    record = orchestrator.submit(
        make_frame(),
        CancelToken(),
        lambda _event: None,
    )

    assert record.exception is None
    assert record.run_outcome["status"] == "completed_with_warnings"
    assert record.run_outcome["official_path"] == "pa_only"
    assert record.run_outcome["degradation_stage"] == "vpa_evidence"
    assert record.stage2_decision["decision"]["trade_confidence"] == 70
    assert record.vpa_evidence is None
    assert record.joint_decision is None
    assert record.run_outcome["status"] == "completed_with_warnings"
    assert record.vpa_sidecar["comparison"] is None
    assert record.role_statuses["vpa"] == "failed"
    assert record.role_statuses["joint"] == "not_attempted"
    assert record.usage_total == {
        "prompt_tokens": 400,
        "cached_prompt_tokens": 0,
        "completion_tokens": 200,
        "total_tokens": 600,
        "api_calls": 4,
        "model_latency_ms": 4,
    }
    assert writer.save_full.call_count == 1


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    (
        (
            VpaPromptBuildError(
                "vpa_measurement_render_failed",
                "synthetic local render failure",
            ),
            "vpa_measurement_render_failed",
        ),
        (RuntimeError("synthetic unexpected prompt failure"), "vpa_prompt_build_failed"),
    ),
)
def test_local_vpa_prompt_failure_skips_provider_call_and_preserves_pa(
    monkeypatch,
    failure: Exception,
    expected_code: str,
) -> None:
    def fail_render(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(PromptAssembler, "_render_vpa_measurements_block", fail_render)
    orchestrator, _client, writer = _orchestrator(
        "shadow",
        vpa_replies=[make_reply(_vpa_report())],
        joint_replies=[],
    )

    record = orchestrator.submit(make_frame(), CancelToken(), lambda _event: None)

    assert record.run_outcome["status"] == "completed_with_warnings"
    assert record.run_outcome["official_path"] == "pa_only"
    assert record.stage2_decision["decision"]["trade_confidence"] == 70
    assert len(record.call_attempts) == 2
    assert all(item["arm"] == "pa" for item in record.call_attempts)
    assert any(
        event.get("code") == expected_code
        and event.get("category") == "local_prompt_build"
        for event in record.validation_audit
    )
    writer.save_full.assert_called_once()


def test_production_soft_is_explicitly_constrained_to_shadow() -> None:
    orchestrator, client, _writer = _orchestrator(
        "soft",
        vpa_replies=[make_reply(_vpa_report())],
        joint_replies=[],
        allow_research_joint=False,
    )

    record = orchestrator.submit(make_frame(), CancelToken(), lambda _event: None)

    assert record.run_outcome["status"] == "completed_with_warnings"
    assert record.run_outcome["official_path"] == "pa_only"
    assert record.run_outcome["requested_vpa_mode"] == "soft"
    assert record.run_outcome["effective_vpa_mode"] == "shadow"
    assert record.vpa_sidecar["requested_mode"] == "soft"
    assert record.vpa_sidecar["effective_mode"] == "shadow"
    assert record.vpa_sidecar["mode"] == "shadow"
    assert record.joint_decision is None
    assert record.pa_baseline_stage2 is None
    assert record.role_runtime["design"] == "pa_vpa_shadow_v2"
    assert client.clone_with_provider.call_count == 2
    assert any(
        event.get("code") == "soft_not_promoted"
        for event in record.validation_audit
    )


def test_production_shadow_vpa_failure_preserves_official_pa_success() -> None:
    orchestrator, client, writer = _orchestrator(
        "shadow",
        vpa_replies=[
            make_reply({"vpa_report": {"catalog_id": "wrong"}}),
            make_reply({"vpa_report": {"catalog_id": "still-wrong"}}),
        ],
        joint_replies=[],
    )

    record = orchestrator.submit(make_frame(), CancelToken(), lambda _event: None)

    assert record.exception is None
    assert record.run_outcome["status"] == "completed_with_warnings"
    assert record.run_outcome["official_path"] == "pa_only"
    assert record.run_outcome["effective_vpa_mode"] == "shadow"
    assert record.run_outcome["degradation_stage"] == "vpa_evidence"
    assert record.stage2_decision["decision"]["trade_confidence"] == 70
    assert record.vpa_evidence is None
    assert record.joint_decision is None
    assert record.role_statuses == {"vpa": "failed"}
    assert client.clone_with_provider.call_count == 2
    assert writer.save_full.call_count == 1


def test_joint_failure_degrades_to_pa_only_without_losing_vpa_report() -> None:
    invalid_joint = _joint_lower_confidence()
    invalid_joint["decision_patch"]["estimated_win_rate"] = 99
    orchestrator, _client, writer = _orchestrator(
        "soft",
        vpa_replies=[make_reply(_vpa_report())],
        joint_replies=[
            make_reply(invalid_joint),
            make_reply(invalid_joint),
        ],
    )

    record = orchestrator.submit(
        make_frame(),
        CancelToken(),
        lambda _event: None,
    )

    assert record.exception is None
    assert record.run_outcome["status"] == "degraded_pa_only"
    assert record.run_outcome["official_path"] == "pa_only"
    assert record.run_outcome["degradation_stage"] == "joint_arbiter"
    assert record.stage2_decision["decision"]["estimated_win_rate"] == 55
    assert record.vpa_evidence is not None
    assert record.joint_decision is None
    assert writer.save_full.call_count == 1


def test_local_joint_prompt_failure_skips_provider_call_and_preserves_pa(
    monkeypatch,
) -> None:
    def fail_build(*_args, **_kwargs):
        raise RuntimeError("synthetic Joint prompt failure")

    monkeypatch.setattr(PromptAssembler, "build_joint_decision", fail_build)
    orchestrator, _client, writer = _orchestrator(
        "soft",
        vpa_replies=[make_reply(_vpa_report())],
        joint_replies=[make_reply(_joint_lower_confidence())],
    )

    record = orchestrator.submit(make_frame(), CancelToken(), lambda _event: None)

    assert record.exception is None
    assert record.run_outcome["status"] == "degraded_pa_only"
    assert record.run_outcome["official_path"] == "pa_only"
    assert record.vpa_evidence is not None
    assert record.joint_decision is None
    assert len(record.call_attempts) == 3
    assert all(item["stage"] != "joint_arbiter" for item in record.call_attempts)
    assert any(
        event.get("code") == "joint_prompt_build_failed"
        and event.get("category") == "local_prompt_build"
        for event in record.validation_audit
    )
    writer.save_full.assert_called_once()


def test_vpa_prose_warning_is_audited_without_retry_or_degradation() -> None:
    report = _vpa_report()
    report["vpa_report"]["observations"][0]["interpretation"] = (
        "Institutional buyers absorbed supply at K1."
    )
    orchestrator, _client, writer = _orchestrator(
        "soft",
        vpa_replies=[make_reply(report)],
        joint_replies=[make_reply(_joint_lower_confidence())],
    )

    record = orchestrator.submit(
        make_frame(),
        CancelToken(),
        lambda _event: None,
    )

    assert record.exception is None
    assert record.run_outcome["status"] == "completed_with_warnings"
    assert record.run_outcome["official_path"] == "joint"
    assert len(record.call_attempts) == 4
    assert any(
        item.get("arm") == "vpa_evidence"
        and item.get("category") == "warning"
        and "actor attribution" in str(item.get("message"))
        for item in record.validation_audit
    )
    writer.save_full.assert_called_once()


def test_pa_contract_failure_is_not_misclassified_as_provider_outage() -> None:
    invalid_stage1 = copy.deepcopy(VALID_STAGE1)
    invalid_stage1.pop("cycle_position")
    orchestrator, _client, writer = _orchestrator(
        "soft",
        baseline_replies=[make_reply(invalid_stage1)],
        vpa_replies=[],
        joint_replies=[],
    )

    record = orchestrator.submit(make_frame(), CancelToken(), lambda _event: None)

    assert record.exception["type"] == "validation_error"
    assert record.run_outcome["status"] == "invalid_model_output"
    assert record.run_outcome["official_path"] == "none"
    writer.save_partial.assert_called_once()
    writer.save_full.assert_not_called()


def test_cancel_during_vpa_keeps_specific_cancel_reason() -> None:
    token = CancelToken()

    def cancel_after_vpa_reply(*_args, **_kwargs):
        token.set()
        return make_reply(_vpa_report())

    orchestrator, _client, writer = _orchestrator(
        "soft",
        vpa_replies=[],
        joint_replies=[],
        vpa_side_effect=cancel_after_vpa_reply,
    )

    record = orchestrator.submit(make_frame(), token, lambda _event: None)

    assert record.run_outcome["status"] == "cancelled"
    assert record.run_outcome["reason"] == "cancelled_during_vpa_evidence"
    assert record.run_outcome["official_path"] == "none"
    writer.save_partial.assert_called_once()
    writer.save_full.assert_not_called()


def test_pa_gate_wait_still_allows_vpa_review_but_skips_joint_call() -> None:
    stage1_wait = copy.deepcopy(VALID_STAGE1)
    stage1_wait["cycle_position"] = "unknown"
    stage1_wait["gate_result"] = "wait"
    stage1_wait["gate_trace"] = [
        {
            "node_id": "1.2",
            "question": "是否能识别市场周期？",
            "answer": "否",
            "action": "等待",
            "reason": "当前存在多个合理周期假设。",
            "bar_range": "K5-K1",
        }
    ]
    orchestrator, _client, writer = _orchestrator(
        "soft",
        baseline_replies=[make_reply(stage1_wait)],
        vpa_replies=[make_reply(_vpa_report(relation="insufficient_data"))],
        joint_replies=[],
    )

    record = orchestrator.submit(make_frame(), CancelToken(), lambda _event: None)

    assert record.run_outcome["status"] == "completed"
    assert record.run_outcome["official_path"] == "pa_only"
    assert record.stage2_decision["decision"]["order_type"] == "不下单"
    assert record.vpa_evidence is not None
    assert record.joint_decision is None
    assert record.joint_stage2 is None
    assert record.vpa_sidecar["comparison"] is None
    assert record.role_statuses["joint"] == "not_applicable"
    assert all(
        item["stage"] != "joint_arbiter" for item in record.call_attempts
    )
    writer.save_full.assert_called_once()


def test_soft_role_path_uses_the_exact_supplied_frozen_catalog(monkeypatch) -> None:
    frame = make_frame()
    catalog = build_vpa_evidence_catalog(frame)
    report = _vpa_report()
    report["vpa_report"]["catalog_id"] = catalog["catalog_id"]
    orchestrator, _client, _writer = _orchestrator(
        "soft",
        vpa_replies=[make_reply(report)],
        joint_replies=[make_reply(_joint_lower_confidence())],
    )

    def must_not_rebuild(_frame):
        raise AssertionError("frozen experiment catalog must not be regenerated")

    monkeypatch.setattr(
        "pa_agent.ai.vpa_measurements.build_vpa_evidence_catalog",
        must_not_rebuild,
    )
    record = orchestrator.submit(
        frame,
        CancelToken(),
        lambda _event: None,
        frozen_vpa_catalog=catalog,
    )

    assert record.exception is None
    assert record.vpa_catalog == catalog


def test_soft_role_path_rejects_a_frozen_catalog_from_another_snapshot() -> None:
    frame = make_frame()
    catalog = build_vpa_evidence_catalog(frame)
    catalog["snapshot_hash"] = "0" * 64
    orchestrator, client, writer = _orchestrator(
        "soft",
        vpa_replies=[],
        joint_replies=[],
    )

    record = orchestrator.submit(
        frame,
        CancelToken(),
        lambda _event: None,
        frozen_vpa_catalog=catalog,
    )

    assert record.run_outcome["status"] == "unavailable_input"
    assert record.exception["failed_check"] == "frozen_vpa_catalog_mismatch"
    client.clone_with_provider.assert_not_called()
    writer.save_partial.assert_called_once()


def test_production_soft_cancel_before_preflight_records_effective_shadow() -> None:
    orchestrator, client, writer = _orchestrator(
        "soft",
        vpa_replies=[],
        joint_replies=[],
        allow_research_joint=False,
    )
    token = CancelToken()
    token.set()

    record = orchestrator.submit(make_frame(), token, lambda _event: None)

    assert record.run_outcome["requested_vpa_mode"] == "soft"
    assert record.run_outcome["effective_vpa_mode"] == "shadow"
    client.clone_with_provider.assert_not_called()
    writer.save_partial.assert_called_once()


def test_production_soft_pa_failure_records_effective_shadow() -> None:
    invalid_stage1 = copy.deepcopy(VALID_STAGE1)
    invalid_stage1.pop("cycle_position")
    orchestrator, _client, writer = _orchestrator(
        "soft",
        baseline_replies=[make_reply(invalid_stage1)],
        vpa_replies=[],
        joint_replies=[],
        allow_research_joint=False,
    )

    record = orchestrator.submit(make_frame(), CancelToken(), lambda _event: None)

    assert record.run_outcome["status"] == "invalid_model_output"
    assert record.run_outcome["requested_vpa_mode"] == "soft"
    assert record.run_outcome["effective_vpa_mode"] == "shadow"
    writer.save_partial.assert_called_once()
