from __future__ import annotations

import copy
import re
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.ai.router import route_strategy_files
from pa_agent.config.paths import PROMPT_DIR
from pa_agent.config.settings import PromptSettings, Settings, ValidationSettings
from pa_agent.orchestrator.two_stage import (
    TwoStageOrchestrator,
    _vpa_authority_violations,
    _vpa_pair_diff,
)
from pa_agent.util.threading import CancelToken, OrchestratorEvent
from tests.integration.conftest import VALID_STAGE1, VALID_STAGE2, make_frame, make_reply


def test_shadow_preflight_rejects_invalid_frame_before_catalog_or_clients() -> None:
    settings = Settings(
        prompt=PromptSettings(vpa_mode="shadow"),
        validation=ValidationSettings(),
    )
    client = MagicMock()
    writer = MagicMock()
    writer.save_partial.return_value.ok = True
    writer.save_partial.return_value.audit_ok = True
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=PromptAssembler(PROMPT_DIR, prompt_settings=settings.prompt),
        router=route_strategy_files,
        validator=MagicMock(),
        pending_writer=writer,
        exp_reader=MagicMock(),
        settings=settings,
    )
    events: list[OrchestratorEvent] = []

    record = orchestrator.submit(
        replace(make_frame(), bars=()),
        CancelToken(),
        events.append,
    )

    assert record.exception == {
        "type": "insufficient_data",
        "stage": "preflight",
        "failed_check": "bars_empty",
        "message": "K线序列为空，无法分析。",
    }
    client.clone_with_provider.assert_not_called()
    writer.save_partial.assert_called_once()
    assert events == [OrchestratorEvent.InsufficientData]


def _no_order_stage2() -> dict:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"] = {
        "order_type": "\u4e0d\u4e0b\u5355",
        "order_direction": None,
        "entry_price": None,
        "take_profit_price": None,
        "take_profit_price_2": None,
        "stop_loss_price": None,
        "entry_rule": None,
        "entry_basis_bar": None,
        "entry_basis_extreme": None,
        "reasoning": "No order for this test.",
        "diagnosis_confidence": 70,
        "diagnosis_confidence_reasoning": "test",
        "trade_confidence": 50,
        "trade_confidence_reasoning": "test",
        "estimated_win_rate": None,
        "estimated_win_rate_reasoning": None,
        "key_factors": [],
        "watch_points": [],
        "risk_assessment": "test",
        "invalidation_condition": "test",
    }
    stage2["terminal"] = {"node_id": "11.2", "outcome": "wait", "label": "test"}
    return stage2


