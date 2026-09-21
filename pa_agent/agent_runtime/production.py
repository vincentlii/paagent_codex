"""VPS wiring for the constrained Codex analysis backend."""

from __future__ import annotations

import hashlib
import json
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
from pa_agent.ai.codex_analysis_backend import CodexAnalysisRoleBackend, CodexCircuitBreaker
from pa_agent.operations.analysis_profile import codex_model_effort_supported


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
    model_catalog: Path | None = None

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
            model_catalog=Path(env["PA_AGENT_CODEX_MODEL_CATALOG"]) if env.get("PA_AGENT_CODEX_MODEL_CATALOG") else None,
        )

    def public_runtime_identity(self) -> dict[str, Any]:
        return codex_runtime_identity(
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            codex_binary=self.codex_binary,
            model_catalog=self.model_catalog,
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
    elif not codex_model_effort_supported(config.model, config.reasoning_effort):
        errors.append("PA_AGENT_CODEX_MODEL/PA_AGENT_CODEX_REASONING pair is unsupported")
    if config.model == "deepseek-flash":
        if config.model_catalog is None:
            errors.append("PA_AGENT_CODEX_MODEL_CATALOG is required for deepseek-flash")
        elif not config.model_catalog.is_file():
            errors.append("DeepSeek Codex model catalog is unavailable")
        else:
            try:
                catalog = json.loads(config.model_catalog.read_text(encoding="utf-8"))
                models = catalog.get("models", [])
                if (
                    len(models) != 1
                    or models[0].get("slug") != "deepseek-flash"
                    or models[0].get("apply_patch_tool_type") is not None
                ):
                    raise ValueError
            except (OSError, UnicodeError, json.JSONDecodeError, AttributeError, ValueError):
                errors.append("DeepSeek Codex model catalog is invalid")
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
    circuit_breaker = CodexCircuitBreaker(
        state_path=Path(state_dir) / "codex-circuit.json"
    )

    def factory(task: Any, _settings: Any) -> CodexAnalysisRoleBackend:
        payload = getattr(task, "payload", None)
        frame = getattr(payload, "frame", None)
        if frame is None:
            raise TypeError("Codex backend task requires a frozen frame")
        task_identity = dict(
            getattr(payload, "analysis_backend_identity", {}) or {}
        )
        expected_runtime = dict(runtime_identity)
        for key in (
            "model",
            "reasoning_effort",
            "profile_fingerprint",
            "analysis_profile_fingerprint",
        ):
            if key in task_identity:
                expected_runtime[key] = task_identity[key]
        selection_keys = {
            "model",
            "reasoning_effort",
            "profile_fingerprint",
            "analysis_profile_fingerprint",
        }
        frozen_immutable = {
            key: value
            for key, value in task_identity.items()
            if key not in selection_keys
        }
        worker_immutable = {
            key: value
            for key, value in runtime_identity.items()
            if key not in selection_keys
        }
        if frozen_immutable != worker_immutable:
            raise ValueError("Codex runtime identity does not match frozen task cohort")
        publisher = CodexJobPublisher(
            frame=frame,
            prompt_dir=prompt_dir,
            job_root=config.job_root,
            runtime_identity=expected_runtime,
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
            circuit_breaker=circuit_breaker,
            runtime_identity=expected_runtime,
        )

    return factory


def build_codex_experiment_backend_factory(
    *,
    state_dir: Path,
    prompt_dir: Path,
    environment: Mapping[str, str] | None = None,
) -> tuple[Callable[..., Any], dict[str, Any]]:
    """Adapt the production backend to an isolated, no-fallback experiment arm."""
    config = CodexProductionConfig.from_environment(
        state_dir=state_dir,
        environment=environment,
    )
    if not config.enabled:
        raise RuntimeError("Codex backend is disabled")
    errors = validate_codex_production_config(config, prompt_dir=prompt_dir)
    if errors:
        raise RuntimeError("; ".join(errors))
    identity = config.public_runtime_identity()
    identity["profile_fingerprint"] = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    def experiment_factory(spec: Any, frozen: Any, settings: Any) -> tuple[Any, dict[str, Any]]:
        del settings
        arm_identity = dict(identity)
        investigation = getattr(spec, "investigation", None)
        if investigation is not None:
            if investigation not in {"fixed", "agentic"}:
                raise ValueError("invalid study investigation condition")
            arm_identity["investigation_policy"] = investigation
            arm_identity["profile_fingerprint"] = hashlib.sha256(
                json.dumps(
                    arm_identity,
                    ensure_ascii=True,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
        publisher = CodexJobPublisher(
            frame=frozen.frame,
            prompt_dir=prompt_dir,
            job_root=config.job_root,
            runtime_identity=arm_identity,
        )
        worker = CodexSpoolClient(
            CodexSpoolConfig(
                job_root=config.job_root,
                inbox_root=config.inbox_root,
                result_root=config.result_root,
            )
        )
        backend = CodexAnalysisRoleBackend(
            worker=worker,
            publish_job=publisher,
            request_full_chain_fallback=False,
            circuit_breaker=CodexCircuitBreaker(),
            runtime_identity=arm_identity,
        )
        return backend, arm_identity

    return experiment_factory, dict(identity)
