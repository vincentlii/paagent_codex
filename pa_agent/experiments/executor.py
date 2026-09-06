"""Fresh-client executor for one arm of the offline A/A/B/B protocol."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

from pa_agent.ai.analysis_role_backend import DirectAnalysisRoleBackend
from pa_agent.ai.client_factory import create_ai_client
from pa_agent.ai.json_validator import JsonValidator
from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.ai.router import route_strategy_files
from pa_agent.experiments.backend_aabb import (
    BackendArmExecution,
    BackendArmSpec,
    FrozenBackendExperimentInput,
    backend_configuration_fingerprint,
)
from pa_agent.experiments.pa_vpa_aabb import ArmExecution, ArmSpec, FrozenExperimentInput
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.records.experience_reader import ExperienceReader
from pa_agent.records.suppressed_writer import SuppressedRecordWriter


class OrchestratorArmExecutor:
    """Construct a fresh orchestrator and provider client for every experiment arm."""

    def __init__(
        self,
        *,
        settings: object,
        prompt_dir: Path,
        experience_dir: Path,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._prompt_dir = Path(prompt_dir)
        self._experience_dir = Path(experience_dir)
        self._logger = logger or logging.getLogger(__name__)

    def __call__(
        self,
        spec: ArmSpec,
        frozen: FrozenExperimentInput,
        cancel_token: object,
    ) -> ArmExecution:
        settings = self._settings.model_copy(deep=True)
        settings.prompt.vpa_mode = spec.mode
        settings.prompt.vpa_enabled = spec.group == "B"
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
        orchestrator = TwoStageOrchestrator(
            client=client,
            assembler=assembler,
            router=route_strategy_files,
            validator=JsonValidator(settings, prompt_dir=self._prompt_dir),
            pending_writer=SuppressedRecordWriter(),
            exp_reader=experience_reader,
            settings=settings,
            call_arm="pa",
            allow_provider_fallback=False,
            allow_research_joint=spec.group == "B",
        )
        record = orchestrator.submit(
            frozen.frame,
            cancel_token,
            lambda _event: None,
            previous_record=None,
            incremental_new_bar_count=None,
            htf_context=frozen.htf_context,
            frozen_vpa_catalog=(frozen.vpa_catalog if spec.group == "B" else None),
        )
        return ArmExecution(
            record=record,
            client_identity=client,
            client_instance_token=uuid4().hex,
        )


class BackendOrchestratorArmExecutor:
    """Build a cold Direct or Codex orchestrator for one Backend A/A/B/B arm."""

    def __init__(
        self,
        *,
        settings: object,
        prompt_dir: Path,
        experience_dir: Path,
        codex_backend_factory: object,
        logger: logging.Logger | None = None,
    ) -> None:
        if not callable(codex_backend_factory):
            raise TypeError("codex_backend_factory must be callable")
        self._settings = settings
        self._prompt_dir = Path(prompt_dir)
        self._experience_dir = Path(experience_dir)
        self._codex_backend_factory = codex_backend_factory
        self._logger = logger or logging.getLogger(__name__)

    def __call__(
        self,
        spec: BackendArmSpec,
        frozen: FrozenBackendExperimentInput,
        cancel_token: object,
    ) -> BackendArmExecution:
        settings = self._settings.model_copy(deep=True)
        settings.prompt.vpa_mode = "shadow"
        settings.prompt.vpa_enabled = True
        settings.general.higher_timeframe_enabled = True
        experience_reader = ExperienceReader(
            experience_dir=self._experience_dir,
            logger=self._logger,
        )
        assembler = PromptAssembler(
            prompt_dir=self._prompt_dir,
            experience_reader=experience_reader,
            prompt_settings=settings.prompt,
        )
        if spec.group == "A":
            client = create_ai_client(settings.provider, logger_=self._logger)
            analysis_backend = DirectAnalysisRoleBackend(
                client=client,
                settings=settings,
                logger=self._logger,
                allow_provider_fallback=False,
            )
            runtime_identity = client
            provider = settings.provider
            runtime_config = (
                provider.model_dump() if hasattr(provider, "model_dump") else vars(provider)
            )
        else:
            client = None
            built = self._codex_backend_factory(spec, frozen, settings)
            if not isinstance(built, tuple) or len(built) != 2 or not isinstance(
                built[1], Mapping
            ):
                raise TypeError(
                    "codex_backend_factory must return (backend, effective_runtime_config)"
                )
            analysis_backend, runtime_config = built
            runtime_identity = analysis_backend
        runtime_fingerprint = backend_configuration_fingerprint(runtime_config)
        expected_fingerprint = (
            frozen.direct_backend_fingerprint
            if spec.group == "A"
            else frozen.codex_backend_fingerprint
        )
        if runtime_fingerprint != expected_fingerprint:
            raise ValueError("effective backend runtime does not match the frozen cohort")
        orchestrator = TwoStageOrchestrator(
            client=client,
            analysis_backend=analysis_backend,
            assembler=assembler,
            router=route_strategy_files,
            validator=JsonValidator(settings, prompt_dir=self._prompt_dir),
            pending_writer=SuppressedRecordWriter(),
            exp_reader=experience_reader,
            settings=settings,
            call_arm="pa",
            allow_provider_fallback=False,
        )
        record = orchestrator.submit(
            frozen.frame,
            cancel_token,
            lambda _event: None,
            previous_record=None,
            incremental_new_bar_count=None,
            htf_context=frozen.htf_context,
            frozen_vpa_catalog=frozen.vpa_catalog,
        )
        return BackendArmExecution(
            record=record,
            runtime_identity=runtime_identity,
            client_instance_token=uuid4().hex,
            runtime_fingerprint=runtime_fingerprint,
        )
