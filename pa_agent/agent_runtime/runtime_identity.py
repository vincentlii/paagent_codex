"""Reproducible identity of the executable Codex analysis runtime."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

from pa_agent.agent_runtime.runtime_assets import load_codex_runtime_assets


def resolve_executable(binary: str) -> Path | None:
    candidate = shutil.which(binary)
    path = Path(candidate or binary)
    if not path.is_file():
        return None
    if os.name == "posix" and not os.access(path, os.X_OK):
        return None
    return path.resolve()


def codex_runtime_identity(
    *,
    model: str,
    reasoning_effort: str,
    codex_binary: str,
) -> dict[str, Any]:
    path = resolve_executable(codex_binary)
    if path is None:
        raise RuntimeError("Codex binary is unavailable or not executable")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    assets = load_codex_runtime_assets()
    return {
        "backend": "codex_agent",
        "provider": "openai_codex_chatgpt",
        "model": str(model),
        "reasoning_effort": str(reasoning_effort),
        "runtime_version": assets.runtime_version,
        "runtime_asset_digest": assets.asset_digest,
        "codex_binary": path.name,
        "codex_binary_digest": digest.hexdigest(),
    }
