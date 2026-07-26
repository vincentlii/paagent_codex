from __future__ import annotations

import copy

from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog
from pa_agent.config.settings import Settings
from pa_agent.experiments.pa_vpa_aabb import balanced_arm_sequence, freeze_experiment_input
from pa_agent.records.quality_gate import QualityThresholds, evaluate_analysis_quality
from tests.integration.conftest import make_frame
from tests.unit.test_pa_vpa_aabb_protocol import _record


def _records(
    *,
    experiment_id: str = "aabb-quality",
    analysis_fingerprint: str = "a" * 64,
):
    frame = make_frame()
    catalog = build_vpa_evidence_catalog(frame)
    frozen = freeze_experiment_input(
        frame,
        None,
        Settings(),
        catalog=catalog,
        analysis_fingerprint=analysis_fingerprint,
    )
    sequence = balanced_arm_sequence(frozen.frozen_input_hash)
    records = []
    for position, arm_id in enumerate(sequence, start=1):
        group = arm_id[0]
        record = _record(
            f"run-{arm_id}",
            mode="off" if group == "A" else "soft",
            catalog=catalog if group == "B" else None,
        )
        context = frozen.arm_context(
            experiment_id=experiment_id,
            arm_id=arm_id,
            sequence=sequence,
            sequence_position=position,
            client_instance_token=f"client-{arm_id}",
            stage1_request_hash="1" * 64,
        )
        records.append(
            record.model_copy(
                update={
                    "snapshot_context": copy.deepcopy(frozen.snapshot_context),
                    "experiment_context": context,
                }
            )
        )
    return records


def _thresholds(**overrides):
    values = {
        "min_unique_snapshots": 1,
        "min_repeatability_snapshots": 2,
        "max_call_p50_latency_ms": 100.0,
        "max_call_p95_latency_ms": 120.0,
        "max_run_p50_latency_ms": 100.0,
        "max_run_p95_latency_ms": 120.0,
        "max_run_p50_tokens": 1_000,
        "max_run_p95_tokens": 1_000,
        "max_b_to_a_latency_ratio_p95": 3.0,
        "max_b_to_a_token_ratio_p95": 3.0,
        "max_a_disagreement_rate": 0.25,
        "max_b_disagreement_rate": 0.35,
    }
    values.update(overrides)
    return QualityThresholds(**values)


def test_quality_gate_passes_one_clean_protocol_complete_experiment() -> None:
    report = evaluate_analysis_quality(_records(), thresholds=_thresholds())

    assert report["status"] == "pass"
    assert report["axes"]["aabb_protocol"]["passed"] is True
    assert report["axes"]["first_pass_clean"]["passed"] is True
    assert report["axes"]["pa_grounding"]["passed"] is True
    assert report["axes"]["vpa_grounding"]["passed"] is True
    assert report["axes"]["performance"]["passed"] is True
    assert report["ready_for_soft"] is False


def test_warning_is_audited_but_does_not_fail_first_pass() -> None:
    records = _records()
    records[0] = records[0].model_copy(
        update={
            "validation_audit": [
                {
                    "stage": "stage1",
                    "category": "warning",
                    "message": "free-text quality finding",
                    "invalid_fields": [],
                    "missing_fields": [],
                }
            ],
            "run_outcome": {
                **records[0].run_outcome,
                "status": "completed_with_warnings",
            },
        }
    )

    report = evaluate_analysis_quality(records, thresholds=_thresholds())

    assert report["axes"]["first_pass_clean"]["passed"] is True
    assert report["status"] == "pass"


def test_normalization_fails_first_pass_but_not_semantic_grounding() -> None:
    records = _records()
    records[0] = records[0].model_copy(
        update={
            "validation_audit": [
                {
                    "stage": "stage1",
                    "category": "normalization",
                    "invalid_fields": ["normalized_alias:direction"],
                    "missing_fields": [],
                }
            ],
        }
    )

    report = evaluate_analysis_quality(records, thresholds=_thresholds())

    assert report["status"] == "fail"
    assert report["axes"]["first_pass_clean"]["failed_runs"] == [records[0].meta.run_id]
    assert report["axes"]["pa_grounding"]["semantic_error_runs"] == []


