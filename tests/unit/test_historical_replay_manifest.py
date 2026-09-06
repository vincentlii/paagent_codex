from __future__ import annotations

import json

import pytest

from pa_agent.experiments.historical_replay_manifest import (
    load_historical_failure_manifest,
    verify_historical_failure_records,
)


def test_historical_failure_cohort_is_fixed_at_twenty_unique_records() -> None:
    manifest = load_historical_failure_manifest()

    assert manifest["cohort_id"] == "windows-vnext-historical-failures-20"
    assert len(manifest["cases"]) == 20
    assert len({case["sha256"] for case in manifest["cases"]}) == 20
    assert manifest["baseline_status_counts"] == {
        "completed_with_warnings": 11,
        "invalid_model_output": 9,
    }
    assert manifest["baseline_audit_code_counts"]["provider_draft_contract"] == 8


def test_historical_failure_verification_is_fail_closed(tmp_path) -> None:
    manifest = load_historical_failure_manifest()
    first = manifest["cases"][0]
    (tmp_path / first["record"]).write_text(json.dumps({"not": "the source"}))

    with pytest.raises(ValueError, match="digest mismatch"):
        verify_historical_failure_records(tmp_path)
