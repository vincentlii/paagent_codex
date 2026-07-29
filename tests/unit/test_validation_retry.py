from __future__ import annotations

import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from pa_agent.ai.json_validator import JsonValidator, Ok, ValidationError
from pa_agent.ai.retry_feedback import (
    build_retry_feedback,
    parse_json_object,
)
from pa_agent.ai.retry_policy import (
    detect_immutable_field_changes,
    extract_feedback_targets,
    should_retry,
)
from pa_agent.config.settings import PromptSettings, Settings, ValidationSettings
from pa_agent.orchestrator.validation_retry import validate_with_retry
from tests.fixtures.ai_payloads import VALID_STAGE2_NO_ORDER
from tests.integration.conftest import (
    VALID_STAGE1,
    VALID_STAGE2,
    make_frame,
    make_reply,
)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        retry_enabled=True,
        retry_max=3,
        retry_max_semantic=1,
    )


def test_retry_policy_allows_one_trade_metric_reconsideration() -> None:
    settings = _settings()

    assert should_retry("a", [], [], attempt=0, settings=settings)
    assert should_retry("c", ["decision_trace.0.answer"], [], attempt=0, settings=settings)
    assert should_retry(
        "c", ["metrics:trader equation failed"], [], attempt=0, settings=settings
    )
    assert not should_retry(
        "c", ["metrics:trader equation failed"], [], attempt=1, settings=settings
    )


def test_trade_metric_retry_can_reject_order_but_cannot_reprice_it() -> None:
    before = {
        "decision": {
            "order_type": "限价单",
            "order_direction": "做多",
            "entry_price": 100.0,
            "stop_loss_price": 95.0,
            "take_profit_price": 104.0,
            "take_profit_price_2": None,
        }
    }
    targets = extract_feedback_targets(
        [
            "metrics:decision prices: risk_reward is below minimum; "
            "set order_type=不下单"
        ],
        [],
    )
    rejected = {
        "decision": {
            "order_type": "不下单",
            "order_direction": None,
            "entry_price": None,
            "stop_loss_price": None,
            "take_profit_price": None,
            "take_profit_price_2": None,
        }
    }
    repriced = copy.deepcopy(before)
    repriced["decision"]["entry_price"] = 99.0

    assert targets == {"decision.order_type"}
    assert detect_immutable_field_changes(
        "stage2",
        before,
        rejected,
        feedback_mentioned=targets,
    ) == []
    assert detect_immutable_field_changes(
        "stage2",
        before,
        repriced,
        feedback_mentioned=targets,
    ) == ["decision.entry_price: 100.0 → 99.0"]


def test_trade_metric_failure_is_program_rejected_without_retry() -> None:
    settings = Settings(
        prompt=PromptSettings(vpa_mode="off"),
        validation=ValidationSettings(retry_max=3, retry_max_semantic=1),
    )
    invalid = copy.deepcopy(VALID_STAGE2)
    invalid["decision"]["take_profit_price"] = 2060.0
    invalid["decision"]["take_profit_price_2"] = 2070.0
    retry_messages: list[list[dict]] = []

    def call_api(messages: list[dict]):
        retry_messages.append(messages)
        return make_reply(copy.deepcopy(VALID_STAGE2_NO_ORDER))

    result = validate_with_retry(
        stage="stage2",
        messages=[{"role": "user", "content": "stage2"}],
        reply=make_reply(invalid),
        validator=JsonValidator(settings),
        validation_settings=settings.validation,
        validate_kwargs={
            "kline_frame": make_frame(),
            "stage1_json": VALID_STAGE1,
        },
        call_api=call_api,
    )

    assert isinstance(result.result, Ok)
    assert retry_messages == []
    assert result.result.obj["decision"]["order_type"] == "不下单"
    assert any(
        event["path"] == "execution_policy"
        for event in result.result.normalization_events
    )
    policy_event = next(
        event
        for event in result.validation_events
        if event.get("code") == "deterministic_execution_policy"
    )
    assert policy_event["category"] == "execution_policy"
    assert policy_event["severity"] == "warning"
    assert policy_event["retry_policy"] == "none"


def test_stage2_may_define_the_concrete_plan_when_stage1_setup_hint_is_empty() -> None:
    settings = Settings(
        prompt=PromptSettings(vpa_mode="off"),
        validation=ValidationSettings(retry_max=3, retry_max_semantic=1),
    )
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["entry_setup"] = ""
    retry_messages: list[list[dict]] = []

    def call_api(messages: list[dict]):
        retry_messages.append(messages)
        raise AssertionError("a valid Stage 2 PA plan must not require repair")

    result = validate_with_retry(
        stage="stage2",
        messages=[{"role": "user", "content": "stage2"}],
        reply=make_reply(copy.deepcopy(VALID_STAGE2)),
        validator=JsonValidator(settings),
        validation_settings=settings.validation,
        validate_kwargs={"kline_frame": make_frame(), "stage1_json": stage1},
        call_api=call_api,
    )

    assert isinstance(result.result, Ok)
    assert result.attempts == 1
    assert retry_messages == []


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


def test_spike_transition_repair_includes_the_complete_state_tuple() -> None:
    settings = Settings(
        prompt=PromptSettings(vpa_mode="off"),
        validation=ValidationSettings(retry_max=3, retry_max_semantic=1),
    )
    invalid = copy.deepcopy(VALID_STAGE1)
    invalid.update(
        {
            "cycle_position": "spike",
            "spike_stage": "transitioning",
            "market_phase": "stable",
            "transition_risk": None,
        }
    )
    invalid["pa_evidence"]["spike_analysis"] = {
        "anchor_bar": "K5",
        "post_anchor_state": "pause_or_pullback_present",
        "first_pause_or_pullback_bar": "K4",
    }
    next(
        item for item in invalid["gate_trace"] if item["node_id"] == "1.2"
    )["branch"] = "spike"
    repaired = copy.deepcopy(invalid)
    repaired["market_phase"] = "transitioning"
    repaired["transition_risk"] = "medium"
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
    assert result.attempts == 2
    retry_draft = json.loads(retry_messages[0][-2]["content"])
    assert retry_draft["market_phase"] == "__REWRITE_REQUIRED__"
    assert retry_draft["transition_risk"] == "__REWRITE_REQUIRED__"
    assert "market_phase" in retry_messages[0][-1]["content"]
    assert "transition_risk" in retry_messages[0][-1]["content"]


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






def test_retry_feedback_includes_schema_allowed_values() -> None:
    error = ValidationError(
        category="c",
        stage="stage1",
        raw_text="{}",
        invalid_fields=["bar_by_bar_summary.0.context_effect"],
        allowed_values={
            "bar_by_bar_summary.0.context_effect": [
                "strengthens_bull",
                "weakens_bull",
            ]
        },
        message="invalid enum",
    )

    text = build_retry_feedback(
        error,
        stage="stage1",
        attempt=1,
        max_attempts=1,
    )

    assert "Schema 合法值" in text
    assert "bar_by_bar_summary.0.context_effect" in text
    assert "strengthens_bull" in text


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










def test_narrative_warning_does_not_trigger_a_repair_call() -> None:
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
    assert retry_messages == []
    assert invalid_reason in str(result.audit_messages)
    assert any(
        event["category"] == "warning"
        for event in result.validation_events or []
    )




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
