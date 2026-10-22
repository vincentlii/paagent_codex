from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from pa_agent.monitoring.analysis_runner import IndependentAnalysisHandler
from pa_agent.monitoring.frozen_task_codec import decode_task, encode_task
from pa_agent.monitoring.scheduler import AnalysisTask
from pa_agent.monitoring.task_factory import (
    _incremental_identity_payload,
    _snapshot_hash,
    create_frozen_analysis_task,
)
from pa_agent.config.settings import Settings
from pa_agent.records.schema import AnalysisRecord
from pa_agent.util.threading import OrchestratorEvent
from tests.unit.test_analysis_history import _record_with_latest, _with_identity
from tests.unit.test_monitoring_task_factory import _frame


def _previous(*, stopped: bool = False) -> AnalysisRecord:
    record = _with_identity(_record_with_latest(8_000))
    record.kline_data.append({**record.kline_data[0], "seq": 2, "ts_open": 7_000})
    value = record.model_dump(mode="json")
    value.update(
        analysis_pipeline={"protocol_version": "analysis_pipeline_v1", "pipeline": "pa_only"},
        analysis_timing={
            "analysis_as_of_ms": 1_000, "model_completed_at_ms": 1_100,
            "validated_at_ms": 1_200, "result_available_at_ms": 1_300,
        },
    )
    if stopped:
        value.update(
            stage2_decision=None,
            role_stop={"status": "incomplete", "role": "stage2", "reason": "missing evidence"},
            run_outcome={"status": "incomplete", "official_path": "none"},
        )
    return AnalysisRecord.model_validate(value)


def _task(previous=None, *, count=1):
    return create_frozen_analysis_task(
        _frame(), {}, Settings(), source="tradingview", venue="OANDA",
        analysis_fingerprint="a" * 64, previous_record=previous,
        incremental_new_bar_count=count, created_at_ms=10_000,
    )


def _encode_value(value):
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def test_new_history_is_versioned_frozen_and_forwarded_to_independent_run():
    previous = _previous()
    task = _task(previous)
    captured = {}

    class Orchestrator:
        def submit(self, frame, token, on_event, **kwargs):
            captured.update(kwargs)
            on_event(OrchestratorEvent.RecordSaved)
            return SimpleNamespace(run_outcome={"status": "completed"})

    assert task.payload.history_context_version == "decision_history_v2"
    assert decode_task(*encode_task(task)).payload.history_context_version == "decision_history_v2"
    IndependentAnalysisHandler(lambda task: Orchestrator())(task, None)
    assert captured["history_context_version"] == "decision_history_v2"
    frozen_bytes = encode_task(task)
    previous.snapshot_context["source_identity"]["venue"] = "MUTATED"
    previous.kline_data[1]["close"] = 777
    previous.run_outcome["status"] = "failed"
    assert encode_task(task) == frozen_bytes


@pytest.mark.parametrize("field", [
    "snapshot_context", "analysis_pipeline", "run_outcome", "exception",
    "role_stop", "analysis_timing", "kline_data",
])
def test_new_history_fields_cannot_change_under_original_task_identity(field):
    task = _task(_previous(stopped=field == "role_stop"))
    value = json.loads(encode_task(task)[0])
    historical = value["payload"]["previous_record"]
    if field == "snapshot_context":
        historical[field]["source_identity"]["venue"] = "OTHER"
    elif field == "analysis_pipeline":
        historical[field]["pipeline"] = "pa_vpa_joint"
    elif field == "run_outcome":
        historical[field]["reason"] = "changed"
    elif field == "exception":
        historical[field] = {"type": "provider_error", "failure_kind": "timeout"}
    elif field == "role_stop":
        historical[field]["reason"] = "changed"
    elif field == "analysis_timing":
        historical[field]["result_available_at_ms"] += 1
    else:
        # Legacy identity binds only the newest bar; the older mapping is new input.
        historical[field][1]["close"] = 999
    with pytest.raises(ValueError, match="frozen market snapshot mismatch"):
        decode_task(*_encode_value(value))


def test_legacy_history_identity_and_codec_remain_exactly_readable():
    previous = _previous()
    plain = previous.model_dump(mode="json")
    expected = {
        "new_bar_count": 1,
        "previous": {
            "meta": plain["meta"], "latest_bar": plain["kline_data"][0],
            "stage1_messages": plain["stage1_messages"],
            "stage1_response_content": None,
            "stage1_diagnosis": plain["stage1_diagnosis"],
            "stage2_decision": plain["stage2_decision"],
            "strategy_files_used": plain["strategy_files_used"],
        },
    }
    assert _incremental_identity_payload(previous, 1) == expected
    new = _task(previous)
    legacy = AnalysisTask.create(
        source=new.source, venue=new.venue, symbol=new.symbol, timeframe=new.timeframe,
        snapshot_hash=_snapshot_hash(new.payload.frame, {}, expected),
        cohort_id=new.cohort_id, created_at_ms=new.created_at_ms,
        payload=replace(new.payload, history_context_version=None),
    )
    encoded = encode_task(legacy)
    assert "history_context_version" not in json.loads(encoded[0])["payload"]
    assert encode_task(decode_task(*encoded)) == encoded
    assert new.task_id != legacy.task_id


def test_no_history_keeps_legacy_snapshot_task_identity_and_encoding():
    task = _task()
    ignored_history = _task(_previous(), count=0)
    assert task.payload.history_context_version is None
    assert task.snapshot_hash == _snapshot_hash(task.payload.frame, {}, None)
    assert ignored_history.task_id == task.task_id
    assert "history_context_version" not in json.loads(encode_task(task)[0])["payload"]
    assert encode_task(decode_task(*encode_task(task))) == encode_task(task)


def test_history_contract_cannot_be_removed_or_changed_on_decode():
    value = json.loads(encode_task(_task(_previous()))[0])
    for version in (None, "future_history"):
        changed = copy.deepcopy(value)
        if version is None:
            changed["payload"].pop("history_context_version")
        else:
            changed["payload"]["history_context_version"] = version
        with pytest.raises(ValueError):
            decode_task(*_encode_value(changed))


def test_unknown_history_contract_is_rejected_before_identity_projection():
    with pytest.raises(ValueError, match="history context version"):
        _incremental_identity_payload(_previous(), 1, history_context_version="future_history")
