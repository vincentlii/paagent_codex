from __future__ import annotations

from hypothesis import given, strategies as st

from pa_agent.ai.stage2_normalizer import normalize_stage2


@given(
    bullish=st.integers(min_value=-100, max_value=200),
    bearish=st.integers(min_value=-100, max_value=200),
    neutral=st.integers(min_value=-100, max_value=200),
    direction=st.sampled_from(["bullish", "bearish", "neutral", "invalid"]),
)
def test_normalizer_canonicalizes_or_degrades_auxiliary_prediction(
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

    normalized = out["next_bar_prediction"]
    values = (bullish, bearish, neutral)
    if all(0 <= value <= 100 for value in values) and 99 <= sum(values) <= 101:
        maximum = max(values)
        winners = [
            key
            for key, value in prediction["probabilities"].items()
            if value == maximum
        ]
        assert normalized["unpredictable"] is False
        assert normalized["direction"] in winners
        assert normalized["probabilities"] == prediction["probabilities"]
    else:
        assert normalized["unpredictable"] is True
        assert normalized["direction"] is None
        assert normalized["probabilities"] is None
