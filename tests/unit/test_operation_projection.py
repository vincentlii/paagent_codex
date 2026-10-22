from __future__ import annotations

import sqlite3
import json

from pa_agent.operations.projection import (
    read_backend_experiment_summary,
    read_recent_runs,
    summarize_recent_runs,
)


def test_recent_runs_display_only_versioned_safe_failure_summary(tmp_path):
    from pa_agent.ai.provider_failures import runtime_failure_summary

    summary = runtime_failure_summary({"type": "provider_error", "stage": "vpa_evidence",
        "failure_kind": "internal", "retryable": False, "message": "SECRET",
        "attempts": [{"worker_diagnostic": {"schema_version": 1, "phase": "turn.failed",
            "category": "unknown_execution", "exit_status": 1, "raw_stderr": "SECRET"}}]})
    assert summary["label"] == "CLI 异常，原因未确定"
    assert "SECRET" not in json.dumps(summary)
    path = tmp_path / "analysis.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE analysis_runs(run_id TEXT,timestamp_local_ms INTEGER,review_context_json TEXT)")
        db.execute("INSERT INTO analysis_runs VALUES(?,?,?)", ("failed", 1, json.dumps({"runtime_failure": summary})))
    row = read_recent_runs(path)[0]
    assert row["failure_label"] == summary["label"]
    assert row["failure_stage"] == "vpa_evidence"


def test_runtime_stop_and_no_model_admission_are_not_schema_failures():
    from pa_agent.ai.provider_failures import runtime_failure_summary

    blocked = runtime_failure_summary({"type": "provider_error", "stage": "stage1", "failure_kind": "unavailable",
        "retryable": True, "attempts": [{"model_call_started": False, "admission_wait_ms": 31}]})
    assert blocked["label"] == "熔断准入等待／拦截"
    assert blocked["model_call_started"] is False
    stopped = runtime_failure_summary({"type": "provider_error", "stage": "vpa_evidence", "failure_kind": "role_stopped",
        "retryable": False, "attempts": [{"worker_diagnostic": {"schema_version": 1,
            "phase": "process_budget", "category": "timeout", "budget_source": "role"}}]})
    assert stopped["label"] == "VPA 预算停止"


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
    assert rows[0]["analysis_pipeline"] is None
    assert rows[0]["error_type"] is None
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

    summary = summarize_recent_runs([row])[0]
    assert summary["backend"] == "codex_agent"
    assert summary["operational_success_rate"] == 1.0
    assert summary["first_pass_rate"] == 0.0
    assert summary["latency_p50_ms"] == 2000.0
    assert summary["latency_p95_ms"] == 2000.0


def test_operational_summary_never_mixes_pipeline_authorities() -> None:
    rows = [
        {"analysis_backend": "codex_agent", "analysis_pipeline": None,
         "run_status": "completed", "total_latency_ms": 900.0},
        {"analysis_backend": "codex_agent", "analysis_pipeline": "pa_vpa_joint",
         "run_status": "completed", "total_latency_ms": 1800.0},
    ]

    summaries = summarize_recent_runs(rows)

    assert len(summaries) == 2
    assert {(item["analysis_pipeline"], item["latency_p95_ms"])
            for item in summaries} == {(None, 900.0), ("pa_vpa_joint", 1800.0)}


def test_unknown_usage_and_failed_first_attempt_are_not_zero_cost_or_first_pass(tmp_path):
    db_path = tmp_path / "audit.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE analysis_runs (run_id TEXT, timestamp_local_ms INTEGER, run_status TEXT)")
        connection.execute("INSERT INTO analysis_runs VALUES ('failed', 1, 'failed')")
        connection.execute("CREATE TABLE analysis_calls (run_id TEXT, attempt INTEGER, latency_ms REAL, total_tokens INTEGER)")
        connection.execute("INSERT INTO analysis_calls VALUES ('failed', 1, NULL, 0)")
    run = read_recent_runs(db_path)[0]
    assert run["total_latency_ms"] is None
    assert run["total_tokens"] is None
    assert summarize_recent_runs([run])[0]["first_pass_rate"] == 0.0


