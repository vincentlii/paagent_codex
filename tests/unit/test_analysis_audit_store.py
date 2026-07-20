from __future__ import annotations

import json
from pathlib import Path

from pa_agent.records.audit_store import AnalysisAuditStore
from pa_agent.records.schema import AnalysisRecord, RecordMeta


def _record() -> AnalysisRecord:
    return AnalysisRecord(
        meta=RecordMeta(
            timestamp_local_iso="2026-07-10T12:00:00+08:00",
            timestamp_local_ms=1_783_657_200_000,
            run_id="run-audit-test",
            symbol="BTCUSDT",
            timeframe="1h",
            bar_count=100,
            ai_provider={"model": "test-model"},
        ),
        kline_data=[],
        htf_text="",
        htf_context={"status": "available", "timeframe": "4h", "reason": None},
        stage1_messages=[],
        stage1_response=None,
        stage1_diagnosis={
            "cycle_position": "trading_range",
            "direction": "bullish",
            "gate_result": "proceed",
            "entry_setup": "buy the lower edge after a bullish signal",
            "pa_evidence": {
                "fact_refs": ["PA.WINDOW.K5", "PA.BAR.K1"],
                "theory_rule_ids": ["BROOKS.RANGE.EDGE_OVER_MIDDLE"],
                "spike_analysis": None,
            },
        },
        stage2_messages=[],
        stage2_response=None,
        stage2_decision={
            "decision": {
                "order_type": "limit",
                "order_direction": "long",
                "trade_confidence": 62,
                "estimated_win_rate": 54,
                "entry_rule": "buy only after a closed bullish signal bar",
                "reasoning": "The range edge offers defined invalidation.",
                "key_factors": ["range edge"],
                "watch_points": ["failed follow-through"],
                "risk_assessment": "A failed signal returns price to the range.",
                "invalidation_condition": "Close below the signal low.",
            },
            "terminal": {"outcome": "trade"},
        },
        strategy_files_used=["range.txt"],
        experience_loaded=[],
        exception=None,
        usage_total={"total_tokens": 1234},
        vpa_sidecar={
            "mode": "shadow",
            "pair_id": "vpa-pair-test",
            "snapshot_hash": "snapshot-test",
            "evidence_catalog": {
                "catalog_id": "vpa-catalog-test",
                "data_status": "limited",
                "provenance": {"venue": "BINANCE", "volume_kind": "real"},
            },
            "stage1_context": {
                "data_status": "limited",
                "observations": [
                    {
                        "evidence_ids": ["vpa-e001"],
                        "finding_kind": "effort_result",
                        "interpretation": "Relative activity adds independent context.",
                    }
                ],
                "background_relation": "supports",
                "entry_timing_relation": "challenges",
            },
            "stage2_impact": {
                "background_relation": "supports",
                "entry_timing_relation": "challenges",
                "evidence_used": ["vpa-e001"],
                "decision_effect": "prefer_retest",
                "reason": "The cited evidence favors waiting for a retest.",
            },
            "comparison": {
                "stage1": {"direction": {"baseline": "bullish", "vpa": "bearish"}},
                "observed_effect": {
                    "classification": "vpa_reduced_entry_attempt",
                    "baseline_has_executable_order": True,
                    "vpa_has_executable_order": False,
                },
            },
            "paired_execution": {
                "experiment_type": "full_prompt_ab",
                "pair_identity_version": "initial_stage1_requests_v1",
                "protocol_valid": True,
                "full_prompt_ab_eligible": True,
                "first_pass_clean": True,
                "authority_violations": [],
            },
            "vpa_arm": {
                "stage2_decision": {
                    "decision": {"order_type": "不下单", "trade_confidence": 41}
                }
            },
        },
        call_attempts=[
            {
                "arm": "baseline",
                "stage": "stage1",
                "attempt": 1,
                "request_hash": "request-1",
                "response_hash": "response-1",
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "cached_prompt_tokens": 25,
                "total_tokens": 140,
                "latency_ms": 1200.5,
                "status": "validated",
                "validation_category": None,
            }
        ],
        validation_audit=[
            {
                "stage": "stage1",
                "attempt": 1,
                "category": "a",
                "message": "JSON syntax repaired",
                "missing_fields": [],
                "invalid_fields": [],
            }
        ],
    )


