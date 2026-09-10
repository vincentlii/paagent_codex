import json
import sqlite3

import pytest

from pa_agent.config.settings import PaperTradingSettings
from pa_agent.config.analysis_pipeline import AnalysisPipelineIdentity
from pa_agent.experiments.study_protocol import digest, write_once
from pa_agent.experiments.study_paper import bind_study_paper, collect_study_paper_outcomes
from pa_agent.records.schema import AnalysisTiming
from tests.unit.test_backend_aabb_protocol import _record
from tests.unit.test_pipeline_paper_coordinator import T, H, clock
from tests.unit.test_execution_plans import _record as plan_record, _spec


def prepared(root, monkeypatch, *, late=False):
    from pa_agent.experiments import study_paper
    now = T + (3 * H if late else H + 500)
    clock(monkeypatch, now)
    monkeypatch.setattr(study_paper, "time_ns", lambda: now * 1_000_000)
    r = _record("paper-run", "codex_agent")
    template = plan_record()
    r.meta.symbol = template.meta.symbol
    r.snapshot_context = template.snapshot_context
    r.stage1_diagnosis = template.stage1_diagnosis
    r.stage2_decision = template.stage2_decision
    r.kline_data = template.kline_data
    r.kline_data[0]["ts_open"] = T
    r.analysis_pipeline = AnalysisPipelineIdentity(pipeline="pa_only")
    r.analysis_timing = AnalysisTiming(
        analysis_as_of_ms=T + H, model_completed_at_ms=T + H + 100,
        validated_at_ms=T + H + 200, result_available_at_ms=T + H + 300,
        decision_expires_at_ms=T + 2 * H,
    )
    assignment = {"case_id": "a" * 24, "sample_id": "s", "arm_id": "A1", "split": "development"}
    body = {"plan": {"contrast": "investigation"}, "assignments": [assignment],
            "samples": [{"sample_id": "s", "episode_id": "e", "window_start_ms": T + H,
                         "window_end_ms": T + 6 * H}]}
    registration = {**body, "registration_digest": digest(body)}
    write_once(root / "registration.json", registration)
    receipt = {"case_id": assignment["case_id"], "registration_digest": registration["registration_digest"],
               "status": "completed", "result": {"output": r.model_dump(mode="json")}}
    write_once(root / "receipts" / f"{assignment['case_id']}.json", {**receipt, "receipt_digest": digest(receipt)})
    return assignment


def test_binding_keeps_activation_separate_from_outcome_horizon_and_recovers(tmp_path, monkeypatch):
    assignment = prepared(tmp_path, monkeypatch)
    db = tmp_path / "paper.sqlite3"
    config = PaperTradingSettings(enabled=True)
    bind_study_paper(tmp_path, db, config)
    binding_path = tmp_path / "paper_bindings" / f"{assignment['case_id']}.json"
    binding = json.loads(binding_path.read_text())
    assert binding["window_end_ms"] == T + 6 * H
    with sqlite3.connect(db) as c:
        row = c.execute("SELECT available_at_ms, expires_at_ms, take_profit_price_2 FROM paper_orders").fetchone()
    assert row == (T + H + 500, T + 2 * H, None)
    binding_path.unlink()  # Crash after order commit, before binding publication.
    from pa_agent.experiments import study_paper
    monkeypatch.setattr(study_paper, "time_ns", lambda: (T + 3 * H) * 1_000_000)
    bind_study_paper(tmp_path, db, config)
    assert json.loads(binding_path.read_text())["order_id"] == binding["order_id"]
    assert collect_study_paper_outcomes(tmp_path, db)[0]["return_r"] is None


def test_late_unactivated_analysis_stays_censored(tmp_path, monkeypatch):
    prepared(tmp_path, monkeypatch, late=True)
    db = tmp_path / "paper.sqlite3"
    bind_study_paper(tmp_path, db, PaperTradingSettings(enabled=True))
    assert collect_study_paper_outcomes(tmp_path, db)[0]["status"] == "pending"
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0


def test_paper_settings_cannot_drift_between_assignments(tmp_path, monkeypatch):
    prepared(tmp_path, monkeypatch)
    db = tmp_path / "paper.sqlite3"
    config = PaperTradingSettings(enabled=True)
    bind_study_paper(tmp_path, db, config)
    config.fee_bps += 1
    with pytest.raises(ValueError, match="settings changed"):
        bind_study_paper(tmp_path, db, config)


def test_tampered_receipt_cannot_activate_paper(tmp_path, monkeypatch):
    assignment = prepared(tmp_path, monkeypatch)
    path = tmp_path / "receipts" / f"{assignment['case_id']}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["result"]["output"]["meta"]["run_id"] = "different-run"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        bind_study_paper(tmp_path, tmp_path / "paper.sqlite3", PaperTradingSettings(enabled=True))


def test_observation_is_bounded_by_preregistered_horizon(tmp_path, monkeypatch):
    prepared(tmp_path, monkeypatch)
    db = tmp_path / "paper.sqlite3"
    bind_study_paper(tmp_path, db, PaperTradingSettings(enabled=True))
    from pa_agent.experiments import study_paper
    from tests.unit.test_paper_execution import _bar
    monkeypatch.setattr(study_paper, "time_ns", lambda: (T + 10 * H) * 1_000_000)
    study_paper.observe_study_paper(tmp_path, db, spec=_spec(), timeframe="1h", bars=[
        _bar(T + 7 * H, open_=2000, high=2010, low=1995, close=2005),
    ])
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM paper_market_bars WHERE ts_open > ?", (T,)).fetchone()[0] == 0
    assert collect_study_paper_outcomes(tmp_path, db)[0]["status"] == "ambiguous"