@pytest.mark.parametrize("configured_mode", ["shadow", "soft"])
def test_shadow_pair_uses_isolated_clients_and_keeps_sidecar_out_of_official_stage2(
    configured_mode: str,
) -> None:
    settings = Settings(
        prompt=PromptSettings(
            vpa_mode=configured_mode,
            vpa_shadow_design="marginal_sidecar",
        ),
        validation=ValidationSettings(),
    )
    baseline_client = MagicMock()
    baseline_client.stream_chat.side_effect = [
        make_reply(copy.deepcopy(VALID_STAGE1)),
        make_reply(_no_order_stage2()),
    ]

    vpa_stage1_attempt = 0

    def vpa_reply(messages, **_kwargs):
        nonlocal vpa_stage1_attempt
        content = "\n".join(str(message.get("content", "")) for message in messages)
        match = re.search(r"catalog_id:\s*(vpa-catalog-[a-f0-9]+)", content)
        status = re.search(r"data_status:\s*(available|limited|unavailable)", content)
        if "PAIRED_VPA_CONTEXT_TASK" in content:
            vpa_stage1_attempt += 1
            return make_reply({
                "vpa_context": {
                "catalog_id": match.group(1),
                "data_status": status.group(1),
                "summary": (
                    "K1 volume was 8500."
                    if vpa_stage1_attempt == 1
                    else "program volume evidence is available"
                ),
                "observations": [
                    {
                        "evidence_ids": ["vpa-e001"],
                        "finding_kind": "effort_result",
                        "interpretation": "K1 relative activity versus the causal baseline adds an effort/result observation.",
                    }
                ],
                "background_relation": "supports",
                "entry_timing_relation": "supports",
                "next_confirmation": {
                    "state": "pending_closed_bar",
                    "trigger": "wait for the next closed bar",
                    "requires_closed_bar": True,
                },
                }
            })
        return make_reply(
            {
                "vpa_impact": {
                    "background_relation": "supports",
                    "entry_timing_relation": "supports",
                    "evidence_used": ["vpa-e001"],
                    "decision_effect": "keep_brooks_baseline",
                    "reason": "no timing change requested",
                }
            }
        )

    vpa_client = MagicMock()
    vpa_client.stream_chat.side_effect = vpa_reply
    client = MagicMock()
    client.clone_with_provider.side_effect = [baseline_client, vpa_client]
    writer = MagicMock()
    writer.save_full.return_value.ok = True
    writer.save_full.return_value.audit_ok = True
    exp_reader = MagicMock()
    exp_reader.read_for_stage2.return_value = []

    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=PromptAssembler(PROMPT_DIR, prompt_settings=settings.prompt),
        router=route_strategy_files,
        validator=MagicMock(),
        pending_writer=writer,
        exp_reader=exp_reader,
        settings=settings,
    )
    events: list[OrchestratorEvent] = []

    record = orchestrator.submit(make_frame(), CancelToken(), events.append)

    assert client.clone_with_provider.call_count == 2
    assert baseline_client.stream_chat.call_count == 2
    assert vpa_client.stream_chat.call_count == 3
    assert record.vpa_sidecar is not None
    assert record.vpa_sidecar["mode"] == "shadow"
    assert record.vpa_sidecar["configured_mode"] == configured_mode
    assert record.vpa_sidecar["paired_execution"]["client_instances_isolated"] is True
    assert record.vpa_sidecar["paired_execution"]["provider_fallback_allowed"] is False
    assert record.vpa_sidecar["comparison"]["observed_effect"]["classification"] == "keep_baseline"
    assert record.vpa_sidecar["comparison"]["stage1"] == {}
    assert record.vpa_sidecar["vpa_arm"]["stage1_diagnosis"]["cycle_position"] == VALID_STAGE1["cycle_position"]
    vpa_stage1_prompt = "\n".join(
        message["content"] for message in record.vpa_sidecar["vpa_arm"]["stage1_messages"]
    )
    assert "PAIRED_VPA_CONTEXT_TASK" in vpa_stage1_prompt
    assert "trend_pullback" in vpa_stage1_prompt
    baseline_s2_messages = baseline_client.stream_chat.call_args_list[1].args[0]
    vpa_s2_messages = vpa_client.stream_chat.call_args_list[2].args[0]
    assert vpa_s2_messages[:-1] == baseline_s2_messages[:-1]
    assert vpa_s2_messages[-1]["content"].startswith(
        baseline_s2_messages[-1]["content"]
    )
    assert "PAIRED_VPA_DECISION_ADDENDUM" in vpa_s2_messages[-1]["content"]
    assert "冻结的 Brooks Stage 2 baseline" in vpa_s2_messages[-1]["content"]
    assert '"order_type": "不下单"' in vpa_s2_messages[-1]["content"]
    assert "你现在输出差量 JSON" in vpa_s2_messages[-1]["content"]
    assert record.vpa_sidecar["paired_execution"]["stage2_baseline_prefix_reused"] is True
    assert record.vpa_sidecar["paired_execution"]["experiment_type"] == (
        "online_vpa_marginal_sidecar"
    )
    assert record.vpa_sidecar["paired_execution"]["counterfactual_scope"] == (
        "vpa_evidence_delta_on_frozen_brooks"
    )
    assert record.vpa_sidecar["paired_execution"]["full_prompt_ab_eligible"] is False
    assert record.vpa_sidecar["paired_execution"]["first_pass_clean"] is False
    assert [
        (call["arm"], call["stage"], call["attempt"], call["status"])
        for call in record.call_attempts
    ] == [
        ("baseline", "stage1", 1, "validated"),
        ("baseline", "stage2", 1, "validated"),
        ("vpa", "stage1", 1, "validation_failed"),
        ("vpa", "stage1", 2, "validated"),
        ("vpa", "stage2", 1, "validated"),
    ]
    assert len(record.validation_audit) == 1
    assert record.validation_audit[0]["arm"] == "vpa"
    assert record.vpa_sidecar["vpa_arm"]["validation_audit"] == record.validation_audit
    assert all(call["request_hash"] for call in record.call_attempts)
    assert all(call["response_hash"] for call in record.call_attempts)
    pair_input_hash = record.vpa_sidecar["paired_execution"]["pair_input_hash"]
    assert record.vpa_sidecar["pair_id"] == f"vpa-{pair_input_hash[:12]}"
    assert (
        record.vpa_sidecar["paired_execution"]["pair_identity_version"]
        == "initial_stage1_requests_v1"
    )
    assert "vpa_sidecar" not in (record.stage2_decision or {})
    expected_official = _no_order_stage2()
    expected_official.pop("next_bar_prediction")
    assert record.stage2_decision == expected_official
    assert OrchestratorEvent.RecordSaved in events


