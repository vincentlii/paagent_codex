import json
import sqlite3
import gc
import hashlib
import subprocess
import sys
from contextlib import closing

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


@pytest.mark.parametrize("separate_process", [False, True])
def test_wal_backup_manifest_survives_connection_and_process_cleanup(tmp_path, separate_process):
    from pa_agent.execution.demo_contracts import DemoPaths
    from pa_agent.execution.demo_store import DemoStore
    from pa_agent.execution.demo_worker import backup_demo

    paths = DemoPaths(tmp_path / "execution")
    DemoStore(paths.state_db).register_intent(intent())
    with closing(sqlite3.connect(paths.state_db)) as source:
        assert source.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        original = "\n".join(source.iterdump())
    directory = tmp_path / "backups"
    stamp = 1800003600500
    if separate_process:
        subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                "from pathlib import Path;import sys;"
                "from pa_agent.execution.demo_contracts import DemoPaths;"
                "from pa_agent.execution.demo_worker import backup_demo;"
                "backup_demo(DemoPaths(Path(sys.argv[1])),Path(sys.argv[2]),now_ms=int(sys.argv[3]))",
                str(paths.root),
                str(directory),
                str(stamp),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        backup_demo(paths, directory, now_ms=stamp)
        gc.collect()
    destination = directory / f"demo-{stamp}"
    manifest = json.loads((destination / "manifest.json").read_text())
    assert not any(name.endswith(("-wal", "-shm")) for name in manifest["files"])
    for name, expected in manifest["files"].items():
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == expected
    with closing(sqlite3.connect(destination / "execution.sqlite3")) as restored:
        assert restored.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert "\n".join(restored.iterdump()) == original
    with closing(sqlite3.connect(paths.state_db)) as source:
        assert "\n".join(source.iterdump()) == original
