from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog
from pa_agent.experiments.backend_aabb import (
    BACKEND_AABB_PROTOCOL_VERSION,
    BackendAabbRunner,
    BackendArmExecution,
    freeze_backend_experiment_input,
    summarize_backend_experiments,
)
from pa_agent.experiments.store import AnalysisExperimentStore
from pa_agent.records.schema import AnalysisRecord, RecordMeta
from pa_agent.util.threading import CancelToken
from tests.integration.conftest import VALID_STAGE1, VALID_STAGE2, make_frame


class _Writer:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save_full(self, record: AnalysisRecord) -> object:
        path = self.root / f"{record.meta.run_id}.json"
        path.write_text("{}", encoding="utf-8")
        return SimpleNamespace(ok=True, audit_ok=True, path=path)


def _record(run_id: str, backend: str) -> AnalysisRecord:
    catalog = build_vpa_evidence_catalog(make_frame())
    calls = []
    for stage in ("stage1", "stage2", "vpa_evidence"):
        calls.append(
            {
                "arm": "pa",
                "stage": stage,
                "attempt": 1,
                "request_hash": ("1" if stage == "stage1" else "2") * 64,
                "response_hash": "3" * 64,
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "latency_ms": 25.0,
                "status": "validated",
                "provider_fallback_used": False,
                "analysis_backend": backend,
            }
        )
    return AnalysisRecord(
        meta=RecordMeta(
            timestamp_local_iso="2026-09-02T12:00:00+08:00",
            timestamp_local_ms=1_788_322_400_000,
            run_id=run_id,
            symbol="XAUUSD",
            timeframe="1h",
            bar_count=len(make_frame().bars),
            ai_provider={"model": "test"},
        ),
        kline_data=[bar.__dict__ for bar in make_frame().bars],
        htf_text="",
        stage1_messages=[],
        stage1_response={},
        stage1_diagnosis=copy.deepcopy(VALID_STAGE1),
        stage2_messages=[],
        stage2_response={},
        stage2_decision=copy.deepcopy(VALID_STAGE2),
        strategy_files_used=[],
        experience_loaded=[],
        call_attempts=calls,
        run_outcome={
            "status": "completed",
            "official_path": "pa_only",
            "requested_vpa_mode": "shadow",
            "effective_vpa_mode": "shadow",
        },
        vpa_catalog=catalog,
        vpa_evidence={
            "catalog_id": catalog["catalog_id"],
            "data_status": "available",
            "observations": [],
            "hypothesis_assessments": [],
            "background_relation": "neutral",
            "entry_timing_relation": "neutral",
        },
    )


def _frozen(*, codex_model: str = "gpt-5"):
    frame = make_frame()
    htf_frame = replace(make_frame(), timeframe="4h")
    return freeze_backend_experiment_input(
        frame,
        {
            "status": "available",
            "timeframe": "4h",
            "current_timeframe": frame.timeframe,
            "frame": htf_frame,
        },
        direct_backend={"provider": "deepseek", "model": "deepseek-reasoner"},
        codex_backend={"runtime": "codex", "model": codex_model, "reasoning": "high"},
        shared_settings={
            "vpa_mode": "shadow",
            "higher_timeframe_enabled": True,
            "decision_stance": "conservative",
        },
        contract_fingerprint="c" * 64,
        analysis_fingerprint="a" * 64,
        vpa_catalog=build_vpa_evidence_catalog(frame),
    )


def test_backend_cohort_changes_without_changing_market_identity() -> None:
    first = _frozen()
    same = _frozen()
    changed = _frozen(codex_model="gpt-5.1")

    assert first.frozen_input_hash == same.frozen_input_hash
    assert first.market_snapshot_hash == changed.market_snapshot_hash
    assert first.cohort_id != changed.cohort_id
    assert first.protocol_version == BACKEND_AABB_PROTOCOL_VERSION


def test_runner_pre_registers_all_arms_and_retains_failed_arm_in_itt(tmp_path: Path) -> None:
    store = AnalysisExperimentStore(tmp_path / "audit.sqlite3", tmp_path / "manifests")
    seen: list[str] = []

    def execute(spec, _frozen_input, _cancel):
        seen.append(spec.arm_id)
        if spec.arm_id == "B1":
            raise RuntimeError("codex unavailable")
        backend = "direct" if spec.group == "A" else "codex_agent"
        return BackendArmExecution(
            record=_record(f"run-{spec.arm_id}", backend),
            runtime_identity=object(),
            client_instance_token=f"client-{spec.arm_id}",
            runtime_fingerprint=(
                _frozen_input.direct_backend_fingerprint
                if spec.group == "A"
                else _frozen_input.codex_backend_fingerprint
            ),
        )

    result = BackendAabbRunner(
        execute_arm=execute,
        pending_writer=_Writer(tmp_path),
        experiment_store=store,
    ).run(_frozen(), CancelToken())

    manifest = store.get_experiment(result.experiment_id)
    assert result.status == "incomplete"
    assert set(seen) == {"A1", "A2", "B1", "B2"}
    assert manifest is not None
    assert len(manifest["arms"]) == 4
    failed = next(arm for arm in manifest["arms"] if arm["arm_id"] == "B1")
    assert failed["persisted"] == 0
    assert failed["run_status"] == "failed"


