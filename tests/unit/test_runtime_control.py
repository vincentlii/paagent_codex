from __future__ import annotations

import json

from pa_agent.operations.runtime_state import RuntimeControlStore, RuntimeStatusStore


def test_control_store_persists_pause_and_resume_atomically(tmp_path) -> None:
    path = tmp_path / "runtime" / "control.json"
    store = RuntimeControlStore(path, now_ms=lambda: 1234)

    assert store.read().desired_state == "running"

    paused = store.set_desired_state("paused", actor="dashboard")
    assert paused.generation == 1
    assert paused.desired_state == "paused"
    assert not list(path.parent.glob("*.tmp"))

    resumed = store.set_desired_state("running", actor="dashboard")
    assert resumed.generation == 2
    assert store.read() == resumed


def test_corrupt_control_state_fails_closed_without_raising(tmp_path) -> None:
    path = tmp_path / "runtime" / "control.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")

    state = RuntimeControlStore(path).read()

    assert state.desired_state == "paused"
    assert state.error == "invalid_control_state"


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
