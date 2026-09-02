from __future__ import annotations

import json

import pytest

from pa_agent.operations.analysis_profile import AnalysisProfile
from pa_agent.operations.runtime_state import (
    RuntimeControlState,
    RuntimeControlConflict,
    RuntimeControlStore,
    RuntimeStatusStore,
    control_state_to_dict,
)


def test_control_store_persists_pause_and_resume_atomically(tmp_path) -> None:
    path = tmp_path / "runtime" / "control.json"
    store = RuntimeControlStore(path, now_ms=lambda: 1234)

    assert store.read().desired_state == "running"
    assert store.read().desired_backend == "deepseek_only"
    assert store.read().schema_version == 3
    assert store.read().analysis_profile == AnalysisProfile()
    assert store.read().run_once_generation == 0

    paused = store.set_desired_state("paused", actor="dashboard")
    assert paused.generation == 1
    assert paused.desired_state == "paused"
    assert not list(path.parent.glob("*.tmp"))

    resumed = store.set_desired_state("running", actor="dashboard")
    assert resumed.generation == 2
    assert store.read() == resumed


def test_control_store_updates_backend_without_changing_run_state(tmp_path) -> None:
    store = RuntimeControlStore(tmp_path / "runtime" / "control.json", now_ms=lambda: 9)
    store.set_desired_state("paused", actor="dashboard")

    selected = store.set_desired_backend("auto", actor="dashboard")

    assert selected.desired_state == "paused"
    assert selected.desired_backend == "auto"
    assert selected.generation == 2
    assert store.read() == selected


def test_corrupt_control_state_fails_closed_without_raising(tmp_path) -> None:
    path = tmp_path / "runtime" / "control.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")

    state = RuntimeControlStore(path).read()

    assert state.desired_state == "paused"
    assert state.error == "invalid_control_state"


@pytest.mark.parametrize("raw", ["null", "[]", '"control"', "7"])
def test_non_object_control_json_fails_closed_without_raising(tmp_path, raw: str) -> None:
    path = tmp_path / "runtime" / "control.json"
    path.parent.mkdir(parents=True)
    path.write_text(raw, encoding="utf-8")

    state = RuntimeControlStore(path).read()

    assert state.desired_state == "paused"
    assert state.error == "invalid_control_state"


def test_v1_control_migrates_to_direct_and_invalid_backend_fails_closed(tmp_path) -> None:
    path = tmp_path / "runtime" / "control.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"schema_version": 1, "desired_state": "running", "generation": 3}),
        encoding="utf-8",
    )
    store = RuntimeControlStore(path)

    migrated = store.read()
    assert migrated.desired_backend == "deepseek_only"
    assert migrated.generation == 3
    assert migrated.schema_version == 3
    assert migrated.analysis_profile == AnalysisProfile()

    path.write_text(
        json.dumps({
            "schema_version": 2,
            "desired_state": "running",
            "desired_backend": "unbounded",
        }),
        encoding="utf-8",
    )
    rejected = store.read()
    assert rejected.desired_state == "paused"
    assert rejected.desired_backend == "deepseek_only"
    assert rejected.error == "invalid_control_state"


def test_v3_control_requires_explicit_profile_and_identity(tmp_path) -> None:
    path = tmp_path / "runtime" / "control.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "schema_version": 3,
            "desired_state": "running",
            "desired_backend": "deepseek_only",
            "generation": 1,
        }),
        encoding="utf-8",
    )

    state = RuntimeControlStore(path).read()

    assert state.desired_state == "paused"
    assert state.error == "invalid_control_state"


def test_profile_compare_and_set_preserves_file_on_conflict(tmp_path) -> None:
    path = tmp_path / "runtime" / "control.json"
    store = RuntimeControlStore(path, now_ms=lambda: 42)
    updated = store.set_analysis_profile(
        AnalysisProfile(deepseek_model="deepseek-v4-pro"),
        expected_generation=0,
        actor="dashboard",
    )
    before = path.read_bytes()

    assert updated.generation == 1
    assert updated.analysis_profile.deepseek_model == "deepseek-v4-pro"
    assert updated.profile_generation == 1
    assert updated.profile_updated_at_ms == 42
    assert updated.profile_actor == "dashboard"
    assert len(updated.profile_fingerprint) == 64
    with pytest.raises(RuntimeControlConflict):
        store.set_analysis_profile(
            AnalysisProfile(), expected_generation=0, actor="stale-dashboard"
        )
    assert path.read_bytes() == before


def test_control_state_derives_fingerprint_from_non_default_profile(tmp_path) -> None:
    path = tmp_path / "runtime" / "control.json"
    path.parent.mkdir(parents=True)
    state = RuntimeControlState(
        desired_state="paused",
        analysis_profile=AnalysisProfile(deepseek_model="deepseek-v4-pro"),
    )
    path.write_text(json.dumps(control_state_to_dict(state)), encoding="utf-8")

    loaded = RuntimeControlStore(path).read()

    assert loaded.error is None
    assert loaded.analysis_profile.deepseek_model == "deepseek-v4-pro"
    assert loaded.profile_fingerprint == state.profile_fingerprint


def test_run_once_generation_is_monotonic_and_preserves_profile(tmp_path) -> None:
    store = RuntimeControlStore(tmp_path / "runtime" / "control.json", now_ms=lambda: 9)
    paused = store.set_desired_state("paused", actor="dashboard")
    selected = store.set_analysis_profile(
        AnalysisProfile(codex_model="gpt-5.6-luna"),
        expected_generation=paused.generation,
        actor="dashboard",
    )

    requested = store.request_run_once(
        expected_generation=selected.generation,
        actor="dashboard",
    )

    assert requested.generation == 3
    assert requested.run_once_generation == 1
    assert requested.desired_state == "paused"
    assert requested.analysis_profile == selected.analysis_profile
    assert store.read() == requested


def test_status_store_rejects_secret_shaped_fields(tmp_path) -> None:
    store = RuntimeStatusStore(tmp_path / "runtime" / "status.json", now_ms=lambda: 2000)

    store.write({"service_state": "running", "assets": [], "scheduler": {}})
    assert store.read()["updated_at_ms"] == 2000

    try:
        store.write({"api_key": "secret"})
    except ValueError as exc:
        assert "secret-bearing" in str(exc)
    else:  # pragma: no cover - assertion aid
        raise AssertionError("secret-shaped status field was accepted")

    raw = json.loads((tmp_path / "runtime" / "status.json").read_text("utf-8"))
    assert "api_key" not in raw


def test_status_store_fails_closed_on_valid_json_with_invalid_types(tmp_path) -> None:
    path = tmp_path / "runtime" / "status.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "schema_version": 1,
            "service_state": "running",
            "updated_at_ms": "yesterday",
            "assets": {},
        }),
        encoding="utf-8",
    )

    assert RuntimeStatusStore(path).read() is None


def test_status_store_rejects_invalid_nested_dashboard_rows(tmp_path) -> None:
    store = RuntimeStatusStore(tmp_path / "runtime" / "status.json")

    for payload in (
        {"service_state": "running", "assets": [None]},
        {"service_state": "running", "recent_runs": ["invalid"]},
        {
            "service_state": "running",
            "paper": {"accounts": [None]},
        },
    ):
        try:
            store.write(payload)
        except TypeError:
            continue
        raise AssertionError(f"invalid nested status was accepted: {payload}")
