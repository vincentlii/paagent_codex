from __future__ import annotations

import copy
import json

from pa_agent.ai.factual_grounding import (
    validate_future_confirmation_narrative,
    validate_inference_only_narrative,
    validate_vpa_comparator_narrative,
)
from pa_agent.ai.json_validator import JsonValidator, Ok, ValidationError
from pa_agent.ai.prompts.schemas import STAGE1_SCHEMA, STAGE2_SCHEMA
from pa_agent.ai.stage1_normalizer import normalize_stage1
from pa_agent.ai.stage2_normalizer import normalize_stage2
from pa_agent.config.settings import PromptSettings, Settings
from tests.integration.conftest import VALID_STAGE1, VALID_STAGE2, make_frame


def _context(
    evidence_ids: list[str],
    *,
    relation: str = "supports",
    data_status: str = "available",
    interpretation: str = "Evidence adds an independent effort/result observation.",
    bar_range: str = "K1",
) -> dict:
    return {
        "catalog_id": "catalog",
        "data_status": data_status,
        "summary": "Source quality bounds the interpretation.",
        "observations": [
            {
                "bar_range": bar_range,
                "evidence_ids": evidence_ids,
                "finding_kind": "effort_result",
                "interpretation": interpretation,
            }
        ],
        "background_relation": relation,
        "entry_timing_relation": relation,
        "next_confirmation": {
            "state": "pending_closed_bar",
            "trigger": "Wait for the next closed bar and assess acceptance.",
            "requires_closed_bar": True,
        },
    }


def _impact(
    evidence_ids: list[str],
    *,
    relation: str = "supports",
    effect: str = "keep_brooks_baseline",
    reason: str = "The cited evidence leaves the Brooks timing unchanged.",
) -> dict:
    return {
        "background_relation": relation,
        "entry_timing_relation": relation,
        "evidence_used": evidence_ids,
        "decision_effect": effect,
        "reason": reason,
    }


def test_inference_narrative_allows_program_evidence_ids_but_not_numbers() -> None:
    assert validate_inference_only_narrative(
        "vpa-e001 and vpa-c002 support the contextual interpretation.",
        path="reason",
    ) == []
    assert validate_inference_only_narrative(
        "vpa-e001 reports volume 8500.",
        path="reason",
    )


def test_inference_narrative_does_not_treat_local_close_position_as_new_low() -> None:
    assert validate_inference_only_narrative(
        "K1 closed near its own lowest point, limiting the bullish result.",
        path="vpa_context.observations[0].interpretation",
    ) == []


def test_future_confirmation_accepts_an_explicit_closed_bar_anchor() -> None:
    assert validate_future_confirmation_narrative(
        "等待下一根已收盘K线接受并收于K1高点上方，否则视为未确认。",
        path="vpa_context.next_confirmation.trigger",
    ) == []
    assert validate_future_confirmation_narrative(
        "等待下一根已收盘 K 线接受并收于 K1 高点上方，否则视为未确认。",
        path="vpa_context.next_confirmation.trigger",
    ) == []


def test_future_confirmation_rejects_an_unanchored_new_high_condition() -> None:
    errors = validate_future_confirmation_narrative(
        "下一根已收盘K线需要创新高，否则可靠性降低。",
        path="vpa_context.next_confirmation.trigger",
    )

    assert any("explicit K-line or structure anchor" in error for error in errors)


def test_future_confirmation_requires_a_closed_bar_not_a_forming_bar() -> None:
    errors = validate_future_confirmation_narrative(
        "观察下一根K线能否站上K1高点。",
        path="vpa_context.next_confirmation.trigger",
    )

    assert any("future closed bar" in error for error in errors)


def test_future_confirmation_volume_condition_requires_a_comparator() -> None:
    errors = validate_future_confirmation_narrative(
        "等待下一根已收盘K线站上K1高点且成交量放大。",
        path="vpa_context.next_confirmation.trigger",
    )

    assert any("explicit comparator" in error for error in errors)
    assert validate_future_confirmation_narrative(
        "等待下一根已收盘K线站上K1高点，且相对因果基线的活动不减弱。",
        path="vpa_context.next_confirmation.trigger",
    ) == []


