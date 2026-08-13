"""Performance contracts for the optional next-bar prediction payload."""
from __future__ import annotations

import time
from statistics import median

from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.ai.token_counter import estimate_tokens
from pa_agent.config.paths import PROMPT_DIR
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame


def _make_frame(n: int = 50) -> KlineFrame:
    bars = tuple(
        KlineBar(
            seq=i + 1,
            ts_open=float(1_700_000_000 - i * 3600),
            open=2600.0 + i,
            high=2610.0 + i,
            low=2590.0 + i,
            close=2605.0 + i,
            volume=1000.0,
            closed=True,
        )
        for i in range(n)
    )
    indicators = IndicatorBundle(
        ema20=tuple(2600.0 + i for i in range(n)),
        atr14=tuple(5.0 for _ in range(n)),
    )
    return KlineFrame(
        symbol="XAUUSD",
        timeframe="1h",
        bars=bars,
        indicators=indicators,
        snapshot_ts_local_ms=1_700_000_000_000,
    )


def _stage1() -> dict[str, str]:
    return {
        "cycle_position": "normal_channel",
        "direction": "bullish",
        "gate_result": "proceed",
    }


def _stage2_messages(
    assembler: PromptAssembler,
    frame: KlineFrame,
    *,
    prediction_enabled: bool,
) -> list[dict]:
    return assembler.build_stage2_continuation(
        frame=frame,
        stage1_messages=[],
        stage1_reply_content="",
        stage1_json=_stage1(),
        strategy_files=[],
        experience_entries=[],
        enable_next_bar_prediction=prediction_enabled,
        use_prefix_chain=False,
    )


def test_prompt_token_delta_within_budget() -> None:
    """The enabled payload adds no more than 800 tokenizer-counted tokens."""
    assembler = PromptAssembler(PROMPT_DIR)
    frame = _make_frame()
    disabled = _stage2_messages(
        assembler,
        frame,
        prediction_enabled=False,
    )
    enabled = _stage2_messages(
        assembler,
        frame,
        prediction_enabled=True,
    )

    token_delta = estimate_tokens(enabled) - estimate_tokens(disabled)

    assert 0 < token_delta <= 800


def test_panel_render_time(qtbot) -> None:
    """Rendering a decision with prediction stays below 50 ms per update."""
    from pa_agent.gui.decision_panel import DecisionPanel
    from pa_agent.gui.stage2_payload import merge_stage2_for_panels

    panel = DecisionPanel()
    qtbot.addWidget(panel)
    stage2 = {
        "decision": {
            "order_type": "\u4e0d\u4e0b\u5355",
            "order_direction": None,
            "entry_price": None,
            "take_profit_price": None,
            "stop_loss_price": None,
            "reasoning": "test",
            "diagnosis_confidence": 40,
            "diagnosis_confidence_reasoning": "test",
            "trade_confidence": 30,
            "trade_confidence_reasoning": "test",
            "estimated_win_rate": None,
            "estimated_win_rate_reasoning": "test",
            "key_factors": [],
            "watch_points": [],
            "risk_assessment": "test",
            "invalidation_condition": "test",
        },
        "decision_trace": [
            {
                "node_id": "10.3",
                "question": "q",
                "answer": "\u5426",
                "reason": "r",
                "bar_range": "K1",
            }
        ],
        "terminal": {"node_id": "10.3", "outcome": "wait", "label": "test"},
        "next_bar_prediction": {
            "direction": "bullish",
            "probabilities": {"bullish": 70, "bearish": 20, "neutral": 10},
            "reasoning": "bullish structure" * 5,
            "unpredictable": False,
            "features_used": ["stage1_diagnosis"],
        },
    }
    decision = merge_stage2_for_panels(stage2)
    stage1 = {**_stage1(), "key_signals": []}
    panel.set_decision(decision, stage1_diagnosis=stage1)

    start = time.perf_counter()
    for _ in range(20):
        panel.set_decision(decision, stage1_diagnosis=stage1)
    elapsed = (time.perf_counter() - start) / 20

    assert elapsed < 0.05


def test_stage2_prompt_assembly_overhead() -> None:
    """Adding the optional payload stays within 15% plus 0.5 ms jitter."""
    assembler = PromptAssembler(PROMPT_DIR)
    frame = _make_frame()
    _stage2_messages(
        assembler,
        frame,
        prediction_enabled=False,
    )
    _stage2_messages(
        assembler,
        frame,
        prediction_enabled=True,
    )

    def measure(enabled: bool) -> float:
        start = time.perf_counter()
        for _ in range(30):
            _stage2_messages(
                assembler,
                frame,
                prediction_enabled=enabled,
            )
        return (time.perf_counter() - start) / 30

    baseline = median(measure(False) for _ in range(7))
    enabled = median(measure(True) for _ in range(7))

    assert enabled <= baseline * 1.15 + 0.0005