def test_failed_vpa_arm_is_not_reported_as_a_strategy_difference() -> None:
    baseline = SimpleNamespace(
        stage1_diagnosis=copy.deepcopy(VALID_STAGE1),
        stage2_decision=_no_order_stage2(),
        strategy_files_used=["baseline.txt"],
        exception=None,
    )
    failed_vpa = SimpleNamespace(
        stage1_diagnosis=None,
        stage2_decision=None,
        strategy_files_used=None,
        exception={"stage": "stage1", "category": "c"},
    )

    comparison = _vpa_pair_diff(baseline, failed_vpa)

    assert comparison["stage1"] == {}
    assert comparison["stage2"] == {}
    assert comparison["observed_effect"]["classification"] == "vpa_arm_failed"
    assert comparison["observed_effect"]["comparison_valid"] is False
    assert comparison["observed_effect"]["stage1_changed"] is None
    assert comparison["observed_effect"]["stage2_changed"] is None


def test_pair_diff_reports_structured_pa_stage1_drift() -> None:
    baseline_stage1 = copy.deepcopy(VALID_STAGE1)
    baseline_stage1["support_levels"] = [100.0]
    baseline_stage1["resistance_levels"] = [110.0]
    baseline_stage1["bar_analysis"]["bar_type"] = "trend_bull"
    vpa_stage1 = copy.deepcopy(baseline_stage1)
    vpa_stage1["support_levels"] = [99.0]
    vpa_stage1["resistance_levels"] = [111.0]
    vpa_stage1["detected_patterns"] = ["breakout_failure"]
    vpa_stage1["bar_analysis"]["bar_type"] = "trend_bear"
    baseline = SimpleNamespace(
        stage1_diagnosis=baseline_stage1,
        stage2_decision=_no_order_stage2(),
        strategy_files_used=["range.txt"],
        exception=None,
    )
    vpa = SimpleNamespace(
        stage1_diagnosis=vpa_stage1,
        stage2_decision=_no_order_stage2(),
        strategy_files_used=["range.txt"],
        exception=None,
    )

    comparison = _vpa_pair_diff(baseline, vpa)

    assert comparison["stage1"]["support_levels"] == {
        "baseline": [100.0],
        "vpa": [99.0],
    }
    assert comparison["stage1"]["resistance_levels"] == {
        "baseline": [110.0],
        "vpa": [111.0],
    }
    assert comparison["stage1"]["detected_patterns"] == {
        "baseline": baseline_stage1["detected_patterns"],
        "vpa": ["breakout_failure"],
    }
    assert comparison["stage1"]["bar_analysis"] == {
        "baseline": baseline_stage1["bar_analysis"],
        "vpa": vpa_stage1["bar_analysis"],
    }
    assert comparison["observed_effect"]["stage1_changed"] is True
    assert comparison["observed_effect"]["classification"] == (
        "diagnosis_or_timing_changed_without_trade_rate_effect"
    )


