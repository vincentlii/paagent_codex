from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pa_agent.operations import backup as backup_module
from pa_agent.operations.backup import create_backup, verify_backup


def _sqlite(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES ('ok')")


def test_backup_is_consistent_and_removes_configuration_credentials(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state = tmp_path / "state"
    control_root = tmp_path / "operation"
    _sqlite(state / "records" / "audit" / "analysis.sqlite3")
    _sqlite(state / "records" / "audit" / "monitoring.sqlite3")
    _sqlite(state / "trade_records" / "paper.sqlite3")
    settings_path = state / "config" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "provider": {
                    "api_key": "plain-secret",
                    "api_key_encrypted": "cipher-secret",
                    "max_tokens": 8192,
                },
                "tushare": {"token": "data-secret"},
            }
        ),
        encoding="utf-8",
    )
    control_path = control_root / "control" / "control.json"
    control_path.parent.mkdir(parents=True)
    control_path.write_text(
        json.dumps({"desired_state": "paused", "dashboard_token": "ui-secret"}),
        encoding="utf-8",
    )
    record = state / "records" / "pending" / "run.json"
    record.parent.mkdir(parents=True)
    record.write_text('{"run_id":"run-1"}', encoding="utf-8")
    experience = state / "experience" / "known.json"
    experience.parent.mkdir(parents=True)
    experience.write_text('{"known_at_ms":100}', encoding="utf-8")
    codex = tmp_path / "codex"
    job = codex / "jobs" / "frozen-digest" / "manifest.json"
    job.parent.mkdir(parents=True)
    job.write_text('{"snapshot_hash":"abc"}', encoding="utf-8")
    acceptance = codex / "results" / ".accepted" / "acceptance-key.json"
    acceptance.parent.mkdir(parents=True)
    acceptance.write_text('{"request_id":"request-1"}', encoding="utf-8")
    monkeypatch.setenv("PA_AGENT_HOME", str(state))
    monkeypatch.setenv("PA_AGENT_OPERATION_DIR", str(control_root))
    monkeypatch.setenv("PA_AGENT_CODEX_STATE_DIR", str(codex))

    destination = create_backup(tmp_path / "backups")

    saved_settings = json.loads(
        (destination / "configuration" / "settings.json").read_text(encoding="utf-8")
    )
    saved_control = json.loads(
        (destination / "configuration" / "control.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert saved_settings["provider"] == {"max_tokens": 8192}
    assert saved_settings["tushare"] == {}
    assert saved_control == {"desired_state": "paused"}
    assert manifest["credential_files_included"] is False
    assert manifest["configuration_credentials_removed"] is True
    assert len(manifest["files"]) == 9
    assert (destination / "records" / "pending" / "run.json").read_text("utf-8") == '{"run_id":"run-1"}'
    assert (destination / "experience" / "known.json").is_file()
    assert (destination / "frozen_jobs" / "frozen-digest" / "manifest.json").is_file()
    assert (destination / "codex_acceptance" / "acceptance-key.json").is_file()
    assert verify_backup(destination) == []
    manifest["files"] = [
        entry for entry in manifest["files"]
        if entry["path"] != "databases/paper.sqlite3"
    ]
    (destination / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert "backup required file is missing: databases/paper.sqlite3" in verify_backup(destination)
    (destination / "manifest.json").write_text(
        json.dumps({**manifest, "files": [
            *manifest["files"],
            {"path": "databases/paper.sqlite3", "sha256": "wrong"},
        ]}),
        encoding="utf-8",
    )
    original_backup_sqlite = backup_module._backup_sqlite
    monkeypatch.setattr(
        backup_module,
        "_backup_sqlite",
        lambda source, target: False if source.name == "paper.sqlite3" else original_backup_sqlite(source, target),
    )
    with pytest.raises(FileNotFoundError, match="required backup database is missing"):
        create_backup(tmp_path / "missing-database-backups")
    (destination / "records" / "pending" / "run.json").write_text("corrupt", encoding="utf-8")
    assert verify_backup(destination)
