from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog
from pa_agent.config.settings import Settings
from pa_agent.data.snapshot_identity import (
    LEGACY_SNAPSHOT_HASH_ALGORITHM,
    SNAPSHOT_HASH_ALGORITHM,
    canonical_market_snapshot_hash,
)
from pa_agent.experiments.pa_vpa_aabb import (
    AABB_PROTOCOL_VERSION,
    LEGACY_AABB_PROTOCOL_VERSION,
    ArmExecution,
    PaVpaAabbRunner,
    balanced_arm_sequence,
    decision_signature,
    freeze_experiment_input,
    validate_aabb_records,
)
from pa_agent.experiments.store import AnalysisExperimentStore
from pa_agent.records.audit_store import AnalysisAuditStore
from pa_agent.records.codec import dump_record
from pa_agent.records.pending_writer import PendingWriter
from pa_agent.records.schema import AnalysisRecord, RecordMeta
from pa_agent.util.threading import CancelToken
from tests.integration.conftest import VALID_STAGE1, VALID_STAGE2, make_frame


def _record(run_id: str, *, mode: str, catalog: dict | None) -> AnalysisRecord:
    sidecar = None
    vpa_evidence = None
    joint_decision = None
    official_path = "pa_only"
    call_attempts = [
        {
            "arm": "pa",
            "stage": "stage1",
            "attempt": 1,
            "request_hash": "1" * 64,
            "response_hash": "2" * 64,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "cached_prompt_tokens": 0,
            "total_tokens": 150,
            "latency_ms": 10.0,
            "status": "validated",
            "validation_category": None,
            "error_type": None,
            "provider_fallback_used": False,
            "created_at_ms": 1,
        },
        {
            "arm": "pa",
            "stage": "stage2",
            "attempt": 1,
            "request_hash": "3" * 64,
            "response_hash": "4" * 64,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "cached_prompt_tokens": 0,
            "total_tokens": 150,
            "latency_ms": 10.0,
            "status": "validated",
            "validation_category": None,
            "error_type": None,
            "provider_fallback_used": False,
            "created_at_ms": 2,
        },
    ]
    if mode == "soft":
        assert catalog is not None
        vpa_evidence = {
            "catalog_id": catalog["catalog_id"],
            "data_status": catalog["data_status"],
            "summary": "Independent relative-activity evidence is available.",
            "observations": [
                {
                    "bar_range": "K1",
                    "evidence_ids": ["vpa-e001"],
                    "finding_kind": "entry_timing",
                    "interpretation": "The cited effort/result relation challenges immediate timing.",
                }
            ],
            "hypothesis_assessments": [
                {
                    "hypothesis_id": "pa-primary",
                    "relation": "challenges",
                    "evidence_ids": ["vpa-e001"],
                    "reason": "The cited evidence challenges the current entry attempt.",
                }
            ],
            "background_relation": "neutral",
            "entry_timing_relation": "challenges",
            "next_confirmation": {
                "state": "pending_closed_bar",
                "trigger": "Wait for the next closed bar.",
                "requires_closed_bar": True,
            },
        }
        joint_decision = {
            "joint_arbitration": {
                "selected_candidate_id": "pa-setup-1",
                "decision_effect": "require_confirmation",
                "evidence_used": ["vpa-e001"],
                "reason": "Independent VPA evidence challenges immediate timing.",
            },
            "decision_patch": {},
        }
        sidecar = {
            "mode": "soft",
            "snapshot_hash": catalog["snapshot_hash"],
            "evidence_catalog": copy.deepcopy(catalog),
            "role_execution": {
                "design": "pa_vpa_joint_v1",
                "provider_fallback_allowed": False,
                "client_instances_isolated": True,
                "authority_violations": [],
            },
        }
        official_path = "joint"
        call_attempts.extend(
            [
                {
                    **call_attempts[0],
                    "arm": "vpa_evidence",
                    "stage": "vpa_evidence",
                    "request_hash": "5" * 64,
                },
                {
                    **call_attempts[0],
                    "arm": "joint_arbiter",
                    "stage": "joint_arbiter",
                    "request_hash": "6" * 64,
                },
            ]
        )
    return AnalysisRecord(
        meta=RecordMeta(
            timestamp_local_iso="2026-07-20T12:00:00+08:00",
            timestamp_local_ms=1_784_520_000_000,
            run_id=run_id,
            symbol="XAUUSD",
            timeframe="1h",
            bar_count=20,
            ai_provider={"model": "test-model"},
        ),
        kline_data=[bar.__dict__ for bar in make_frame().bars],
        htf_text="",
        stage1_messages=[],
        stage1_response={},
        stage1_diagnosis=copy.deepcopy(VALID_STAGE1),
        stage2_messages=[],
        stage2_response={},
        stage2_decision=copy.deepcopy(VALID_STAGE2),
        pa_baseline_stage2=copy.deepcopy(VALID_STAGE2) if mode == "soft" else None,
        joint_stage2=copy.deepcopy(VALID_STAGE2) if mode == "soft" else None,
        strategy_files_used=[],
        experience_loaded=[],
        exception=None,
        usage_total={"total_tokens": sum(item["total_tokens"] for item in call_attempts)},
        call_attempts=call_attempts,
        pa_hypotheses={"primary": {"hypothesis_id": "pa-primary"}},
        vpa_evidence=vpa_evidence,
        joint_decision=joint_decision,
        vpa_catalog=(
            copy.deepcopy(catalog)
            if mode == "soft" and catalog is not None
            else None
        ),
        role_statuses=(
            {"vpa": "validated", "joint": "validated"}
            if mode == "soft"
            else {}
        ),
        role_runtime=(
            copy.deepcopy(sidecar["role_execution"])
            if isinstance(sidecar, dict)
            else {}
        ),
        run_outcome={
            "status": "completed",
            "official_path": official_path,
            "requested_vpa_mode": mode,
            "degradation_stage": None,
            "reason": None,
        },
    )


