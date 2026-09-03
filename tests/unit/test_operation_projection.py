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


def test_recent_run_projection_summarizes_real_call_audit_fields(tmp_path) -> None:
    db_path = tmp_path / "analysis.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE analysis_runs ("
            "run_id TEXT PRIMARY KEY, timestamp_local_ms INTEGER, symbol TEXT, "
            "timeframe TEXT, run_status TEXT, terminal_outcome TEXT)"
        )
        connection.execute(
            "INSERT INTO analysis_runs VALUES (?, ?, ?, ?, ?, ?)",
            ("run-1", 1234, "XAUUSDT", "1h", "completed", "trade"),
        )
        connection.execute(
            "CREATE TABLE analysis_calls ("
            "run_id TEXT, analysis_backend TEXT, model TEXT, "
            "requested_model TEXT, effective_model TEXT, "
            "requested_reasoning_effort TEXT, effective_reasoning_effort TEXT, "
            "latency_ms REAL, attempt INTEGER, created_at_ms INTEGER)"
        )
        connection.executemany(
            "INSERT INTO analysis_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "run-1", "codex_agent", "gpt-5.6-sol", "gpt-5.6-sol",
                    "gpt-5.6-sol", "high", "high", 1200.0, 1, 10,
                ),
                (
                    "run-1", "codex_agent", "gpt-5.6-sol", "gpt-5.6-sol",
                    "gpt-5.6-sol", "high", "high", 800.0, 2, 20,
                ),
            ],
        )
        connection.execute(
            "CREATE TABLE analysis_outcomes (run_id TEXT PRIMARY KEY, outcome TEXT)"
        )
        connection.execute(
            "INSERT INTO analysis_outcomes VALUES (?, ?)",
            ("run-1", "win"),
        )

    row = read_recent_runs(db_path)[0]

    assert row["analysis_backend"] == "codex_agent"
    assert row["requested_model"] == "gpt-5.6-sol"
    assert row["effective_model"] == "gpt-5.6-sol"
    assert row["effective_reasoning_effort"] == "high"
    assert row["total_latency_ms"] == 2000.0
    assert row["repair_count"] == 1
    assert row["outcome"] == "win"
