from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from pa_agent.config.settings import PaperTradingSettings
from pa_agent.data.base import KlineBar
from pa_agent.execution.coordinator import PaperExecutionCoordinator
from tests.unit.test_execution_plans import _record, _spec


def _bar(ts: int) -> KlineBar:
    return KlineBar(
        seq=1,
        ts_open=ts,
        open=2005.0,
        high=2008.0,
        low=1998.0,
        close=2004.0,
        volume=100.0,
        closed=True,
    )


def test_official_coordinator_is_idempotent_by_task_id(tmp_path) -> None:
    db = tmp_path / "paper.sqlite3"
    coordinator = PaperExecutionCoordinator(
        db,
        PaperTradingSettings(enabled=True),
    )
    record = _record()

    first = coordinator.submit_official(record, _spec(), task_id="task-1")
    second = coordinator.submit_official(record, _spec(), task_id="task-1")

    assert first.order_id == second.order_id
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1


def test_active_market_observation_scope_survives_coordinator_restart(tmp_path):
    db = tmp_path / "paper.sqlite3"
    settings = PaperTradingSettings(enabled=True)
    coordinator = PaperExecutionCoordinator(db, settings)
    action = coordinator.submit_official(_record(), _spec(), task_id="active-1")
    expected = {(_spec().provider, _spec().venue, _spec().symbol, "1h")}
    assert PaperExecutionCoordinator(db, settings).active_market_keys() == expected
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE paper_orders SET status = 'closed' WHERE order_id = ?", (action.order_id,))
    assert coordinator.active_market_keys() == set()


def test_research_coordinator_uses_four_isolated_accounts_only_after_valid_protocol(
    tmp_path,
) -> None:
    db = tmp_path / "paper.sqlite3"
    coordinator = PaperExecutionCoordinator(db, PaperTradingSettings(enabled=True))
    records = (
        _record(path="pa_only", mode="off", arm="A1"),
        _record(path="pa_only", mode="off", arm="A2"),
        _record(path="joint", mode="soft", arm="B1"),
        _record(path="joint", mode="soft", arm="B2"),
    )
    frozen = SimpleNamespace(
        cohort_id="c" * 64,
        market_snapshot_hash="m" * 64,
        instrument_spec=_spec(),
        frame=SimpleNamespace(timeframe="1h", bars=(_bar(2000),)),
    )
    result = SimpleNamespace(
        status="completed",
        protocol_report={"valid": True, "errors": []},
        records=records,
    )

    actions = coordinator.record_aabb(result, frozen)

    assert len(actions) == 4
    assert {action.account_id for action in actions} == {
        f"research-{'c' * 64}-a1",
        f"research-{'c' * 64}-a2",
        f"research-{'c' * 64}-b1",
        f"research-{'c' * 64}-b2",
    }
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 4


def test_disabled_coordinator_writes_nothing(tmp_path) -> None:
    db = tmp_path / "paper.sqlite3"
    coordinator = PaperExecutionCoordinator(db, PaperTradingSettings(enabled=False))

    action = coordinator.submit_official(_record(), _spec(), task_id="task-1")

    assert action.status == "disabled"
    assert not db.exists()


def test_no_trade_disposition_survives_restart(tmp_path) -> None:
    db = tmp_path / "paper.sqlite3"
    settings = PaperTradingSettings(enabled=True)
    record = _record(order_type="\u4e0d\u4e0b\u5355")
    first = PaperExecutionCoordinator(db, settings).submit_official(
        record, _spec(), task_id="task-no-trade"
    )
    second = PaperExecutionCoordinator(db, settings).submit_official(
        record, _spec(), task_id="task-no-trade"
    )

    assert first.status == "no_trade"
    assert second.status == "no_trade"
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_decisions").fetchone()[0] == 1


