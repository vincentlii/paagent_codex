from __future__ import annotations

import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from pa_agent.ai.json_validator import JsonValidator, Ok, ValidationError
from pa_agent.ai.retry_feedback import (
    build_retry_feedback,
    build_sanitized_retry_draft,
    parse_json_object,
)
from pa_agent.ai.retry_policy import (
    detect_immutable_field_changes,
    extract_feedback_targets,
    should_retry,
)
from pa_agent.config.settings import PromptSettings, Settings, ValidationSettings
from pa_agent.orchestrator.validation_retry import validate_with_retry
from tests.integration.conftest import VALID_STAGE1, make_frame, make_reply


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        retry_enabled=True,
        retry_max=3,
        retry_max_semantic=1,
    )


def test_retry_policy_allows_format_error_but_not_trade_metric_rewrite() -> None:
    settings = _settings()

    assert should_retry("a", [], [], attempt=0, settings=settings)
    assert should_retry("c", ["decision_trace.0.answer"], [], attempt=0, settings=settings)
    assert should_retry(
        "c",
        ["vpa:vpa_context.summary must cite program evidence instead of restating numeric market facts"],
        [],
        attempt=0,
        settings=settings,
    )
    assert should_retry(
        "c",
        ["vpa_context.next_confirmation.state"],
        [],
        attempt=0,
        settings=settings,
    )
    assert should_retry(
        "c",
        ["vpa_impact.decision_effect"],
        [],
        attempt=0,
        settings=settings,
    )
    assert not should_retry(
        "c", ["metrics:trader equation failed"], [], attempt=0, settings=settings
    )


def test_retry_guard_blocks_unrequested_stage1_thesis_change() -> None:
    before = {"cycle_position": "normal_channel", "direction": "bullish", "gate_result": "proceed"}
    after = {"cycle_position": "normal_channel", "direction": "bearish", "gate_result": "proceed"}

    violations = detect_immutable_field_changes("stage1", before, after)

    assert violations == ["direction: 'bullish' → 'bearish'"]


def test_retry_guard_allows_field_explicitly_named_by_feedback() -> None:
    before = {"direction": "bullish"}
    after = {"direction": "bearish"}

    assert detect_immutable_field_changes(
        "stage1", before, after, feedback_mentioned={"direction"}
    ) == []


def test_retry_guard_blocks_unrequested_pa_evidence_change() -> None:
    before = {
        "pa_evidence": {
            "fact_refs": ["PA.BAR.K1"],
            "theory_rule_ids": ["BROOKS.CHANNEL.CONTEXT"],
            "spike_analysis": None,
        }
    }
    after = {
        "pa_evidence": {
            "fact_refs": ["PA.BAR.K2"],
            "theory_rule_ids": ["BROOKS.CHANNEL.CONTEXT"],
            "spike_analysis": None,
        }
    }

    violations = detect_immutable_field_changes("stage1", before, after)

    assert len(violations) == 1
    assert violations[0].startswith("pa_evidence.fact_refs:")


def test_retry_guard_only_allows_the_pa_evidence_leaf_named_by_feedback() -> None:
    before = {
        "pa_evidence": {
            "fact_refs": ["PA.BAR.K1"],
            "theory_rule_ids": ["BROOKS.CHANNEL.CONTEXT"],
            "spike_analysis": None,
        }
    }
    after = {
        "pa_evidence": {
            "fact_refs": ["PA.BAR.K2"],
            "theory_rule_ids": ["BROOKS.RANGE.EDGE_OVER_MIDDLE"],
            "spike_analysis": None,
        }
    }
    targets = extract_feedback_targets(
        ["pa_evidence.fact_refs contains an unknown fact ID"],
        [],
    )

    violations = detect_immutable_field_changes(
        "stage1",
        before,
        after,
        feedback_mentioned=targets,
    )

    assert targets == {"pa_evidence.fact_refs"}
    assert len(violations) == 1
    assert violations[0].startswith("pa_evidence.theory_rule_ids:")