def test_stage1_normalizes_only_unambiguous_vpa_contract_aliases() -> None:
    raw = copy.deepcopy(VALID_STAGE1)
    raw["bar_by_bar_summary"][0]["context_effect"] = "weakened_bull"
    raw["vpa_context"] = _context(["v1"])
    raw["vpa_context"]["next_confirmation"]["state"] = "waiting"

    normalized = normalize_stage1(raw, vpa_enabled=True)

    assert normalized["bar_by_bar_summary"][0]["context_effect"] == "weakens_bull"
    assert (
        normalized["vpa_context"]["next_confirmation"]["state"]
        == "pending_closed_bar"
    )


def test_validator_audits_unambiguous_alias_normalization() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["vpa_context"] = _context(
        ["v1"],
        relation="neutral",
        data_status="limited",
        interpretation=(
            "K1 activity is above the causal baseline; resolution remains conditional."
        ),
    )
    stage1["vpa_context"]["next_confirmation"]["state"] = "waiting"
    result = JsonValidator(
        Settings(prompt=PromptSettings(vpa_mode="shadow"))
    ).validate(
        "stage1",
        json.dumps(stage1),
        kline_frame=make_frame(),
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "limited",
            "capabilities": {
                "actor_attribution": False,
                "interpretation_ceiling": "relative_activity_only",
            },
            "comparators": {"seasonality": {"market_session": "unclassified"}},
            "evidence": [
                {
                    "evidence_id": "v1",
                    "source": "volume",
                    "bar_range": "K1",
                    "facts": {"volume_vs_baseline_relation": "above"},
                }
            ],
        },
    )

    assert isinstance(result, Ok)
    assert result.obj["vpa_context"]["next_confirmation"]["state"] == "pending_closed_bar"
    assert result.normalization_events == [
        {
            "path": "vpa_context.next_confirmation.state",
            "before": "waiting",
            "after": "pending_closed_bar",
        }
    ]


def test_vpa_enabled_schema_rejects_null_required_outputs() -> None:
    settings = Settings(prompt=PromptSettings(vpa_mode="shadow"))
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["vpa_context"] = None
    stage1_result = JsonValidator(settings).validate(
        "stage1",
        json.dumps(stage1),
        kline_frame=make_frame(),
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "unavailable",
            "evidence": [],
        },
    )

    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["vpa_impact"] = None
    stage2_result = JsonValidator(settings).validate(
        "stage2",
        json.dumps(stage2),
        kline_frame=make_frame(),
        stage1_json=stage1,
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "unavailable",
            "evidence": [],
        },
        skip_next_bar=True,
    )

    assert isinstance(stage1_result, ValidationError)
    assert "vpa_context" in stage1_result.invalid_fields
    assert isinstance(stage2_result, ValidationError)
    assert "vpa_impact" in stage2_result.invalid_fields


def test_cross_window_close_extreme_is_not_misclassified_as_local_position() -> None:
    for text in (
        "K1 closed at the lowest price in the K5-K1 window.",
        "K1 closed at the low of the K5-K1 window.",
        "K1 收盘位于 K5-K1 窗口最低点。",
        "K1 收盘接近 K5-K1 窗口低点。",
    ):
        errors = validate_inference_only_narrative(
            text,
            path="vpa_context.observations[0].interpretation",
        )
        assert any("unbounded new-high/new-low" in error for error in errors)


def test_vpa_comparator_detects_k_reference_adjacent_to_chinese_text() -> None:
    errors = validate_vpa_comparator_narrative(
        "K1区间成交量高于因果基线。",
        evidence_items=[
            {
                "bar_range": "K1",
                "facts": {"volume_vs_baseline_relation": "below"},
            }
        ],
        path="vpa_context.summary",
    )

    assert any("K1" in error and "conflicts" in error for error in errors)


def test_vpa_comparator_binds_comma_delimited_clauses_to_their_own_bars() -> None:
    errors = validate_vpa_comparator_narrative(
        "K1 above causal baseline\uff0cK2 below causal baseline\u3002",
        evidence_items=[
            {
                "evidence_id": "vpa-e001",
                "bar_range": "K1",
                "facts": {"volume_vs_baseline_relation": "above"},
            },
            {
                "evidence_id": "vpa-e002",
                "bar_range": "K2",
                "facts": {"volume_vs_baseline_relation": "below"},
            },
        ],
        path="vpa_context.observations[0].interpretation",
    )

    assert errors == []