def test_audit_store_indexes_run_and_manual_outcome(tmp_path: Path) -> None:
    store = AnalysisAuditStore(tmp_path / "audit.sqlite3")

    assert store.record_analysis(_record(), record_path=tmp_path / "record.json").ok
    run = store.get_run("run-audit-test")
    assert run is not None
    assert run["symbol"] == "BTCUSDT"
    assert run["vpa_mode"] == "shadow"
    assert run["vpa_effect"] == "prefer_retest"
    assert run["vpa_background_relation"] == "supports"
    assert run["vpa_timing_relation"] == "challenges"
    assert run["catalog_id"] == "vpa-catalog-test"
    assert run["htf_status"] == "available"
    assert run["htf_timeframe"] == "4h"
    assert run["baseline_order_type"] == "limit"
    assert run["vpa_order_type"] == "不下单"
    assert run["observed_effect"] == "vpa_reduced_entry_attempt"
    assert run["experiment_type"] == "full_prompt_ab"
    assert run["pair_identity_version"] == "initial_stage1_requests_v1"
    assert run["ab_protocol_valid"] == 1
    assert run["full_prompt_ab_eligible"] == 1
    assert run["ab_first_pass_clean"] == 1
    review = json.loads(run["review_context_json"])
    assert review["pa_evidence"]["fact_refs"] == ["PA.WINDOW.K5", "PA.BAR.K1"]
    assert review["pa_evidence"]["theory_rule_ids"] == [
        "BROOKS.RANGE.EDGE_OVER_MIDDLE"
    ]
    assert review["vpa_context"]["observations"][0]["evidence_ids"] == [
        "vpa-e001"
    ]
    assert review["vpa_impact"]["decision_effect"] == "prefer_retest"
    assert review["decision_review"]["risk_assessment"].startswith("A failed signal")
    assert json.loads(run["authority_violations_json"]) == []
    assert json.loads(run["validation_audit_json"])[0]["category"] == "a"
    assert run["record_path"].endswith("record.json")
    calls = store.get_calls("run-audit-test")
    assert len(calls) == 1
    assert calls[0]["arm"] == "baseline"
    assert calls[0]["stage"] == "stage1"
    assert calls[0]["attempt"] == 1
    assert calls[0]["cached_prompt_tokens"] == 25
    assert calls[0]["latency_ms"] == 1200.5

    assert store.record_outcome(
        "run-audit-test",
        entry_status="filled",
        outcome="win",
        horizon_bars=12,
        realized_return_r=1.5,
        mfe_r=2.0,
        mae_r=-0.4,
        notes="manual review",
    ).ok
    outcome = store.get_outcome("run-audit-test")
    assert outcome is not None
    assert outcome["entry_status"] == "filled"
    assert outcome["outcome"] == "win"
    assert outcome["realized_return_r"] == 1.5


def test_audit_store_upserts_one_row_per_run(tmp_path: Path) -> None:
    store = AnalysisAuditStore(tmp_path / "audit.sqlite3")
    record = _record()

    assert store.record_analysis(record, record_path=tmp_path / "first.json").ok
    assert store.record_analysis(record, record_path=tmp_path / "second.json").ok

    rows = store.list_runs(symbol="BTCUSDT", timeframe="1h")
    assert len(rows) == 1
    assert rows[0]["record_path"].endswith("second.json")


def test_audit_store_rejects_uncontrolled_outcome_labels(tmp_path: Path) -> None:
    store = AnalysisAuditStore(tmp_path / "audit.sqlite3")
    assert store.record_analysis(_record(), record_path=tmp_path / "record.json").ok

    result = store.record_outcome(
        "run-audit-test",
        entry_status="filled",
        outcome="looks_good",
    )

    assert result.ok is False
    assert "invalid outcome" in (result.error or "")


def test_audit_store_rebuilds_derived_index_from_json_records(tmp_path: Path) -> None:
    record_path = tmp_path / "record.json"
    record_path.write_text(json.dumps(_record().model_dump()), encoding="utf-8")
    store = AnalysisAuditStore(tmp_path / "audit.sqlite3")

    result = store.rebuild_from_records(tmp_path)

    assert result.indexed == 1
    assert result.skipped == 0
    assert store.get_run("run-audit-test") is not None


