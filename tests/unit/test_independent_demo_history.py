import pytest

from pa_agent.execution.demo_contracts import DemoConfig
from pa_agent.execution.demo_dispatch import OfficialExecutionDispatcher
from pa_agent.operations.demo_trading import compose_trading_projection
from pa_agent.operations.trading import safe_trading_projection
from tests.unit.test_trading_projection import ledgers, _order, NOW


def test_disabled_demo_does_not_fallback_to_paper():
    dispatcher = OfficialExecutionDispatcher(None, None, DemoConfig(), now_ms=lambda: 1)
    assert not dispatcher.enabled
    assert dispatcher.submit_official(None, None, task_id="old").status == "execution_disabled"
    assert dispatcher.reconcile_official((), None) == ()
    assert dispatcher.observe_official() == dispatcher.expire_pending() == 0
    assert dispatcher.active_market_keys() == set()
    result = dispatcher.compose_projection({"schema_version": 1, "orders": []})
    assert result["selected_backend"] == "okx_demo" and result["demo"]["state"] == "unavailable"
    assert result["paper_versions"]["paper_latency_v4"]["history_frozen"]


def test_paper_versions_remain_separate_frozen_history():
    paper = {
        "schema_version": 1,
        "orders": [],
        "execution_versions": {
            "paper_latency_v3": {
                "schema_version": 1,
                "scope": {"simulation_version": "paper_latency_v3"},
                "orders": [],
            }
        },
    }
    result = compose_trading_projection(paper, None, selected_backend="okx_demo", now_ms=1)
    clean = safe_trading_projection(result)
    assert set(clean["paper_versions"]) == {"paper_latency_v4", "paper_latency_v3"}
    assert all(p["history_frozen"] is True for p in clean["paper_versions"].values())


@pytest.mark.parametrize("target", ["paper", "okx_demo"])
def test_demo_monitor_never_constructs_writable_paper(tmp_path, monkeypatch, target):
    from pathlib import Path
    from pa_agent.config.paths import resolve_runtime_paths
    from pa_agent.config.settings import Settings
    from pa_agent.headless import build_headless_monitor

    def forbidden(*args, **kwargs):
        raise AssertionError("writable Paper constructed")

    monkeypatch.setattr("pa_agent.execution.coordinator.PaperExecutionCoordinator", forbidden)
    monkeypatch.delenv("PA_AGENT_DEMO_CONFIG", raising=False)
    monkeypatch.setattr(
        "pa_agent.execution.demo_dispatch.load_demo_config",
        lambda: DemoConfig(enabled=target == "okx_demo", execution_target=target),
    )
    monkeypatch.setattr("pa_agent.execution.demo_dispatch.require_cutover", lambda *args: None)
    settings = Settings()
    settings.provider.api_key = "test"
    settings.paper_trading.enabled = True
    paths = resolve_runtime_paths(
        project_root=Path(__file__).resolve().parents[2], environ={"PA_AGENT_HOME": str(tmp_path)}
    )
    monitor = build_headless_monitor(settings, paths)
    assert monitor is not None
    assert not paths.paper_trading_db_path.exists()


def test_pause_observe_expire_and_restart_do_not_write_paper(ledgers, tmp_path, monkeypatch):
    """Existing v3/v4 rows, schema and files survive real Host read projections."""
    import hashlib
    import sqlite3
    from contextlib import closing
    from dataclasses import replace

    from pa_agent.execution.demo_contracts import DemoPaths
    from pa_agent.execution.demo_spool import DemoSpool
    from tests.unit.test_headless_monitor import _native_monitor
    from pa_agent.operations.trading import build_trading_projection

    # Only control the fixture's clock; retain the real Host review/read path.
    monkeypatch.setattr(
        "pa_agent.operations.projection.build_trading_projection",
        lambda *args, **kwargs: build_trading_projection(*args, **kwargs, now_ms=NOW),
    )

    for version in ("paper_latency_v3", "paper_latency_v4"):
        for state in ("pending", "open"):
            _order(
                ledgers,
                version + state,
                status=state,
                pnl=None,
                simulation_version=version,
                expires_at_ms=NOW - 1,
            )
    path = ledgers[2]

    def ledger_snapshot():
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            logical = tuple(db.iterdump())
        files = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.parent.glob(path.name + "*")
            if p.is_file()
        }
        return logical, files

    before = ledger_snapshot()
    for cycle in range(2):
        dispatcher = OfficialExecutionDispatcher(
            None,
            DemoSpool(DemoPaths(tmp_path / "demo")),
            DemoConfig(enabled=True, execution_target="okx_demo", cutover_id="test"),
            now_ms=lambda: NOW,
        )
        monitor, _, _, _, _ = _native_monitor(tmp_path / str(cycle), paper_execution=dispatcher)
        monitor._paths = replace(
            monitor._paths,
            analysis_audit_db_path=ledgers[0],
            monitoring_audit_db_path=ledgers[1],
            paper_trading_db_path=path,
        )
        try:
            monitor.poll_once()
            monitor._expire_paper_pending()
            assert dispatcher.observe_official(None, "1h", (), observed_at_ms=NOW) == 0
            assert dispatcher.reconcile_official((), monitor._paths.records_pending_dir) == ()
            monitor._publish_runtime_status(paused=True)
            projection = monitor._status_store.read()["trading"]
            assert projection["selected_backend"] == "okx_demo"
            versions = projection["paper_versions"]
            for version in ("paper_latency_v3", "paper_latency_v4"):
                assert versions[version]["history_frozen"]
                assert {o["status"] for o in versions[version]["orders"]} == {"pending", "open"}
        finally:
            monitor.shutdown()
        assert ledger_snapshot() == before