def test_vpa_comparator_binds_an_explicit_evidence_id_without_a_k_reference() -> None:
    errors = validate_vpa_comparator_narrative(
        "vpa-e001 below causal baseline.",
        evidence_items=[
            {
                "evidence_id": "vpa-e001",
                "bar_range": "K1",
                "facts": {"volume_vs_baseline_relation": "above"},
            }
        ],
        path="vpa_context.observations[0].interpretation",
    )

    assert any("vpa-e001" in error and "conflicts" in error for error in errors)


def test_vpa_comparator_keeps_a_selector_bound_across_intro_punctuation() -> None:
    evidence = [
        {
            "evidence_id": "vpa-e001",
            "bar_range": "K1",
            "facts": {"volume_vs_baseline_relation": "above"},
        }
    ]

    for separator in (",", ":", "\uFF0C", "\uFF1A"):
        errors = validate_vpa_comparator_narrative(
            f"vpa-e001{separator} below causal baseline.",
            evidence_items=evidence,
            path="vpa_context.observations[0].interpretation",
        )
        assert any("vpa-e001" in error and "conflicts" in error for error in errors)


def test_vpa_comparator_binds_period_delimited_sentences_independently() -> None:
    errors = validate_vpa_comparator_narrative(
        "K1 above causal baseline. K2 below causal baseline.",
        evidence_items=[
            {
                "evidence_id": "vpa-e001",
                "bar_range": "K1",
                "facts": {"volume_vs_baseline_relation": "above"},
            },
            {
                "evidence_id": "vpa-e002",
                "bar_range": "K2",
                "facts": {"volume_vs_baseline_relation": "below"},
            },
        ],
        path="vpa_context.observations[0].interpretation",
    )

    assert errors == []


def test_vpa_fields_are_declared_but_not_prediction_sources() -> None:
    assert "vpa_context" in STAGE1_SCHEMA["properties"]
    assert "vpa_impact" in STAGE2_SCHEMA["properties"]
    bar_sources = STAGE2_SCHEMA["properties"]["next_bar_prediction"]["properties"][
        "features_used"
    ]["items"]["enum"]
    cycle_sources = STAGE2_SCHEMA["properties"]["next_cycle_prediction"]["properties"][
        "features_used"
    ]["items"]["enum"]
    assert "vpa_context" not in bar_sources
    assert "vpa_measurements" not in cycle_sources


def test_stage_normalizers_strip_vpa_fields_when_disabled() -> None:
    stage1 = normalize_stage1(
        {"vpa_context": {"summary": "old"}, "wyckoff_phase": "candidate"},
        vpa_enabled=False,
    )
    stage2 = normalize_stage2(
        {
            "decision": {},
            "vpa_impact": {
                "background_relation": "supports",
                "entry_timing_relation": "supports",
            },
            "wyckoff_signal": "old",
        },
        vpa_enabled=False,
    )

    assert stage1 == {}
    assert stage2 == {"decision": {}}


def test_stage_normalizers_keep_vpa_fields_when_enabled_without_repair() -> None:
    context = {"summary": "kept as supplied"}
    impact = {"decision_effect": "lower_confidence"}

    assert normalize_stage1({"vpa_context": context}, vpa_enabled=True)["vpa_context"] == context
    assert normalize_stage2({"vpa_impact": impact}, vpa_enabled=True)["vpa_impact"] == impact


def test_vpa_enabled_validator_requires_both_output_contracts() -> None:
    validator = JsonValidator(Settings(prompt=PromptSettings(vpa_enabled=True)))
    stage1_result = validator.validate(
        "stage1",
        json.dumps(VALID_STAGE1, ensure_ascii=False),
        kline_frame=make_frame(),
    )
    stage2_result = validator.validate(
        "stage2",
        json.dumps(VALID_STAGE2, ensure_ascii=False),
        kline_frame=make_frame(),
        stage1_json=VALID_STAGE1,
    )

    assert isinstance(stage1_result, ValidationError)
    assert "vpa_context" in stage1_result.missing_fields
    assert isinstance(stage2_result, ValidationError)
    assert "vpa_impact" in stage2_result.missing_fields