def test_validation_retry_rejects_unrequested_pa_evidence_rewrite() -> None:
    settings = Settings(
        prompt=PromptSettings(vpa_mode="off"),
        validation=ValidationSettings(retry_max=3, retry_max_semantic=1),
    )
    invalid = copy.deepcopy(VALID_STAGE1)
    invalid["support_levels"] = ["not-a-price-level"]
    repaired = copy.deepcopy(VALID_STAGE1)
    repaired["pa_evidence"]["fact_refs"] = [
        "PA.WINDOW.K5",
        "PA.BAR.K1",
        "PA.BAR.K3",
    ]

    result = validate_with_retry(
        stage="stage1",
        messages=[{"role": "user", "content": "stage1"}],
        reply=make_reply(invalid),
        validator=JsonValidator(settings),
        validation_settings=settings.validation,
        validate_kwargs={"kline_frame": make_frame()},
        call_api=lambda _messages: make_reply(repaired),
    )

    assert isinstance(result.result, ValidationError)
    assert result.cheat_detected is True
    assert result.attempts == 2
    assert any(
        field.startswith("retry_immutable:pa_evidence.fact_refs:")
        for field in result.result.invalid_fields
    )


def test_feedback_target_extraction_is_path_based() -> None:
    targets = extract_feedback_targets(
        ["s2:decision.order_direction conflicts with stage1"],
        ["next_bar_prediction"],
    )

    assert targets == {"direction", "decision.order_direction", "next_bar_prediction"}


def test_retry_parser_is_strict_json_only() -> None:
    assert parse_json_object('{"direction": "bullish"}') == {"direction": "bullish"}
    assert parse_json_object('{"direction": "bullish"; }') is None


def test_retry_feedback_forbids_unrelated_semantic_changes() -> None:
    error = ValidationError(
        category="c",
        stage="stage2",
        raw_text="{}",
        invalid_fields=["decision_trace.0.answer"],
        message="invalid answer",
    )

    text = build_retry_feedback(error, stage="stage2", attempt=1, max_attempts=1)

    assert "仅修下列项" in text
    assert "未被反馈点名时改变 order_type" in text
    assert "完整" in text and "裸 JSON" in text


def test_paired_vpa_retry_feedback_requests_only_the_sidecar() -> None:
    error = ValidationError(
        category="c",
        stage="stage1",
        raw_text="{}",
        invalid_fields=[
            "vpa:vpa_context.summary must cite program evidence instead of restating numeric market facts"
        ],
        message="invalid VPA narrative",
    )

    text = build_retry_feedback(
        error,
        stage="stage1",
        attempt=1,
        max_attempts=1,
        paired_vpa_context=True,
    )

    assert "只输出顶层 `vpa_context`" in text
    assert "bar_range 由程序生成" in text
    assert "完整阶段一" not in text


def test_full_prompt_vpa_retry_feedback_includes_exact_contract_repairs() -> None:
    error = ValidationError(
        category="c",
        stage="stage1",
        raw_text="{}",
        invalid_fields=[
            "vpa_context.next_confirmation.state",
            "vpa:vpa_context.next_confirmation.trigger contains an unbounded "
            "new-high/new-low claim; use a concrete K-line comparison",
        ],
        message=(
            "'waiting' is not one of ['pending_closed_bar', 'not_applicable']"
        ),
    )

    text = build_retry_feedback(
        error,
        stage="stage1",
        attempt=1,
        max_attempts=1,
        full_prompt_vpa=True,
    )

    assert "`pending_closed_bar` / `not_applicable`" in text
    assert "下一根已收盘 K 线" in text
    assert "K1 高点" in text
    assert "输出**完整**阶段一" in text


def test_retry_feedback_includes_schema_allowed_values() -> None:
    error = ValidationError(
        category="c",
        stage="stage1",
        raw_text="{}",
        invalid_fields=["vpa_context.next_confirmation.state"],
        allowed_values={
            "vpa_context.next_confirmation.state": [
                "pending_closed_bar",
                "not_applicable",
            ]
        },
        message="invalid enum",
    )

    text = build_retry_feedback(
        error,
        stage="stage1",
        attempt=1,
        max_attempts=1,
        full_prompt_vpa=True,
    )

    assert "Schema 合法值" in text
    assert "vpa_context.next_confirmation.state" in text
    assert "pending_closed_bar" in text


