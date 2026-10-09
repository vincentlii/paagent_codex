from __future__ import annotations

import subprocess
import sys

from pa_agent.config.settings import Settings
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.data.instrument import InstrumentSpec
from pa_agent.monitoring.audit import SchedulerAuditStore
from pa_agent.monitoring.production import ProductionOrchestratorFactory
from pa_agent.monitoring.scheduler import JobCompletion, SubmissionResult
from pa_agent.monitoring.service import MultiAssetAnalysisService
from pa_agent.monitoring.task_factory import create_frozen_analysis_task
from pa_agent.operations.analysis_profile import AnalysisProfile
from pa_agent.orchestrator.two_stage import _build_empty_record
from pa_agent.records.audit_store import AnalysisAuditStore
from pa_agent.records.pending_writer import PendingWriter
from pa_agent.records.schema import AnalysisTiming


def _task():
    frame = KlineFrame(
        symbol="XAUUSD",
        timeframe="1h",
        bars=(
            KlineBar(seq=1, ts_open=1_800_000_000_000, open=100, high=102,
                     low=99, close=101, volume=1000),
        ),
        indicators=IndicatorBundle(ema20=(100.5,), atr14=(2.0,)),
        snapshot_ts_local_ms=1_800_003_600_000,
        data_metadata={"analysis_as_of_ms": 1_800_003_600_000},
    )
    spec = InstrumentSpec(
        provider="binance", venue="BINANCE", symbol="XAUUSD",
        instrument_type="perpetual", tick_size=0.01, quantity_step=0.001,
        minimum_quantity=0.001, maximum_quantity=1000,
        minimum_notional=5,
    )
    return create_frozen_analysis_task(
        frame, {"status": "disabled", "reason": "test"}, Settings(),
        source="binance", venue="BINANCE", analysis_fingerprint="a" * 64,
        instrument_spec=spec,
    )


def test_accepted_task_can_be_reloaded_from_a_fresh_audit_store(tmp_path):
    task = _task()
    path = tmp_path / "monitoring.sqlite3"
    store = SchedulerAuditStore(path, require_frozen_payload=True)
    assert store.record_submission(
        task, SubmissionResult("accepted", task.task_id, task.asset_key)
    )

    restarted = SchedulerAuditStore(path, require_frozen_payload=True)
    restored = restarted.load_incomplete_tasks()
    assert len(restored) == 1
    assert restored[0].task_id == task.task_id
    assert restored[0].payload.frame == task.payload.frame
    assert restored[0].payload.htf_context == task.payload.htf_context
    assert restored[0].payload.instrument_spec == task.payload.instrument_spec


def test_recovery_keeps_htf_and_incremental_record_frozen(tmp_path):
    base = _task()
    previous = _build_empty_record(base.payload.frame, None)
    task = create_frozen_analysis_task(
        base.payload.frame,
        {"status": "available", "frame": base.payload.frame, "timeframe": "4h"},
        Settings(), source="binance", venue="BINANCE",
        analysis_fingerprint="a" * 64, previous_record=previous,
        incremental_new_bar_count=1,
    )
    store = SchedulerAuditStore(tmp_path / "monitoring.sqlite3", require_frozen_payload=True)
    assert store.record_submission(
        task, SubmissionResult("accepted", task.task_id, task.asset_key)
    )
    recovered = SchedulerAuditStore(store.db_path, require_frozen_payload=True).load_incomplete_tasks()[0]
    assert recovered.payload.htf_context["frame"] == task.payload.htf_context["frame"]
    assert recovered.payload.previous_record.meta.run_id == previous.meta.run_id


def test_started_task_is_not_offered_for_automatic_dispatch(tmp_path):
    task = _task()
    store = SchedulerAuditStore(tmp_path / "monitoring.sqlite3", require_frozen_payload=True)
    assert store.record_submission(
        task, SubmissionResult("accepted", task.task_id, task.asset_key)
    )
    assert store.mark_started(task.task_id)

    restarted = SchedulerAuditStore(store.db_path, require_frozen_payload=True)
    assert restarted.load_incomplete_tasks() == []
    assert [row["task_id"] for row in restarted.list_unresolved_tasks()] == [task.task_id]


def test_cancelled_started_task_remains_in_doubt_across_restart(tmp_path):
    task = _task()
    store = SchedulerAuditStore(tmp_path / "monitoring.sqlite3", require_frozen_payload=True)
    assert store.record_submission(
        task, SubmissionResult("accepted", task.task_id, task.asset_key)
    )
    assert store.mark_started(task.task_id)
    assert store.record_completion(JobCompletion(task=task, status="cancelled"))
    assert store.load_incomplete_tasks() == []
    assert [row["task_id"] for row in store.list_unresolved_tasks()] == [task.task_id]


def test_headless_record_retains_scheduler_task_identity():
    task = _task()
    record = _build_empty_record(
        task.payload.frame, None, monitoring_task_id=task.task_id,
    )
    assert record.monitoring_task_id == task.task_id
    assert record.model_dump(mode="json")["monitoring_task_id"] == task.task_id


def test_service_marks_start_before_running_handler(tmp_path):
    task = _task()
    store = SchedulerAuditStore(tmp_path / "monitoring.sqlite3", require_frozen_payload=True)
    observed = []

    def handler(_task, _cancel):
        observed.append(store.list_unresolved_tasks()[0]["started_at_ms"])
        return None

    service = MultiAssetAnalysisService(handler, audit_store=store)
    assert service.submit(task).accepted
    assert service.wait_idle(2)
    service.shutdown()
    assert len(observed) == 1 and observed[0] is not None
    assert store.list_unresolved_tasks() == []


