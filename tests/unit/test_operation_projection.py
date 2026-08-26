from __future__ import annotations

import sqlite3

from pa_agent.operations.projection import read_recent_runs


def test_recent_run_projection_is_read_only_and_tolerates_older_schema(tmp_path) -> None:
    db_path = tmp_path / "analysis.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE analysis_runs ("
            "run_id TEXT PRIMARY KEY, timestamp_local_ms INTEGER, "
            "symbol TEXT, timeframe TEXT, run_status TEXT)"
        )
        connection.execute(
            "INSERT INTO analysis_runs VALUES (?, ?, ?, ?, ?)",
            ("run-1", 1234, "XAUUSDT", "1h", "completed"),
        )
        connection.execute("PRAGMA user_version = 42")

    rows = read_recent_runs(db_path)

    assert rows[0]["run_id"] == "run-1"
    assert rows[0]["vpa_effect"] is None
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 42
