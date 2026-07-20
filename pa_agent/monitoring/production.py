"""Fresh production orchestrator factory for concurrent asset jobs."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pa_agent.ai.client_factory import create_ai_client
from pa_agent.ai.json_validator import JsonValidator
from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.ai.router import route_strategy_files
from pa_agent.monitoring.scheduler import AnalysisTask
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
    ) -> None:
        self._settings = settings
        self._pending_writer = pending_writer
        self._prompt_dir = Path(prompt_dir)
        self._experience_dir = Path(experience_dir)
        self._logger = logger or logging.getLogger(__name__)

    def __call__(self, _task: AnalysisTask) -> TwoStageOrchestrator:
        settings = self._settings.model_copy(deep=True)
        client = create_ai_client(settings.provider, logger_=self._logger)
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
            assembler=assembler,
            router=route_strategy_files,
            validator=JsonValidator(settings),
            pending_writer=self._pending_writer,
            exp_reader=experience_reader,
            settings=settings,
        )
