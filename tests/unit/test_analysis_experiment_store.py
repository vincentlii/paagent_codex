from __future__ import annotations

import sqlite3
from pathlib import Path

from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog
from pa_agent.experiments.store import AnalysisExperimentStore
from tests.integration.conftest import make_frame
from tests.unit.test_pa_vpa_aabb_protocol import _record
from tools.review_analysis_audit import _load_completed_experiment_records


def _manifest(
    experiment_id: str,
    frozen_hash: str = "f" * 64,
    *,
    market_snapshot_hash: str = "m" * 64,
    cohort_id: str = "o" * 64,
    analysis_fingerprint: str = "a" * 64,
) -> dict:
    return {
        "experiment_id": experiment_id,
        "protocol_version": "pa_vpa_aabb_v1",
        "status": "running",
        "symbol": "XAUUSD",
        "timeframe": "1h",
        "frozen_input_hash": frozen_hash,
        "snapshot_hash": "s" * 64,
        "htf_hash": "h" * 64,
        "catalog_id": "vpa-catalog-test",
        "provider_fingerprint": "p" * 64,
        "settings_fingerprint": "c" * 64,
        "analysis_fingerprint": analysis_fingerprint,
        "market_snapshot_hash": market_snapshot_hash,
        "cohort_id": cohort_id,
        "order_scheme": "ABBA",
        "arm_sequence": ["A1", "B1", "B2", "A2"],
        "source_run_id": "source-run",
        "source_record_path": "source.json",
    }


def _arm(arm_id: str, run_id: str, *, first_pass: bool = True) -> dict:
    group = arm_id[0]
    return {
        "arm_id": arm_id,
        "group_name": group,
        "replicate": int(arm_id[1]),
        "sequence_position": {"A1": 1, "B1": 2, "B2": 3, "A2": 4}[arm_id],
        "assigned_mode": "off" if group == "A" else "soft",
        "received_path": "pa_only" if group == "A" else "joint",
        "run_id": run_id,
        "record_path": f"{run_id}.json",
        "run_status": "completed",
        "first_pass_clean": first_pass,
        "treatment_received": group == "B",
        "stage1_request_hash": "1" * 64,
        "client_instance_token": f"client-{arm_id}",
        "persisted": True,
        "error": None,
    }


def test_experiment_store_keeps_running_manifest_and_four_arm_ledger(tmp_path: Path) -> None:
    store = AnalysisExperimentStore(tmp_path / "analysis.sqlite3")
    manifest = _manifest("aabb-one")

    assert store.start_experiment(manifest).ok
    for arm_id in manifest["arm_sequence"]:
        assert store.record_arm("aabb-one", _arm(arm_id, f"run-{arm_id}")).ok
    assert store.finish_experiment(
        "aabb-one",
        status="completed",
        protocol_report={"valid": True, "errors": []},
        external_comparison={"a_disagreement": False, "b_disagreement": True},
    ).ok

    loaded = store.get_experiment("aabb-one")
    assert loaded is not None
    assert loaded["status"] == "completed"
    assert loaded["protocol_valid"] == 1
    assert [arm["arm_id"] for arm in loaded["arms"]] == manifest["arm_sequence"]
    artifact = tmp_path / "experiments" / "aabb-one.json"
    assert artifact.exists()
    assert '"status": "completed"' in artifact.read_text(encoding="utf-8")