def test_reconcile_recovers_persisted_analysis_without_model_rerun(
    tmp_path,
    monkeypatch,
) -> None:
    db = tmp_path / "paper.sqlite3"
    coordinator = PaperExecutionCoordinator(db, PaperTradingSettings(enabled=True))
    record = _record()
    monkeypatch.setattr(
        "pa_agent.execution.coordinator.list_record_paths",
        lambda _directory: [Path("saved.json")],
    )
    monkeypatch.setattr(
        "pa_agent.execution.coordinator.load_record",
        lambda _path: record,
    )

    actions = coordinator.reconcile_official(
        [{"task_id": "task-recover", "run_id": record.meta.run_id}],
        tmp_path,
    )

    assert len(actions) == 1
    assert actions[0].status == "submitted"
    assert coordinator.reconcile_official(
        [{"task_id": "task-recover", "run_id": record.meta.run_id}],
        tmp_path,
    ) == ()


def test_b_fallback_is_recorded_as_itt_without_treatment(tmp_path) -> None:
    coordinator = PaperExecutionCoordinator(
        tmp_path / "paper.sqlite3",
        PaperTradingSettings(enabled=True),
    )
    records = (
        _record(path="pa_only", mode="off", arm="A1"),
        _record(path="pa_only", mode="off", arm="A2"),
        _record(path="joint", mode="soft", arm="B1"),
        _record(path="pa_only", mode="soft", arm="B2"),
    )
    frozen = SimpleNamespace(
        cohort_id="c" * 64,
        market_snapshot_hash="m" * 64,
        instrument_spec=_spec(),
        frame=SimpleNamespace(timeframe="1h", bars=(_bar(2000),)),
    )
    result = SimpleNamespace(
        status="completed", protocol_report={"valid": True}, records=records
    )

    actions = coordinator.record_aabb(result, frozen)
    b2 = next(action for action in actions if action.arm == "B2")

    assert b2.received_path == "pa_only"
    assert b2.treatment_received is False


def test_aabb_prevalidates_all_arms_before_writing_any_order(tmp_path) -> None:
    db = tmp_path / "paper.sqlite3"
    coordinator = PaperExecutionCoordinator(db, PaperTradingSettings(enabled=True))
    invalid = _record(path="joint", mode="shadow", arm="B2")
    records = (
        _record(path="pa_only", mode="off", arm="A1"),
        _record(path="pa_only", mode="off", arm="A2"),
        _record(path="joint", mode="soft", arm="B1"),
        invalid,
    )
    frozen = SimpleNamespace(
        cohort_id="c" * 64,
        market_snapshot_hash="m" * 64,
        instrument_spec=_spec(),
        frame=SimpleNamespace(timeframe="1h", bars=(_bar(2000),)),
    )
    result = SimpleNamespace(
        status="completed", protocol_report={"valid": True}, records=records
    )

    try:
        coordinator.record_aabb(result, frozen)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid final arm should reject the research set")

    with sqlite3.connect(db) as connection:
        order_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'paper_orders'"
        ).fetchone()
        assert order_table is None
        assert connection.execute("SELECT COUNT(*) FROM paper_decisions").fetchone()[0] == 0


def test_aabb_paper_requires_spec_from_frozen_experiment_not_external_input(
    tmp_path,
) -> None:
    coordinator = PaperExecutionCoordinator(
        tmp_path / "paper.sqlite3", PaperTradingSettings(enabled=True)
    )
    result = SimpleNamespace(
        status="completed",
        protocol_report={"valid": True},
        records=(
            _record(path="pa_only", mode="off", arm="A1"),
            _record(path="pa_only", mode="off", arm="A2"),
            _record(path="joint", mode="soft", arm="B1"),
            _record(path="joint", mode="soft", arm="B2"),
        ),
    )
    frozen = SimpleNamespace(
        cohort_id="c" * 64,
        market_snapshot_hash="m" * 64,
        instrument_spec=None,
        frame=SimpleNamespace(timeframe="1h", bars=(_bar(2000),)),
    )

    try:
        coordinator.record_aabb(result, frozen)
    except ValueError as exc:
        assert "frozen instrument spec" in str(exc)
    else:
        raise AssertionError("research paper must not use a non-frozen instrument spec")
