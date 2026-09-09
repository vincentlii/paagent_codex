import json

import pytest


def condition(**changes):
    return {
        "backend": "codex_agent",
        "model": "fixture",
        "reasoning": "high",
        "pipeline": "pa_only",
        "investigation": "fixed",
        **changes,
    }


def config(**changes):
    return {
        "contrast": "investigation",
        "a": condition(),
        "b": condition(investigation="agentic"),
        "domain_fingerprint": "a" * 64,
        "execution_version": "paper_latency_v3",
        "primary_metric": "blind_quality",
        "sample_size_rationale": "fixture only; no statistical power claim",
        "holdout_start_ms": 2_000_000_002_000,
        **changes,
    }


def sample():
    from tests.unit.test_backend_review import review_evidence

    evidence = review_evidence()
    return {
        "sample_id": "s1",
        "market_key": "venue:X:1h",
        "snapshot_hash": evidence["snapshot_hash"],
        "window_start_ms": 2_000_000_001_000,
        "window_end_ms": 2_000_000_001_500,
        "evidence": evidence,
    }


def execution(request):
    from tests.unit.test_backend_aabb_protocol import _record
    from pa_agent.config.analysis_pipeline import AnalysisPipelineIdentity

    record = _record(request["case_id"], "codex_agent")
    record.analysis_pipeline = AnalysisPipelineIdentity(pipeline=request["condition"]["pipeline"])
    evidence = request["evidence"]
    for name in ("kline_data", "snapshot_context", "htf_context", "htf_kline_data", "vpa_catalog"):
        setattr(record, name, evidence[name])
    record.role_runtime["analysis_as_of_ms"] = evidence["as_of_ms"]
    return {
        "condition": request["condition"],
        "client_token": request["case_id"],
        "runtime_identity": object(),
        "output": record.model_dump(mode="json"),
        "evidence_digest": request["evidence_digest"],
        "domain_fingerprint": "a" * 64,
    }


def test_protocol_rejects_confounding_model_change():
    from pa_agent.experiments import study_protocol as study

    with pytest.raises(ValueError, match="investigation"):
        study.StudyPlan.model_validate(config(b=condition(model="other", investigation="agentic")))


def test_preregistration_survives_missing_arm_and_blocks_rerun(tmp_path):
    from pa_agent.experiments import study_protocol as study

    registered = study.register_study(tmp_path / "study", config(), [sample()])
    seen = []

    def execute(request):
        disk = json.loads((tmp_path / "study" / "registration.json").read_text())
        assert len(disk["assignments"]) == 4
        seen.append(request["arm_id"])
        if request["arm_id"] == "B1":
            raise RuntimeError("fixture unavailable")
        return execution(request)

    study.run_study(tmp_path / "study", execute)
    result = study.summarize_study(tmp_path / "study")
    assert len(seen) == 4
    assert result["coverage"]["failed"] == 1
    assert result["coverage"]["completed"] == 3
    assert result["ready_for_promotion"] is False
    with pytest.raises(FileExistsError):
        study.run_study(tmp_path / "study", execute)
    assert registered["plan"]["contrast"] == "investigation"


def test_episode_split_rejects_holdout_overlap(tmp_path):
    from pa_agent.experiments import study_protocol as study

    crossing = sample()
    crossing["window_end_ms"] = 2_000_000_002_200
    with pytest.raises(ValueError, match="holdout"):
        study.register_study(tmp_path / "study", config(), [crossing])


def test_export_score_unblind_keeps_identity_and_future_outside_public_packet(tmp_path):
    from pa_agent.experiments import study_protocol as study
    from pa_agent.experiments.backend_review import (
        export_study_review,
        score_blinded_backend_review,
        unblind_backend_scores,
        QUALITY_WEIGHTS,
        VPA_WEIGHTS,
    )

    root = tmp_path / "study"
    study.register_study(root, config(), [sample()])
    study.run_study(root, execution)
    packet = export_study_review(
        root,
        tmp_path / "reviewer",
        tmp_path / "private/key.json",
        blinding_salt="private-salt-123456789",
    )
    text = json.dumps(packet)
    assert "codex_agent" not in text and "client_token" not in text and "ai_provider" not in text
    assert len(packet["cases"]) == 4 and len(packet["evidence"]) == 1
    ratings = [
        {
            "blind_id": case["blind_id"],
            **{n: 4 for n in QUALITY_WEIGHTS},
            "vpa": {n: 3 for n in VPA_WEIGHTS},
        }
        for case in packet["cases"]
    ]
    scored = score_blinded_backend_review(packet, ratings)
    key = json.loads((tmp_path / "private/key.json").read_text())
    result = unblind_backend_scores(scored, key)
    assert result["contrast"] == "investigation"
    assert "codex_minus_direct_score" not in result
    assert result["paired_episodes"]["complete"] == 1
    assert result["paired_episodes"]["mean_b_minus_a"] == 0


