"""Reproducible identity of the executable Codex analysis runtime."""

from __future__ import annotations

import hashlib
import json
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
    model_catalog: Path | None = None,
) -> dict[str, Any]:
    path = resolve_executable(codex_binary)
    if path is None:
        raise RuntimeError("Codex binary is unavailable or not executable")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    assets = load_codex_runtime_assets()
    identity = {
        "backend": "codex_agent",
        "provider": "openai_codex_chatgpt",
        "model": str(model),
        "reasoning_effort": str(reasoning_effort),
        "runtime_version": assets.runtime_version,
        "runtime_asset_digest": assets.asset_digest,
        "codex_binary": path.name,
        "codex_binary_digest": digest.hexdigest(),
    }
    if model == "deepseek-flash":
        if model_catalog is None:
            raise ValueError("DeepSeek Codex requires an explicit model catalog")
        data = model_catalog.read_bytes()
        models = json.loads(data).get("models", [])
        if len(models) != 1 or models[0].get("slug") != model:
            raise ValueError("DeepSeek Codex catalog must contain only deepseek-flash")
        if models[0].get("apply_patch_tool_type") is not None:
            raise ValueError("DeepSeek analysis catalog must not advertise apply_patch")
        identity.update(provider="deepseek_codex_responses",
                        model_catalog_digest=hashlib.sha256(data).hexdigest())
    return identity
