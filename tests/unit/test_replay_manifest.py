import json
from pathlib import Path

from pa_agent.quality.replay_manifest import build_replay_manifest, write_replay_manifest
from pa_agent.records.codec import dump_record
from pa_agent.records.schema import AnalysisRecord, RecordMeta


def _record(run_id: str, *, close: float = 100.0) -> dict:
    return dump_record(
        AnalysisRecord(
            meta=RecordMeta(
                timestamp_local_iso="2026-07-22T12:00:00+08:00",
                timestamp_local_ms=1_700_000_000_000,
                run_id=run_id,
                symbol="TEST",
                timeframe="15m",
                bar_count=1,
                ai_provider={"model": "test"},
            ),
            kline_data=[
            {
                "seq": 1,
                "ts_open": 1_700_000_000_000,
                "open": close - 1,
                "high": close + 1,
                "low": close - 2,
                "close": close,
                "volume": 10,
                "closed": True,
            }
            ],
            htf_text="",
            htf_context={"timeframe": "1h"},
            stage1_messages=[],
            stage2_messages=[],
            strategy_files_used=[],
            experience_loaded=[],
        )
    )


def test_manifest_is_deterministic_and_does_not_copy_market_payload(tmp_path: Path) -> None:
    first = tmp_path / "b.json"
    second = tmp_path / "a.json"
    first.write_text(json.dumps(_record("run-b", close=101.0)), encoding="utf-8")
    second.write_text(json.dumps(_record("run-a")), encoding="utf-8")

    manifest = build_replay_manifest([first, second])

    assert [case["run_id"] for case in manifest["cases"]] == ["run-a", "run-b"]
    assert manifest["case_count"] == 2
    assert manifest["invalid_records"] == []
    assert "kline_data" not in manifest["cases"][0]
    assert len(manifest["cases"][0]["snapshot_id"]) == 64


def test_manifest_reports_duplicates_and_invalid_records(tmp_path: Path) -> None:
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    invalid = tmp_path / "invalid.json"
    first.write_text(json.dumps(_record("same")), encoding="utf-8")
    second.write_text(json.dumps(_record("same")), encoding="utf-8")
    invalid.write_text("{", encoding="utf-8")

    manifest = build_replay_manifest([first, second, invalid])

    assert manifest["duplicate_run_ids"] == ["same"]
    assert len(manifest["duplicate_snapshot_ids"]) == 1
    assert manifest["invalid_records"][0]["path"] == "invalid.json"


def test_write_manifest_creates_parent_directory(tmp_path: Path) -> None:
    records = tmp_path / "records"
    records.mkdir()
    (records / "run.json").write_text(json.dumps(_record("run")), encoding="utf-8")
    output = tmp_path / "generated" / "manifest.json"

    result = write_replay_manifest(records, output)

    assert result["case_count"] == 1
    assert json.loads(output.read_text(encoding="utf-8"))["case_count"] == 1

