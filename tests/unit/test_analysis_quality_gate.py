from __future__ import annotations

from types import SimpleNamespace

from pa_agent.records.quality_gate import QualityThresholds, evaluate_analysis_quality


def _record(
    run_id: str,
    *,
    baseline_latency: float = 80.0,
    vpa_latency: float = 90.0,
    validation_audit: list[dict] | None = None,
    authority_violations: list[str] | None = None,
    experiment_type: str = "full_prompt_ab",
) -> SimpleNamespace:
    calls = [
        {
            "arm": "baseline",
            "stage": "stage1",
            "attempt": 1,
            "status": "validated",
            "latency_ms": baseline_latency,
            "total_tokens": 100,
        },
        {
            "arm": "vpa",
            "stage": "stage1",
            "attempt": 1,
            "status": "validated",
            "latency_ms": vpa_latency,
            "total_tokens": 120,
        },
    ]
    return SimpleNamespace(
        meta=SimpleNamespace(run_id=run_id),
        exception=None,
        validation_audit=list(validation_audit or []),
        call_attempts=calls,
        stage1_diagnosis={
            "pa_evidence": {
                "fact_refs": ["PA.WINDOW.K5"],
                "theory_rule_ids": ["BROOKS.TREND.FOLLOW_THROUGH"],
                "spike_analysis": None,
            }
        },
        vpa_sidecar={
            "pair_id": "vpa-aaaaaaaaaaaa",
            "evidence_catalog": {
                "catalog_id": "catalog",
                "data_status": "available",
                "evidence": [
                    {
                        "evidence_id": "vpa-e001",
                        "source": "volume",
                        "bar_range": "K1",
                        "facts": {
                            "volume_available": True,
                            "volume_vs_previous_relation": "below",
                        },
                    }
                ],
            },
            "stage1_context": {
                "data_status": "available",
                "observations": [
                    {
                        "evidence_ids": ["vpa-e001"],
                        "finding_kind": "effort_result",
                    }
                ],
            },
            "stage2_impact": {
                "background_relation": "supports",
                "entry_timing_relation": "supports",
                "decision_effect": "keep_brooks_baseline",
            },
            "paired_execution": {
                "experiment_type": experiment_type,
                "pair_identity_version": "initial_stage1_requests_v1",
                "pair_input_hash": "a" * 64,
                "protocol_valid": True,
                "full_prompt_ab_eligible": experiment_type == "full_prompt_ab",
                "authority_violations": list(authority_violations or []),
                "official_arm": "baseline",
                "arm_sequence": ["baseline", "vpa"],
                "provider_fallback_allowed": False,
                "client_instances_isolated": True,
                "same_frozen_input": True,
                "baseline_has_vpa_catalog": False,
                "vpa_has_vpa_catalog": True,
                "intended_prompt_difference_present": True,
                "incremental_context_disabled_for_protocol": True,
                "stage2_baseline_prefix_reused": False,
                "baseline_prompt_price_only": True,
                "baseline_stage1_messages_hash": "b" * 64,
                "vpa_stage1_messages_hash": "c" * 64,
            },
        },
    )


def _thresholds(**overrides: object) -> QualityThresholds:
    values = {
        "min_completed_runs": 2,
        "max_call_p50_latency_ms": 100.0,
        "max_call_p95_latency_ms": 120.0,
        "max_run_p50_latency_ms": 200.0,
        "max_run_p95_latency_ms": 220.0,
        "max_run_p50_tokens": 250,
        "max_run_p95_tokens": 300,
        "max_vpa_to_baseline_latency_ratio_p95": 1.5,
    }
    values.update(overrides)
    return QualityThresholds(**values)


def test_quality_gate_passes_only_clean_full_prompt_runs_within_slo() -> None:
    report = evaluate_analysis_quality(
        [_record("run-1"), _record("run-2", baseline_latency=85, vpa_latency=95)],
        thresholds=_thresholds(),
    )

    assert report["status"] == "pass"
    assert report["quality_gate_passed"] is True
    assert report["axes"]["first_pass_clean"]["passed"] is True
    assert report["axes"]["pa_grounding"]["passed"] is True
    assert report["axes"]["vpa_grounding"]["passed"] is True
    assert report["axes"]["ab_protocol"]["passed"] is True
    assert report["axes"]["performance"]["passed"] is True
    assert report["ready_for_soft"] is False


def test_quality_gate_fails_corrected_semantic_error_and_authority_drift() -> None:
    report = evaluate_analysis_quality(
        [
            _record(
                "run-1",
                validation_audit=[
                    {
                        "stage": "stage1",
                        "category": "c",
                        "invalid_fields": ["s1:program fact conflict"],
                    }
                ],
                authority_violations=["stage1.direction_changed"],
            ),
            _record("run-2"),
        ],
        thresholds=_thresholds(),
    )

    assert report["status"] == "fail"
    assert report["axes"]["first_pass_clean"]["failed_runs"] == ["run-1"]
    assert report["axes"]["pa_grounding"]["semantic_error_runs"] == ["run-1"]
    assert report["axes"]["vpa_grounding"]["authority_violation_runs"] == ["run-1"]