def test_executor_cannot_claim_success_for_an_unvalidated_dict(tmp_path):
    from pa_agent.experiments import study_protocol as study

    study.register_study(tmp_path, config(), [sample()])

    def invalid(request):
        result = execution(request)
        result["output"] = {"stage2_decision": {"decision": {"order_type": "不下单"}}}
        return result

    study.run_study(tmp_path, invalid)
    assert study.summarize_study(tmp_path)["coverage"]["failed"] == 4


def test_missing_outcome_censors_episode_instead_of_zeroing_return(tmp_path):
    from pa_agent.experiments import study_protocol as study

    registration = study.register_study(
        tmp_path, config(primary_metric="episode_itt_r"), [sample()]
    )
    study.run_study(tmp_path, execution)
    outcomes = [
        {
            "case_id": a["case_id"],
            "execution_version": "paper_latency_v3",
            "status": "closed",
            "return_r": 1.0,
            "window_end_ms": sample()["window_end_ms"],
        }
        for a in registration["assignments"][:3]
    ]
    summary = study.summarize_outcomes(tmp_path, outcomes)
    assert summary["coverage"]["missing"] == 1
    assert summary["paired_episodes"]["mean_b_minus_a"] is None
    assert summary["paired_episodes"]["censored"] == 1
    assert summary["calibrated_probability"] is False


def test_holdout_cannot_be_exported_until_explicit_final_release(tmp_path):
    from pa_agent.experiments import study_protocol as study
    from pa_agent.experiments.backend_review import export_study_review

    held = sample()
    held.update(window_start_ms=2_000_000_002_000, window_end_ms=2_000_000_003_000)
    study.register_study(tmp_path / "study", config(), [held])
    with pytest.raises(ValueError, match="sealed"):
        export_study_review(
            tmp_path / "study",
            tmp_path / "public",
            tmp_path / "key.json",
            blinding_salt="1234567890123456",
            split="holdout",
        )
    study.release_holdout(tmp_path / "study", reason="final predeclared evaluation")
    packet = export_study_review(
        tmp_path / "study",
        tmp_path / "public",
        tmp_path / "key.json",
        blinding_salt="1234567890123456",
        split="holdout",
    )
    assert all(c["run_status"] == "missing" for c in packet["cases"])


def test_all_three_contrasts_are_separate_and_reject_joint_model_drift():
    from pa_agent.experiments.study_protocol import StudyPlan

    assert (
        StudyPlan.model_validate(
            config(contrast="backend", a=condition(backend="deepseek_direct"))
        ).contrast
        == "backend"
    )
    assert (
        StudyPlan.model_validate(
            config(contrast="joint", b=condition(pipeline="pa_vpa_joint"))
        ).contrast
        == "joint"
    )
    with pytest.raises(ValueError, match="joint"):
        StudyPlan.model_validate(
            config(contrast="joint", b=condition(pipeline="pa_vpa_joint", reasoning="low"))
        )


def test_episode_weighting_does_not_count_replicates_or_correlated_snapshots_as_independent():
    from pa_agent.experiments.study_protocol import paired_episode_summary

    rows = []
    for sample_id, episode, scores in (
        ("s1", "e1", (0, 0, 2, 2)),
        ("s2", "e1", (0, 0, 4, 4)),
        ("s3", "e2", (0, 0, 1, 1)),
    ):
        rows.extend(
            {"sample_id": sample_id, "episode_id": episode, "arm_id": arm, "r": score}
            for arm, score in zip(("A1", "A2", "B1", "B2"), scores)
        )
    report = paired_episode_summary(rows, "r")
    assert report["assigned"] == 2 and report["complete"] == 2
    assert report["mean_b_minus_a"] == 2  # mean(mean(2,4),1), not mean(2,4,1)


def test_cli_freezes_scores_and_refuses_overwrite(tmp_path):
    from pa_agent.experiments.study_cli import main
    from pa_agent.experiments.backend_review import QUALITY_WEIGHTS

    packet, ratings, scores = (
        tmp_path / name for name in ("packet.json", "ratings.json", "scores.json")
    )
    packet.write_text(json.dumps({"schema_version": 2, "cases": [{"blind_id": "x"}]}))
    ratings.write_text(
        json.dumps(
            [
                {
                    "blind_id": "x",
                    **{n: 4 for n in QUALITY_WEIGHTS},
                    "vpa_not_applicable_reason": "PA only",
                }
            ]
        )
    )
    assert main(["score", str(packet), str(ratings), str(scores)]) == 0
    assert json.loads(scores.read_text())["coverage"]["rated"] == 1
    with pytest.raises(FileExistsError):
        main(["score", str(packet), str(ratings), str(scores)])


def test_reusing_runtime_cannot_pass_by_minting_new_client_tokens(tmp_path):
    from pa_agent.experiments import study_protocol as study

    study.register_study(tmp_path, config(), [sample()])
    shared_runtime = object()

    def reused(request):
        result = execution(request)
        result["runtime_identity"] = shared_runtime
        return result

    study.run_study(tmp_path, reused)
    assert study.summarize_study(tmp_path)["coverage"]["completed"] == 1
