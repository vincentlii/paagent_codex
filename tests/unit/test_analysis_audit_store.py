from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pa_agent.records.audit_store import AnalysisAuditStore
from pa_agent.records.schema import AnalysisRecord, RecordMeta


def _bar(
    ts_open: int,
    *,
    open_: float = 103.0,
    high: float = 104.0,
    low: float = 101.0,
    close: float = 102.0,
) -> dict:
    return {
        "seq": 1,
        "ts_open": ts_open,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1.0,
        "closed": True,
    }


def _record(
    *,
    run_id: str = "run-audit-test",
    bars: list[dict] | None = None,
    order_type: str = "limit",
) -> AnalysisRecord:
    decision = {
        "order_type": order_type,
        "order_direction": "long" if order_type != "no_trade" else None,
        "trade_confidence": 62,
        "estimated_win_rate": 54 if order_type != "no_trade" else None,
        "entry_price": 100.0 if order_type != "no_trade" else None,
        "stop_loss_price": 95.0 if order_type != "no_trade" else None,
        "take_profit_price": 110.0 if order_type != "no_trade" else None,
        "take_profit_price_2": 115.0 if order_type != "no_trade" else None,
        "entry_rule": "buy only after a closed bullish signal bar",
        "reasoning": "The range edge offers defined invalidation.",
        "key_factors": ["range edge"],
        "watch_points": ["failed follow-through"],
        "risk_assessment": "A failed signal returns price to the range.",
        "invalidation_condition": "Close below the signal low.",
    }
    return AnalysisRecord(
        meta=RecordMeta(
            timestamp_local_iso="2026-07-10T12:00:00+08:00",
            timestamp_local_ms=1_783_657_200_000,
            run_id=run_id,
            symbol="BTCUSDT",
            timeframe="1h",
            bar_count=100,
            ai_provider={"model": "test-model"},
        ),
        kline_data=list(bars or []),
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
            "decision": decision,
            "terminal": {"outcome": "wait" if order_type == "no_trade" else "trade"},
        },
        strategy_files_used=["range.txt"],
        experience_loaded=[],
        exception=None,
        usage_total={"total_tokens": 1234},
        run_outcome={"status": "completed", "official_path": "joint"},
        vpa_sidecar={
            "mode": "soft",
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
                "observed_effect": {
                    "classification": "joint_reduced_entry_attempt",
                }
            },
            "role_execution": {
                "design": "pa_vpa_joint_v1",
                "authority_violations": [],
            },
            "baseline_stage2": {"decision": {"order_type": "limit"}},
            "joint_stage2": {"decision": {"order_type": order_type}},
        },
        call_attempts=[
            {
                "arm": "pa",
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
                "category": "warning",
                "message": "Non-blocking review note.",
                "missing_fields": [],
                "invalid_fields": [],
            }
        ],
    )


def test_concurrent_asset_records_are_indexed_once(tmp_path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    store = AnalysisAuditStore(db_path)
    records = [_record(run_id=f"run-concurrent-{index}") for index in range(20)]

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda record: store.record_analysis(
                    record,
                    record_path=tmp_path / f"{record.meta.run_id}.json",
                ),
                records,
            )
        )

    assert all(result.ok for result in results)
    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0]
    assert count == 20


def test_audit_store_indexes_official_run_and_manual_outcome(tmp_path: Path) -> None:
    store = AnalysisAuditStore(tmp_path / "audit.sqlite3")

    assert store.record_analysis(_record(), record_path=tmp_path / "record.json").ok
    run = store.get_run("run-audit-test")
    assert run is not None
    assert run["symbol"] == "BTCUSDT"
    assert run["vpa_mode"] == "soft"
    assert run["vpa_effect"] == "prefer_retest"
    assert run["baseline_order_type"] == "limit"
    assert run["joint_order_type"] == "limit"
    assert run["joint_observed_effect"] == "joint_reduced_entry_attempt"
    review = json.loads(run["review_context_json"])
    assert review["pa_evidence"]["fact_refs"] == ["PA.WINDOW.K5", "PA.BAR.K1"]
    assert review["vpa_impact"]["decision_effect"] == "prefer_retest"
    assert json.loads(run["authority_violations_json"]) == []
    assert store.get_calls("run-audit-test")[0]["cached_prompt_tokens"] == 25

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


def test_counterfactual_replays_only_the_saved_official_decision(tmp_path: Path) -> None:
    store = AnalysisAuditStore(tmp_path / "audit.sqlite3", simulation_horizon_bars=10)
    first = _record(bars=[_bar(1_000)])
    assert store.record_analysis(first, record_path=tmp_path / "first.json").ok

    pending = store.get_counterfactual_outcome("run-audit-test")
    assert pending is not None
    assert pending["arm"] == "official"
    assert pending["outcome"] == "pending"
    assert store.get_counterfactual_outcome("run-audit-test", arm="vpa") is None


def test_counterfactual_schema_migrates_legacy_baseline_arm(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    store = AnalysisAuditStore(db_path)
    assert store.record_analysis(
        _record(bars=[_bar(1_000)]),
        record_path=tmp_path / "record.json",
    ).ok
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE counterfactual_outcomes SET arm = 'baseline' WHERE arm = 'official'"
        )

    migrated = store.get_counterfactual_outcome("run-audit-test")

    assert migrated is not None
    assert migrated["arm"] == "official"

    later = _record(
        run_id="run-audit-later",
        bars=[
            _bar(3_000, open_=102.0, high=111.0, low=101.0, close=109.0),
            _bar(2_000, open_=103.0, high=104.0, low=99.0, close=102.0),
            _bar(1_000),
        ],
        order_type="no_trade",
    )
    assert store.record_analysis(later, record_path=tmp_path / "later.json").ok

    outcome = store.get_counterfactual_outcome("run-audit-test")
    assert outcome is not None
    assert outcome["entry_status"] == "filled"
    assert outcome["outcome"] == "win"
    assert outcome["realized_return_r"] == 2.0
    assert store.get_counterfactual_outcome("run-audit-test", arm="vpa") is None


def test_counterfactual_waits_when_origin_bar_has_rolled_out(tmp_path: Path) -> None:
    store = AnalysisAuditStore(tmp_path / "audit.sqlite3", simulation_horizon_bars=10)
    first = _record(bars=[_bar(1_000)])
    assert store.record_analysis(first, record_path=tmp_path / "first.json").ok

    history_gap = _record(
        run_id="run-history-gap",
        bars=[_bar(3_000), _bar(2_000)],
        order_type="no_trade",
    )
    assert store.record_analysis(history_gap, record_path=tmp_path / "gap.json").ok

    outcome = store.get_counterfactual_outcome("run-audit-test")
    assert outcome is not None
    assert outcome["outcome"] == "pending"


def test_counterfactual_horizon_is_capped_by_saved_bar_window(tmp_path: Path) -> None:
    store = AnalysisAuditStore(tmp_path / "audit.sqlite3", simulation_horizon_bars=500)
    record = _record(bars=[_bar(1_000)]).model_copy(
        update={"meta": _record().meta.model_copy(update={"bar_count": 20})}
    )

    assert store.record_analysis(record, record_path=tmp_path / "record.json").ok
    outcome = store.get_counterfactual_outcome("run-audit-test")

    assert outcome is not None
    assert outcome["horizon_bars"] == 19