def test_alias_normalization_is_not_misclassified_as_a_semantic_error() -> None:
    report = evaluate_analysis_quality(
        [
            _record(
                "run-1",
                validation_audit=[
                    {
                        "stage": "stage1",
                        "category": "normalization",
                        "invalid_fields": [
                            "normalized_alias:vpa_context.next_confirmation.state"
                        ],
                    }
                ],
            ),
            _record("run-2"),
        ],
        thresholds=_thresholds(),
    )

    assert report["axes"]["first_pass_clean"]["failed_runs"] == ["run-1"]
    assert report["axes"]["pa_grounding"]["semantic_error_runs"] == []
    assert report["axes"]["vpa_grounding"]["semantic_error_runs"] == []


def test_quality_gate_attributes_missing_pa_and_vpa_contracts_to_their_axes() -> None:
    report = evaluate_analysis_quality(
        [
            _record(
                "run-1",
                validation_audit=[
                    {
                        "stage": "stage1",
                        "category": "b",
                        "missing_fields": ["pa_evidence", "vpa_context"],
                        "invalid_fields": [],
                    }
                ],
            ),
            _record("run-2"),
        ],
        thresholds=_thresholds(),
    )

    assert report["axes"]["pa_grounding"]["semantic_error_runs"] == ["run-1"]
    assert report["axes"]["vpa_grounding"]["semantic_error_runs"] == ["run-1"]


def test_quality_gate_never_passes_an_insufficient_sample() -> None:
    report = evaluate_analysis_quality(
        [_record("run-1")],
        thresholds=_thresholds(min_completed_runs=3),
    )

    assert report["status"] == "insufficient_sample"
    assert report["quality_gate_passed"] is False


def test_quality_gate_rejects_marginal_sidecar_as_full_prompt_ab() -> None:
    report = evaluate_analysis_quality(
        [_record("run-1", experiment_type="online_vpa_marginal_sidecar"), _record("run-2")],
        thresholds=_thresholds(),
    )

    assert report["status"] == "fail"
    assert report["axes"]["ab_protocol"]["invalid_runs"] == ["run-1"]


def test_quality_gate_rejects_legacy_pair_identity() -> None:
    legacy = _record("run-1")
    legacy.vpa_sidecar["paired_execution"].pop("pair_identity_version")

    report = evaluate_analysis_quality(
        [legacy, _record("run-2")],
        thresholds=_thresholds(),
    )

    assert report["status"] == "fail"
    assert report["axes"]["ab_protocol"]["invalid_runs"] == ["run-1"]


def test_quality_gate_independently_rejects_inconsistent_pair_metadata() -> None:
    tampered = _record("run-1")
    tampered.vpa_sidecar["pair_id"] = "vpa-not-the-input-hash"
    tampered.vpa_sidecar["paired_execution"]["same_frozen_input"] = False

    report = evaluate_analysis_quality(
        [tampered, _record("run-2")],
        thresholds=_thresholds(),
    )

    assert report["status"] == "fail"
    assert report["axes"]["ab_protocol"]["invalid_runs"] == ["run-1"]


def test_quality_gate_enforces_latency_and_token_percentiles() -> None:
    slow = _record("run-2", baseline_latency=200, vpa_latency=250)
    slow.call_attempts[0]["total_tokens"] = 500
    slow.call_attempts[1]["total_tokens"] = 600

    report = evaluate_analysis_quality(
        [_record("run-1"), slow],
        thresholds=_thresholds(),
    )

    assert report["status"] == "fail"
    performance = report["axes"]["performance"]
    assert performance["passed"] is False
    assert performance["violations"]


def test_quality_gate_rejects_incomplete_call_telemetry() -> None:
    missing = _record("run-2")
    missing.call_attempts[1]["latency_ms"] = None
    missing.call_attempts[1]["total_tokens"] = 0

    report = evaluate_analysis_quality(
        [_record("run-1"), missing],
        thresholds=_thresholds(),
    )

    performance = report["axes"]["performance"]
    assert performance["passed"] is False
    assert performance["missing_latency_runs"] == ["run-2"]
    assert performance["missing_token_usage_runs"] == ["run-2"]
    assert "incomplete_call_latency" in performance["violations"]
    assert "incomplete_token_usage" in performance["violations"]


def test_quality_gate_does_not_count_raw_volume_as_vpa_information_increment() -> None:
    ungrounded = _record("run-1")
    evidence = ungrounded.vpa_sidecar["evidence_catalog"]["evidence"][0]
    evidence["facts"] = {
        "volume_available": True,
        "volume_vs_baseline_relation": "unavailable",
        "volume_vs_previous_relation": "unavailable",
        "volume_vs_seasonality_relation": "unavailable",
    }

    report = evaluate_analysis_quality(
        [ungrounded, _record("run-2")],
        thresholds=_thresholds(),
    )

    grounding = report["axes"]["vpa_grounding"]
    assert grounding["passed"] is False
    assert grounding["missing_information_increment_runs"] == ["run-1"]


def test_quality_gate_requires_both_ab_arms_in_call_ledger() -> None:
    missing_arm = _record("run-2")
    missing_arm.call_attempts = [missing_arm.call_attempts[0]]

    report = evaluate_analysis_quality(
        [_record("run-1"), missing_arm],
        thresholds=_thresholds(),
    )

    performance = report["axes"]["performance"]
    assert performance["passed"] is False
    assert performance["missing_arm_runs"] == ["run-2"]
    assert "incomplete_ab_call_ledger" in performance["violations"]