def test_reports_use_assigned_treatment_for_itt_and_received_treatment_for_per_protocol(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "analysis.sqlite3"
    store = AnalysisExperimentStore(db_path)
    manifest = _manifest("aabb-report")
    assert store.start_experiment(manifest).ok
    arms = [
        _arm("A1", "run-A1"),
        _arm("B1", "run-B1"),
        _arm("B2", "run-B2"),
        _arm("A2", "run-A2"),
    ]
    arms[2]["received_path"] = "pa_only"
    arms[2]["run_status"] = "degraded_pa_only"
    arms[2]["treatment_received"] = False
    for arm in arms:
        assert store.record_arm("aabb-report", arm).ok
    assert store.finish_experiment(
        "aabb-report",
        status="completed",
        protocol_report={"valid": True, "errors": []},
        external_comparison={},
    ).ok

    with sqlite3.connect(db_path) as connection:
        connection.execute("""
            CREATE TABLE analysis_runs (
                run_id TEXT PRIMARY KEY, symbol TEXT, timeframe TEXT,
                order_type TEXT, run_status TEXT
            )
            """)
        connection.executemany(
            "INSERT INTO analysis_runs VALUES (?, 'XAUUSD', '1h', ?, ?)",
            [
                ("run-A1", "limit", "completed"),
                ("run-A2", "limit", "completed"),
                ("run-B1", "limit", "completed"),
                ("run-B2", "limit", "degraded"),
            ],
        )
        connection.execute("""
            CREATE TABLE counterfactual_outcomes (
                run_id TEXT, arm TEXT, status TEXT, entry_status TEXT,
                outcome TEXT, realized_return_r REAL
            )
            """)
        connection.executemany(
            "INSERT INTO counterfactual_outcomes VALUES (?, 'official', 'completed', 'filled', ?, ?)",
            [
                ("run-A1", "win", 2.0),
                ("run-A2", "loss", -1.0),
                ("run-B1", "win", 2.0),
                ("run-B2", "loss", -1.0),
            ],
        )

    frequency = store.frequency_report(min_unique_snapshots=1)
    assert frequency["intention_to_treat"]["A"]["runs"] == 2
    assert frequency["intention_to_treat"]["B"]["runs"] == 2
    assert frequency["per_protocol"]["B"]["runs"] == 1

    outcomes = store.counterfactual_report(
        min_decisive_snapshots=1,
        cost_per_trade_r=0.1,
    )
    assert outcomes["status"] == "counterfactual_only"
    assert outcomes["intention_to_treat"]["B"]["decisive_runs"] == 2
    assert outcomes["per_protocol"]["B"]["decisive_runs"] == 1
    assert outcomes["intention_to_treat"]["A"]["cost_adjusted_mean_return_r"] == 0.4
    assert outcomes["matched_comparison"]["matched_decisive_snapshots"] == 1
    assert outcomes["matched_comparison"]["mean_return_r_delta"] == 0.0
    assert outcomes["matched_comparison"]["win_rate_delta"] == 0.0
    assert outcomes["matched_comparison"]["directional_sign_test_p"] is None
    assert outcomes["matched_comparison"]["status"] == "paired_closed_ohlc_episode_itt"
    assert outcomes["per_protocol_matched_comparison"]["mean_return_r_delta"] == 1.5
    assert outcomes["per_protocol_matched_comparison"]["win_rate_delta"] == 0.5
    assert (
        outcomes["per_protocol_matched_comparison"]["status"]
        == "paired_closed_ohlc_episode_per_protocol"
    )
    assert outcomes["ready_for_soft"] is False


def test_itt_keeps_assigned_arms_that_never_produced_a_record(tmp_path: Path) -> None:
    db_path = tmp_path / "analysis.sqlite3"
    store = AnalysisExperimentStore(db_path)
    manifest = _manifest("aabb-failed")
    assert store.start_experiment(manifest).ok
    for arm_id in manifest["arm_sequence"]:
        arm = _arm(arm_id, f"run-{arm_id}")
        arm.update(
            {
                "received_path": None,
                "run_id": None,
                "record_path": None,
                "run_status": "assigned",
                "first_pass_clean": False,
                "treatment_received": False,
                "persisted": False,
            }
        )
        assert store.record_arm("aabb-failed", arm).ok
    assert store.finish_experiment(
        "aabb-failed",
        status="incomplete",
        protocol_report={"valid": False, "errors": ["arm_execution_failed:B1"]},
        external_comparison={},
    ).ok

    report = store.frequency_report(min_unique_snapshots=1)

    assert report["assigned_arms"] == 4
    assert report["unpersisted_arms"] == 4
    assert report["intention_to_treat"]["A"]["runs"] == 2
    assert report["intention_to_treat"]["B"]["runs"] == 2
    assert report["intention_to_treat"]["A"]["executable_rate"] == 0.0
    assert report["per_protocol"]["A"]["runs"] == 0
    assert report["reviewable_unique_snapshots"] == 0

    outcomes = store.counterfactual_report(min_decisive_snapshots=1)
    assert outcomes["status"] == "counterfactual_only"
    assert outcomes["assigned_arms"] == 4
    assert outcomes["matched_decisive_snapshots"] == 0
    assert outcomes["intention_to_treat"]["A"]["missing_runs"] == 2
    assert outcomes["intention_to_treat"]["B"]["missing_runs"] == 2
    assert outcomes["intention_to_treat"]["A"]["outcome_coverage"] == 0.0
    assert outcomes["missing_outcome_arms"] == 4
    assert outcomes["statistically_reviewable"] is False


def test_missing_assigned_arm_blocks_review_even_when_an_episode_is_complete(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "analysis.sqlite3"
    store = AnalysisExperimentStore(db_path)
    cohort = "q" * 64
    run_ids: list[str] = []
    for index in (1, 2):
        manifest = _manifest(
            f"aabb-missing-{index}",
            frozen_hash=str(index) * 64,
            market_snapshot_hash=str(index + 5) * 64,
            cohort_id=cohort,
            analysis_fingerprint=cohort,
        )
        assert store.start_experiment(manifest).ok
        for arm_id in manifest["arm_sequence"]:
            arm = _arm(arm_id, f"run-{index}-{arm_id}")
            if index == 2 and arm_id == "B2":
                arm.update(
                    run_id=None,
                    record_path=None,
                    run_status="assigned",
                    persisted=False,
                    treatment_received=False,
                )
            else:
                run_ids.append(str(arm["run_id"]))
            assert store.record_arm(manifest["experiment_id"], arm).ok
        assert store.finish_experiment(
            manifest["experiment_id"],
            status="completed" if index == 1 else "incomplete",
            protocol_report={"valid": index == 1, "errors": []},
            external_comparison={},
        ).ok

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE analysis_runs (run_id TEXT PRIMARY KEY, order_type TEXT)"
        )
        connection.execute(
            "CREATE TABLE counterfactual_outcomes ("
            "run_id TEXT, arm TEXT, status TEXT, entry_status TEXT, "
            "outcome TEXT, realized_return_r REAL)"
        )
        for run_id in run_ids:
            connection.execute(
                "INSERT INTO analysis_runs VALUES (?, 'limit')",
                (run_id,),
            )
            connection.execute(
                "INSERT INTO counterfactual_outcomes VALUES "
                "(?, 'official', 'completed', 'filled', 'win', 1.0)",
                (run_id,),
            )

    report = store.counterfactual_report(min_decisive_snapshots=1)

    assert report["matched_episodes"] == 1
    assert report["missing_outcome_arms"] == 1
    assert report["statistically_reviewable"] is False


def test_itt_counts_no_order_and_untriggered_entries_as_zero_return(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "analysis.sqlite3"
    store = AnalysisExperimentStore(db_path)
    manifest = _manifest("aabb-zero-return")
    assert store.start_experiment(manifest).ok
    for arm_id in manifest["arm_sequence"]:
        assert store.record_arm(
            manifest["experiment_id"],
            _arm(arm_id, f"run-{arm_id}"),
        ).ok
    assert store.finish_experiment(
        manifest["experiment_id"],
        status="completed",
        protocol_report={"valid": True, "errors": []},
        external_comparison={},
    ).ok

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE analysis_runs ("
            "run_id TEXT PRIMARY KEY, order_type TEXT, "
            "snapshot_latest_ts_open INTEGER, bar_count INTEGER)"
        )
        connection.executemany(
            "INSERT INTO analysis_runs VALUES (?, ?, 1000000, 100)",
            [
                ("run-A1", "limit"),
                ("run-A2", "no_order"),
                ("run-B1", "limit"),
                ("run-B2", "limit"),
            ],
        )
        connection.execute(
            "CREATE TABLE counterfactual_outcomes ("
            "run_id TEXT, arm TEXT, status TEXT, entry_status TEXT, "
            "outcome TEXT, realized_return_r REAL)"
        )
        connection.executemany(
            "INSERT INTO counterfactual_outcomes VALUES "
            "(?, 'official', ?, ?, ?, ?)",
            [
                ("run-A1", "completed", "filled", "win", 2.0),
                ("run-B1", "completed", "filled", "loss", -1.0),
                (
                    "run-B2",
                    "entry_not_triggered",
                    "not_triggered",
                    "not_applicable",
                    None,
                ),
            ],
        )

    report = store.counterfactual_report(min_decisive_snapshots=1)

    assert report["intention_to_treat"]["A"]["zero_return_runs"] == 1
    assert report["intention_to_treat"]["B"]["zero_return_runs"] == 1
    assert report["intention_to_treat"]["A"]["mean_return_r"] == 1.0
    assert report["intention_to_treat"]["B"]["mean_return_r"] == -0.5
    assert report["matched_comparison"]["mean_return_r_delta"] == -1.5
    assert report["matched_comparison"]["matched_episodes"] == 1


def test_outcome_integrity_conflicts_are_not_treated_as_evaluable_returns() -> None:
    no_order_with_trade = {
        "persisted": 1,
        "run_id": "run-no-order",
        "indexed_order_type": "no_order",
        "cf_status": "completed",
        "entry_status": "filled",
        "outcome": "win",
        "realized_return_r": 1.0,
    }
    win_with_negative_return = {
        **no_order_with_trade,
        "run_id": "run-wrong-return",
        "indexed_order_type": "limit",
        "realized_return_r": -1.0,
    }
    win_without_return = {
        **win_with_negative_return,
        "run_id": "run-missing-return",
        "realized_return_r": None,
    }

    for row in (no_order_with_trade, win_with_negative_return):
        outcome = AnalysisExperimentStore._arm_outcome(
            row,  # type: ignore[arg-type]
            cost_per_trade_r=0.0,
        )
        assert outcome.state == "missing"
        assert outcome.integrity_conflict is True
        assert outcome.adjusted_return_r is None

    missing_return = AnalysisExperimentStore._arm_outcome(
        win_without_return,  # type: ignore[arg-type]
        cost_per_trade_r=0.0,
    )
    assert missing_return.integrity_conflict is False
    assert missing_return.traded is False


def test_counterfactual_report_groups_overlapping_forward_windows_into_episodes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "analysis.sqlite3"
    store = AnalysisExperimentStore(db_path)
    cohort = "e" * 64
    starts = (1_000_000, 4_600_000, 37_000_000)
    run_rows: list[tuple[str, str, int, int]] = []
    outcome_rows: list[tuple[str, str, str, str, float, int]] = []
    for index, start in enumerate(starts, start=1):
        manifest = _manifest(
            f"aabb-episode-{index}",
            frozen_hash=str(index) * 64,
            market_snapshot_hash=str(index + 3) * 64,
            cohort_id=cohort,
            analysis_fingerprint=cohort,
        )
        assert store.start_experiment(manifest).ok
        for arm_id in manifest["arm_sequence"]:
            run_id = f"run-{index}-{arm_id}"
            assert store.record_arm(
                manifest["experiment_id"],
                _arm(arm_id, run_id),
            ).ok
            run_rows.append((run_id, "limit", start, 100))
            is_b = arm_id.startswith("B")
            outcome_rows.append(
                (
                    run_id,
                    "completed",
                    "filled",
                    "win",
                    2.0 if is_b else 1.0,
                    start + 4 * 3_600_000,
                )
            )
        assert store.finish_experiment(
            manifest["experiment_id"],
            status="completed",
            protocol_report={"valid": True, "errors": []},
            external_comparison={},
        ).ok

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE analysis_runs ("
            "run_id TEXT PRIMARY KEY, order_type TEXT, "
            "snapshot_latest_ts_open INTEGER, bar_count INTEGER)"
        )
        connection.executemany(
            "INSERT INTO analysis_runs VALUES (?, ?, ?, ?)",
            run_rows,
        )
        connection.execute(
            "CREATE TABLE counterfactual_outcomes ("
            "run_id TEXT, arm TEXT, status TEXT, entry_status TEXT, "
            "outcome TEXT, realized_return_r REAL, evaluated_through_ts INTEGER)"
        )
        connection.executemany(
            "INSERT INTO counterfactual_outcomes VALUES "
            "(?, 'official', ?, ?, ?, ?, ?)",
            outcome_rows,
        )

    report = store.counterfactual_report(min_decisive_snapshots=2)

    assert report["matched_comparison"]["matched_evaluable_snapshots"] == 3
    assert report["matched_comparison"]["matched_episodes"] == 2
    assert report["matched_comparison"]["mean_return_r_delta"] == 1.0
    assert report["statistically_reviewable"] is True


def test_reports_do_not_count_one_market_snapshot_twice_across_cohorts(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "analysis.sqlite3"
    store = AnalysisExperimentStore(db_path)
    market_hash = "9" * 64
    for version, cohort in (("v1", "1" * 64), ("v2", "2" * 64)):
        manifest = _manifest(
            f"aabb-{version}",
            frozen_hash=version[-1] * 64,
            market_snapshot_hash=market_hash,
            cohort_id=cohort,
            analysis_fingerprint=cohort,
        )
        assert store.start_experiment(manifest).ok
        for arm_id in manifest["arm_sequence"]:
            assert store.record_arm(
                manifest["experiment_id"],
                _arm(arm_id, f"run-{version}-{arm_id}"),
            ).ok
        assert store.finish_experiment(
            manifest["experiment_id"],
            status="completed",
            protocol_report={"valid": True, "errors": []},
            external_comparison={},
        ).ok

    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE analysis_runs (run_id TEXT PRIMARY KEY, order_type TEXT)")
        connection.execute(
            "CREATE TABLE counterfactual_outcomes ("
            "run_id TEXT, arm TEXT, status TEXT, entry_status TEXT, "
            "outcome TEXT, realized_return_r REAL)"
        )
        for version in ("v1", "v2"):
            for arm_id in ("A1", "B1", "B2", "A2"):
                run_id = f"run-{version}-{arm_id}"
                connection.execute(
                    "INSERT INTO analysis_runs VALUES (?, 'limit')",
                    (run_id,),
                )
                connection.execute(
                    "INSERT INTO counterfactual_outcomes VALUES "
                    "(?, 'official', 'completed', 'filled', 'win', 1.0)",
                    (run_id,),
                )

    frequency = store.frequency_report(min_unique_snapshots=1)
    assert frequency["unique_snapshots"] == 1
    assert frequency["cohort_count"] == 2

    outcomes = store.counterfactual_report(min_decisive_snapshots=1)
    assert outcomes["matched_decisive_snapshots"] == 1
    assert outcomes["cohort_count"] == 2
    assert outcomes["statistically_reviewable"] is False
    assert outcomes["matched_comparison"]["status"] == "mixed_cohorts_not_comparable"
    assert outcomes["matched_comparison"]["mean_return_r_delta"] is None


def test_schema_upgrade_backfills_legacy_experiment_identity(tmp_path: Path) -> None:
    db_path = tmp_path / "analysis.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute("""
            CREATE TABLE analysis_experiments (
                experiment_id TEXT PRIMARY KEY,
                protocol_version TEXT NOT NULL,
                status TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                frozen_input_hash TEXT NOT NULL,
                snapshot_hash TEXT NOT NULL,
                htf_hash TEXT NOT NULL,
                catalog_id TEXT NOT NULL,
                provider_fingerprint TEXT NOT NULL,
                settings_fingerprint TEXT NOT NULL,
                order_scheme TEXT NOT NULL,
                arm_sequence_json TEXT NOT NULL,
                source_run_id TEXT,
                source_record_path TEXT,
                protocol_valid INTEGER,
                protocol_errors_json TEXT NOT NULL,
                external_comparison_json TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                completed_at_ms INTEGER
            )
            """)
        connection.execute(
            "INSERT INTO analysis_experiments VALUES "
            "('legacy', 'pa_vpa_aabb_v1', 'completed', 'XAUUSD', '1h', ?, ?, ?, "
            "'catalog', ?, ?, 'ABBA', '[]', NULL, NULL, 1, '[]', '{}', 1, 2)",
            ("f" * 64, "s" * 64, "h" * 64, "p" * 64, "c" * 64),
        )

    loaded = AnalysisExperimentStore(db_path).get_experiment("legacy")

    assert loaded is not None
    assert loaded["market_snapshot_hash"] == "f" * 64
    assert len(loaded["cohort_id"]) == 64


def test_quality_record_loader_defaults_to_latest_completed_valid_cohort(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "analysis.sqlite3"
    store = AnalysisExperimentStore(db_path)
    catalog = build_vpa_evidence_catalog(make_frame())
    cohorts = (("old", "1" * 64, 100), ("new", "2" * 64, 200))
    for version, cohort, created_at in cohorts:
        manifest = _manifest(
            f"aabb-{version}",
            frozen_hash=version[0] * 64,
            cohort_id=cohort,
            analysis_fingerprint=cohort,
        )
        assert store.start_experiment(manifest).ok
        for arm_id in manifest["arm_sequence"]:
            run_id = f"run-{version}-{arm_id}"
            record = _record(
                run_id,
                mode="off" if arm_id.startswith("A") else "soft",
                catalog=catalog if arm_id.startswith("B") else None,
            )
            record_path = tmp_path / f"{run_id}.json"
            record_path.write_text(record.model_dump_json(), encoding="utf-8")
            arm = _arm(arm_id, run_id)
            arm["record_path"] = str(record_path)
            assert store.record_arm(manifest["experiment_id"], arm).ok
        assert store.finish_experiment(
            manifest["experiment_id"],
            status="completed",
            protocol_report={"valid": True, "errors": []},
            external_comparison={},
        ).ok
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE analysis_experiments SET created_at_ms = ? WHERE experiment_id = ?",
                (created_at, manifest["experiment_id"]),
            )

    records, report = _load_completed_experiment_records(
        store,
        symbol=None,
        timeframe=None,
        limit_experiments=10,
    )

    assert report["selected_cohort_id"] == "2" * 64
    assert report["skipped_other_cohort_experiments"] == 1
    assert {record.meta.run_id.split("-")[1] for record in records} == {"new"}

    old_records, old_report = _load_completed_experiment_records(
        store,
        symbol=None,
        timeframe=None,
        limit_experiments=10,
        cohort_id="1" * 64,
    )
    assert old_report["selected_cohort_id"] == "1" * 64
    assert {record.meta.run_id.split("-")[1] for record in old_records} == {"old"}
