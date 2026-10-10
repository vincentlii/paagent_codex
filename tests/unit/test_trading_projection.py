from __future__ import annotations

import json
import sqlite3

import pytest

from pa_agent.execution.paper import PaperBroker, RiskPolicy
from pa_agent.monitoring.audit import SchedulerAuditStore
from pa_agent.operations.trading import build_trading_projection

HOUR = 3_600_000
NOW = 1_800_000_000_000
ACCOUNT = "pipeline-pa_vpa_joint"


def _insert(path, table, **values):
    with sqlite3.connect(path) as connection:
        columns = ",".join(values)
        marks = ",".join("?" for _ in values)
        connection.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values())
        )


@pytest.fixture
def ledgers(tmp_path):
    analysis = tmp_path / "analysis.sqlite3"
    monitoring = tmp_path / "monitoring.sqlite3"
    paper = tmp_path / "paper.sqlite3"
    SchedulerAuditStore(monitoring)
    PaperBroker(paper, account_id=ACCOUNT, initial_equity=10_000, risk_policy=RiskPolicy())
    with sqlite3.connect(analysis) as connection:
        connection.execute("""CREATE TABLE analysis_runs (
            run_id TEXT PRIMARY KEY, symbol TEXT, timeframe TEXT, analysis_pipeline TEXT,
            timestamp_local_ms INTEGER, stage1_cycle_position TEXT, stage1_direction TEXT,
            stage1_gate_result TEXT, order_type TEXT, order_direction TEXT, model_name TEXT,
            trade_confidence REAL, review_context_json TEXT, source_provider TEXT, venue TEXT,
            resolved_symbol TEXT, instrument_type TEXT)""")
    with sqlite3.connect(paper) as connection:
        connection.execute("""CREATE TABLE paper_decisions (
            account_id TEXT, arm TEXT, dedupe_key TEXT, run_id TEXT, status TEXT,
            order_id TEXT, reason TEXT, PRIMARY KEY(account_id,arm,dedupe_key))""")
    return analysis, monitoring, paper


def _task(ledgers, name, *, symbol="XAUUSDT", success=1, status="completed", **overrides):
    _insert(
        ledgers[1],
        "monitoring_jobs",
        **{
            "task_id": name,
            "asset_key": symbol,
            "source": "binance_usdm",
            "venue": "BINANCE",
            "symbol": symbol,
            "timeframe": "1h",
            "snapshot_hash": name,
            "cohort_id": "cohort",
            "task_created_at_ms": NOW - HOUR,
            "first_submission_status": "accepted",
            "submission_status": "duplicate",
            "submission_count": 5,
            "submitted_at_ms": NOW - HOUR,
            "updated_at_ms": NOW,
            "completion_status": status,
            "analysis_success": success,
            "run_id": name,
            "execution_latency_ms": 60_000,
            "completed_at_ms": NOW,
            **overrides,
        },
    )


def _analysis(ledgers, name, *, order_type="不下单", **overrides):
    _insert(
        ledgers[0],
        "analysis_runs",
        **{
            "run_id": name,
            "symbol": "XAUUSDT",
            "timeframe": "1h",
            "analysis_pipeline": "pa_vpa_joint",
            "timestamp_local_ms": NOW,
            "source_provider": "binance_usdm",
            "venue": "BINANCE",
            "resolved_symbol": "XAUUSDT",
            "instrument_type": "perpetual",
            "order_type": order_type,
            "stage1_cycle_position": "Trading Range",
            **overrides,
        },
    )


def _order(ledgers, name, *, status="closed", pnl=100.0, **overrides):
    _insert(
        ledgers[2],
        "paper_orders",
        **{
            "order_id": name,
            "account_id": ACCOUNT,
            "run_id": name,
            "arm": "pa_vpa_joint",
            "provider": "binance_usdm",
            "venue": "BINANCE",
            "instrument_type": "perpetual",
            "symbol": "XAUUSDT",
            "timeframe": "1h",
            "side": "long",
            "order_type": "limit",
            "entry_price": 100.0,
            "stop_price": 90.0,
            "take_profit_price": 120.0,
            "quantity": 2.0,
            "contract_multiplier": 1.0,
            "created_bar_ts": NOW - 3 * HOUR,
            "available_at_ms": NOW - 2 * HOUR,
            "status": status,
            "fill_price": 100.0,
            "fill_bar_ts": NOW - 2 * HOUR,
            "exit_bar_ts": NOW - HOUR,
            "realized_pnl": pnl,
            "fees": 5.0,
            "simulation_version": "paper_latency_v3",
            "evaluated_through_ts": NOW - HOUR,
            **overrides,
        },
    )