def test_vpa_relations_require_program_volume_evidence() -> None:
    errors = JsonValidator._check_vpa_contract(
        "stage1",
        {
            "vpa_context": {
                "catalog_id": "catalog",
                "data_status": "available",
                "background_relation": "supports",
                "entry_timing_relation": "supports",
                "observations": [
                    {
                        "bar_range": "K1",
                        "evidence_ids": ["p1"],
                        "finding_kind": "effort_result",
                        "interpretation": "price",
                    }
                ],
            }
        },
        stage1_json=None,
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "available",
            "evidence": [{"evidence_id": "p1", "source": "price_context", "bar_range": "K1"}],
        },
    )

    assert any("require program volume evidence" in error for error in errors)


def test_vpa_rejects_actor_attribution_without_order_flow_capability() -> None:
    context = _context(
        ["v1"],
        interpretation="Institutional buyers absorbed all selling pressure.",
    )
    errors = JsonValidator._check_vpa_contract(
        "stage1",
        {"vpa_context": context},
        stage1_json=None,
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "available",
            "capabilities": {"actor_attribution": False},
            "evidence": [{"evidence_id": "v1", "source": "volume", "bar_range": "K1"}],
        },
    )

    assert any("volume provenance cannot support actor attribution" in error for error in errors)


def test_proxy_volume_rejects_certain_active_supply_or_demand_claims() -> None:
    context = _context(
        ["v1"],
        data_status="limited",
        interpretation="The proxy proves active demand at K1.",
    )
    errors = JsonValidator._check_vpa_contract(
        "stage1",
        {"vpa_context": context},
        stage1_json=None,
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "limited",
            "capabilities": {
                "actor_attribution": False,
                "interpretation_ceiling": "relative_activity_only",
            },
            "evidence": [{"evidence_id": "v1", "source": "volume", "bar_range": "K1"}],
        },
    )

    assert any("relative activity only" in error for error in errors)


def test_proxy_volume_rejects_certain_directional_actor_effort_claims() -> None:
    context = _context(
        ["v1"],
        data_status="limited",
        interpretation="K1相对活动高于因果基线，表明多头全力推动。",
    )
    errors = JsonValidator._check_vpa_contract(
        "stage1",
        {"vpa_context": context},
        stage1_json=None,
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "limited",
            "capabilities": {
                "actor_attribution": False,
                "interpretation_ceiling": "relative_activity_only",
            },
            "evidence": [
                {
                    "evidence_id": "v1",
                    "source": "volume",
                    "bar_range": "K1",
                    "facts": {"volume_vs_baseline_relation": "above"},
                }
            ],
        },
    )

    assert any("relative activity only" in error for error in errors)


def test_vpa_confirmation_cannot_use_forming_or_unclosed_bar() -> None:
    for trigger in (
        "Use K0 forming bar as confirmation.",
        "\u7b49\u5f85K0\u786e\u8ba4\u3002",
    ):
        context = _context(["v1"])
        context["next_confirmation"]["trigger"] = trigger
        errors = JsonValidator._check_vpa_contract(
            "stage1",
            {"vpa_context": context},
            stage1_json=None,
            vpa_evidence_catalog={
                "catalog_id": "catalog",
                "data_status": "available",
                "evidence": [
                    {"evidence_id": "v1", "source": "volume", "bar_range": "K1"}
                ],
            },
        )

        assert any("must wait for a future closed bar" in error for error in errors)


def test_available_vpa_requires_information_beyond_pa_restatement() -> None:
    context = _context(["v1"])
    context["observations"][0]["finding_kind"] = "background"
    context["observations"][0]["interpretation"] = "PA already calls this a trend."
    errors = JsonValidator._check_vpa_contract(
        "stage1",
        {"vpa_context": context},
        stage1_json=None,
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "available",
            "evidence": [{"evidence_id": "v1", "source": "volume", "bar_range": "K1"}],
        },
    )

    assert any(
        "available VPA context requires an independent price-volume finding" in error
        for error in errors
    )


