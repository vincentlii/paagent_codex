from __future__ import annotations

import copy

import pytest

from pa_agent.records.codec import (
    UnsupportedRecordVersion,
    decode_record,
    dump_record,
)
from pa_agent.records.schema import AnalysisRecord, RecordMeta


def _record() -> AnalysisRecord:
    calls = [
        {
            "arm": "pa",
            "stage": "stage1",
            "attempt": 1,
            "request_hash": "1" * 64,
            "response_hash": "2" * 64,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cached_prompt_tokens": 0,
            "total_tokens": 15,
            "latency_ms": 20.0,
            "status": "validated",
            "created_at_ms": 1,
        }
    ]
    return AnalysisRecord(
        meta=RecordMeta(
            timestamp_local_iso="2026-07-22T12:00:00+08:00",
            timestamp_local_ms=1_784_694_400_000,
            run_id="codec-test",
            symbol="XAUUSD",
            timeframe="15m",
            bar_count=1,
            ai_provider={"model": "test"},
        ),
        kline_data=[],
        htf_text="",
        stage1_messages=[],
        stage1_response=None,
        stage1_diagnosis=None,
        stage2_messages=[],
        stage2_response=None,
        stage2_decision=None,
        strategy_files_used=[],
        experience_loaded=[],
        exception=None,
        call_attempts=calls,
        pa_hypotheses={"primary": {"hypothesis_id": "pa-primary"}},
        vpa_catalog={"catalog_id": "catalog-1"},
        vpa_evidence={"data_status": "available"},
        role_statuses={"vpa": "validated", "joint": "not_applicable"},
    )


def test_canonical_v5_round_trip_has_no_derived_duplicates() -> None:
    record = _record()

    persisted = dump_record(record)
    loaded = decode_record(persisted)

    assert loaded.replayable
    assert loaded.record == record
    assert persisted["schema_version"] == 5
    assert "usage_total" not in persisted
    assert "vpa_sidecar" not in persisted
    assert loaded.record.usage_total["total_tokens"] == 15


def test_missing_pipeline_and_delivery_are_not_backfilled() -> None:
    persisted = dump_record(_record())
    assert "analysis_pipeline" not in persisted
    assert "analysis_timing" not in persisted
    restored = decode_record(persisted).record
    assert restored.analysis_pipeline is None
    assert restored.analysis_timing is None


def test_versioned_delivery_roundtrip_preserves_late_result() -> None:
    raw = dump_record(_record())
    raw["analysis_pipeline"] = {"pipeline": "pa_vpa_joint"}
    raw["analysis_timing"] = {
        "analysis_as_of_ms": 1000,
        "model_completed_at_ms": 2100,
        "validated_at_ms": 2200,
        "result_available_at_ms": 2300,
        "decision_expires_at_ms": 2000,
    }
    record = decode_record(raw).record
    restored = decode_record(dump_record(record)).record
    assert restored == record
    assert restored.analysis_timing.decision_expires_at_ms == 2000


@pytest.mark.parametrize("overrides", [
    {"model_completed_at_ms": 999},
    {"validated_at_ms": 1200},
    {"result_available_at_ms": 1300},
    {"decision_expires_at_ms": 1000},
    {"analysis_as_of_ms": True},
])
def test_delivery_rejects_invented_or_reversed_times(overrides) -> None:
    from pydantic import ValidationError
    from pa_agent.records.schema import AnalysisTiming

    with pytest.raises(ValidationError):
        AnalysisTiming.model_validate({"analysis_as_of_ms": 1000, **overrides})


def test_nondecision_stop_roundtrip_and_rejects_false_success() -> None:
    from pydantic import ValidationError

    raw = dump_record(_record())
    raw["role_stop"] = {"status": "review_required", "role": "stage2", "reason": "upstream contradiction"}
    raw["run_outcome"] = {"status": "review_required", "official_path": "none"}
    assert decode_record(raw).record.role_stop.status == "review_required"
    with pytest.raises(ValidationError, match="final decision"):
        decode_record({**raw, "stage2_decision": {"decision": {"order_type": "不下单"}}})
    with pytest.raises(ValidationError, match="stop status"):
        decode_record({**raw, "run_outcome": {"status": "completed", "official_path": "none"}})


def test_v4_sidecar_migrates_to_canonical_v5() -> None:
    canonical = dump_record(_record())
    raw = copy.deepcopy(canonical)
    raw["schema_version"] = 4
    catalog = raw.pop("vpa_catalog")
    report = raw.pop("vpa_evidence")
    hypotheses = raw.pop("pa_hypotheses")
    raw.pop("role_statuses")
    raw["usage_total"] = {"total_tokens": 999}
    raw["vpa_sidecar"] = {
        "evidence_catalog": catalog,
        "vpa_report": report,
        "pa_hypotheses": hypotheses,
        "role_execution": {"vpa_first_pass": True},
    }

    loaded = decode_record(raw)

    assert loaded.replayable
    assert loaded.record.schema_version == 5
    assert loaded.record.source_schema_version == 4
    assert loaded.record.vpa_catalog == catalog
    assert loaded.record.vpa_evidence == report
    assert loaded.record.usage_total["total_tokens"] == 15
    assert {issue.code for issue in loaded.migration_issues} == {
        "usage_ledger_mismatch"
    }


def test_v4_duplicate_conflict_is_visible_and_not_replayable() -> None:
    raw = dump_record(_record())
    raw["schema_version"] = 4
    raw["vpa_sidecar"] = {
        "pa_hypotheses": {"primary": {"hypothesis_id": "different"}}
    }

    loaded = decode_record(raw)

    assert not loaded.replayable
    assert [issue.code for issue in loaded.migration_issues] == [
        "migration_conflict"
    ]
    assert loaded.record.pa_hypotheses == raw["pa_hypotheses"]


def test_future_record_version_is_rejected() -> None:
    raw = dump_record(_record())
    raw["schema_version"] = 6

    with pytest.raises(UnsupportedRecordVersion):
        decode_record(raw)