def _report(ledgers):
    return build_trading_projection(*ledgers, now_ms=NOW)


def _asset(report, symbol="XAUUSDT", period="all"):
    return next(row for row in report["periods"][period]["assets"] if row["symbol"] == symbol)


def test_unique_tasks_not_submissions_and_failure_is_not_no_trade(ledgers):
    _task(ledgers, "no-trade")
    _analysis(ledgers, "no-trade")
    _task(ledgers, "candidate")
    _analysis(ledgers, "candidate", order_type="限价单")
    _task(ledgers, "failed", success=0)
    _task(ledgers, "queued", success=None, status=None)
    _task(
        ledgers,
        "queue-refused",
        success=None,
        status=None,
        first_submission_status="queue_full",
        submission_status="queue_full",
    )
    _task(ledgers, "other-venue", venue="OTHER")
    _insert(
        ledgers[2],
        "paper_decisions",
        account_id=ACCOUNT,
        arm="pa_vpa_joint",
        dedupe_key="candidate",
        run_id="candidate",
        status="rejected",
        reason="expired",
    )

    result = _report(ledgers)
    xau = _asset(result)
    assert result["analysis_available"] is True
    assert (xau["tasks"], xau["completed"], xau["failed"], xau["outstanding"]) == (4, 2, 1, 1)
    assert (xau["joint_completed"], xau["model_no_trade"], xau["candidates"]) == (2, 1, 1)
    assert (xau["paper_rejected"], xau["orders"]) == (1, 0)
    assert xau["protocol_unknown"] == 2
    assert _asset(result, "BTCUSDT")["tasks"] == 0


def test_net_pnl_win_rate_and_curve_exclude_research_old_versions_ambiguous(ledgers):
    for name, pnl in (("win", 100.0), ("loss", -40.0), ("flat", 0.0)):
        _order(ledgers, name, pnl=pnl)
    _order(ledgers, "unknown", pnl=None)
    _order(ledgers, "ambiguous", status="ambiguous", pnl=999.0)
    _order(ledgers, "research", account_id="research-A1", pnl=9999.0)
    _order(ledgers, "old", simulation_version="paper_ohlc_v2", pnl=9999.0)
    _order(ledgers, "wrong-source", provider="other", pnl=9999.0)
    result = _report(ledgers)
    xau = _asset(result)
    assert (xau["closed"], xau["pnl_known"], xau["ambiguous"]) == (4, 3, 1)
    assert (xau["wins"], xau["losses"], xau["breakeven"]) == (1, 1, 1)
    assert xau["win_rate"] == pytest.approx(1 / 3)
    assert xau["realized_pnl"] == 60.0  # already net of fees, not 45
    assert result["periods"]["all"]["curve"][-1]["pnl"] == 60.0
    assert result["account"]["outside_scope_orders"] == 2
    assert all(
        row["order_id"] not in {"old", "research", "wrong-source"} for row in result["orders"]
    )


def test_period_closed_pnl_uses_exit_not_plan_creation_and_analysis_uses_task_time(ledgers):
    _order(ledgers, "old-plan", available_at_ms=NOW - 40 * 24 * HOUR)
    _order(ledgers, "old-exit", pnl=200, exit_bar_ts=NOW - 40 * 24 * HOUR)
    _task(ledgers, "old-task", task_created_at_ms=NOW - 40 * 24 * HOUR)
    _analysis(ledgers, "old-task")
    result = _report(ledgers)
    assert _asset(result, period="7d")["realized_pnl"] == 100
    assert _asset(result, period="7d")["tasks"] == 0
    assert _asset(result)["realized_pnl"] == 300