def test_grounded_effort_result_observation_is_accepted() -> None:
    errors = JsonValidator._check_vpa_contract(
        "stage1",
        {"vpa_context": _context(["v1"])},
        stage1_json=None,
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "available",
            "capabilities": {"actor_attribution": False},
            "comparators": {"seasonality": {"market_session": "unclassified"}},
            "evidence": [
                {
                    "evidence_id": "v1",
                    "source": "volume",
                    "bar_range": "K1",
                    "facts": {
                        "volume_available": True,
                        "volume_vs_previous_relation": "below",
                    },
                }
            ],
        },
    )

    assert errors == []


def test_raw_volume_without_a_comparator_is_not_vpa_information_increment() -> None:
    errors = JsonValidator._check_vpa_contract(
        "stage1",
        {"vpa_context": _context(["v1"])},
        stage1_json=None,
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "available",
            "comparators": {"seasonality": {"market_session": "unclassified"}},
            "evidence": [
                {
                    "evidence_id": "v1",
                    "source": "volume",
                    "bar_range": "K1",
                    "facts": {
                        "volume_available": True,
                        "volume_vs_baseline_relation": "unavailable",
                        "volume_vs_previous_relation": "unavailable",
                        "volume_vs_seasonality_relation": "unavailable",
                    },
                }
            ],
        },
    )

    assert any(
        "available VPA context requires an independent price-volume finding" in error
        for error in errors
    )


def test_non_baseline_vpa_effect_requires_relative_volume_evidence() -> None:
    stage1 = {"vpa_context": _context(["v1"], relation="challenges")}
    errors = JsonValidator._check_vpa_contract(
        "stage2",
        {
            "vpa_impact": _impact(
                ["v1"],
                relation="challenges",
                effect="lower_confidence",
            )
        },
        stage1_json=stage1,
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "available",
            "evidence": [
                {
                    "evidence_id": "v1",
                    "source": "volume",
                    "bar_range": "K1",
                    "facts": {
                        "volume_available": True,
                        "volume_vs_previous_relation": "unavailable",
                    },
                }
            ],
        },
    )

    assert any(
        "non-baseline decision_effect requires relative-volume evidence" in error
        for error in errors
    )


def test_neutral_or_insufficient_vpa_cannot_change_brooks_baseline() -> None:
    catalog = {
        "catalog_id": "catalog",
        "data_status": "available",
        "evidence": [{"evidence_id": "v1", "source": "volume", "bar_range": "K1"}],
    }
    for relation in ("neutral", "insufficient_data"):
        stage1 = {"vpa_context": _context(["v1"], relation=relation)}
        errors = JsonValidator._check_vpa_contract(
            "stage2",
            {
                "vpa_impact": {
                    "background_relation": relation,
                    "entry_timing_relation": relation,
                    "evidence_used": ["v1"],
                    "decision_effect": "lower_confidence",
                }
            },
            stage1_json=stage1,
            vpa_evidence_catalog=catalog,
        )
        assert any(
            "neutral/insufficient_data timing must keep_brooks_baseline" in error
            for error in errors
        )


def test_vpa_context_cannot_claim_evidence_outside_declared_range() -> None:
    errors = JsonValidator._check_vpa_contract(
        "stage1",
        {
            "vpa_context": {
                "catalog_id": "catalog",
                "data_status": "limited",
                "background_relation": "neutral",
                "entry_timing_relation": "neutral",
                "summary": "test",
                "next_confirmation": {
                    "state": "pending_closed_bar",
                    "trigger": "Wait for the next closed bar.",
                    "requires_closed_bar": True,
                },
                "observations": [
                    {
                        "bar_range": "K5-K1",
                        "evidence_ids": ["v8"],
                        "finding_kind": "sequence_comparison",
                        "interpretation": "\u89c2\u5bdfK8\u4ecd\u5728\u8303\u56f4\u5916",
                    }
                ],
            }
        },
        stage1_json=None,
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "limited",
            "evidence": [{"evidence_id": "v8", "source": "volume", "bar_range": "K8"}],
        },
    )

    assert any(
        "evidence_id v8 is outside observation bar_range K5-K1" in error
        for error in errors
    )
    assert any(
        "interpretation references K8 outside observation bar_range K5-K1" in error
        for error in errors
    )


