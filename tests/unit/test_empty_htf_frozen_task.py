"""Empty HTF input is frozen once; restoration never rewrites stored identity."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace

import pytest

from pa_agent.config.settings import Settings
from pa_agent.monitoring.audit import SchedulerAuditStore
from pa_agent.monitoring.frozen_task_codec import decode_task, encode_task
from pa_agent.monitoring.scheduler import AnalysisTask, SubmissionResult
from pa_agent.monitoring.task_factory import _snapshot_hash, create_frozen_analysis_task
from tests.unit.test_monitoring_task_factory import _frame


def _task(htf):
    return create_frozen_analysis_task(_frame(), htf, Settings(), source="tradingview",
        venue="OANDA", analysis_fingerprint="a" * 64, created_at_ms=10_000)


def _seal(raw):
    encoded = json.dumps(raw, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return encoded, hashlib.sha256(encoded.encode()).hexdigest()


@pytest.mark.parametrize("status", [None, "empty", "available", "unavailable", "disabled"])
def test_empty_or_explicit_htf_roundtrips_without_identity_or_fact_drift(status):
    htf = None if status is None else {} if status == "empty" else {
        "status": status, "current_timeframe": "1h", "timeframe": "4h",
        "reason": "test", "metadata": {"source": "tradingview"},
    }
    if status == "available":
        htf["frame"] = replace(_frame(), timeframe="4h")
    before = copy.deepcopy(htf)
    task = _task(htf)
    encoded, checksum = encode_task(task)
    restored = decode_task(encoded, checksum)
    assert restored == task
    assert restored.payload.htf_context == ({} if htf is None else before)
    assert restored.snapshot_hash == task.snapshot_hash
    assert restored.task_id == task.task_id
    assert encode_task(restored) == (encoded, checksum)
    assert htf == before


def test_missing_htf_is_normalized_before_identity_and_matches_explicit_empty_input():
    implicit, explicit = _task(None), _task({})
    assert implicit.payload.htf_context == explicit.payload.htf_context == {}
    assert implicit.task_id == explicit.task_id
    assert implicit.snapshot_hash == explicit.snapshot_hash
    assert implicit.cohort_id == explicit.cohort_id


def test_new_empty_htf_task_recovers_from_persisted_audit_without_reanalysis(tmp_path):
    task = _task(None)
    path = tmp_path / "monitoring.sqlite3"
    store = SchedulerAuditStore(path, require_frozen_payload=True)
    assert store.record_submission(task, SubmissionResult("accepted", task.task_id, task.asset_key))
    recovered = SchedulerAuditStore(path, require_frozen_payload=True).load_incomplete_tasks()
    assert len(recovered) == 1
    assert recovered[0].task_id == task.task_id
    assert recovered[0].snapshot_hash == task.snapshot_hash
    assert recovered[0].payload.htf_context == {}


def test_legacy_none_hash_with_empty_payload_remains_rejected_and_is_not_repaired():
    reference = _task({})
    # Pre-fix factory hashed None but persisted {}. Keep its actual original hash.
    legacy = AnalysisTask.create(source=reference.source, venue=reference.venue,
        symbol=reference.symbol, timeframe=reference.timeframe,
        snapshot_hash=_snapshot_hash(reference.payload.frame, None, None),
        cohort_id=reference.cohort_id, created_at_ms=reference.created_at_ms,
        payload=reference.payload)
    encoded, checksum = encode_task(legacy)
    assert json.loads(encoded)["payload"]["htf_context"] == {}
    assert legacy.snapshot_hash != reference.snapshot_hash
    with pytest.raises(ValueError, match="frozen market snapshot mismatch"):
        decode_task(encoded, checksum)
    assert encode_task(legacy) == (encoded, checksum)


@pytest.mark.parametrize("htf", [None, [], False])
def test_non_object_frozen_htf_is_explicitly_rejected_even_with_resealed_envelope(htf):
    encoded, _ = encode_task(_task({}))
    raw = json.loads(encoded)
    raw["payload"]["htf_context"] = htf
    with pytest.raises(ValueError, match="frozen higher-timeframe context must be an object"):
        decode_task(*_seal(raw))


def test_valid_htf_object_tampering_still_fails_inner_snapshot_verification():
    encoded, _ = encode_task(_task({}))
    raw = json.loads(encoded)
    raw["payload"]["htf_context"] = {"status": "disabled"}
    with pytest.raises(ValueError, match="frozen market snapshot mismatch"):
        decode_task(*_seal(raw))