def test_balanced_sequence_is_deterministic_and_only_uses_abba_or_baab() -> None:
    first = balanced_arm_sequence("0" * 64)
    second = balanced_arm_sequence("f" * 64)

    assert first in (("A1", "B1", "B2", "A2"), ("B1", "A1", "A2", "B2"))
    assert second in (("A1", "B1", "B2", "A2"), ("B1", "A1", "A2", "B2"))
    assert first != second
    assert balanced_arm_sequence("0" * 64) == first


def test_frozen_input_hash_covers_htf_catalog_provider_and_settings() -> None:
    frame = make_frame()
    settings = Settings()
    catalog = build_vpa_evidence_catalog(frame)

    frozen = freeze_experiment_input(
        frame,
        None,
        settings,
        catalog=catalog,
        analysis_fingerprint="a" * 64,
    )
    same = freeze_experiment_input(
        frame,
        None,
        settings,
        catalog=copy.deepcopy(catalog),
        analysis_fingerprint="a" * 64,
    )
    changed_provider = settings.model_copy(deep=True)
    changed_provider.provider.model = "different-model"

    assert frozen.frozen_input_hash == same.frozen_input_hash
    assert frozen.catalog_id == catalog["catalog_id"]
    assert frozen.snapshot_hash == catalog["snapshot_hash"]
    assert len(frozen.settings_fingerprint) == 64
    assert frozen.analysis_fingerprint == "a" * 64
    assert len(frozen.market_snapshot_hash) == 64
    assert frozen.market_snapshot_hash_algorithm == SNAPSHOT_HASH_ALGORITHM
    assert len(frozen.cohort_id) == 64
    assert (
        freeze_experiment_input(
            frame,
            None,
            changed_provider,
            catalog=catalog,
            analysis_fingerprint="a" * 64,
        ).frozen_input_hash
        != frozen.frozen_input_hash
    )
    changed_runtime = freeze_experiment_input(
        frame,
        None,
        settings,
        catalog=catalog,
        analysis_fingerprint="b" * 64,
    )
    assert changed_runtime.frozen_input_hash != frozen.frozen_input_hash
    assert changed_runtime.market_snapshot_hash == frozen.market_snapshot_hash
    assert changed_runtime.cohort_id != frozen.cohort_id


def test_frozen_input_hash_covers_instrument_execution_constraints() -> None:
    frame = make_frame()
    base_spec = {
        "provider": "okx_swap",
        "venue": "OKX",
        "symbol": frame.symbol,
        "instrument_type": "linear_swap",
        "tick_size": 0.1,
        "quantity_step": 1.0,
        "minimum_quantity": 1.0,
        "maximum_quantity": 100000.0,
        "minimum_notional": 0.0,
        "contract_multiplier": 0.001,
    }
    first_frame = replace(frame, data_metadata={"instrument_spec": base_spec})
    second_frame = replace(
        frame,
        data_metadata={
            "instrument_spec": {**base_spec, "contract_multiplier": 0.01}
        },
    )
    first = freeze_experiment_input(first_frame, None, Settings())
    second = freeze_experiment_input(second_frame, None, Settings())

    assert first.instrument_spec.contract_multiplier == 0.001
    assert first.instrument_spec_hash != second.instrument_spec_hash
    assert first.frozen_input_hash != second.frozen_input_hash
    assert first.market_snapshot_hash != second.market_snapshot_hash