def test_shadow_defaults_to_independent_full_prompt_ab() -> None:
    settings = Settings(
        prompt=PromptSettings(vpa_mode="shadow"),
        validation=ValidationSettings(),
    )
    baseline_client = MagicMock()
    baseline_client.stream_chat.side_effect = [
        make_reply(copy.deepcopy(VALID_STAGE1)),
        make_reply(_no_order_stage2()),
    ]

    def full_vpa_reply(messages, **_kwargs):
        content = "\n".join(str(message.get("content", "")) for message in messages)
        match = re.search(r"catalog_id:\s*(vpa-catalog-[a-f0-9]+)", content)
        status = re.search(r"data_status:\s*(available|limited|unavailable)", content)
        if "阶段一任务" in content and '"decision"' not in content:
            stage1 = copy.deepcopy(VALID_STAGE1)
            stage1["vpa_context"] = {
                "catalog_id": match.group(1),
                "data_status": status.group(1),
                "summary": "Volume provenance bounds this independent observation.",
                "observations": [
                    {
                        "evidence_ids": ["vpa-e001"],
                        "finding_kind": "effort_result",
                        "interpretation": "vpa-e001 adds an effort/result observation.",
                    }
                ],
                "background_relation": "supports",
                "entry_timing_relation": "supports",
                "next_confirmation": {
                    "state": "pending_closed_bar",
                    "trigger": "Wait for the next closed bar and assess acceptance.",
                    "requires_closed_bar": True,
                },
            }
            return make_reply(stage1)
        stage2 = _no_order_stage2()
        stage2["vpa_impact"] = {
            "background_relation": "supports",
            "entry_timing_relation": "supports",
            "evidence_used": ["vpa-e001"],
            "decision_effect": "keep_brooks_baseline",
            "reason": "vpa-e001 leaves the Brooks entry timing unchanged.",
        }
        return make_reply(stage2)

    vpa_client = MagicMock()
    vpa_client.stream_chat.side_effect = full_vpa_reply
    client = MagicMock()
    client.clone_with_provider.side_effect = [baseline_client, vpa_client]
    writer = MagicMock()
    writer.save_full.return_value.ok = True
    writer.save_full.return_value.audit_ok = True
    exp_reader = MagicMock()
    exp_reader.read_for_stage2.return_value = []
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=PromptAssembler(PROMPT_DIR, prompt_settings=settings.prompt),
        router=route_strategy_files,
        validator=MagicMock(),
        pending_writer=writer,
        exp_reader=exp_reader,
        settings=settings,
    )

    record = orchestrator.submit(make_frame(), CancelToken(), lambda _event: None)

    paired = record.vpa_sidecar["paired_execution"]
    assert paired["experiment_type"] == "full_prompt_ab"
    assert paired["counterfactual_scope"] == "independent_brooks_vs_brooks_plus_vpa"
    assert paired["full_prompt_ab_eligible"] is True
    assert paired["first_pass_clean"] is True
    assert paired["authority_violations"] == []
    assert paired["stage2_baseline_prefix_reused"] is False
    vpa_prompt = "\n".join(
        message["content"]
        for message in record.vpa_sidecar["vpa_arm"]["stage1_messages"]
    )
    baseline_prompt = "\n".join(message["content"] for message in record.stage1_messages)
    assert "PAIRED_VPA_CONTEXT_TASK" not in vpa_prompt
    assert "VPA_PROGRAM_EVIDENCE_CATALOG" in vpa_prompt
    assert "VPA_PROGRAM_EVIDENCE_CATALOG" not in baseline_prompt
    assert record.stage2_decision == {
        key: value
        for key, value in _no_order_stage2().items()
        if key != "next_bar_prediction"
    }


