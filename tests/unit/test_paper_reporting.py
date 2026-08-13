from __future__ import annotations

import sqlite3

from pa_agent.execution.paper import PaperBroker, RiskPolicy
from pa_agent.execution.reporting import build_paper_report


def test_paper_report_summarizes_accounts_and_research_dispositions(tmp_path) -> None:
    db = tmp_path / "paper.sqlite3"
    PaperBroker(
        db,
        account_id="pa-official",
        initial_equity=10_000,
        risk_policy=RiskPolicy(),
    )
    PaperBroker(
        db,
        account_id="research-cohort-1-A1",
        initial_equity=10_000,
        risk_policy=RiskPolicy(),
    )
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE paper_decisions("
            "account_id TEXT, arm TEXT, dedupe_key TEXT, run_id TEXT, status TEXT, "
            "order_id TEXT, reason TEXT, received_path TEXT, treatment_received INTEGER, "
            "cohort_id TEXT, market_snapshot_hash TEXT)"
        )
        connection.executemany(
            "INSERT INTO paper_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("research-cohort-1-A1", "A1", "snap", "r1", "no_trade", None,
                 None, "pa_only", None, "cohort-1", "snap"),
                ("research-cohort-1-B1", "B1", "snap", "r2", "no_trade", None,
                 None, "joint", 1, "cohort-1", "snap"),
            ],
        )

    report = build_paper_report(db)

    assert report["available"] is True
    assert report["accounts"][0]["account_id"] == "pa-official"
    assert report["accounts"][0]["equity"] == 10_000
    assert report["decisions"]["by_status"] == {"no_trade": 2}
    assert report["research"]["cohorts"] == 1
    assert report["research"]["unit_of_analysis"] == "paired_market_snapshot"
    assert report["research"]["descriptive_only"] is True
    assert report["simulation"] == {
        "current_version": "paper_ohlc_v2",
        "by_version": [],
    }


def test_paper_report_handles_missing_database(tmp_path) -> None:
    report = build_paper_report(tmp_path / "missing.sqlite3")

    assert report["available"] is False
    assert report["accounts"] == []


def test_research_report_pairs_arms_by_cohort_and_exact_market_snapshot(tmp_path) -> None:
    db = tmp_path / "paper.sqlite3"
    PaperBroker(db, account_id="pa-official", initial_equity=10_000, risk_policy=RiskPolicy())
    rows = []
    for snapshot in ("snap-1", "snap-2"):
        for arm in ("A1", "A2", "B1", "B2"):
            rows.append(
                (f"research-c1-{arm}", arm, snapshot, f"{snapshot}-{arm}", "no_trade",
                 None, None, "joint" if arm.startswith("B") else "pa_only",
                 1 if arm.startswith("B") else None, "cohort-1", snapshot)
            )
    rows.extend(
        [
            ("mixed-A1", "A1", "snap-x", "x-a1", "no_trade", None, None,
             "pa_only", None, "cohort-mixed", "snap-x"),
            ("mixed-A2", "A2", "snap-x", "x-a2", "no_trade", None, None,
             "pa_only", None, "cohort-mixed", "snap-x"),
            ("mixed-B1", "B1", "snap-y", "y-b1", "no_trade", None, None,
             "joint", 1, "cohort-mixed", "snap-y"),
            ("mixed-B2", "B2", "snap-y", "y-b2", "no_trade", None, None,
             "joint", 1, "cohort-mixed", "snap-y"),
        ]
    )
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE paper_decisions("
            "account_id TEXT, arm TEXT, dedupe_key TEXT, run_id TEXT, status TEXT, "
            "order_id TEXT, reason TEXT, received_path TEXT, treatment_received INTEGER, "
            "cohort_id TEXT, market_snapshot_hash TEXT)"
        )
        connection.executemany(
            "INSERT INTO paper_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )

    research = build_paper_report(db)["research"]

    assert research["cohorts"] == 2
    assert research["complete_aabb_snapshots"] == 2
    assert research["complete_aabb_cohorts"] == 1
