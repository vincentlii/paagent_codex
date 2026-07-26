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