def test_paired_vpa_stage1_accepts_sidecar_only_and_materializes_evidence_range() -> None:
    validator = JsonValidator(Settings(prompt=PromptSettings(vpa_enabled=True)))
    catalog = {
        "catalog_id": "vpa-catalog-test",
        "data_status": "limited",
        "capabilities": {
            "actor_attribution": False,
            "interpretation_ceiling": "relative_activity_only",
        },
        "comparators": {"seasonality": {"market_session": "unclassified"}},
        "evidence": [
            {
                "evidence_id": "vpa-c001",
                "source": "volume",
                "bar_range": "K6-K1",
                "facts": {
                    "volume_comparison_available": True,
                    "current_vs_prior_volume_relation": "below",
                },
            }
        ],
    }
    raw = {
        "vpa_context": {
            "catalog_id": "vpa-catalog-test",
            "data_status": "limited",
            "summary": "The source is limited, so the interpretation is conditional.",
            "observations": [
                {
                    "bar_range": "K3-K1",
                    "evidence_ids": ["vpa-c001"],
                    "finding_kind": "sequence_comparison",
                    "interpretation": "The later sequence shows less effort, but resolution is pending.",
                }
            ],
            "background_relation": "neutral",
            "entry_timing_relation": "neutral",
            "next_confirmation": {
                "state": "pending_closed_bar",
                "trigger": "Wait for the next closed bar and assess acceptance.",
                "requires_closed_bar": True,
            },
        }
    }

    result = validator.validate(
        "stage1",
        json.dumps(raw),
        kline_frame=make_frame(),
        vpa_evidence_catalog=catalog,
        paired_baseline_stage1=VALID_STAGE1,
    )

    assert isinstance(result, Ok)
    assert result.obj["cycle_position"] == VALID_STAGE1["cycle_position"]
    observation = result.obj["vpa_context"]["observations"][0]
    assert observation["bar_range"] == "K6-K1"


def test_vpa_narrative_rejects_unverifiable_numeric_restatements() -> None:
    errors = JsonValidator._check_vpa_contract(
        "stage1",
        {
            "vpa_context": {
                "catalog_id": "catalog",
                "data_status": "limited",
                "background_relation": "neutral",
                "entry_timing_relation": "neutral",
                "summary": "Source quality is limited.",
                "next_confirmation": {
                    "state": "pending_closed_bar",
                    "trigger": "Wait for the next closed bar.",
                    "requires_closed_bar": True,
                },
                "observations": [
                    {
                        "bar_range": "K4",
                        "evidence_ids": ["v4"],
                        "finding_kind": "effort_result",
                        "interpretation": "K4 volume was 8500 and therefore buying was weak.",
                    }
                ],
            }
        },
        stage1_json=None,
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "limited",
            "comparators": {"seasonality": {"market_session": "unclassified"}},
            "evidence": [{"evidence_id": "v4", "source": "volume", "bar_range": "K4"}],
        },
    )

    assert any("numeric market facts" in error for error in errors)


def test_vpa_narrative_rejects_unbounded_new_high_or_low_claims() -> None:
    errors = JsonValidator._check_vpa_contract(
        "stage1",
        {
            "vpa_context": {
                "catalog_id": "catalog",
                "data_status": "limited",
                "background_relation": "neutral",
                "entry_timing_relation": "neutral",
                "summary": "Source quality is limited.",
                "next_confirmation": {
                    "state": "pending_closed_bar",
                    "trigger": "Wait for the next closed bar.",
                    "requires_closed_bar": True,
                },
                "observations": [
                    {
                        "bar_range": "K1",
                        "evidence_ids": ["v1"],
                        "finding_kind": "effort_result",
                        "interpretation": "K1 created a new low, so sellers remain dominant.",
                    }
                ],
            }
        },
        stage1_json=None,
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "limited",
            "comparators": {"seasonality": {"market_session": "unclassified"}},
            "evidence": [{"evidence_id": "v1", "source": "volume", "bar_range": "K1"}],
        },
    )

    assert any("unbounded new-high/new-low" in error for error in errors)