def test_full_prompt_ab_reports_vpa_authority_violations() -> None:
    baseline = SimpleNamespace(
        stage1_diagnosis={
            "cycle_position": "trading_range",
            "alternative_cycle_position": None,
            "direction": "bullish",
            "diagnosis_confidence": 75,
            "gate_result": "proceed",
            "spike_stage": None,
            "climax_risk": "none",
            "market_phase": "stable",
            "transition_risk": None,
            "support_levels": ["100"],
            "resistance_levels": ["110"],
            "bar_analysis": {
                "always_in": "long",
                "bar_type": "trend_bull",
                "signal_bar": {"bar": "K2", "quality": "strong", "pattern": "H1"},
                "entry_setup_type": "pullback",
                "follow_through": True,
            },
        },
        stage2_decision={
            "decision": {
                "order_type": "no_order",
                "order_direction": None,
                "estimated_win_rate": 53,
                "diagnosis_confidence": 75,
            }
        },
        strategy_files_used=["range.txt"],
    )
    vpa = SimpleNamespace(
        stage1_diagnosis={
            "cycle_position": "normal_channel",
            "alternative_cycle_position": "trading_range",
            "direction": "bearish",
            "diagnosis_confidence": 61,
            "gate_result": "wait",
            "spike_stage": "ending",
            "climax_risk": "warning",
            "market_phase": "transitioning",
            "transition_risk": "possible reversal",
            "support_levels": ["99"],
            "resistance_levels": ["111"],
            "bar_analysis": {
                "always_in": "short",
                "bar_type": "trend_bear",
                "signal_bar": {"bar": "K3", "quality": "weak", "pattern": "L1"},
                "entry_setup_type": "reversal",
                "follow_through": False,
            },
        },
        stage2_decision={
            "decision": {
                "order_type": "limit",
                "order_direction": "short",
                "estimated_win_rate": 61,
                "diagnosis_confidence": 61,
            }
        },
        strategy_files_used=["trend.txt"],
    )

    violations = _vpa_authority_violations(baseline, vpa)

    assert "stage1.cycle_position_changed" in violations
    assert "stage1.alternative_cycle_position_changed" in violations
    assert "stage1.direction_changed" in violations
    assert "stage1.diagnosis_confidence_changed" in violations
    assert "stage1.gate_result_changed" in violations
    assert "stage1.spike_stage_changed" in violations
    assert "stage1.climax_risk_changed" in violations
    assert "stage1.market_phase_changed" in violations
    assert "stage1.transition_risk_changed" in violations
    assert "stage1.support_levels_changed" in violations
    assert "stage1.resistance_levels_changed" in violations
    assert "stage1.bar_analysis.always_in_changed" in violations
    assert "stage1.bar_analysis.bar_type_changed" in violations
    assert "stage1.bar_analysis.signal_bar.bar_changed" in violations
    assert "stage1.bar_analysis.signal_bar.quality_changed" in violations
    assert "stage1.bar_analysis.signal_bar.pattern_changed" in violations
    assert "stage1.bar_analysis.entry_setup_type_changed" in violations
    assert "stage1.bar_analysis.follow_through_changed" in violations
    assert "routing.strategy_files_changed" in violations
    assert "stage2.created_trade_from_no_order" in violations
    assert "stage2.estimated_win_rate_changed" in violations
    assert "stage2.diagnosis_confidence_changed" in violations