def test_closed_mark_is_same_market_causal_and_stale_remains_unknown(ledgers):
    _order(ledgers, "long", status="open", pnl=None, exit_bar_ts=None)
    _order(
        ledgers,
        "short",
        symbol="BTCUSDT",
        side="short",
        status="open",
        pnl=None,
        fill_bar_ts=NOW - 6 * HOUR,
    )
    _order(ledgers, "pending", status="pending", fill_price=None, fill_bar_ts=None, pnl=None)
    for symbol, price, ts in (
        ("XAUUSDT", 105.0, NOW - HOUR),
        ("BTCUSDT", 90.0, NOW - 5 * HOUR),
        ("XAUUSDT", 999.0, NOW),
        ("XAUUSDT", 999.0, NOW - 2 * HOUR),
    ):
        _insert(
            ledgers[2],
            "paper_market_bars",
            provider="binance_usdm",
            venue="BINANCE",
            instrument_type="perpetual",
            symbol=symbol,
            timeframe="1h",
            ts_open=ts,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=1,
            amount=price,
        )
    result = _report(ledgers)
    assert result["account"]["marks_known"] == 1
    assert result["account"]["unrealized_pnl"] is None  # no partially marked portfolio PnL
    assert result["account"]["open_risk"] == 40
    assert result["account"]["pending_risk"] == 20
    orders = {row["order_id"]: row for row in result["orders"]}
    assert orders["long"]["unrealized_pnl"] == 10
    assert orders["short"]["mark_status"] == "stale"
    assert orders["pending"]["unrealized_pnl"] is None


def test_review_is_bounded_canonical_and_never_infers_shadow_effect(ledgers):
    _task(ledgers, "review")
    _analysis(
        ledgers,
        "review",
        review_context_json=json.dumps(
            {
                "decision_review": {
                    "reasoning": "Brooks PA 理由",
                    "watch_points": ["下一闭盘"],
                    "secret": "not-exposed",
                },
                "vpa_evidence": {
                    "summary": "canonical VPA",
                    "background_relation": "supports_bull",
                    "entry_timing_relation": "neutral",
                    "observations": [
                        {"interpretation": "evidence first", "evidence_ids": ["V.K1"]}
                    ],
                },
                "vpa_impact": {"decision_effect": "shadow_only_no_effect"},
                "raw_prompt": "not-exposed",
            }
        ),
    )
    result = _report(ledgers)
    review = result["analyses"][0]["review"]
    assert review["reasoning"] == "Brooks PA 理由"
    assert review["vpa_summary"] == "canonical VPA"
    assert "not-exposed" not in json.dumps(result)
    assert "shadow_only_no_effect" not in json.dumps(result)


def test_missing_or_incompatible_ledgers_do_not_create_files_or_show_fake_zero(tmp_path):
    paths = tuple(tmp_path / f"{name}.sqlite3" for name in ("a", "m", "p"))
    result = _report(paths)
    assert result["paper_available"] is False
    assert result["analysis_available"] is False
    assert _asset(result)["realized_pnl"] is None
    assert not any(path.exists() for path in paths)
    paths[2].write_bytes(b"not sqlite")
    assert _report(paths)["paper_available"] is False


def test_projection_does_not_mutate_any_ledger(ledgers):
    _task(ledgers, "closed")
    _analysis(ledgers, "closed")
    _order(ledgers, "closed")
    before = [path.read_bytes() for path in ledgers]
    _report(ledgers)
    assert [path.read_bytes() for path in ledgers] == before


def test_no_closed_trades_has_no_win_rate(ledgers):
    result = _report(ledgers)
    assert result["paper_available"] is True
    assert _asset(result)["win_rate"] is None
    assert _asset(result)["realized_pnl"] == 0


def test_order_review_remains_available_after_latest_analysis_window(ledgers):
    _analysis(
        ledgers,
        "old-order",
        review_context_json=json.dumps(
            {"decision_review": {"reasoning": "original stored reasoning"}}
        ),
    )
    _order(ledgers, "old-order")
    for index in range(40):
        _task(ledgers, f"new-{index}")
        _analysis(ledgers, f"new-{index}")
    result = _report(ledgers)
    assert len(result["analyses"]) == 30
    assert _asset(result)["tasks"] == 40
    assert result["orders"][0]["review"]["reasoning"] == "original stored reasoning"


