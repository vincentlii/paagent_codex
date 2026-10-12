from dataclasses import replace
from types import SimpleNamespace

import pytest

from pa_agent.execution.delivery import validated_delivery
from pa_agent.execution.paper import PaperBroker, RiskPolicy
from pa_agent.execution.plans import official_paper_plan
from pa_agent.records.schema import AnalysisTiming
from tests.unit.test_execution_plans import _record, _spec
from tests.unit.test_latency_paper import H, T, plan


def timed_record():
    from pa_agent.config.analysis_pipeline import AnalysisPipelineIdentity

    r = _record(mode="off")
    r.analysis_pipeline = AnalysisPipelineIdentity(pipeline="pa_only")
    r.meta.timeframe = "1h"
    r.kline_data[0]["ts_open"] = T
    r.role_runtime = {"analysis_as_of_ms": T + H, "execution_policy": "limit_3bar_v1"}
    r.call_attempts = [{"response_hash": "valid", "created_at_ms": T + H + 100}]
    r.analysis_timing = None
    return r


def test_limit_lifetime_is_frozen_separately_from_result_freshness():
    r = timed_record()
    timing = validated_delivery(r, now_ms=T + H + 200)
    assert timing.decision_expires_at_ms == T + 2 * H
    assert timing.limit_expires_at_ms == T + 4 * H
    assert timing.execution_policy == "limit_3bar_v1"
    r.analysis_timing = AnalysisTiming.model_validate({
        **timing.model_dump(), "result_available_at_ms": T + H + 300,
    })
    p = official_paper_plan(r, _spec(), dedupe_key="new", now_ms=T + H + 500)
    assert p.expires_at_ms == T + 4 * H
    assert p.result_expires_at_ms == T + 2 * H
    with pytest.raises(ValueError, match="expired"):
        official_paper_plan(r, _spec(), dedupe_key="late", now_ms=T + 2 * H)
    r.stage2_decision["decision"]["order_type"] = "市价单"
    market = official_paper_plan(r, _spec(), dedupe_key="market", now_ms=T + H + 500)
    assert market.expires_at_ms == T + 2 * H
    assert market.result_expires_at_ms is None


def test_broker_rechecks_result_freshness_under_write_lock(tmp_path, monkeypatch):
    from pa_agent.execution import paper

    b = PaperBroker(tmp_path / "paper.sqlite3", account_id="new", initial_equity=10000,
                    risk_policy=RiskPolicy())
    p = replace(plan(), expires_at_ms=T + 4 * H, result_expires_at_ms=T + 2 * H)
    class Clock:
        @classmethod
        def now(cls, tz=None):
            return SimpleNamespace(timestamp=lambda: (T + 2 * H) / 1000)
    monkeypatch.setattr(paper, "datetime", Clock)
    with pytest.raises(ValueError, match="expired"):
        b.submit(p, _spec(), activate_now=True)
    import sqlite3
    with sqlite3.connect(tmp_path / "paper.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0


def test_new_policy_never_relabels_legacy_delivery():
    r = timed_record()
    r.role_runtime.pop("execution_policy")
    timing = validated_delivery(r, now_ms=T + H + 200)
    assert timing.execution_policy is None
    assert timing.limit_expires_at_ms is None


def test_v4_waits_past_first_close_and_can_fill_after_restart(tmp_path):
    import sqlite3
    from tests.unit.test_paper_execution import _bar

    path = tmp_path / "paper.sqlite3"
    def broker():
        return PaperBroker(path, account_id="new", initial_equity=10000, risk_policy=RiskPolicy())
    b = broker()
    p = replace(plan(), available_at_ms=T + H + 100, expires_at_ms=T + 4 * H,
                result_expires_at_ms=T + 2 * H)
    new = b.submit(p, _spec())
    legacy = b.submit(replace(plan(), run_id="legacy", available_at_ms=T + H + 100), _spec())
    b.observe_market(_spec(), "1h", [_bar(T + H, open_=2005, high=2010, low=2003, close=2007)])
    assert b.order(new.order_id)["status"] == "pending"
    assert b.order(legacy.order_id)["status"] == "cancelled"
    assert b.expire_pending(now_ms=T + 2 * H) == 0
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT simulation_version, result_expires_at_ms FROM paper_orders WHERE order_id=?",
                          (new.order_id,)).fetchone() == ("paper_latency_v4", T + 2 * H)
    restarted = broker()
    assert restarted.order(new.order_id)["result_expires_at_ms"] == T + 2 * H
    restarted.observe_market(_spec(), "1h", [_bar(T + 2 * H, open_=2000, high=2008, low=1998, close=2005)])
    assert restarted.order(new.order_id)["status"] == "open"