def test_shadow_pair_reuses_initial_stage2_request_after_baseline_retry() -> None:
    settings = Settings(
        prompt=PromptSettings(
            vpa_mode="shadow",
            vpa_shadow_design="marginal_sidecar",
        ),
        validation=ValidationSettings(),
    )
    invalid_stage2 = _no_order_stage2()
    invalid_stage2["terminal"] = {
        "node_id": "11.2",
        "outcome": "trade",
        "label": "INITIAL_INVALID_STAGE2_SENTINEL",
    }
    baseline_client = MagicMock()
    baseline_client.stream_chat.side_effect = [
        make_reply(copy.deepcopy(VALID_STAGE1)),
        make_reply(invalid_stage2),
        make_reply(_no_order_stage2()),
    ]

    def vpa_reply(messages, **_kwargs):
        content = "\n".join(str(message.get("content", "")) for message in messages)
        match = re.search(r"catalog_id:\s*(vpa-catalog-[a-f0-9]+)", content)
        status = re.search(r"data_status:\s*(available|limited|unavailable)", content)
        if "PAIRED_VPA_CONTEXT_TASK" in content:
            return make_reply(
                {
                    "vpa_context": {
                        "catalog_id": match.group(1),
                        "data_status": status.group(1),
                        "summary": "program evidence is usable",
                        "observations": [
                            {
                                "evidence_ids": ["vpa-e001"],
                                "finding_kind": "effort_result",
                                "interpretation": "K1 relative activity versus the causal baseline adds an effort/result observation.",
                            }
                        ],
                        "background_relation": "supports",
                        "entry_timing_relation": "supports",
                        "next_confirmation": {
                            "state": "pending_closed_bar",
                            "trigger": "wait for the next closed bar",
                            "requires_closed_bar": True,
                        },
                    }
                }
            )
        return make_reply(
            {
                "vpa_impact": {
                    "background_relation": "supports",
                    "entry_timing_relation": "supports",
                    "evidence_used": ["vpa-e001"],
                    "decision_effect": "keep_brooks_baseline",
                    "reason": "no timing change requested",
                }
            }
        )

    vpa_client = MagicMock()
    vpa_client.stream_chat.side_effect = vpa_reply
    client = MagicMock()
    client.clone_with_provider.side_effect = [baseline_client, vpa_client]
    writer = MagicMock()
    writer.save_full.return_value.ok = True
    writer.save_full.return_value.audit_ok = True
    exp_reader = MagicMock()
    exp_reader.read_for_stage2.return_value = []
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=PromptAssembler(PROMPT_DIR, prompt_settings=settings.prompt),
        router=route_strategy_files,
        validator=MagicMock(),
        pending_writer=writer,
        exp_reader=exp_reader,
        settings=settings,
    )

    record = orchestrator.submit(make_frame(), CancelToken(), lambda _event: None)

    assert record.exception is None
    assert baseline_client.stream_chat.call_count == 3
    baseline_stage2_calls = [
        call
        for call in record.call_attempts
        if call["arm"] == "baseline" and call["stage"] == "stage2"
    ]
    assert [call["status"] for call in baseline_stage2_calls] == [
        "validation_failed",
        "validated",
    ]
    assert baseline_stage2_calls[0]["validation_category"] == "c"
    assert baseline_stage2_calls[0]["request_hash"] != baseline_stage2_calls[1]["request_hash"]
    initial_stage2_messages = baseline_client.stream_chat.call_args_list[1].args[0]
    vpa_stage2_messages = vpa_client.stream_chat.call_args_list[1].args[0]
    assert vpa_stage2_messages[:-1] == initial_stage2_messages[:-1]
    assert vpa_stage2_messages[-1]["content"].startswith(
        initial_stage2_messages[-1]["content"]
    )
    combined = "\n".join(message["content"] for message in vpa_stage2_messages)
    assert "INITIAL_INVALID_STAGE2_SENTINEL" not in combined