def test_frequency_guard_groups_repeat_attempts_by_frozen_snapshot(tmp_path: Path) -> None:
    store = AnalysisAuditStore(tmp_path / "audit.sqlite3")
    first = _record()
    second = _record()
    second = second.model_copy(
        update={
            "meta": second.meta.model_copy(update={"run_id": "run-audit-repeat"}),
            "stage2_decision": {
                "decision": {"order_type": "不下单", "trade_confidence": 30},
                "terminal": {"outcome": "wait"},
            },
            "vpa_sidecar": {
                **(second.vpa_sidecar or {}),
                "comparison": {
                    "observed_effect": {
                        "classification": "vpa_added_entry_attempt",
                        "baseline_has_executable_order": False,
                        "vpa_has_executable_order": True,
                    }
                },
                "vpa_arm": {
                    "stage2_decision": {
                        "decision": {"order_type": "limit", "trade_confidence": 48}
                    }
                },
            },
        }
    )

    assert store.record_analysis(first, record_path=tmp_path / "first.json").ok
    assert store.record_analysis(second, record_path=tmp_path / "second.json").ok
    report = store.vpa_frequency_guard_report(min_unique_snapshots=1)

    assert report["paired_attempts"] == 2
    assert report["unique_snapshots"] == 1
    assert report["baseline_executable_rate"] == 0.5
    assert report["vpa_executable_rate"] == 0.5
    assert report["relative_trade_rate_decline"] == 0.0
    assert report["effect_counts"] == {"mixed_repeated_attempts": 1}
    assert report["attempt_effect_counts"] == {
        "vpa_reduced_entry_attempt": 1,
        "vpa_added_entry_attempt": 1,
    }
    assert report["mixed_repeated_attempts"] == 1
    assert report["ready_for_soft"] is False


def test_frequency_guard_excludes_marginal_sidecar_runs(tmp_path: Path) -> None:
    store = AnalysisAuditStore(tmp_path / "audit.sqlite3")
    full = _record()
    marginal = _record().model_copy(
        update={
            "meta": _record().meta.model_copy(update={"run_id": "run-marginal"}),
            "vpa_sidecar": {
                **(_record().vpa_sidecar or {}),
                "pair_id": "vpa-marginal-pair",
                "paired_execution": {
                    "experiment_type": "online_vpa_marginal_sidecar",
                    "protocol_valid": True,
                    "full_prompt_ab_eligible": False,
                    "authority_violations": [],
                },
            },
        }
    )

    assert store.record_analysis(full, record_path=tmp_path / "full.json").ok
    assert store.record_analysis(marginal, record_path=tmp_path / "marginal.json").ok

    report = store.vpa_frequency_guard_report(min_unique_snapshots=1)

    assert report["experiment_type"] == "full_prompt_ab"
    assert report["all_paired_attempts"] == 1
    assert report["unique_snapshots"] == 1


def test_frequency_guard_reports_failed_vpa_arms_separately(tmp_path: Path) -> None:
    store = AnalysisAuditStore(tmp_path / "audit.sqlite3")
    completed = _record()
    failed = _record().model_copy(
        update={
            "meta": _record().meta.model_copy(update={"run_id": "run-vpa-failed"}),
            "vpa_sidecar": {
                **(_record().vpa_sidecar or {}),
                "pair_id": "vpa-pair-failed",
                "comparison": {
                    "observed_effect": {
                        "classification": "vpa_arm_failed",
                        "comparison_valid": False,
                    }
                },
                "paired_execution": {
                    "experiment_type": "full_prompt_ab",
                    "pair_identity_version": "initial_stage1_requests_v1",
                    "protocol_valid": False,
                    "full_prompt_ab_eligible": False,
                    "authority_violations": [],
                },
                "vpa_arm": {
                    "stage2_decision": None,
                    "exception": {"stage": "stage1", "category": "c"},
                },
            },
        }
    )

    assert store.record_analysis(completed, record_path=tmp_path / "completed.json").ok
    assert store.record_analysis(failed, record_path=tmp_path / "failed.json").ok

    report = store.vpa_frequency_guard_report(min_unique_snapshots=1)

    assert report["paired_attempts"] == 1
    assert report["all_paired_attempts"] == 2
    assert report["failed_vpa_attempts"] == 1
    assert report["protocol_invalid_attempts"] == 1
    assert report["ineligible_attempts"] == 1
    assert report["vpa_arm_failure_rate"] == 0.5
    assert any("excluded" in reason for reason in report["reasons"])


