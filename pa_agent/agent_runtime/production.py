"""VPS wiring for the constrained Codex analysis backend."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pa_agent.agent_runtime.publisher import CodexJobPublisher
from pa_agent.agent_runtime.spool_worker import CodexSpoolClient, CodexSpoolConfig
from pa_agent.agent_runtime.runtime_identity import (
    codex_runtime_identity,
    resolve_executable,
)
from pa_agent.ai.codex_analysis_backend import CodexAnalysisRoleBackend


@dataclass(frozen=True)
class CodexProductionConfig:
    enabled: bool
    model: str
    reasoning_effort: str
    codex_binary: str
    codex_home: Path
    job_root: Path
    runtime_root: Path
    outbox_root: Path
    inbox_root: Path
    result_root: Path

    @classmethod
    def from_environment(
        cls,
        *,
        state_dir: Path,
        environment: Mapping[str, str] | None = None,
    ) -> "CodexProductionConfig":
        env = dict(os.environ if environment is None else environment)
        enabled = str(env.get("PA_AGENT_CODEX_ENABLED", "0")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        root = Path(env.get("PA_AGENT_CODEX_STATE_DIR") or (Path(state_dir) / "codex"))
        return cls(
            enabled=enabled,
            model=str(env.get("PA_AGENT_CODEX_MODEL", "")).strip(),
            reasoning_effort=str(env.get("PA_AGENT_CODEX_REASONING", "high")).strip(),
            codex_binary=str(env.get("PA_AGENT_CODEX_BINARY", "codex")).strip(),
            codex_home=root / "home",
            job_root=root / "jobs",
            runtime_root=root / "runtime",
            outbox_root=root / "outbox",
            inbox_root=root / "inbox",
            result_root=root / "results",
        )

    def public_runtime_identity(self) -> dict[str, Any]:
        return codex_runtime_identity(
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            codex_binary=self.codex_binary,
        )


def validate_codex_production_config(
    config: CodexProductionConfig,
    *,
    prompt_dir: Path,
) -> list[str]:
    if not config.enabled:
        return []
    errors: list[str] = []
    if not config.model:
        errors.append("PA_AGENT_CODEX_MODEL is required when Codex is enabled")
    if config.reasoning_effort not in {"minimal", "low", "medium", "high", "xhigh"}:
        errors.append("PA_AGENT_CODEX_REASONING is unsupported")
    if not config.codex_binary:
        errors.append("PA_AGENT_CODEX_BINARY is required")
    elif resolve_executable(config.codex_binary) is None:
        errors.append("Codex binary is unavailable or not executable")
    if not Path(prompt_dir).is_dir():
        errors.append("Codex prompt directory is unavailable")
    for directory in (
        config.job_root,
        config.inbox_root,
        config.result_root,
    ):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".pa-agent-codex-write-check"
            probe.write_text("ok", encoding="ascii")
            probe.unlink()
        except OSError as exc:
            errors.append(f"Codex runtime directory is not writable: {directory}: {exc}")
    return errors


def build_codex_backend_factory(
    *,
    state_dir: Path,
    prompt_dir: Path,
    environment: Mapping[str, str] | None = None,
) -> Callable[[Any, Any], CodexAnalysisRoleBackend] | None:
    config = CodexProductionConfig.from_environment(
        state_dir=state_dir,
        environment=environment,
    )
    if not config.enabled:
        return None
    errors = validate_codex_production_config(config, prompt_dir=prompt_dir)
    if errors:
        raise RuntimeError("; ".join(errors))

    runtime_identity = config.public_runtime_identity()

    def factory(task: Any, _settings: Any) -> CodexAnalysisRoleBackend:
        payload = getattr(task, "payload", None)
        frame = getattr(payload, "frame", None)
        if frame is None:
            raise TypeError("Codex backend task requires a frozen frame")
        if dict(getattr(payload, "analysis_backend_identity", {}) or {}) != runtime_identity:
            raise ValueError("Codex runtime identity does not match frozen task cohort")
        publisher = CodexJobPublisher(
            frame=frame,
            prompt_dir=prompt_dir,
            job_root=config.job_root,
            runtime_identity=runtime_identity,
        )
        worker = CodexSpoolClient(
            CodexSpoolConfig(
                job_root=config.job_root,
                inbox_root=config.inbox_root,
                result_root=config.result_root,
            )
        )
        mode = str(getattr(payload, "analysis_backend_mode", "codex_only") or "codex_only")
        return CodexAnalysisRoleBackend(
            worker=worker,
            publish_job=publisher,
            request_full_chain_fallback=mode == "auto",
            runtime_identity=runtime_identity,
        )

    return factory
