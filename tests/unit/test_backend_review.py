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


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_blind_scores_reject_non_finite_or_boolean_ratings(value):
    rating = {"blind_id": "x", **{name: 3 for name in QUALITY_WEIGHTS}}
    rating["market_context"] = value
    with pytest.raises(ValueError):
        score_blinded_backend_review({"cases": [{"blind_id": "x"}]}, [rating])


def review_evidence():
    from pa_agent.experiments.backend_review import freeze_review_evidence
    from tests.unit.test_backend_aabb_protocol import _record, _frozen

    record = _record("private-run", "direct")
    frozen = _frozen()
    record.snapshot_context = frozen.snapshot_context
    from pa_agent.data.contracts import ResolvedInstrumentIdentity

    record.snapshot_context.update(
        ResolvedInstrumentIdentity(
            provider="fixture",
            requested_symbol="XAUUSD",
            resolved_symbol="XAUUSD",
            venue="TEST",
            instrument_type="spot",
        ).to_metadata()
    )
    record.htf_context = frozen.htf_snapshot_context
    record.htf_kline_data = list(frozen.htf_kline_data)
    record.role_runtime["analysis_as_of_ms"] = 2_000_000_000_000
    # Historical fixture timestamps are synthetic seconds, not persisted UTC ms.
    for bars, step in ((record.kline_data, 3_600_000), (record.htf_kline_data, 14_400_000)):
        for index, bar in enumerate(bars):
            bar["ts_open"] = 1_800_000_000_000 - index * step
    from pa_agent.experiments.replay import frozen_input_from_record
    from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog

    frame, _, _ = frozen_input_from_record(record)
    record.vpa_catalog = build_vpa_evidence_catalog(frame)
    return freeze_review_evidence(
        record,
        knowledge={"pa/core.md": "full fixture knowledge"},
        measurements={"fixture_measurement": 1},
    )


def test_evidence_packet_has_full_facts_and_no_future_outcomes():
    evidence = review_evidence()
    assert len(evidence["kline_data"]) == 20
    assert evidence["htf_kline_data"]
    assert evidence["vpa_catalog"]["evidence"]
    assert evidence["knowledge"]["pa/core.md"] == "full fixture knowledge"
    assert "outcome" not in evidence and "run_id" not in evidence


def test_v2_scores_keep_unscorable_coverage_and_separate_vpa_dimensions():
    from pa_agent.experiments.backend_review import VPA_WEIGHTS

    packet = {"schema_version": 2, "cases": [{"blind_id": "x"}, {"blind_id": "y"}]}
    scored = score_blinded_backend_review(
        packet,
        [
            {
                "blind_id": "x",
                **{name: 4 for name in QUALITY_WEIGHTS},
                "vpa": {name: 3 for name in VPA_WEIGHTS},
                "error_kind": "reasonable_disagreement",
            },
            {"blind_id": "y", "review_status": "unscorable", "reviewer_notes": "missing output"},
        ],
    )
    assert scored["coverage"] == {"assigned": 2, "rated": 1, "unscorable": 1}
    assert scored["scored_cases"][0]["vpa_score_0_100"] == 60
    assert scored["scored_cases"][1]["weighted_score_0_100"] is None


def test_duplicate_packet_cases_cannot_hide_missing_ratings():
    with pytest.raises(ValueError, match="case"):
        score_blinded_backend_review(
            {"schema_version": 2, "cases": [{"blind_id": "x"}, {"blind_id": "x"}]},
            [
                {
                    "blind_id": "x",
                    **{name: 4 for name in QUALITY_WEIGHTS},
                    "vpa_not_applicable_reason": "PA-only",
                }
            ],
        )


def test_registered_evidence_rejects_future_bars_even_with_recomputed_digest():
    from pa_agent.experiments.backend_review import validate_review_evidence
    from pa_agent.experiments.study_protocol import digest

    evidence = review_evidence()
    evidence["as_of_ms"] = 1
    evidence["content_digest"] = digest(
        {k: v for k, v in evidence.items() if k != "content_digest"}
    )
    with pytest.raises(ValueError, match="as-of"):
        validate_review_evidence(evidence)
