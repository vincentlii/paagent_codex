"""Bounded, idle-only retention of service-owned Codex rollout sessions."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from uuid import UUID


def prune_codex_sessions(
    codex_home: Path,
    runtime_root: Path,
    inbox_root: Path,
    *,
    codex_binary: str,
    now: float | None = None,
    runner=subprocess.run,
) -> dict[str, int | bool]:
    """Call only when no worker is active; never touch auth or PA audit records.

    A surviving PA workspace is a possible resume reference. Unknown rollouts,
    symlinks and other projects are deliberately left alone. CLI deletion owns
    Codex's indexes; deleting JSONL files ourselves would leave stale DB state.
    """
    result: dict[str, int | bool] = {"deleted": 0, "errors": 0, "deferred": False}
    home = Path(codex_home).resolve()
    workspaces = Path(runtime_root).resolve() / "pa-sessions"
    inbox = Path(inbox_root)

    def pending() -> bool:
        return any(inbox.glob("*.json")) or any(inbox.glob("*.running"))

    if pending():
        result["deferred"] = True
        return result
    cutoff = (time.time() if now is None else now) - 14 * 86400
    candidates = []
    for name in ("sessions", "archived_sessions"):
        root = home / name
        if root.is_symlink():
            continue
        for path in root.rglob("*.jsonl"):
            try:
                if (path.is_symlink() or not path.resolve().is_relative_to(root)
                        or path.stat().st_mtime >= cutoff):
                    continue
                with path.open(encoding="utf-8") as handle:
                    line = handle.readline(262144)
                meta = json.loads(line)
                if not isinstance(meta, dict) or meta.get("type") != "session_meta":
                    continue
                payload = meta["payload"]
                session_id = str(UUID(payload["id"]))
                raw_cwd = Path(payload["cwd"])
                if not raw_cwd.is_absolute():
                    continue
                cwd = raw_cwd.resolve()
                if not cwd.is_relative_to(workspaces) or cwd == workspaces or cwd.exists():
                    continue
                candidates.append((path.stat().st_mtime, path, session_id))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    # Eight commands, each bounded at three seconds; repeat hourly when idle.
    for _, path, session_id in sorted(candidates)[:8]:
        if pending():
            result["deferred"] = True
            break
        try:
            completed = runner(
                [codex_binary, "delete", session_id, "--force"],
                env={**os.environ, "CODEX_HOME": str(home)},
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=3, check=False,
            )
            key = "deleted" if completed.returncode == 0 and not path.exists() else "errors"
        except (OSError, subprocess.TimeoutExpired):
            key = "errors"
        result[key] += 1
    return result