def test_paired_vpa_stage2_freezes_win_rate_and_brooks_diagnosis() -> None:
    baseline = copy.deepcopy(VALID_STAGE2)
    candidate = copy.deepcopy(baseline)
    candidate["decision"]["estimated_win_rate"] = (
        int(baseline["decision"]["estimated_win_rate"]) + 1
    )
    candidate["vpa_impact"] = _impact(["v1"])
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["vpa_context"] = _context(["v1"])
    validator = JsonValidator(Settings(prompt=PromptSettings(vpa_enabled=True)))

    raw_delta = {
        "decision": candidate["decision"],
        "vpa_impact": candidate["vpa_impact"],
    }
    result = validator.validate(
        "stage2",
        json.dumps(raw_delta),
        kline_frame=make_frame(),
        stage1_json=stage1,
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "available",
            "evidence": [{"evidence_id": "v1", "source": "volume", "bar_range": "K1"}],
        },
        paired_baseline_stage2=baseline,
    )

    assert isinstance(result, ValidationError)
    assert any("estimated_win_rate" in field for field in result.invalid_fields)


def test_paired_vpa_stage2_accepts_impact_only_and_merges_frozen_baseline() -> None:
    baseline = copy.deepcopy(VALID_STAGE2)
    delta = {
        "vpa_impact": _impact(["v1"])
    }
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["vpa_context"] = _context(["v1"])
    validator = JsonValidator(Settings(prompt=PromptSettings(vpa_enabled=True)))

    result = validator.validate(
        "stage2",
        json.dumps(delta),
        kline_frame=make_frame(),
        stage1_json=stage1,
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "available",
            "evidence": [{"evidence_id": "v1", "source": "volume", "bar_range": "K1"}],
        },
        paired_baseline_stage2=baseline,
    )

    assert isinstance(result, Ok)
    assert result.obj["decision"] == baseline["decision"]
    assert result.obj["next_cycle_prediction"] == baseline["next_cycle_prediction"]
    assert result.obj["vpa_impact"] == delta["vpa_impact"]


def test_paired_vpa_stage2_ignores_runtime_execution_policy_metadata() -> None:
    baseline = copy.deepcopy(VALID_STAGE2)
    baseline["confidence_threshold"] = {
        "configured": 80,
        "trade_confidence": 70,
        "result": "rejected",
    }
    delta = {
        "vpa_impact": _impact(["v1"])
    }
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["vpa_context"] = _context(["v1"])
    validator = JsonValidator(Settings(prompt=PromptSettings(vpa_enabled=True)))

    result = validator.validate(
        "stage2",
        json.dumps(delta),
        kline_frame=make_frame(),
        stage1_json=stage1,
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "available",
            "evidence": [{"evidence_id": "v1", "source": "volume", "bar_range": "K1"}],
        },
        paired_baseline_stage2=baseline,
    )

    assert isinstance(result, Ok)
    assert "confidence_threshold" not in result.obj


def test_vpa_comparator_claim_must_match_each_cited_bar() -> None:
    errors = JsonValidator._check_vpa_contract(
        "stage1",
        {
            "vpa_context": {
                "catalog_id": "catalog",
                "data_status": "available",
                "background_relation": "supports",
                "entry_timing_relation": "supports",
                "summary": "Source quality is usable.",
                "next_confirmation": {
                    "state": "pending_closed_bar",
                    "trigger": "Wait for the next closed bar.",
                    "requires_closed_bar": True,
                },
                "observations": [
                    {
                        "bar_range": "K2-K1",
                        "evidence_ids": ["v1", "v2"],
                        "finding_kind": "sequence_comparison",
                        "interpretation": "K2 and K1 are mostly below the causal baseline.",
                    }
                ],
            }
        },
        stage1_json=None,
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "available",
            "comparators": {"seasonality": {"market_session": "unclassified"}},
            "evidence": [
                {
                    "evidence_id": "v1",
                    "source": "volume",
                    "bar_range": "K1",
                    "facts": {"volume_vs_baseline_relation": "below"},
                },
                {
                    "evidence_id": "v2",
                    "source": "volume",
                    "bar_range": "K2",
                    "facts": {"volume_vs_baseline_relation": "above"},
                },
            ],
        },
    )

    assert any("causal baseline" in error and "K2" in error for error in errors)


