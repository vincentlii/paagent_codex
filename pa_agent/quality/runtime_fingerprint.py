"""Content fingerprint for the code and prompt assets used by analysis."""

from __future__ import annotations

import hashlib
import marshal
from pathlib import Path

VALIDATION_POLICY_VERSION = "structured-hard-scoped-prose-warning-v2"


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


def loaded_validation_fingerprint() -> str:
    """Hash the validation code objects actually loaded by this process."""
    from pa_agent.ai.coherence_checks import (
        collect_stage1_quality_warnings,
        validate_stage1_coherence,
        validate_stage2_coherence,
    )
    from pa_agent.ai.json_validator import JsonValidator
    from pa_agent.ai.role_validator import validate_vpa_evidence
    from pa_agent.orchestrator.validation_retry import validate_with_retry

    callables = (
        JsonValidator.validate,
        validate_stage1_coherence,
        collect_stage1_quality_warnings,
        validate_stage2_coherence,
        validate_vpa_evidence,
        validate_with_retry,
    )
    digest = hashlib.sha256()
    digest.update(VALIDATION_POLICY_VERSION.encode("ascii"))
    for function in callables:
        digest.update(function.__module__.encode("utf-8"))
        digest.update(b"\0")
        digest.update(function.__qualname__.encode("utf-8"))
        digest.update(b"\0")
        digest.update(marshal.dumps(function.__code__))
        digest.update(b"\0")
    return digest.hexdigest()
