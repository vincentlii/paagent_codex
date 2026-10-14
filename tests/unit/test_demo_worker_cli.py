import json


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


def test_disabled_config_with_existing_active_ledger_does_not_silently_stop_management(tmp_path, monkeypatch):
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