def test_projection_uses_canonical_quality_and_preserves_metric_coverage(tmp_path):
    db_path = tmp_path / "audit.sqlite3"
    quality = {
        "first_pass": {"clean": False, "status": "normalized"},
        "metrics": {"protocol_version": "analysis_metrics_v1", "calls": 2, "values": {
            "total_tokens": {"actual_count": 1, "estimated_count": 0, "unknown_count": 1, "actual_sum": 50, "complete_actual": False},
            "initial_context_tokens": {"actual_count": 0, "estimated_count": 2, "unknown_count": 0, "estimated_sum": 30, "complete_actual": False},
        }},
    }
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE analysis_runs (run_id TEXT, timestamp_local_ms INTEGER, run_status TEXT, review_context_json TEXT)")
        connection.execute("INSERT INTO analysis_runs VALUES ('normalized', 1, 'completed', ?)", (json.dumps({"operational_quality": quality, "decision_review": {"reasoning": "private"}}),))
    run = read_recent_runs(db_path)[0]
    assert run["first_pass_clean"] is False
    assert run["total_tokens"] is None
    assert run["initial_context_tokens"] == 30
    assert run["metric_statuses"]["total_tokens"] == "unknown"
    assert run["metric_statuses"]["initial_context_tokens"] == "estimated"
    assert "review_context_json" not in run
    assert "decision_review" not in run
    assert summarize_recent_runs([run])[0]["first_pass_rate"] == 0.0


def test_old_success_without_first_pass_evidence_stays_unknown():
    summary = summarize_recent_runs([{"run_status": "completed"}])[0]
    assert summary["first_pass_rate"] is None
    assert summary["first_pass_known_count"] == 0


def test_chinese_no_trade_is_not_an_executable_candidate(tmp_path):
    db_path = tmp_path / "audit.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE analysis_runs (run_id TEXT, timestamp_local_ms INTEGER, order_type TEXT)")
        connection.executemany("INSERT INTO analysis_runs VALUES (?, ?, ?)", [
            ('wait', 3, '不下单'), ('trade', 2, '限价单'), ('unknown', 1, 'unexpected'),
        ])
    rows = read_recent_runs(db_path)
    assert [run['decision_kind'] for run in rows] == ['no_trade', 'trade_candidate', 'unknown']


def test_backend_experiment_projection_is_read_only_and_protocol_scoped(tmp_path) -> None:
    db_path = tmp_path / "analysis.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE analysis_experiments ("
            "experiment_id TEXT PRIMARY KEY, protocol_version TEXT, status TEXT, "
            "market_snapshot_hash TEXT, cohort_id TEXT, protocol_valid INTEGER)"
        )
        connection.execute(
            "CREATE TABLE experiment_arms ("
            "experiment_id TEXT, arm_id TEXT, persisted INTEGER, run_status TEXT)"
        )
        connection.executemany(
            "INSERT INTO analysis_experiments VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("backend-1", "analysis_backend_aabb_v2", "completed", "s1", "c1", 1),
                ("legacy-1", "pa_vpa_aabb_v3", "completed", "s2", "c2", 1),
            ],
        )
        connection.executemany(
            "INSERT INTO experiment_arms VALUES (?, ?, ?, ?)",
            [
                ("backend-1", "A1", 1, "completed"),
                ("backend-1", "A2", 1, "completed"),
                ("backend-1", "B1", 1, "completed"),
                ("backend-1", "B2", 0, "failed"),
                ("legacy-1", "A1", 1, "completed"),
            ],
        )
        connection.execute("PRAGMA user_version = 42")

    summary = read_backend_experiment_summary(db_path)

    assert summary == {
        "available": True,
        "protocol_version": "analysis_backend_aabb_v2",
        "experiments": 1,
        "completed_experiments": 1,
        "valid_experiments": 1,
        "unique_snapshots": 1,
        "cohort_count": 1,
        "assigned_arms": 4,
        "persisted_arms": 3,
        "failed_arms": 1,
        "arm_coverage": 0.75,
    }
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 42