def test_retry_feedback_lists_all_typical_validation_failures() -> None:
    error = ValidationError(
        category="c",
        stage="stage1",
        raw_text="{}",
        invalid_fields=[f"gate_trace.{index}.reason" for index in range(13)],
        message="multiple invalid fields",
    )

    text = build_retry_feedback(
        error,
        stage="stage1",
        attempt=1,
        max_attempts=1,
    )

    assert "gate_trace.12.reason" in text


def test_full_prompt_vpa_mode_is_inferred_from_validation_catalog() -> None:
    settings = Settings(
        prompt=PromptSettings(vpa_mode="shadow"),
        validation=ValidationSettings(retry_max=3, retry_max_semantic=1),
    )
    invalid = copy.deepcopy(VALID_STAGE1)
    invalid["vpa_context"] = {
        "catalog_id": "catalog",
        "data_status": "limited",
        "summary": "Source quality is limited.",
        "observations": [],
        "background_relation": "neutral",
        "entry_timing_relation": "neutral",
        "next_confirmation": {
            "state": "invalid_state",
            "trigger": "等待下一根已收盘 K 线。",
            "requires_closed_bar": True,
        },
    }
    retry_messages: list[list[dict]] = []

    def call_api(messages: list[dict]):
        retry_messages.append(messages)
        return make_reply(invalid)

    result = validate_with_retry(
        stage="stage1",
        messages=[{"role": "user", "content": "full stage1 with VPA"}],
        reply=make_reply(invalid),
        validator=JsonValidator(settings),
        validation_settings=settings.validation,
        validate_kwargs={
            "kline_frame": make_frame(),
            "vpa_evidence_catalog": {
                "catalog_id": "catalog",
                "data_status": "limited",
                "evidence": [],
            },
        },
        call_api=call_api,
    )

    assert isinstance(result.result, ValidationError)
    assert "`pending_closed_bar` / `not_applicable`" in retry_messages[0][-1]["content"]
    first_event = result.validation_events[0]
    assert first_event["raw_text"]
    assert first_event["retry_feedback"]
    assert first_event["allowed_values"]["vpa_context.next_confirmation.state"] == [
        "pending_closed_bar",
        "not_applicable",
    ]


def test_full_prompt_vpa_retry_repairs_contract_without_changing_pa_thesis() -> None:
    settings = Settings(
        prompt=PromptSettings(vpa_mode="shadow"),
        validation=ValidationSettings(retry_max=3, retry_max_semantic=1),
    )
    catalog = {
        "catalog_id": "catalog",
        "data_status": "limited",
        "capabilities": {
            "actor_attribution": False,
            "interpretation_ceiling": "relative_activity_only",
        },
        "comparators": {"seasonality": {"market_session": "unclassified"}},
        "evidence": [
            {
                "evidence_id": "vpa-e001",
                "source": "volume",
                "bar_range": "K1",
                "facts": {"volume_vs_baseline_relation": "above"},
            }
        ],
    }

    def payload(*, trigger: str, interpretation: str) -> dict:
        stage1 = copy.deepcopy(VALID_STAGE1)
        stage1["vpa_context"] = {
            "catalog_id": "catalog",
            "data_status": "limited",
            "summary": "Source quality limits the interpretation.",
            "observations": [
                {
                    "evidence_ids": ["vpa-e001"],
                    "finding_kind": "effort_result",
                    "interpretation": interpretation,
                }
            ],
            "background_relation": "neutral",
            "entry_timing_relation": "neutral",
            "next_confirmation": {
                "state": "waiting",
                "trigger": trigger,
                "requires_closed_bar": True,
            },
        }
        return stage1

    invalid = payload(
        trigger="Wait for the next closed bar to make a new high.",
        interpretation=(
            "K1 is above the causal baseline, proving bulls fully pushed the move."
        ),
    )
    repaired = payload(
        trigger="Wait for the next closed bar to accept above the K1 high.",
        interpretation=(
            "K1 activity is above the causal baseline; price resolution remains "
            "conditional without identifying participants."
        ),
    )
    retry_messages: list[list[dict]] = []

    def call_api(messages: list[dict]):
        retry_messages.append(messages)
        return make_reply(repaired)

    result = validate_with_retry(
        stage="stage1",
        messages=[{"role": "user", "content": "full Stage 1 with VPA"}],
        reply=make_reply(invalid),
        validator=JsonValidator(settings),
        validation_settings=settings.validation,
        validate_kwargs={
            "kline_frame": make_frame(),
            "vpa_evidence_catalog": catalog,
        },
        call_api=call_api,
    )

    assert isinstance(result.result, Ok)
    assert result.attempts == 2
    assert result.cheat_detected is False
    assert result.result.obj["direction"] == VALID_STAGE1["direction"]
    assert (
        result.result.obj["vpa_context"]["next_confirmation"]["state"]
        == "pending_closed_bar"
    )
    feedback = retry_messages[0][-1]["content"]
    assert "explicit K-line or structure anchor" in feedback
    assert "VPA provenance limit" in feedback


