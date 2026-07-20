from __future__ import annotations

from hypothesis import given, strategies as st

from pa_agent.ai.stage2_normalizer import normalize_stage2


@given(
    bullish=st.integers(min_value=-100, max_value=200),
    bearish=st.integers(min_value=-100, max_value=200),
    neutral=st.integers(min_value=-100, max_value=200),
    direction=st.sampled_from(["bullish", "bearish", "neutral", "invalid"]),
)
def test_normalizer_never_repairs_model_prediction(
    bullish: int,
    bearish: int,
    neutral: int,
    direction: str,
) -> None:
    prediction = {
        "direction": direction,
        "probabilities": {
            "bullish": bullish,
            "bearish": bearish,
            "neutral": neutral,
        },
        "reasoning": "property fixture",
        "unpredictable": False,
        "features_used": ["stage1_diagnosis"],
    }
    payload = {
        "decision": {},
        "decision_trace": [],
        "next_bar_prediction": prediction,
    }

    out = normalize_stage2(payload)

    assert out["next_bar_prediction"] == prediction