def test_protocol_validator_replays_legacy_unversioned_market_hash() -> None:
    frame = make_frame()
    settings = Settings()
    catalog = build_vpa_evidence_catalog(frame)
    frozen = freeze_experiment_input(frame, None, settings, catalog=catalog)
    sequence = balanced_arm_sequence(frozen.frozen_input_hash)
    legacy_market_hash = canonical_market_snapshot_hash(
        symbol=frame.symbol,
        timeframe=frame.timeframe,
        kline_data=[bar.__dict__ for bar in frame.bars],
        snapshot_context=frozen.snapshot_context,
        htf_context=None,
        htf_kline_data=[],
        algorithm=LEGACY_SNAPSHOT_HASH_ALGORITHM,
    )
    legacy_frozen_hash = hashlib.sha256(
        json.dumps(
            {
                "market_snapshot_hash": legacy_market_hash,
                "catalog_id": frozen.catalog_id,
                "cohort_id": frozen.cohort_id,
            },
            ensure_ascii=True,
            default=str,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    records: list[AnalysisRecord] = []
    for position, arm_id in enumerate(sequence, start=1):
        mode = "off" if arm_id.startswith("A") else "soft"
        context = frozen.arm_context(
            experiment_id="legacy-aabb",
            arm_id=arm_id,
            sequence=sequence,
            sequence_position=position,
            client_instance_token=f"legacy-{arm_id}",
            stage1_request_hash="1" * 64,
        )
        context["protocol_version"] = LEGACY_AABB_PROTOCOL_VERSION
        context.pop("market_snapshot_hash_algorithm")
        context.pop("instrument_spec_hash")
        context["market_snapshot_hash"] = legacy_market_hash
        context["frozen_input_hash"] = legacy_frozen_hash
        context["first_pass_clean"] = True
        records.append(
            _record(f"legacy-{arm_id}", mode=mode, catalog=catalog).model_copy(
                update={
                    "snapshot_context": copy.deepcopy(frozen.snapshot_context),
                    "experiment_context": context,
                }
            )
        )

    assert validate_aabb_records(records)["valid"] is True


def test_protocol_validator_rejects_leakage_hash_drift_and_catalog_drift() -> None:
    frame = make_frame()
    settings = Settings()
    catalog = build_vpa_evidence_catalog(frame)
    frozen = freeze_experiment_input(frame, None, settings, catalog=catalog)
    sequence = balanced_arm_sequence(frozen.frozen_input_hash)
    records: list[AnalysisRecord] = []
    for position, arm_id in enumerate(sequence, start=1):
        mode = "off" if arm_id.startswith("A") else "soft"
        record = _record(f"run-{arm_id}", mode=mode, catalog=catalog)
        context = frozen.arm_context(
            experiment_id="aabb-test",
            arm_id=arm_id,
            sequence=sequence,
            sequence_position=position,
            client_instance_token=f"client-{arm_id}",
            stage1_request_hash="1" * 64,
        )
        context["first_pass_clean"] = True
        record = record.model_copy(
            update={
                "snapshot_context": copy.deepcopy(frozen.snapshot_context),
                "experiment_context": context,
            }
        )
        records.append(record)

    assert validate_aabb_records(records)["valid"] is True

    a_record = next(record for record in records if record.experiment_context["group"] == "A")
    leaked = a_record.model_copy(update={"vpa_evidence": {"unexpected": True}})
    assert (
        "a_arm_vpa_leakage"
        in validate_aabb_records([leaked if item is a_record else item for item in records])[
            "errors"
        ]
    )

    drifted = records[1].model_copy(
        update={
            "experiment_context": {
                **records[1].experiment_context,
                "stage1_request_hash": "9" * 64,
            }
        }
    )
    assert (
        "pa_stage1_request_hash_drift"
        in validate_aabb_records([records[0], drifted, *records[2:]])["errors"]
    )

    bad_b = next(record for record in records if record.experiment_context["group"] == "B")
    bad_catalog = copy.deepcopy(bad_b.vpa_catalog)
    bad_catalog["catalog_id"] = "vpa-catalog-tampered"
    tampered = bad_b.model_copy(update={"vpa_catalog": bad_catalog})
    assert (
        "b_arm_catalog_drift"
        in validate_aabb_records([tampered if item is bad_b else item for item in records])[
            "errors"
        ]
    )

    persisted = dump_record(bad_b)
    assert "vpa_sidecar" not in persisted
    assert persisted["vpa_evidence"] == bad_b.vpa_evidence
    assert persisted["joint_decision"] == bad_b.joint_decision


def test_runner_persists_four_independent_records_and_manifest(tmp_path: Path) -> None:
    settings = Settings()
    frame = make_frame()
    catalog = build_vpa_evidence_catalog(frame)
    frozen = freeze_experiment_input(frame, None, settings, catalog=catalog)
    executed: list[tuple[str, str | None]] = []

    def execute(spec, frozen_input, cancel_token):
        assert cancel_token.is_set() is False
        passed_catalog = frozen_input.vpa_catalog["catalog_id"] if spec.group == "B" else None
        executed.append((spec.arm_id, passed_catalog))
        return ArmExecution(
            record=_record(
                f"run-{spec.arm_id}",
                mode="off" if spec.group == "A" else "soft",
                catalog=catalog,
            ),
            client_identity=object(),
            client_instance_token=f"client-{spec.arm_id}",
        )

    db_path = tmp_path / "audit" / "analysis.sqlite3"
    store = AnalysisExperimentStore(db_path)
    writer = PendingWriter(
        pending_dir=tmp_path / "pending",
        audit_store=AnalysisAuditStore(db_path),
    )
    result = PaVpaAabbRunner(
        execute_arm=execute,
        pending_writer=writer,
        experiment_store=store,
    ).run(frozen, CancelToken(), source_run_id="source-run")

    assert result.protocol_report["valid"] is True
    assert len(result.records) == 4
    assert tuple(item[0] for item in executed) == balanced_arm_sequence(frozen.frozen_input_hash)
    assert all(catalog_id is None for arm, catalog_id in executed if arm.startswith("A"))
    assert all(
        catalog_id == catalog["catalog_id"] for arm, catalog_id in executed if arm.startswith("B")
    )
    manifest = store.get_experiment(result.experiment_id)
    assert manifest is not None
    assert manifest["protocol_version"] == AABB_PROTOCOL_VERSION
    assert manifest["status"] == "completed"
    assert manifest["protocol_valid"] == 1
    assert {item["arm_id"] for item in manifest["arms"]} == {"A1", "A2", "B1", "B2"}
    assert len(list((tmp_path / "pending").glob("*.json"))) == 4


def test_runner_keeps_all_assignments_when_an_arm_fails(tmp_path: Path) -> None:
    settings = Settings()
    frame = make_frame()
    catalog = build_vpa_evidence_catalog(frame)
    frozen = freeze_experiment_input(frame, None, settings, catalog=catalog)
    first_arm = balanced_arm_sequence(frozen.frozen_input_hash)[0]
    store = AnalysisExperimentStore(tmp_path / "analysis.sqlite3")

    def execute(spec, frozen_input, cancel_token):
        raise RuntimeError(f"failed {spec.arm_id}")

    result = PaVpaAabbRunner(
        execute_arm=execute,
        pending_writer=object(),
        experiment_store=store,
    ).run(frozen, CancelToken())

    assert result.status == "incomplete"
    assert result.protocol_report["valid"] is False
    manifest = store.get_experiment(result.experiment_id)
    assert manifest is not None
    assert len(manifest["arms"]) == 4
    by_arm = {arm["arm_id"]: arm for arm in manifest["arms"]}
    assert by_arm[first_arm]["run_status"] == "failed"
    assert by_arm[first_arm]["persisted"] == 0
    assert all(
        arm["run_status"] == "assigned" for arm_id, arm in by_arm.items() if arm_id != first_arm
    )


def test_runner_does_not_mark_audit_index_failure_as_persisted(tmp_path: Path) -> None:
    settings = Settings()
    frame = make_frame()
    catalog = build_vpa_evidence_catalog(frame)
    frozen = freeze_experiment_input(frame, None, settings, catalog=catalog)
    first_arm = balanced_arm_sequence(frozen.frozen_input_hash)[0]
    store = AnalysisExperimentStore(tmp_path / "analysis.sqlite3")

    def execute(spec, frozen_input, cancel_token):
        return ArmExecution(
            record=_record(
                f"run-{spec.arm_id}",
                mode="off" if spec.group == "A" else "soft",
                catalog=catalog if spec.group == "B" else None,
            ),
            client_identity=object(),
            client_instance_token=f"client-{spec.arm_id}",
        )

    writer = SimpleNamespace(
        save_full=lambda record: SimpleNamespace(
            ok=True,
            audit_ok=False,
            audit_error="database is busy",
            path=tmp_path / f"{record.meta.run_id}.json",
        )
    )
    result = PaVpaAabbRunner(
        execute_arm=execute,
        pending_writer=writer,
        experiment_store=store,
    ).run(frozen, CancelToken())

    assert result.status == "incomplete"
    manifest = store.get_experiment(result.experiment_id)
    assert manifest is not None
    by_arm = {arm["arm_id"]: arm for arm in manifest["arms"]}
    assert by_arm[first_arm]["persisted"] == 0
    assert by_arm[first_arm]["error"] == "audit_index_failed"


def test_decision_signature_ignores_model_self_declared_comparison() -> None:
    record = _record("run-a", mode="off", catalog=None)
    signature = decision_signature(record)

    assert signature["cycle_position"] == VALID_STAGE1["cycle_position"]
    assert signature["order_type"] == VALID_STAGE2["decision"]["order_type"]
    assert "comparison" not in signature