def test_paired_vpa_numeric_hallucination_retries_without_mutating_baseline() -> None:
    settings = Settings(
        prompt=PromptSettings(vpa_mode="shadow"),
        validation=ValidationSettings(retry_max=3, retry_max_semantic=1),
    )
    catalog = {
        "catalog_id": "vpa-catalog-test",
        "data_status": "limited",
        "provenance": {"volume_kind": "unknown"},
        "capabilities": {
            "actor_attribution": False,
            "interpretation_ceiling": "relative_activity_only",
        },
        "comparators": {"seasonality": {"market_session": "unclassified"}},
        "evidence": [
            {
                "evidence_id": "vpa-e001",
                "source": "volume",
                "bar_range": "K1",
                "facts": {
                    "volume_available": True,
                    "volume_vs_previous_relation": "below",
                },
            }
        ],
    }

    def context(interpretation: str) -> dict:
        return {
            "vpa_context": {
                "catalog_id": "vpa-catalog-test",
                "data_status": "limited",
                "summary": "The source is limited.",
                "observations": [
                    {
                        "evidence_ids": ["vpa-e001"],
                        "finding_kind": "effort_result",
                        "interpretation": interpretation,
                    }
                ],
                "background_relation": "neutral",
                "entry_timing_relation": "neutral",
                "next_confirmation": {
                    "state": "pending_closed_bar",
                    "trigger": "Wait for the next closed bar.",
                    "requires_closed_bar": True,
                },
            }
        }

    bad_reply = make_reply(context("K1 volume was 8500, so effort contracted."))
    good_reply = make_reply(context("K1 suggests lower effort, but resolution is pending."))
    retry_messages: list[list[dict]] = []

    def call_api(messages: list[dict]):
        retry_messages.append(messages)
        return good_reply

    result = validate_with_retry(
        stage="stage1",
        messages=[{"role": "user", "content": "PAIRED_VPA_CONTEXT_TASK"}],
        reply=bad_reply,
        validator=JsonValidator(settings),
        validation_settings=settings.validation,
        validate_kwargs={
            "kline_frame": make_frame(),
            "vpa_evidence_catalog": catalog,
            "paired_baseline_stage1": VALID_STAGE1,
        },
        call_api=call_api,
    )

    assert isinstance(result.result, Ok)
    assert result.attempts == 2
    assert result.cheat_detected is False
    assert result.result.obj["direction"] == VALID_STAGE1["direction"]
    assert result.result.obj["vpa_context"]["observations"][0]["bar_range"] == "K1"
    assert "只输出顶层 `vpa_context`" in retry_messages[0][-1]["content"]
    assert "第 1/1 次重试" in retry_messages[0][-1]["content"]


