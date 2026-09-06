from __future__ import annotations

import pytest

from pa_agent.experiments.backend_review import (
    QUALITY_WEIGHTS,
    score_blinded_backend_review,
    unblind_backend_scores,
)


def test_blind_scores_use_preregistered_weights_and_unblind_after_scoring() -> None:
    packet = {"cases": [{"blind_id": "x"}, {"blind_id": "y"}]}
    ratings = [
        {"blind_id": "x", **{name: 5 for name in QUALITY_WEIGHTS}},
        {"blind_id": "y", **{name: 4 for name in QUALITY_WEIGHTS}},
    ]

    scored = score_blinded_backend_review(packet, ratings)
    result = unblind_backend_scores(
        scored,
        {
            "cases": [
                {"blind_id": "x", "group": "A", "assigned_backend": "deepseek_direct"},
                {"blind_id": "y", "group": "B", "assigned_backend": "codex_agent"},
            ]
        },
    )

    assert result["groups"]["A"]["mean_weighted_score_0_100"] == 100
    assert result["groups"]["B"]["mean_weighted_score_0_100"] == 80
    assert result["codex_minus_direct_score"] == -20


def test_blind_score_rejects_missing_case() -> None:
    with pytest.raises(ValueError, match="exactly once"):
        score_blinded_backend_review(
            {"cases": [{"blind_id": "x"}, {"blind_id": "y"}]},
            [{"blind_id": "x", **{name: 5 for name in QUALITY_WEIGHTS}}],
        )
