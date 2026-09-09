import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

from pa_agent.config.analysis_pipeline import AnalysisPipelineIdentity
from pa_agent.config.settings import PaperTradingSettings
from pa_agent.execution.coordinator import PaperExecutionCoordinator
from pa_agent.records.schema import AnalysisTiming
from tests.unit.test_execution_plans import _record, _spec
from tests.unit.test_paper_execution import _bar

T = 1_800_000_000_000
H = 3_600_000


def record():
    r = _record(path="joint", mode="off")
    r.analysis_pipeline = AnalysisPipelineIdentity(pipeline="pa_vpa_joint")
    r.stage2_decision["analysis_basis"] = {"protocol_version": "pa_vpa_stage2_draft_v1"}
    r.vpa_evidence = {"catalog_id": "validated"}
    r.kline_data[0]["ts_open"] = T
    r.analysis_timing = AnalysisTiming(
        analysis_as_of_ms=T + H,
        model_completed_at_ms=T + H + 100,
        validated_at_ms=T + H + 200,
        result_available_at_ms=T + H + 300,
        decision_expires_at_ms=T + 2 * H,
    )
    return r


def clock(monkeypatch, at):
    fake = SimpleNamespace(now=lambda *args: datetime.fromtimestamp(at / 1000, tz=timezone.utc))
    monkeypatch.setattr("pa_agent.execution.plans.datetime", fake)
    monkeypatch.setattr("pa_agent.execution.coordinator.datetime", fake)
    monkeypatch.setattr("pa_agent.execution.paper.datetime", fake)


def test_versioned_account_submission_replay_and_restart_are_isolated(tmp_path, monkeypatch):
    clock(monkeypatch, T + H + 500)
    db = tmp_path / "paper.sqlite3"
    settings = PaperTradingSettings(enabled=True)
    coordinator = PaperExecutionCoordinator(db, settings)
    r = record()
    action = coordinator.submit_official(r, _spec(), task_id="new-task")
    assert action.status == "submitted", action.reason
    assert action.account_id == "pipeline-pa_vpa_joint"
    second = PaperExecutionCoordinator(db, settings).submit_official(r, _spec(), task_id="new-task")
    assert second.order_id == action.order_id
    coordinator.observe_official(
        _spec(), "1h", [_bar(T + H, open_=2005, high=2010, low=2000, close=2005)]
    )
    with sqlite3.connect(db) as c:
        row = c.execute(
            "SELECT status, fill_price, available_at_ms, take_profit_price_2 FROM paper_orders"
        ).fetchone()
    assert row == ("ambiguous", None, T + H + 500, None)


def test_expired_analysis_cannot_supersede_pending_plan(tmp_path, monkeypatch):
    clock(monkeypatch, T + H + 500)
    db = tmp_path / "paper.sqlite3"
    coordinator = PaperExecutionCoordinator(db, PaperTradingSettings(enabled=True))
    r = record()
    first = coordinator.submit_official(r, _spec(), task_id="first")
    clock(monkeypatch, T + 2 * H)
    r.meta.run_id = "late"
    late = coordinator.submit_official(r, _spec(), task_id="late")
    assert late.status == "rejected"
    assert "expired" in late.reason
    with sqlite3.connect(db) as c:
        assert (
            c.execute(
                "SELECT status FROM paper_orders WHERE order_id = ?", (first.order_id,)
            ).fetchone()[0]
            == "pending"
        )


def test_failed_role_is_not_authorized_by_pipeline_name(tmp_path, monkeypatch):
    clock(monkeypatch, T + H + 500)
    r = record()
    r.role_stop = {"status": "review_required"}
    coordinator = PaperExecutionCoordinator(
        tmp_path / "paper.sqlite3", PaperTradingSettings(enabled=True)
    )
    assert coordinator.submit_official(r, _spec(), task_id="stopped").status == "rejected"


def test_replacement_does_not_invent_zero_pnl_for_unobserved_window(tmp_path, monkeypatch):
    clock(monkeypatch, T + H + 500)
    db = tmp_path / "paper.sqlite3"
    coordinator = PaperExecutionCoordinator(db, PaperTradingSettings(enabled=True))
    r = record()
    first = coordinator.submit_official(r, _spec(), task_id="first")
    clock(monkeypatch, T + H + 1000)
    r.meta.run_id = "replacement"
    assert coordinator.submit_official(r, _spec(), task_id="replacement").status == "submitted"
    with sqlite3.connect(db) as c:
        row = c.execute(
            "SELECT status, realized_pnl, exit_reason FROM paper_orders WHERE order_id = ?",
            (first.order_id,),
        ).fetchone()
    assert row == ("ambiguous", None, "superseded_unobserved_window")


def test_clock_sweep_needs_no_market_request(tmp_path, monkeypatch):
    clock(monkeypatch, T + H + 500)
    db = tmp_path / "paper.sqlite3"
    coordinator = PaperExecutionCoordinator(db, PaperTradingSettings(enabled=True))
    action = coordinator.submit_official(record(), _spec(), task_id="clock-only")
    clock(monkeypatch, T + 2 * H)
    assert coordinator.expire_pending() == 1
    assert coordinator.expire_pending() == 0
    with sqlite3.connect(db) as c:
        assert c.execute(
            "SELECT status, realized_pnl FROM paper_orders WHERE order_id = ?", (action.order_id,)
        ).fetchone() == ("ambiguous", None)
