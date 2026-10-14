import json
import sqlite3

import pytest

from tests.unit.test_demo_plans import intent


def test_backup_is_consistent_and_excludes_credential_neighbors(tmp_path):
    from pa_agent.execution.demo_contracts import DemoPaths
    from pa_agent.execution.demo_store import DemoStore
    from pa_agent.execution.demo_worker import backup_demo

    paths = DemoPaths(tmp_path / "execution")
    store = DemoStore(paths.state_db)
    store.register_intent(intent())
    (paths.root / "okx-demo-secret").write_text("secret-value")
    destination = backup_demo(paths, tmp_path / "backups", now_ms=1800003600500)
    manifest = json.loads((destination / "manifest.json").read_text())
    assert "execution.sqlite3" in manifest["files"]
    assert not tuple(destination.rglob("*secret*"))
    with sqlite3.connect(destination / "execution.sqlite3") as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert db.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 1


def test_backup_refuses_running_writer_to_keep_receipts_consistent(tmp_path):
    from pa_agent.execution.demo_worker import backup_demo
    from tests.unit.test_demo_worker import setup

    worker, exchange, store, spool = setup(tmp_path / "execution")
    with pytest.raises(RuntimeError, match="worker_already_running"):
        backup_demo(worker.paths, tmp_path / "backups", now_ms=1800003600500)
    assert not (tmp_path / "backups").exists()
    worker.close()