def test_nullable_migration_preserves_all_legacy_order_fields(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.sqlite3"
    b = PaperBroker(path, account_id="old", initial_equity=10000, risk_policy=RiskPolicy())
    old = b.submit(plan(), _spec())
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE paper_orders DROP COLUMN result_expires_at_ms")
        before = db.execute("SELECT * FROM paper_orders").fetchall()
    restored = PaperBroker(path, account_id="old", initial_equity=10000, risk_policy=RiskPolicy())
    with sqlite3.connect(path) as db:
        columns = [c[1] for c in db.execute("PRAGMA table_info(paper_orders)") if c[1] != "result_expires_at_ms"]
        assert db.execute("SELECT " + ",".join(columns) + " FROM paper_orders").fetchall() == before
    assert restored.order(old.order_id)["simulation_version"] == "paper_latency_v3"
    assert restored.order(old.order_id)["result_expires_at_ms"] is None


@pytest.mark.parametrize("later_open,later_low", [(2000, 1998), (2005, 2003)])
@pytest.mark.parametrize("observe_first", [True, False])
def test_v4_missing_pending_bar_cannot_confirm_fill_or_expiry(tmp_path, later_open, later_low, observe_first):
    from tests.unit.test_paper_execution import _bar

    path = tmp_path / "gap.sqlite3"
    def broker():
        return PaperBroker(path, account_id="new", initial_equity=10000, risk_policy=RiskPolicy())
    b = broker()
    p = replace(plan(), available_at_ms=T + H + 100, expires_at_ms=T + 4 * H,
                result_expires_at_ms=T + 2 * H)
    order = b.submit(p, _spec())
    if observe_first:
        b.observe_market(_spec(), "1h", [_bar(T + H, open_=2005, high=2010, low=2003, close=2007)])
    restarted = broker()
    restarted.observe_market(_spec(), "1h", [
        _bar(T + 3 * H, open_=later_open, high=2010, low=later_low, close=2007),
    ])
    row = restarted.order(order.order_id)
    assert row["status"] == "ambiguous"
    assert row["exit_reason"] == "missing_pending_order_bars"
    assert row["fill_price"] is None
    assert row["realized_pnl"] is None


@pytest.mark.parametrize("fields", [
    {"execution_policy": "unknown"},
    {"execution_policy": "limit_3bar_v1"},
    {"limit_expires_at_ms": T + 4 * H},
    {"execution_policy": "limit_3bar_v1", "limit_expires_at_ms": T + 2 * H},
])
def test_policy_pair_and_expiry_are_not_inferred(fields):
    with pytest.raises(ValueError):
        AnalysisTiming(analysis_as_of_ms=T + H, decision_expires_at_ms=T + 2 * H, **fields)


def test_policy_survives_frozen_task_codec_without_changing_legacy_cohort():
    from pa_agent.config.settings import Settings
    from pa_agent.config.analysis_pipeline import AnalysisPipelineIdentity
    from pa_agent.monitoring.frozen_task_codec import decode_task, encode_task
    from pa_agent.monitoring.task_factory import create_frozen_analysis_task
    from tests.unit.test_monitoring_recovery import _task

    settings = Settings(analysis_pipeline=AnalysisPipelineIdentity(pipeline="pa_only"))
    args = dict(source="tradingview", analysis_fingerprint="a" * 64)
    payload = _task().payload
    frame, htf = payload.frame, dict(payload.htf_context)
    legacy = create_frozen_analysis_task(frame, htf, settings, **args)
    current = create_frozen_analysis_task(frame, htf, settings, execution_policy="limit_3bar_v1", **args)
    assert current.snapshot_hash == legacy.snapshot_hash
    assert current.cohort_id != legacy.cohort_id
    encoded, digest = encode_task(current)
    assert decode_task(encoded, digest).payload.execution_policy == "limit_3bar_v1"
    old, old_digest = encode_task(legacy)
    assert '"execution_policy"' not in old
    assert decode_task(old, old_digest).payload.execution_policy is None


def test_only_stage1_first_draft_budget_increases():
    from pa_agent.agent_runtime.publisher import CodexJobPublisher

    publisher = object.__new__(CodexJobPublisher)
    publisher._budget = None
    args = dict(mandatory_module_count=0, mandatory_knowledge_bytes=0,
                bar_count=100, role_evidence_count=0, role_evidence_bytes=0)
    assert publisher._budget_for("stage1", 1, bounded_packet_bytes=100, **args).max_wall_seconds == 300
    assert publisher._budget_for("stage1", 2, **args).max_wall_seconds == 180
    assert publisher._budget_for("vpa_evidence", 1, **args).max_wall_seconds == 180
    assert publisher._budget_for("stage2", 1, **args).max_wall_seconds == 300