def test_new_snapshot_auto_updates_prior_counterfactual_outcome(tmp_path: Path) -> None:
    store = AnalysisAuditStore(tmp_path / "audit.sqlite3", simulation_horizon_bars=10)
    first = _record().model_copy(
        update={
            "kline_data": [
                {
                    "seq": 1,
                    "ts_open": 1_000,
                    "open": 103.0,
                    "high": 104.0,
                    "low": 101.0,
                    "close": 102.0,
                    "volume": 1.0,
                    "closed": True,
                }
            ],
            "stage2_decision": {
                "decision": {
                    "order_type": "限价单",
                    "order_direction": "做多",
                    "entry_price": 100.0,
                    "stop_loss_price": 95.0,
                    "take_profit_price": 110.0,
                    "take_profit_price_2": 115.0,
                    "trade_confidence": 62,
                    "estimated_win_rate": 54,
                },
                "terminal": {"outcome": "trade"},
            },
        }
    )
    assert store.record_analysis(first, record_path=tmp_path / "first.json").ok

    pending = store.get_counterfactual_outcome("run-audit-test", arm="baseline")
    assert pending is not None
    assert pending["outcome"] == "pending"

    missing_origin = first.model_copy(
        update={
            "meta": first.meta.model_copy(update={"run_id": "run-history-gap"}),
            "kline_data": [
                {
                    "seq": 1,
                    "ts_open": 3_000,
                    "open": 102.0,
                    "high": 111.0,
                    "low": 101.0,
                    "close": 109.0,
                    "volume": 1.0,
                    "closed": True,
                },
                {
                    "seq": 2,
                    "ts_open": 2_000,
                    "open": 103.0,
                    "high": 104.0,
                    "low": 99.0,
                    "close": 102.0,
                    "volume": 1.0,
                    "closed": True,
                },
            ],
            "vpa_sidecar": None,
        }
    )
    assert store.record_analysis(
        missing_origin,
        record_path=tmp_path / "missing-origin.json",
    ).ok
    still_pending = store.get_counterfactual_outcome("run-audit-test", arm="baseline")
    assert still_pending is not None
    assert still_pending["outcome"] == "pending"

    second = first.model_copy(
        update={
            "meta": first.meta.model_copy(
                update={"run_id": "run-audit-later", "timestamp_local_ms": 1_783_660_800_000}
            ),
            "kline_data": [
                {
                    "seq": 1,
                    "ts_open": 3_000,
                    "open": 102.0,
                    "high": 111.0,
                    "low": 101.0,
                    "close": 109.0,
                    "volume": 1.0,
                    "closed": True,
                },
                {
                    "seq": 2,
                    "ts_open": 2_000,
                    "open": 103.0,
                    "high": 104.0,
                    "low": 99.0,
                    "close": 102.0,
                    "volume": 1.0,
                    "closed": True,
                },
                first.kline_data[0],
            ],
            "stage2_decision": {
                "decision": {
                    "order_type": "不下单",
                    "order_direction": None,
                    "trade_confidence": 30,
                    "estimated_win_rate": None,
                },
                "terminal": {"outcome": "wait"},
            },
            "vpa_sidecar": None,
        }
    )
    assert store.record_analysis(second, record_path=tmp_path / "second.json").ok

    outcome = store.get_counterfactual_outcome("run-audit-test", arm="baseline")
    assert outcome is not None
    assert outcome["entry_status"] == "filled"
    assert outcome["outcome"] == "win"
    assert outcome["realized_return_r"] == 2.0

    reconfigured = AnalysisAuditStore(
        tmp_path / "audit.sqlite3",
        simulation_horizon_bars=1,
    )
    assert reconfigured.record_analysis(second, record_path=tmp_path / "second.json").ok
    shortened = reconfigured.get_counterfactual_outcome(
        "run-audit-test",
        arm="baseline",
    )
    assert shortened is not None
    assert shortened["horizon_bars"] == 1
    assert shortened["status"] == "horizon_reached"
    assert shortened["outcome"] == "unresolved"