def test_protocol_rejects_missing_arm_and_a_arm_vpa_leakage() -> None:
    missing = _records()[:-1]
    report = evaluate_analysis_quality(missing, thresholds=_thresholds())
    assert report["status"] == "fail"
    assert report["axes"]["aabb_protocol"]["passed"] is False
    assert report["sample"]["completed_protocol_experiments"] == 0

    leaked = _records()
    a_index = next(
        index for index, record in enumerate(leaked) if record.experiment_context["group"] == "A"
    )
    leaked[a_index] = leaked[a_index].model_copy(update={"vpa_evidence": {"bad": True}})
    report = evaluate_analysis_quality(leaked, thresholds=_thresholds())
    assert report["status"] == "fail"
    assert "a_arm_vpa_leakage" in next(
        iter(report["axes"]["aabb_protocol"]["invalid_experiments"].values())
    )


def test_vpa_grounding_rejects_missing_information_increment_and_authority_drift() -> None:
    records = _records()
    b_index = next(
        index for index, record in enumerate(records) if record.experiment_context["group"] == "B"
    )
    vpa_evidence = copy.deepcopy(records[b_index].vpa_evidence)
    vpa_evidence["observations"] = []
    role_runtime = copy.deepcopy(records[b_index].role_runtime)
    role_runtime["authority_violations"] = ["direction_changed"]
    records[b_index] = records[b_index].model_copy(
        update={"vpa_evidence": vpa_evidence, "role_runtime": role_runtime}
    )

    report = evaluate_analysis_quality(records, thresholds=_thresholds())

    grounding = report["axes"]["vpa_grounding"]
    assert grounding["passed"] is False
    assert records[b_index].meta.run_id in grounding["missing_information_increment_runs"]
    assert records[b_index].meta.run_id in grounding["authority_violation_runs"]


def test_performance_rejects_missing_telemetry_and_excessive_b_cost() -> None:
    records = _records()
    b_index = next(
        index for index, record in enumerate(records) if record.experiment_context["group"] == "B"
    )
    calls = copy.deepcopy(records[b_index].call_attempts)
    calls[0]["latency_ms"] = None
    calls[1]["total_tokens"] = 0
    records[b_index] = records[b_index].model_copy(update={"call_attempts": calls})

    report = evaluate_analysis_quality(records, thresholds=_thresholds())

    performance = report["axes"]["performance"]
    assert performance["passed"] is False
    assert records[b_index].meta.run_id in performance["missing_latency_runs"]
    assert records[b_index].meta.run_id in performance["missing_token_usage_runs"]


def test_repeatability_gate_uses_external_signatures() -> None:
    records = _records()
    a2_index = next(
        index for index, record in enumerate(records) if record.experiment_context["arm_id"] == "A2"
    )
    stage2 = copy.deepcopy(records[a2_index].stage2_decision)
    stage2["decision"]["order_type"] = "no_order"
    records[a2_index] = records[a2_index].model_copy(update={"stage2_decision": stage2})

    report = evaluate_analysis_quality(
        records,
        thresholds=_thresholds(
            min_repeatability_snapshots=1,
            max_a_disagreement_rate=0.01,
        ),
    )

    assert report["axes"]["repeatability"]["passed"] is False
    assert report["axes"]["repeatability"]["a_disagreement_rate"] == 1.0


def test_quality_gate_rejects_mixed_analysis_cohorts() -> None:
    records = [
        *_records(experiment_id="aabb-v1", analysis_fingerprint="a" * 64),
        *_records(experiment_id="aabb-v2", analysis_fingerprint="b" * 64),
    ]

    report = evaluate_analysis_quality(
        records,
        thresholds=_thresholds(min_unique_snapshots=2),
    )

    assert report["status"] == "fail"
    assert report["axes"]["cohort_consistency"]["passed"] is False
    assert report["axes"]["cohort_consistency"]["cohort_count"] == 2


def test_repeated_experiments_on_one_market_snapshot_do_not_inflate_sample() -> None:
    records = [
        *_records(experiment_id="aabb-repeat-1"),
        *_records(experiment_id="aabb-repeat-2"),
    ]

    report = evaluate_analysis_quality(
        records,
        thresholds=_thresholds(min_unique_snapshots=2),
    )

    assert report["status"] == "insufficient_sample"
    assert report["sample"]["completed_protocol_experiments"] == 2
    assert report["sample"]["unique_market_snapshots"] == 1
    assert report["axes"]["repeatability"]["unique_market_snapshots"] == 1


def test_insufficient_unique_snapshot_sample_never_passes() -> None:
    report = evaluate_analysis_quality(
        _records(),
        thresholds=_thresholds(min_unique_snapshots=2),
    )

    assert report["status"] == "insufficient_sample"
    assert report["quality_gate_passed"] is False
