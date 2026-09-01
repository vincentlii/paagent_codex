"""Fresh production orchestrator factory for concurrent asset jobs."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from pa_agent.ai.analysis_role_backend import DirectAnalysisRoleBackend
from pa_agent.ai.client_factory import create_ai_client
from pa_agent.ai.json_validator import JsonValidator
from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.ai.router import route_strategy_files
from pa_agent.monitoring.scheduler import AnalysisTask
from pa_agent.orchestrator.backend_fallback import FullChainFallbackOrchestrator
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.records.experience_reader import ExperienceReader


class ProductionOrchestratorFactory:
    """Build isolated mutable model-call state for every scheduled task."""

    def __init__(
        self,
        *,
        settings: Any,
        pending_writer: Any,
        prompt_dir: Path,
        experience_dir: Path,
        logger: logging.Logger | None = None,
        codex_backend_factory: Callable[[AnalysisTask, Any], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._pending_writer = pending_writer
        self._prompt_dir = Path(prompt_dir)
        self._experience_dir = Path(experience_dir)
        self._logger = logger or logging.getLogger(__name__)
        self._codex_backend_factory = codex_backend_factory

    def activate_backend_mode(self, mode: str) -> bool:
        """Return whether newly frozen tasks can use this bounded backend mode."""
        if mode == "deepseek_only":
            return True
        if mode not in {"auto", "codex_only"}:
            return False
        from pa_agent.config.vpa import resolve_vpa_mode

        configured_vpa = resolve_vpa_mode(getattr(self._settings, "prompt", None))
        return self._codex_backend_factory is not None and configured_vpa == "off"

    def __call__(self, task: AnalysisTask) -> Any:
        mode = str(
            getattr(task.payload, "analysis_backend_mode", "deepseek_only")
            or "deepseek_only"
        )
        if not self.activate_backend_mode(mode):
            raise RuntimeError(f"analysis backend is not configured: {mode}")
        if mode == "deepseek_only":
            return self._build_direct()

        settings = self._settings.model_copy(deep=True)
        assert self._codex_backend_factory is not None
        primary = self._build_orchestrator(
            settings=settings,
            client=None,
            analysis_backend=self._codex_backend_factory(task, settings),
        )
        if mode == "codex_only":
            return primary
        return FullChainFallbackOrchestrator(
            primary=primary,
            cold_fallback=self._build_direct(),
        )

    def _build_direct(self) -> TwoStageOrchestrator:
        settings = self._settings.model_copy(deep=True)
        client = create_ai_client(settings.provider, logger_=self._logger)
        return self._build_orchestrator(
            settings=settings,
            client=client,
            analysis_backend=DirectAnalysisRoleBackend(
                client=client,
                settings=settings,
                logger=self._logger,
            ),
        )

    def _build_orchestrator(
        self,
        *,
        settings: Any,
        client: Any,
        analysis_backend: Any,
    ) -> TwoStageOrchestrator:
        experience_reader = ExperienceReader(
            experience_dir=self._experience_dir,
            logger=self._logger,
        )
        assembler = PromptAssembler(
            prompt_dir=self._prompt_dir,
            experience_reader=experience_reader,
            prompt_settings=settings.prompt,
        )
        return TwoStageOrchestrator(
            client=client,
            analysis_backend=analysis_backend,
            assembler=assembler,
            router=route_strategy_files,
            validator=JsonValidator(settings, prompt_dir=self._prompt_dir),
            pending_writer=self._pending_writer,
            exp_reader=experience_reader,
            settings=settings,
        )
