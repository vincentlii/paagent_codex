import json

from pa_agent.records.pending_writer import PendingWriter
from pa_agent.records.schema import AnalysisTiming
from tests.unit.test_joint_pipeline_runtime import run_joint


def timed_record():
    record, _, _ = run_joint()
    record.analysis_timing = AnalysisTiming(
        analysis_as_of_ms=1000,
        model_completed_at_ms=1100,
        validated_at_ms=1200,
        decision_expires_at_ms=2000,
    )
    return record


def test_writer_records_availability_only_after_primary_persistence(tmp_path, monkeypatch):
    record = timed_record()
    writer = PendingWriter(pending_dir=tmp_path / "pending")
    original = writer._write_json
    observed = []

    def write(path, data):
        observed.append(data.get("analysis_timing", {}).get("result_available_at_ms"))
        return original(path, data)

    monkeypatch.setattr(writer, "_write_json", write)
    result = writer.save_full(record)
    assert result.ok and result.audit_ok
    assert observed[0] is None
    assert record.analysis_timing.result_available_at_ms is not None
    raw = json.loads(result.path.read_text(encoding="utf-8"))
    assert (
        raw["analysis_timing"]["result_available_at_ms"]
        == record.analysis_timing.result_available_at_ms
    )


def test_disk_failure_never_publishes_availability(tmp_path, monkeypatch):
    record = timed_record()
    writer = PendingWriter(pending_dir=tmp_path / "pending")
    monkeypatch.setattr(writer, "_write_json", lambda *args: False)
    assert not writer.save_full(record).ok
    assert record.analysis_timing.result_available_at_ms is None


def test_new_pipeline_plan_requires_delivery_and_rejects_stale_result():
    from pa_agent.execution.plans import official_paper_plan
    from pa_agent.config.analysis_pipeline import AnalysisPipelineIdentity
    from tests.unit.test_execution_plans import _record, _spec
    import pytest

    record = _record(path="joint", mode="off")
    record.analysis_pipeline = AnalysisPipelineIdentity(pipeline="pa_vpa_joint")
    record.stage2_decision["analysis_basis"] = {"protocol_version": "pa_vpa_stage2_draft_v1"}
    record.vpa_evidence = {"catalog_id": "validated"}
    with pytest.raises(ValueError, match="delivery"):
        official_paper_plan(record, _spec(), dedupe_key="task")
    record.kline_data[0]["ts_open"] = 1_800_000_000_000
    record.analysis_timing = AnalysisTiming(
        analysis_as_of_ms=1_800_003_600_000,
        model_completed_at_ms=1_800_003_600_100,
        validated_at_ms=1_800_003_600_200,
        result_available_at_ms=1_800_003_600_300,
        decision_expires_at_ms=1_800_007_200_000,
    )
    plan = official_paper_plan(record, _spec(), dedupe_key="task", now_ms=1_800_003_600_500)
    assert plan.available_at_ms == 1_800_003_600_500
    assert plan.arm == "pa_vpa_joint"
    assert plan.take_profit_price_2 is None
    with pytest.raises(ValueError, match="expired"):
        official_paper_plan(record, _spec(), dedupe_key="task", now_ms=1_800_007_200_000)


def test_delivery_uses_observed_response_time_and_frozen_next_close():
    from pa_agent.execution.delivery import validated_delivery

    record = timed_record()
    record.role_runtime["analysis_as_of_ms"] = 1_800_003_600_000
    record.meta.timeframe = "1h"
    record.kline_data = [{"ts_open": 1_800_000_000_000}]
    record.call_attempts = [{"response_hash": "a", "created_at_ms": 1_800_003_600_100}]
    timing = validated_delivery(record, now_ms=1_800_003_600_200)
    assert timing.model_completed_at_ms == 1_800_003_600_100
    assert timing.decision_expires_at_ms == 1_800_007_200_000
    assert timing.result_available_at_ms is None
    assert validated_delivery(record, now_ms=1_800_003_600_050) is None
    record.role_runtime.pop("analysis_as_of_ms")
    assert validated_delivery(record, now_ms=1_800_003_600_200) is None


def test_receipt_write_failure_leaves_no_available_record(tmp_path, monkeypatch):
    record = timed_record()
    writer = PendingWriter(pending_dir=tmp_path / "pending")
    original = writer._write_json
    calls = []

    def write(path, data):
        calls.append(path)
        return original(path, data) if len(calls) == 1 else False

    monkeypatch.setattr(writer, "_write_json", write)
    result = writer.save_full(record)
    assert not result.ok
    assert record.analysis_timing.result_available_at_ms is None
    assert (
        "result_available_at_ms"
        not in json.loads(result.path.read_text(encoding="utf-8"))["analysis_timing"]
    )
