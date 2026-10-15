import json

import pytest


def test_default_check_is_offline_and_creates_no_database(tmp_path, monkeypatch, capsys):
    from pa_agent.execution.demo_worker import main

    monkeypatch.delenv("PA_AGENT_DEMO_CONFIG", raising=False)
    monkeypatch.setenv("PA_AGENT_EXECUTION_HOME", str(tmp_path))
    assert main(["--check"]) == 0
    assert "disabled" in capsys.readouterr().out
    assert not tuple(tmp_path.rglob("*.sqlite3"))


def test_running_default_never_loads_credentials_or_opens_network(tmp_path, monkeypatch):
    from pa_agent.execution.demo_worker import main

    monkeypatch.delenv("PA_AGENT_DEMO_CONFIG", raising=False)
    monkeypatch.setenv("PA_AGENT_EXECUTION_HOME", str(tmp_path))
    assert main([]) == 0
    assert not tuple(tmp_path.rglob("*.sqlite3"))


def test_bad_config_does_not_print_content_or_credentials(tmp_path, monkeypatch, capsys):
    from pa_agent.execution.demo_worker import main

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"secret": "secret-value"}))
    monkeypatch.setenv("PA_AGENT_DEMO_CONFIG", str(config))
    assert main(["--check"]) == 2
    assert "secret-value" not in str(capsys.readouterr())


def test_disabled_config_with_existing_active_ledger_does_not_silently_stop_management(
    tmp_path, monkeypatch
):
    from pa_agent.execution.demo_worker import main
    from pa_agent.execution.demo_spool import atomic_json
    from tests.unit.test_demo_worker import setup

    worker, exchange, store, spool = setup(tmp_path / "execution")
    worker.tick()
    worker.close()
    config_path = tmp_path / "config.json"
    cfg = worker.config.model_copy(update={"enabled": False, "entry_enabled": False})
    atomic_json(config_path, cfg.model_dump(mode="json"))
    monkeypatch.setenv("PA_AGENT_DEMO_CONFIG", str(config_path))
    monkeypatch.setenv("PA_AGENT_EXECUTION_HOME", str(worker.paths.root))
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    assert main([]) == 2  # Management needs credentials; may not return 'disabled' success.


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "schema",
        "scheduler",
        "bool_count",
        "float_count",
        "missing_count",
        "stale",
        "busy",
        "recovery",
        "missing",
    ],
)
def test_cutover_reads_host_status_contract_without_spool_size_limit(
    tmp_path, monkeypatch, invalid
):
    from pa_agent.execution.demo_contracts import DemoPaths
    from pa_agent.execution.demo_dispatch import require_cutover
    from pa_agent.execution.demo_spool import MAX_BYTES, atomic_json
    from pa_agent.execution.demo_worker import main
    from pa_agent.operations.runtime_state import RuntimeStatusStore
    from tests.unit.test_demo_execution_flow import config

    cfg, paths = config(), DemoPaths(tmp_path / "execution")
    config_path, host_path = tmp_path / "config.json", tmp_path / "host.json"
    atomic_json(config_path, cfg.model_dump(mode="json"))
    monkeypatch.setenv("PA_AGENT_DEMO_CONFIG", str(config_path))
    monkeypatch.setenv("PA_AGENT_EXECUTION_HOME", str(paths.root))
    monkeypatch.setenv("PA_AGENT_HOST_STATUS", str(host_path))
    monkeypatch.setattr("time.time", lambda: 1800003600.5)
    now = 1800003600500
    RuntimeStatusStore(host_path, now_ms=lambda: now).write(
        {
            "service_state": "paused",
            "scheduler": {"running": 0, "queued": 0, "unfinished": 0},
            "recovery_blocked_assets": [],
            "recent_runs": [{"outcome": "x" * MAX_BYTES}],
        }
    )
    assert host_path.stat().st_size > MAX_BYTES
    atomic_json(
        paths.projection,
        {
            "state": "ready",
            "exchange_observed_at_ms": now,
            "coverage": {"orders": True},
            "total_active_trades": 0,
            "outside_orders": 0,
            "positions": [],
        },
    )
    raw = json.loads(host_path.read_text())
    if invalid == "schema":
        raw["schema_version"] = 99
    elif invalid == "scheduler":
        raw["scheduler"] = []
    elif invalid == "bool_count":
        raw["scheduler"]["running"] = False
    elif invalid == "float_count":
        raw["scheduler"]["queued"] = 0.0
    elif invalid == "missing_count":
        del raw["scheduler"]["unfinished"]
    elif invalid == "stale":
        raw["updated_at_ms"] = now - 30001
    elif invalid == "busy":
        raw["scheduler"]["unfinished"] = 1
    elif invalid == "recovery":
        raw["recovery_blocked_assets"] = ["BTCUSDT"]
    if invalid == "missing":
        host_path.unlink()
    else:
        host_path.write_text(json.dumps(raw))
    assert main(["--prepare-cutover"]) == (0 if invalid is None else 2)
    if invalid is None:
        require_cutover(paths, cfg)
    else:
        assert not (paths.intents / "cutover.json").exists()