def test_retry_keeps_audit_transcript_but_returns_clean_canonical_request() -> None:
    settings = Settings(
        prompt=PromptSettings(vpa_mode="shadow"),
        validation=ValidationSettings(retry_max=3, retry_max_semantic=1),
    )
    catalog = {
        "catalog_id": "vpa-catalog-test",
        "data_status": "limited",
        "provenance": {"volume_kind": "unknown"},
        "capabilities": {
            "actor_attribution": False,
            "interpretation_ceiling": "relative_activity_only",
        },
        "comparators": {"seasonality": {"market_session": "unclassified"}},
        "evidence": [
            {
                "evidence_id": "vpa-e001",
                "source": "volume",
                "bar_range": "K1",
                "facts": {"volume_vs_baseline_relation": "above"},
            }
        ],
    }

    def context(summary: str) -> dict:
        return {
            "vpa_context": {
                "catalog_id": "vpa-catalog-test",
                "data_status": "limited",
                "summary": summary,
                "observations": [
                    {
                        "evidence_ids": ["vpa-e001"],
                        "finding_kind": "effort_result",
                        "interpretation": "K1 relative activity is elevated versus the causal baseline.",
                    }
                ],
                "background_relation": "neutral",
                "entry_timing_relation": "neutral",
                "next_confirmation": {
                    "state": "pending_closed_bar",
                    "trigger": "Assess acceptance after the next closed bar.",
                    "requires_closed_bar": True,
                },
            }
        }

    initial = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "PAIRED_VPA_CONTEXT_TASK"},
    ]
    bad_reply = make_reply(context("K1 volume was 8500."))
    good_reply = make_reply(context("Source quality limits the interpretation."))

    result = validate_with_retry(
        stage="stage1",
        messages=initial,
        reply=bad_reply,
        validator=JsonValidator(settings),
        validation_settings=settings.validation,
        validate_kwargs={
            "kline_frame": make_frame(),
            "vpa_evidence_catalog": catalog,
            "paired_baseline_stage1": VALID_STAGE1,
        },
        call_api=lambda _messages: good_reply,
    )

    assert isinstance(result.result, Ok)
    assert result.canonical_messages == initial
    assert [item["role"] for item in result.audit_messages] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert "8500" not in str(result.canonical_messages)
    assert "8500" in str(result.audit_messages)


def test_retry_request_redacts_invalid_leaf_instead_of_replaying_it() -> None:
    settings = Settings(
        prompt=PromptSettings(vpa_mode="off"),
        validation=ValidationSettings(retry_max=3, retry_max_semantic=1),
    )
    invalid = copy.deepcopy(VALID_STAGE1)
    invalid_reason = "K1 created an unbounded new high."
    invalid["bar_by_bar_summary"][0]["reason"] = invalid_reason
    repaired = copy.deepcopy(VALID_STAGE1)
    repaired["bar_by_bar_summary"][0]["reason"] = (
        "K1 extends the move, while follow-through remains pending."
    )
    retry_messages: list[list[dict]] = []

    def call_api(messages: list[dict]):
        retry_messages.append(messages)
        return make_reply(repaired)

    result = validate_with_retry(
        stage="stage1",
        messages=[{"role": "user", "content": "stage1"}],
        reply=make_reply(invalid),
        validator=JsonValidator(settings),
        validation_settings=settings.validation,
        validate_kwargs={"kline_frame": make_frame()},
        call_api=call_api,
    )

    assert isinstance(result.result, Ok)
    retry_assistant = retry_messages[0][-2]["content"]
    assert invalid_reason not in retry_assistant
    assert "__REWRITE_REQUIRED__" in retry_assistant
    assert invalid_reason in str(result.audit_messages)


def test_vpa_contract_error_redacts_the_sidecar_transaction_boundary() -> None:
    previous = {
        "vpa_context": {
            "summary": "Unusable prior interpretation.",
            "observations": [{"finding_kind": "effort_result"}],
        }
    }

    sanitized = build_sanitized_retry_draft(
        json.dumps(previous),
        [
            "vpa:vpa_context available VPA context requires an independent "
            "price-volume finding"
        ],
        [],
    )

    assert json.loads(sanitized)["vpa_context"] == "__REWRITE_REQUIRED__"


def test_extreme_grounding_feedback_allows_fact_refs_to_be_repaired() -> None:
    targets = extract_feedback_targets(
        [
            "s1:bar_by_bar_summary[2].reason contains an unbounded new-high/new-low claim"
        ],
        [],
    )

    assert "pa_evidence.fact_refs" in targets


