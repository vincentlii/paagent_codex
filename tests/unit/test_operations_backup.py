from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pa_agent.operations.backup import create_backup


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
    monkeypatch.setenv("PA_AGENT_HOME", str(state))
    monkeypatch.setenv("PA_AGENT_OPERATION_DIR", str(control_root))

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
    assert len(manifest["files"]) == 5
