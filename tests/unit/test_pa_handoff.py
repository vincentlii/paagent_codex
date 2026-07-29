from __future__ import annotations

import copy

import pytest

from pa_agent.ai.pa_handoff import compile_pa_handoff
from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.config.paths import PROMPT_DIR
from pa_agent.config.settings import PromptSettings
from tests.integration.conftest import VALID_STAGE1, VALID_STAGE2, make_frame


def _planned_limit() -> dict:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"].update(
        {
            "order_type": "限价单",
            "entry_price": 2035.0,
            "entry_basis_bar": None,
            "entry_basis_extreme": None,
            "entry_rule": None,
            "stop_loss_price": 2025.0,
            "take_profit_price": 2055.0,
            "take_profit_price_2": 2070.0,
            "estimated_win_rate": 55,
            "invalidation_condition": None,
        }
    )
    stage2["bar_analysis"]["entry_bar"] = {
        "bar": None,
        "strength": "not_triggered",
        "follow_through": None,
        "still_valid": None,
        "freshness": "pending",
    }
    return stage2


def test_limit_plan_without_entry_rule_compiles_one_structured_candidate() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["entry_setup"] = ""

    handoff = compile_pa_handoff(stage1, _planned_limit(), make_frame())

    assert len(handoff.setup_candidates) == 1
    candidate = handoff.setup_candidates[0]
    assert candidate.setup is None
    assert candidate.trigger is None
    assert candidate.order_type == "限价单"
    assert candidate.order_direction == "做多"
    assert candidate.entry_price == 2035.0
    assert candidate.stop_loss_price == 2025.0
    assert candidate.take_profit_price == 2055.0
    assert candidate.take_profit_price_2 == 2070.0
    assert candidate.invalidation is None
    assert candidate.timing_state == "waiting"
    assert candidate.trade_math.risk == 10.0
    assert candidate.trade_math.reward_to_tp1 == 20.0
    assert candidate.trade_math.reward_risk_ratio == 2.0
    assert candidate.trade_math.break_even_win_rate_pct == pytest.approx(100 / 3)
    assert candidate.trade_math.estimated_expectancy == pytest.approx(6.5)
    assert candidate.trade_math.win_rate_provenance == "llm_subjective_prior"
    assert candidate.trade_math.calibration_id is None
    assert [item.fact_id for item in candidate.window_context] == [
        "PA.WINDOW.K5"
    ]
    assert candidate.window_context[0].entry_fraction_from_low == pytest.approx(
        (2035.0 - 2020.0) / (2048.0 - 2020.0)
    )
    assert handoff.price_snapshot_hash


@pytest.mark.parametrize(
    ("order_type", "entry_rule", "basis_bar", "basis_extreme", "timing_state"),
    [
        ("市价单", None, None, None, "eligible"),
        ("突破单", "above K2 high", "K2", "high", "eligible"),
    ],
)
def test_market_and_breakout_plans_compile_without_inventing_semantics(
    order_type: str,
    entry_rule: str | None,
    basis_bar: str | None,
    basis_extreme: str | None,
    timing_state: str,
) -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"].update(
        {
            "order_type": order_type,
            "entry_rule": entry_rule,
            "entry_basis_bar": basis_bar,
            "entry_basis_extreme": basis_extreme,
        }
    )

    candidate = compile_pa_handoff(
        VALID_STAGE1,
        stage2,
        make_frame(),
    ).setup_candidates[0]

    assert candidate.order_type == order_type
    assert candidate.trigger == entry_rule
    assert candidate.entry_basis_bar == basis_bar
    assert candidate.entry_basis_extreme == basis_extreme
    assert candidate.timing_state == timing_state


def test_no_order_compiles_no_candidate_and_does_not_invent_setup_text() -> None:
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["entry_setup"] = ""
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"].update(
        {
            "order_type": "不下单",
            "order_direction": None,
            "entry_price": None,
            "entry_basis_bar": None,
            "entry_basis_extreme": None,
            "entry_rule": None,
            "stop_loss_price": None,
            "take_profit_price": None,
            "take_profit_price_2": None,
            "trade_confidence": None,
            "estimated_win_rate": None,
            "estimated_win_rate_reasoning": None,
        }
    )

    handoff = compile_pa_handoff(stage1, stage2, make_frame())

    assert handoff.setup_candidates == []
    assert handoff.primary.setup is None
    assert "No current PA setup" not in handoff.primary.thesis


def test_handoff_digest_covers_prices_and_frozen_price_snapshot() -> None:
    first = compile_pa_handoff(VALID_STAGE1, VALID_STAGE2, make_frame())
    changed_stage2 = copy.deepcopy(VALID_STAGE2)
    changed_stage2["decision"]["entry_price"] = 2051.0
    changed = compile_pa_handoff(VALID_STAGE1, changed_stage2, make_frame())

    assert first.source_digest != changed.source_digest


def test_executable_plan_with_missing_structured_price_fails_loudly() -> None:
    stage2 = copy.deepcopy(VALID_STAGE2)
    stage2["decision"]["entry_price"] = None

    with pytest.raises(ValueError, match="validated executable PA plan"):
        compile_pa_handoff(VALID_STAGE1, stage2, make_frame())


def test_compiled_order_facts_reach_the_vpa_prompt() -> None:
    frame = make_frame()
    handoff = compile_pa_handoff(VALID_STAGE1, _planned_limit(), frame)
    assembler = PromptAssembler(
        PROMPT_DIR,
        prompt_settings=PromptSettings(vpa_mode="shadow"),
    )

    messages = assembler.build_vpa_evidence_report(
        frame,
        VALID_STAGE1,
        handoff.model_dump(mode="json"),
    )
    prompt = "\n".join(str(message["content"]) for message in messages)

    assert '"entry_price": 2035.0' in prompt
    assert '"win_rate_provenance": "llm_subjective_prior"' in prompt
    assert '"fact_id": "PA.WINDOW.K5"' in prompt