def test_count_grounding_feedback_allows_fact_refs_to_be_repaired() -> None:
    targets = extract_feedback_targets(
        [
            "s1:gate_trace[2].reason bearish count must cite PA.WINDOW instead "
            "of restating an exact bar count"
        ],
        [],
    )

    assert "pa_evidence.fact_refs" in targets


@pytest.mark.parametrize(
    ("stage", "invalid_field", "expected_hint"),
    [
        (
            "stage1",
            "htf_context must cite at least one injected HTF.H# bar when HTF is available",
            "HTF 引用提示",
        ),
        (
            "stage1",
            "vpa_context.summary uses relative-volume language without an explicit comparator",
            "VPA 比较器提示",
        ),
        (
            "stage1",
            "vpa_context.observations[0].interpretation proxy/unknown volume "
            "supports relative activity only, not active demand/supply",
            "VPA provenance limit",
        ),
        (
            "stage1",
            "bar_by_bar_summary[0].reason EMA slope direction conflicts with program EMA deltas",
            "EMA 事实提示",
        ),
        (
            "stage1",
            "gate_trace[2].reason bearish count must cite PA.WINDOW instead of restating an exact bar count",
            "PA count citation",
        ),
        (
            "stage1",
            "s1:gate_trace[3].reason 引用了 bar_range=K100-K41 之外的 K40",
            "gate_trace 引用范围提示",
        ),
        (
            "stage2",
            "decision.entry_rule claims one tick without verified tick size or an execution-time disclosure",
            "Tick size 提示",
        ),
        (
            "stage2",
            "decision_effect=prefer_retest must change entry timing fields",
            "VPA 时机影响提示",
        ),
    ],
)
def test_retry_feedback_targets_program_fact_violation(
    stage: str,
    invalid_field: str,
    expected_hint: str,
) -> None:
    error = ValidationError(
        category="c",
        stage=stage,
        raw_text="{}",
        invalid_fields=[invalid_field],
        message="program fact mismatch",
    )

    text = build_retry_feedback(error, stage=stage, attempt=1, max_attempts=1)

    assert expected_hint in text


def test_retry_feedback_distinguishes_local_close_from_window_extreme() -> None:
    error = ValidationError(
        category="c",
        stage="stage1",
        raw_text="{}",
        invalid_fields=[
            "bar_by_bar_summary[0].reason contains an unbounded new-high/new-low claim"
        ],
        message="program fact mismatch",
    )

    text = build_retry_feedback(error, stage="stage1", attempt=1, max_attempts=1)

    assert "本棒高点/低点" in text
    assert "跨窗口" in text
    assert "Additional editable path: pa_evidence.fact_refs" in text


def test_retry_feedback_repairs_a_local_close_geometry_conflict() -> None:
    error = ValidationError(
        category="c",
        stage="stage1",
        raw_text="{}",
        invalid_fields=[
            "bar_analysis.signal_bar.reason local close position conflicts with "
            "program PA.BAR.K1 close_position=0.523"
        ],
        message="program fact mismatch",
    )

    text = build_retry_feedback(error, stage="stage1", attempt=1, max_attempts=1)

    assert "单棒收盘位置提示" in text
    assert "middle_half" in text


def test_retry_feedback_keeps_geometry_when_a_bar_has_zero_range() -> None:
    frame = make_frame()
    flat = replace(
        frame.bars[0],
        open=2045.0,
        high=2045.0,
        low=2045.0,
        close=2045.0,
    )
    frame = replace(frame, bars=(flat, *frame.bars[1:]))
    error = ValidationError(
        category="c",
        stage="stage1",
        raw_text="{}",
        invalid_fields=["bar_by_bar_summary[0].reason conflicts with program geometry"],
        message="program fact mismatch",
    )

    text = build_retry_feedback(
        error,
        stage="stage1",
        attempt=1,
        max_attempts=1,
        frame=frame,
    )

    assert "K1:" in text
    assert "body_ratio=" not in text
    assert "close_position=" not in text
    assert "close_zone=" in text
    assert "K2:" in text