def test_counterfactual_horizon_is_capped_by_saved_bar_window(tmp_path: Path) -> None:
    store = AnalysisAuditStore(tmp_path / "audit.sqlite3", simulation_horizon_bars=500)
    record = _record().model_copy(
        update={
            "meta": _record().meta.model_copy(update={"bar_count": 20}),
            "kline_data": [
                {
                    "seq": 1,
                    "ts_open": 1_000,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1.0,
                    "closed": True,
                }
            ],
        }
    )

    assert store.record_analysis(record, record_path=tmp_path / "record.json").ok
    outcome = store.get_counterfactual_outcome("run-audit-test", arm="baseline")

    assert outcome is not None
    assert outcome["horizon_bars"] == 19


def test_counterfactual_report_is_paired_and_snapshot_balanced(tmp_path: Path) -> None:
    store = AnalysisAuditStore(tmp_path / "audit.sqlite3")

    def paired_record(run_id: str, pair_id: str, snapshot_hash: str) -> AnalysisRecord:
        record = _record()
        return record.model_copy(
            update={
                "meta": record.meta.model_copy(update={"run_id": run_id}),
                "vpa_sidecar": {
                    **(record.vpa_sidecar or {}),
                    "pair_id": pair_id,
                    "snapshot_hash": snapshot_hash,
                },
            }
        )

    ineligible = paired_record("run-c1", "pair-c", "snapshot-c")
    ineligible = ineligible.model_copy(
        update={
            "vpa_sidecar": {
                **(ineligible.vpa_sidecar or {}),
                "paired_execution": {
                    "experiment_type": "full_prompt_ab",
                    "pair_identity_version": "initial_stage1_requests_v1",
                    "protocol_valid": True,
                    "full_prompt_ab_eligible": False,
                    "authority_violations": ["stage1.direction_changed"],
                },
            }
        }
    )
    records = [
        paired_record("run-a1", "pair-a", "snapshot-a"),
        paired_record("run-a2", "pair-a", "snapshot-a"),
        # Same market snapshot but a different frozen experiment input. Pair
        # identity, not raw OHLC identity, must keep these groups separate.
        paired_record("run-b1", "pair-b", "snapshot-a"),
        ineligible,
        _record().model_copy(
            update={
                "meta": _record().meta.model_copy(update={"run_id": "run-off"}),
                "vpa_sidecar": None,
            }
        ),
    ]
    for record in records:
        assert store.record_analysis(
            record,
            record_path=tmp_path / f"{record.meta.run_id}.json",
        ).ok

    outcomes = [
        ("run-a1", "baseline", "win", 2.0),
        ("run-a1", "vpa", "loss", -1.0),
        ("run-a2", "baseline", "loss", -1.0),
        ("run-a2", "vpa", "loss", -1.0),
        ("run-b1", "baseline", "win", 2.0),
        ("run-b1", "vpa", "win", 2.0),
        ("run-c1", "baseline", "win", 2.0),
        ("run-c1", "vpa", "win", 2.0),
        ("run-off", "baseline", "loss", -1.0),
    ]
    with store._connect() as connection:
        store._ensure_schema(connection)
        for run_id, arm, outcome, realized_r in outcomes:
            connection.execute(
                """
                INSERT INTO counterfactual_outcomes (
                    run_id, arm, status, entry_status, outcome, horizon_bars,
                    bars_observed, realized_return_r, assumption_version, notes,
                    updated_at_ms
                ) VALUES (?, ?, 'completed', 'filled', ?, 20, 2, ?, ?, '', 1)
                """,
                (run_id, arm, outcome, realized_r, store._COUNTERFACTUAL_VERSION),
            )

    report = store.counterfactual_performance_report(min_decisive_snapshots=2)

    assert report["statistically_reviewable"] is True
    assert report["matched_decisive_snapshots"] == 2
    assert report["baseline"]["plans"] == 3
    assert report["baseline"]["attempt_win_rate"] == 0.6667
    assert report["baseline"]["win_rate"] == 0.75
    assert report["baseline"]["mean_return_r"] == 1.25
    assert report["vpa"]["win_rate"] == 0.5
    assert report["matched_comparison"]["win_rate_delta"] == -0.25
    assert report["matched_comparison"]["mean_return_r_delta"] == -0.75
    assert report["matched_comparison"]["improved_snapshots"] == 0
    assert report["matched_comparison"]["worsened_snapshots"] == 1
    assert report["matched_comparison"]["unchanged_snapshots"] == 1
    assert report["matched_comparison"]["directional_sign_test_p"] == 1.0
    assert any("saved bar window" in item for item in report["limitations"])