def test_ambiguous_with_known_entry_counts_fill_but_never_profit(ledgers):
    _order(ledgers, "known-entry", status="ambiguous", pnl=None)
    _order(
        ledgers, "unknown-entry", status="ambiguous", pnl=None, fill_price=None, fill_bar_ts=None
    )
    result = _report(ledgers)
    assert _asset(result)["filled"] == 1
    assert _asset(result)["ambiguous"] == 2
    assert _asset(result)["pnl_known"] == 0


def test_mark_is_unknown_when_order_replay_lags_shared_market_data(ledgers):
    _order(ledgers, "lag", status="open", pnl=None, evaluated_through_ts=NOW - 2 * HOUR)
    _insert(
        ledgers[2],
        "paper_market_bars",
        provider="binance_usdm",
        venue="BINANCE",
        instrument_type="perpetual",
        symbol="XAUUSDT",
        timeframe="1h",
        ts_open=NOW - HOUR,
        open=110,
        high=110,
        low=110,
        close=110,
        volume=1,
        amount=110,
    )
    result = _report(ledgers)
    assert result["orders"][0]["mark_status"] == "execution_lag"
    assert result["account"]["unrealized_pnl"] is None


def test_wrong_or_missing_analysis_identity_cannot_supply_joint_intent_or_review(ledgers):
    _task(ledgers, "wrong")
    _analysis(ledgers, "wrong", order_type="限价单", venue="OTHER")
    _order(ledgers, "wrong")
    _task(ledgers, "missing")
    _analysis(ledgers, "missing", instrument_type=None)
    result = _report(ledgers)
    xau = _asset(result)
    assert (xau["tasks"], xau["completed"], xau["protocol_unknown"]) == (2, 2, 2)
    assert xau["joint_completed"] == 0
    assert not result["orders"][0].get("review")


def test_full_history_counts_are_not_limited_to_recent_journal(ledgers):
    for index in range(205):
        _task(ledgers, f"history-{index}")
        _analysis(ledgers, f"history-{index}")
    result = _report(ledgers)
    assert _asset(result)["tasks"] == 205
    assert _asset(result)["model_no_trade"] == 205
    assert len(result["analyses"]) == 30


def test_headless_review_publishes_safe_trader_block_without_database_mutation(ledgers, tmp_path):
    from pa_agent.operations.projection import build_review_projection
    from pa_agent.operations.runtime_state import RuntimeStatusStore

    projection = build_review_projection(ledgers[0], ledgers[2], monitoring_db_path=ledgers[1])
    status = RuntimeStatusStore(tmp_path / "status.json")
    status.write({"service_state": "running", **projection})
    assert status.read()["trading"]["paper_available"] is True
    assert status.read()["trading"]["analysis_available"] is True


@pytest.mark.parametrize("instrument_type", ["perpetual", "tradifi_perpetual"])
def test_provider_perpetual_product_types_keep_intent_pnl_and_review(ledgers, instrument_type):
    _task(ledgers, "product")
    _analysis(
        ledgers,
        "product",
        order_type="限价单",
        instrument_type=instrument_type,
        review_context_json=json.dumps({"decision_review": {"reasoning": "stored PA"}}),
    )
    _order(ledgers, "product", instrument_type=instrument_type, pnl=12.0)
    result = _report(ledgers)
    assert _asset(result)["joint_completed"] == 1
    assert _asset(result)["candidates"] == 1
    assert _asset(result)["realized_pnl"] == 12.0
    assert result["orders"][0]["instrument_type"] == instrument_type
    assert result["orders"][0]["review"]["reasoning"] == "stored PA"


def test_product_types_are_not_canonicalized_or_cross_bound(ledgers):
    _analysis(
        ledgers,
        "mismatch",
        instrument_type="tradifi_perpetual",
        review_context_json=json.dumps({"decision_review": {"reasoning": "other type"}}),
    )
    _order(ledgers, "mismatch", instrument_type="perpetual")
    _order(ledgers, "unsupported", instrument_type="linear_swap", pnl=999.0)
    result = _report(ledgers)
    assert len(result["orders"]) == 1
    assert _asset(result)["realized_pnl"] == 100.0
    assert not result["orders"][0].get("review")
