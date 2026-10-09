import json

import pytest


def test_subagent_audit_fields_do_not_change_direct_call_records():
    from pa_agent.orchestrator.two_stage import _call_attempt_entry

    kwargs = {"arm": "A", "stage": "stage1", "attempt": 1, "messages": []}
    direct = _call_attempt_entry(**kwargs)
    assert direct["analysis_backend"] == "direct"
    assert "stage_subagents" not in direct
    assert "subagent_events" not in direct

    codex = _call_attempt_entry(**kwargs, backend_id="codex_agent")
    assert codex["stage_subagents"] == "off"
    assert codex["subagent_events"] == []


def condition(**changes):
    return {
        "backend": "codex_agent",
        "model": "gpt-5.6-luna",
        "reasoning": "max",
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
    for call in record.call_attempts:
        if call.get("analysis_backend") == "codex_agent":
            call["investigation_policy"] = request["condition"]["investigation"]
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


def test_codex_arm_accepts_luna_xhigh_and_preserves_frozen_max():
    from pa_agent.experiments import study_protocol as study

    assert study.StudyPlan.model_validate(config(
        a=condition(model="gpt-6-luna"),
        b=condition(model="gpt-6-luna", investigation="agentic"),
    )).a.model == "gpt-6-luna"
    assert study.StudyPlan.model_validate(config(
        a=condition(model="gpt-6-luna", reasoning="xhigh"),
        b=condition(model="gpt-6-luna", reasoning="xhigh", investigation="agentic"),
    )).a.reasoning == "xhigh"
    with pytest.raises(ValueError, match="approved Luna model/effort pair"):
        study.StudyPlan.model_validate(
            config(
                a=condition(reasoning="high"),
                b=condition(reasoning="high", investigation="agentic"),
            )
        )


def test_stage_subagent_contrast_is_isolated_and_off_for_direct():
    from pa_agent.experiments import study_protocol as study

    a = condition(model="gpt-6-luna", reasoning="xhigh", pipeline="pa_vpa_joint", investigation="agentic")
    b = {**a, "stage_subagents": "single_challenge_v1"}
    plan = study.StudyPlan.model_validate(config(contrast="stage_subagents", a=a, b=b))
    assert plan.a.stage_subagents == "off"
    assert plan.b.stage_subagents == "single_challenge_v1"
    with pytest.raises(ValueError):
        study.StudyPlan.model_validate(config(contrast="stage_subagents",
            a={**a, "investigation": "fixed"}, b={**b, "investigation": "fixed"}))
    for policy in ("stage1_challenge_v1", "vpa_challenge_v1", "stage2_challenge_v1"):
        scoped = study.StudyPlan.model_validate(config(
            contrast="stage_subagents", a=a,
            b={**a, "stage_subagents": policy},
        ))
        assert scoped.b.stage_subagents == policy
    with pytest.raises(ValueError, match="stage_subagents"):
        study.StudyPlan.model_validate(config(
            contrast="stage_subagents", a=a,
            b={**b, "reasoning": "max"},
        ))
    with pytest.raises(ValueError, match="stage_subagents"):
        study.StudyPlan.model_validate(config(
            contrast="stage_subagents",
            a={**a, "backend": "deepseek_direct", "investigation": "fixed"}, b=b,
        ))


def test_layered_knowledge_is_only_a_joint_codex_research_contrast():
    from pa_agent.experiments import study_protocol as study

    a = condition(model="gpt-6-luna", reasoning="xhigh",
                  pipeline="pa_vpa_joint", investigation="agentic")
    b = {**a, "knowledge_delivery": "layered_stage2_research_v1"}
    plan = study.StudyPlan.model_validate(config(contrast="knowledge_delivery", a=a, b=b))
    assert plan.a.knowledge_delivery == "full"
    assert plan.b.knowledge_delivery == "layered_stage2_research_v1"
    for invalid_a, invalid_b in (
        ({**a, "pipeline": "pa_only"}, {**b, "pipeline": "pa_only"}),
        ({**a, "investigation": "fixed"}, {**b, "investigation": "fixed"}),
        (a, {**b, "stage_subagents": "stage2_challenge_v1"}),
        (a, {**b, "reasoning": "max"}),
    ):
        with pytest.raises(ValueError, match="knowledge_delivery"):
            study.StudyPlan.model_validate(config(
                contrast="knowledge_delivery", a=invalid_a, b=invalid_b,
            ))


def test_layered_study_checks_initial_stage2_policy_not_pointer_repair(tmp_path):
    from pa_agent.experiments import study_protocol as study

    a = condition(model="gpt-6-luna", reasoning="xhigh",
                  pipeline="pa_vpa_joint", investigation="agentic")
    b = {**a, "knowledge_delivery": "layered_stage2_research_v1"}
    study.register_study(tmp_path, config(contrast="knowledge_delivery", a=a, b=b), [sample()])

    def execute(request):
        result = execution(request)
        calls = result["output"]["call_attempts"]
        initial = next(call for call in calls if call["stage"] == "stage2")
        initial["knowledge_delivery_policy"] = (
            "layered_stage2_research_v1" if request["condition"]["knowledge_delivery"]
            == "layered_stage2_research_v1" else "inline_mandatory_knowledge_v1"
        )
        calls.append({**initial, "attempt": 2, "knowledge_delivery_policy": None})
        return result

    study.run_study(tmp_path, execute)
    assert study.summarize_study(tmp_path)["coverage"]["completed"] == 4

    missing_control = tmp_path / "missing-control-policy"
    study.register_study(missing_control, config(
        contrast="knowledge_delivery", a=a, b=b,
    ), [sample()])

    def missing_full_policy(request):
        result = execute(request)
        if request["condition"]["knowledge_delivery"] == "full":
            next(call for call in result["output"]["call_attempts"]
                 if call["stage"] == "stage2" and call["attempt"] == 1)[
                     "knowledge_delivery_policy"] = None
        return result

    study.run_study(missing_control, missing_full_policy)
    assert study.summarize_study(missing_control)["coverage"]["completed"] == 2


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


def test_child_failure_or_rejected_start_remains_itt_but_not_clean_per_protocol():
    from pa_agent.experiments.study_protocol import collaboration_coverage
    from pa_agent.agent_runtime.execution_contract import STAGE_CHALLENGE_PROTOCOL
    def call(status, rejections=0):
        return {"stage": "stage1", "attempt": 1, "job_digest": "test", "collaboration": {
            "protocol": STAGE_CHALLENGE_PROTOCOL, "job_digest": "test", "status": status,
            "collected": True, "start_rejections": rejections}}
    for status in ("failed", "cancelled", "running"):
        assert not collaboration_coverage([call(status)], "stage1_challenge_v1")["per_protocol"]
    assert collaboration_coverage([call("not_requested")], "stage1_challenge_v1")["per_protocol"]
    assert collaboration_coverage([call("completed")], "stage1_challenge_v1")["per_protocol"]
    coverage = collaboration_coverage([call("completed", 1)], "stage1_challenge_v1")
    assert coverage["start_rejected"] == 1 and not coverage["per_protocol"]
