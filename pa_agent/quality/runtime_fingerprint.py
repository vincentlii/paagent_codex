"""Content fingerprint for the code and prompt assets used by analysis."""

from __future__ import annotations

import hashlib
from pathlib import Path


def analysis_runtime_fingerprint(
    project_root: Path | None = None,
    *,
    prompt_dir: Path | None = None,
) -> str:
    """Hash Python analysis code and prompt assets without Git metadata."""
    root = (
        Path(project_root).resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[2]
    )
    prompts = (
        Path(prompt_dir).resolve()
        if prompt_dir is not None
        else root / "prompt_engineering"
    )
    candidates: list[tuple[str, Path]] = []
    source_root = root / "pa_agent"
    if source_root.is_dir():
        candidates.extend(
            (f"pa_agent/{path.relative_to(source_root).as_posix()}", path)
            for path in source_root.rglob("*.py")
            if path.is_file()
        )
    if prompts.is_dir():
        candidates.extend(
            (f"prompt_engineering/{path.relative_to(prompts).as_posix()}", path)
            for path in prompts.rglob("*")
            if path.is_file() and path.suffix.lower() in {".json", ".md", ".txt"}
        )
    if not candidates:
        raise ValueError("analysis runtime files are unavailable for fingerprinting")
    digest = hashlib.sha256()
    for relative, path in sorted(candidates, key=lambda item: item[0]):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