def test_service_resumes_only_unstarted_accepted_task(tmp_path):
    task = _task()
    path = tmp_path / "monitoring.sqlite3"
    first = SchedulerAuditStore(path, require_frozen_payload=True)
    assert first.record_submission(
        task, SubmissionResult("accepted", task.task_id, task.asset_key)
    )
    restarted = SchedulerAuditStore(path, require_frozen_payload=True)
    called = []
    service = MultiAssetAnalysisService(
        lambda recovered, _cancel: called.append(recovered.task_id),
        audit_store=restarted,
    )
    assert service.recover_unstarted(restarted.load_incomplete_tasks()[0]).accepted
    assert service.wait_idle(2)
    service.shutdown()
    assert called == [task.task_id]


def test_saved_result_reconciles_without_a_second_model_call(tmp_path):
    from pa_agent.monitoring.recovery import reconcile_saved_tasks

    task = _task()
    store = SchedulerAuditStore(tmp_path / "monitoring.sqlite3", require_frozen_payload=True)
    assert store.record_submission(
        task, SubmissionResult("accepted", task.task_id, task.asset_key)
    )
    assert store.mark_started(task.task_id)
    record = _build_empty_record(
        task.payload.frame, None, monitoring_task_id=task.task_id,
    )
    record.stage1_diagnosis = {"gate_result": "proceed"}
    record.stage2_decision = {"order_type": "no_trade"}
    record.run_outcome = {"status": "completed", "official_path": "joint"}
    record.analysis_timing = AnalysisTiming(
        analysis_as_of_ms=1_800_003_600_000,
        model_completed_at_ms=1_800_003_600_100,
        validated_at_ms=1_800_003_600_200,
        result_available_at_ms=1_800_003_600_300,
        decision_expires_at_ms=1_800_007_200_000,
    )
    records = tmp_path / "records" / "pending"
    index = AnalysisAuditStore(tmp_path / "records" / "audit" / "analysis.sqlite3")
    assert PendingWriter(records, audit_store=index).save_full(record).ok

    blocked = reconcile_saved_tasks(store, records, index)
    assert blocked == []
    assert store.was_successful(task.task_id)
    assert store.list_successful_jobs() == [{"task_id": task.task_id, "run_id": record.meta.run_id}]


def test_codex_factory_uses_task_frozen_profile_after_restart(tmp_path):
    profile = AnalysisProfile(codex_model="gpt-6-luna", codex_reasoning_effort="xhigh")
    original = _task()
    task = create_frozen_analysis_task(
        original.payload.frame, original.payload.htf_context, Settings(),
        source="binance", venue="BINANCE", analysis_fingerprint="a" * 64,
        analysis_backend_mode="codex_only",
        analysis_backend_identity={"runtime_asset_digest": "b" * 64},
        analysis_profile=profile,
    )
    factory = ProductionOrchestratorFactory(
        settings=Settings(), pending_writer=object(), prompt_dir=tmp_path,
        experience_dir=tmp_path, codex_backend_factory=lambda *_args: object(),
    )
    frozen = factory._frozen_profile_for_task(task)
    assert frozen.codex_model == "gpt-6-luna"
    assert frozen.codex_reasoning_effort == "xhigh"


def test_process_exit_after_accept_recovers_exact_frozen_task(tmp_path):
    path = tmp_path / "monitoring.sqlite3"
    code = (
        "import os, sys; "
        "from pathlib import Path; "
        "from tests.unit.test_monitoring_recovery import _task; "
        "from pa_agent.monitoring.audit import SchedulerAuditStore; "
        "from pa_agent.monitoring.scheduler import SubmissionResult; "
        "task = _task(); "
        "store = SchedulerAuditStore(Path(sys.argv[1]), require_frozen_payload=True); "
        "ok = store.record_submission(task, SubmissionResult('accepted', task.task_id, task.asset_key)); "
        "os._exit(67 if ok else 68)"
    )
    exited = subprocess.run([sys.executable, "-c", code, str(path)], check=False)
    assert exited.returncode == 67
    restored = SchedulerAuditStore(path, require_frozen_payload=True).load_incomplete_tasks()
    assert len(restored) == 1
    assert restored[0].payload.frame.bars[0].close == 101


def test_process_exit_after_start_remains_blocked_without_record(tmp_path):
    from pa_agent.monitoring.recovery import reconcile_saved_tasks

    path = tmp_path / "monitoring.sqlite3"
    code = (
        "import os, sys; "
        "from pathlib import Path; "
        "from tests.unit.test_monitoring_recovery import _task; "
        "from pa_agent.monitoring.audit import SchedulerAuditStore; "
        "from pa_agent.monitoring.scheduler import SubmissionResult; "
        "task = _task(); "
        "store = SchedulerAuditStore(Path(sys.argv[1]), require_frozen_payload=True); "
        "store.record_submission(task, SubmissionResult('accepted', task.task_id, task.asset_key)); "
        "ok = store.mark_started(task.task_id); "
        "os._exit(67 if ok else 68)"
    )
    exited = subprocess.run([sys.executable, "-c", code, str(path)], check=False)
    assert exited.returncode == 67
    store = SchedulerAuditStore(path, require_frozen_payload=True)
    assert store.load_incomplete_tasks() == []
    assert reconcile_saved_tasks(
        store, tmp_path / "records", AnalysisAuditStore(tmp_path / "analysis.sqlite3")
    ) == [store.list_unresolved_tasks()[0]["task_id"]]