def test_complete_backend_protocol_reports_repeatability_and_cost(tmp_path: Path) -> None:
    store = AnalysisExperimentStore(tmp_path / "audit.sqlite3", tmp_path / "manifests")

    def execute(spec, _frozen_input, _cancel):
        backend = "direct" if spec.group == "A" else "codex_agent"
        return BackendArmExecution(
            record=_record(f"run-{spec.arm_id}", backend),
            runtime_identity=object(),
            client_instance_token=f"client-{spec.arm_id}",
            runtime_fingerprint=(
                _frozen_input.direct_backend_fingerprint
                if spec.group == "A"
                else _frozen_input.codex_backend_fingerprint
            ),
        )

    result = BackendAabbRunner(
        execute_arm=execute,
        pending_writer=_Writer(tmp_path),
        experiment_store=store,
    ).run(_frozen(), CancelToken())
    report = summarize_backend_experiments(store, min_unique_snapshots=1)

    assert result.status == "completed"
    assert result.protocol_report == {"valid": True, "errors": []}
    assert report["protocol_version"] == BACKEND_AABB_PROTOCOL_VERSION
    assert report["unique_snapshots"] == 1
    assert report["complete_four_arm_snapshots"] == 1
    assert report["engineering_repeatability_reviewable"] is True
    assert report["groups"]["A"]["mean_total_tokens"] == 360.0
    assert report["groups"]["B"]["mean_latency_ms"] == 75.0


def test_protocol_rejects_backend_fallback_metadata(tmp_path: Path) -> None:
    store = AnalysisExperimentStore(tmp_path / "audit.sqlite3", tmp_path / "manifests")

    def execute(spec, _frozen_input, _cancel):
        backend = "direct" if spec.group == "A" else "codex_agent"
        record = _record(f"run-{spec.arm_id}", backend)
        if spec.arm_id == "B2":
            record.role_runtime = {"backend_fallback": {"from": "codex_agent", "to": "direct"}}
        return BackendArmExecution(
            record=record,
            runtime_identity=object(),
            client_instance_token=f"client-{spec.arm_id}",
            runtime_fingerprint=(
                _frozen_input.direct_backend_fingerprint
                if spec.group == "A"
                else _frozen_input.codex_backend_fingerprint
            ),
        )

    result = BackendAabbRunner(
        execute_arm=execute,
        pending_writer=_Writer(tmp_path),
        experiment_store=store,
    ).run(_frozen(), CancelToken())

    assert result.status == "protocol_invalid"
    assert "backend_fallback_used" in result.protocol_report["errors"]


def test_protocol_rejects_record_with_snapshot_drift(tmp_path: Path) -> None:
    store = AnalysisExperimentStore(tmp_path / "audit.sqlite3", tmp_path / "manifests")

    def execute(spec, _frozen_input, _cancel):
        backend = "direct" if spec.group == "A" else "codex_agent"
        record = _record(f"run-{spec.arm_id}", backend)
        if spec.arm_id == "A2":
            record.kline_data[-1]["close"] += 1.0
        return BackendArmExecution(
            record=record,
            runtime_identity=object(),
            client_instance_token=f"client-{spec.arm_id}",
            runtime_fingerprint=(
                _frozen_input.direct_backend_fingerprint
                if spec.group == "A"
                else _frozen_input.codex_backend_fingerprint
            ),
        )

    result = BackendAabbRunner(
        execute_arm=execute,
        pending_writer=_Writer(tmp_path),
        experiment_store=store,
    ).run(_frozen(), CancelToken())

    assert result.status == "protocol_invalid"
    assert "persisted_market_snapshot_hash_mismatch" in result.protocol_report["errors"]


def test_protocol_rejects_effective_runtime_fingerprint_drift(tmp_path: Path) -> None:
    store = AnalysisExperimentStore(tmp_path / "audit.sqlite3", tmp_path / "manifests")

    def execute(spec, frozen_input, _cancel):
        backend = "direct" if spec.group == "A" else "codex_agent"
        expected = (
            frozen_input.direct_backend_fingerprint
            if spec.group == "A"
            else frozen_input.codex_backend_fingerprint
        )
        return BackendArmExecution(
            record=_record(f"run-{spec.arm_id}", backend),
            runtime_identity=object(),
            client_instance_token=f"client-{spec.arm_id}",
            runtime_fingerprint="d" * 64 if spec.arm_id == "B2" else expected,
        )

    result = BackendAabbRunner(
        execute_arm=execute,
        pending_writer=_Writer(tmp_path),
        experiment_store=store,
    ).run(_frozen(), CancelToken())

    assert result.status == "protocol_invalid"
    assert "runtime_backend_fingerprint_mismatch" in result.protocol_report["errors"]


def test_repeat_attempt_cannot_hide_an_earlier_assigned_experiment(tmp_path: Path) -> None:
    store = AnalysisExperimentStore(tmp_path / "audit.sqlite3", tmp_path / "manifests")

    def execute(spec, frozen_input, _cancel):
        backend = "direct" if spec.group == "A" else "codex_agent"
        return BackendArmExecution(
            record=_record(f"run-{spec.arm_id}", backend),
            runtime_identity=object(),
            client_instance_token=f"client-{spec.arm_id}",
            runtime_fingerprint=(
                frozen_input.direct_backend_fingerprint
                if spec.group == "A"
                else frozen_input.codex_backend_fingerprint
            ),
        )

    runner = BackendAabbRunner(
        execute_arm=execute,
        pending_writer=_Writer(tmp_path),
        experiment_store=store,
    )
    frozen = _frozen()
    assert runner.run(frozen, CancelToken()).status == "completed"
    assert runner.run(frozen, CancelToken()).status == "completed"

    report = summarize_backend_experiments(store, min_unique_snapshots=1)

    assert report["unique_snapshots"] == 1
    assert report["attempted_experiments"] == 2
    assert report["repeat_conflict_snapshots"] == 1
    assert report["complete_four_arm_snapshots"] == 0
    assert report["engineering_repeatability_reviewable"] is False