def test_vpa_volume_language_requires_an_explicit_comparator() -> None:
    errors = JsonValidator._check_vpa_contract(
        "stage2",
        {
            "vpa_impact": {
                "background_relation": "supports",
                "entry_timing_relation": "supports",
                "evidence_used": ["v1"],
                "decision_effect": "keep_brooks_baseline",
                "reason": "v1 shows a low-volume rebound, so keep the plan.",
            }
        },
        stage1_json={
            "vpa_context": {"observations": [{"evidence_ids": ["v1"]}]}
        },
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "available",
            "evidence": [
                {
                    "evidence_id": "v1",
                    "source": "volume",
                    "bar_range": "K1",
                    "facts": {"volume_vs_baseline_relation": "above"},
                }
            ],
        },
    )

    assert any("explicit comparator" in error for error in errors)


def test_vpa_stage2_must_cite_the_evidence_it_arbitrated() -> None:
    errors = JsonValidator._check_vpa_contract(
        "stage2",
        {
            "vpa_impact": {
                "background_relation": "neutral",
                "entry_timing_relation": "neutral",
                "evidence_used": [],
                "decision_effect": "keep_brooks_baseline",
                "reason": "The Brooks baseline remains unchanged.",
            }
        },
        stage1_json={
            "gate_result": "proceed",
            "vpa_context": {
                "background_relation": "neutral",
                "entry_timing_relation": "neutral",
                "observations": [{"evidence_ids": ["v1"]}],
            },
        },
        vpa_evidence_catalog={
            "catalog_id": "catalog",
            "data_status": "available",
            "evidence": [
                {"evidence_id": "v1", "source": "volume", "bar_range": "K1"}
            ],
        },
    )

    assert any(
        "VPA Stage 2 must cite evidence_used when the PA gate proceeds" in error
        for error in errors
    )


def test_paired_vpa_keep_baseline_freezes_full_decision_and_trace() -> None:
    baseline = copy.deepcopy(VALID_STAGE2)
    candidate = copy.deepcopy(baseline)
    candidate["decision"]["reasoning"] = "VPA rewrote the Brooks reasoning."
    candidate["decision_trace"][0]["reason"] = "VPA rewrote the trace."
    candidate["vpa_impact"] = {
        "decision_effect": "keep_brooks_baseline",
    }

    errors = JsonValidator._check_paired_vpa_stage2_contract(candidate, baseline)

    assert any(error.startswith("decision must equal baseline") for error in errors)
    assert any(error.startswith("decision_trace must equal baseline") for error in errors)


def test_paired_vpa_stage2_cannot_create_trade_from_no_order_baseline() -> None:
    baseline = {"decision": {"order_type": "不下单"}}
    candidate = {
        "decision": {"order_type": "限价单"},
        "vpa_impact": {"decision_effect": "prefer_retest"},
    }

    errors = JsonValidator._check_paired_vpa_stage2_contract(candidate, baseline)

    assert "decision.order_type cannot create a trade from a no-order baseline" in errors


def test_paired_vpa_non_baseline_effect_must_change_existing_decision_fields() -> None:
    baseline = copy.deepcopy(VALID_STAGE2)
    candidate = copy.deepcopy(baseline)
    candidate["vpa_impact"] = {
        "decision_effect": "prefer_retest",
    }

    errors = JsonValidator._check_paired_vpa_stage2_contract(candidate, baseline)

    assert (
        "non-baseline decision_effect must be applied to existing Stage 2 fields"
        in errors
    )


def test_require_confirmation_must_change_timing_not_only_confidence() -> None:
    baseline = copy.deepcopy(VALID_STAGE2)
    candidate = copy.deepcopy(baseline)
    candidate["decision"]["trade_confidence"] -= 5
    candidate["vpa_impact"] = {"decision_effect": "require_confirmation"}

    errors = JsonValidator._check_paired_vpa_stage2_contract(candidate, baseline)

    assert "decision_effect=require_confirmation must change entry timing fields" in errors
