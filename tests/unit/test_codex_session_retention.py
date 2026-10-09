from __future__ import annotations

import json
import os
from types import SimpleNamespace
from uuid import uuid4

from pa_agent.agent_runtime.session_retention import prune_codex_sessions


def _session(tmp_path, *, age, now, cwd=None):
    session_id = str(uuid4())
    root = tmp_path / "home" / "sessions" / "2026" / "09"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"rollout-{session_id}.jsonl"
    path.write_text(json.dumps({"type": "session_meta", "payload": {
        "id": session_id, "cwd": str(cwd or tmp_path / "runtime" / "pa-sessions" / ("a" * 64)),
    }}) + "\n", encoding="utf-8")
    os.utime(path, (now - age, now - age))
    return session_id, path


def test_retention_only_deletes_old_service_sessions_through_uuid_cli(tmp_path):
    now = 1_800_000_000
    old, old_path = _session(tmp_path, age=15 * 86400, now=now)
    _session(tmp_path, age=86400, now=now)
    _session(tmp_path, age=15 * 86400, now=now, cwd=tmp_path / "other-project")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["env"]["CODEX_HOME"] == str(tmp_path / "home")
        old_path.unlink()
        return SimpleNamespace(returncode=0)

    result = prune_codex_sessions(tmp_path / "home", tmp_path / "runtime", inbox,
                                  codex_binary="codex", now=now, runner=run)
    assert result["deleted"] == 1
    assert calls == [["codex", "delete", old, "--force"]]
    assert len(list((tmp_path / "home" / "sessions").rglob("*.jsonl"))) == 2


def test_retention_defers_when_a_dispatch_or_pa_workspace_can_still_resume(tmp_path):
    now = 1_800_000_000
    _, path = _session(tmp_path, age=15 * 86400, now=now)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "pending.json").write_text("{}", encoding="utf-8")
    calls = []
    kwargs = dict(codex_binary="codex", now=now, runner=lambda *a, **k: calls.append(a))
    assert prune_codex_sessions(tmp_path / "home", tmp_path / "runtime", inbox, **kwargs)["deferred"]
    (inbox / "pending.json").unlink()
    (tmp_path / "runtime" / "pa-sessions" / ("a" * 64)).mkdir(parents=True)
    assert prune_codex_sessions(tmp_path / "home", tmp_path / "runtime", inbox, **kwargs)["deleted"] == 0
    assert path.exists() and calls == []


def test_retention_keeps_failed_delete_and_never_reads_auth(tmp_path):
    now = 1_800_000_000
    _, path = _session(tmp_path, age=15 * 86400, now=now)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    auth = tmp_path / "home" / "auth.json"
    auth.write_text("opaque-credential", encoding="utf-8")
    result = prune_codex_sessions(tmp_path / "home", tmp_path / "runtime", inbox,
                                  codex_binary="codex", now=now,
                                  runner=lambda *a, **k: SimpleNamespace(returncode=1))
    assert result["errors"] == 1 and result["deleted"] == 0
    assert path.exists() and auth.read_text(encoding="utf-8") == "opaque-credential"


def test_unknown_rollout_format_is_kept_without_interrupting_worker(tmp_path):
    now = 1_800_000_000
    _, path = _session(tmp_path, age=15 * 86400, now=now)
    path.write_text("[]\n", encoding="utf-8")
    os.utime(path, (now - 15 * 86400, now - 15 * 86400))
    result = prune_codex_sessions(tmp_path / "home", tmp_path / "runtime", tmp_path / "inbox",
                                  codex_binary="codex", now=now,
                                  runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not delete")))
    assert result["deleted"] == 0 and path.exists()
