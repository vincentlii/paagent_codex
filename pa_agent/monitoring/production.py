"""Fresh production orchestrator factory for concurrent asset jobs."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pa_agent.ai.analysis_role_backend import DirectAnalysisRoleBackend
from pa_agent.ai.client_factory import create_ai_client
from pa_agent.ai.json_validator import JsonValidator
from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.ai.router import route_strategy_files
from pa_agent.config.settings import provider_api_key_configured
from pa_agent.monitoring.scheduler import AnalysisTask
from pa_agent.operations.analysis_profile import (
    AnalysisProfile,
    profile_fingerprint,
    validate_analysis_profile,
)
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
        self._analysis_profile = AnalysisProfile()
        self._analysis_profile_fingerprint = profile_fingerprint(self._analysis_profile)
        self._profile_identity = self._profile_identity_for(self._analysis_profile)
        self._profile_settings = self._settings_for_profile(self._analysis_profile)

    @property
    def analysis_profile(self) -> AnalysisProfile:
        """Return the immutable profile used by future orchestrators."""
        return self._analysis_profile

    @property
    def analysis_profile_fingerprint(self) -> str:
        return self._analysis_profile_fingerprint

    def apply_analysis_profile(self, profile: AnalysisProfile) -> bool:
        """Atomically stage a validated profile for future analysis clients.

        The production settings object is deliberately never edited here.  A
        private deep copy is rebuilt so existing orchestrators retain their
        original provider and prompt state while subsequent tasks see the new
        Direct model controls.
        """
        try:
            validated = validate_analysis_profile(profile)
            candidate = self._settings_for_profile(validated)
        except (TypeError, ValueError) as exc:
            self._logger.warning("Analysis profile rejected: %s", exc)
            return False
        self._analysis_profile = validated
        self._analysis_profile_fingerprint = profile_fingerprint(validated)
        self._profile_identity = self._profile_identity_for(validated)
        self._profile_settings = candidate
        return True

    @staticmethod
    def _profile_identity_for(profile: AnalysisProfile) -> dict[str, Any]:
        return {
            **profile.model_dump(mode="json"),
            "profile_fingerprint": profile_fingerprint(profile),
        }

    def _settings_for_profile(self, profile: AnalysisProfile) -> Any:
        settings = self._settings.model_copy(deep=True)
        provider = settings.provider
        provider.model = profile.deepseek_model
        provider.thinking = profile.deepseek_thinking
        provider.reasoning_effort = profile.deepseek_reasoning_effort
        settings.prompt.vpa_mode = profile.vpa_mode
        settings.prompt.vpa_enabled = True
        settings.general.higher_timeframe_enabled = profile.higher_timeframe_enabled
        settings.general.higher_timeframe_override = profile.higher_timeframe_override
        settings.general.higher_timeframe_bar_count = profile.higher_timeframe_bar_count
        settings.monitoring.max_concurrency = profile.max_concurrency
        settings.general.decision_stance = profile.decision_stance
        return settings

    def activate_backend_mode(self, mode: str) -> bool:
        """Return whether newly frozen tasks can use this bounded backend mode."""
        if mode == "deepseek_only":
            return provider_api_key_configured(self._settings)
        if mode not in {"auto", "codex_only"}:
            return False
        return self._codex_backend_factory is not None and (
            mode == "codex_only" or provider_api_key_configured(self._settings)
        )

    def __call__(self, task: AnalysisTask) -> Any:
        mode = str(
            getattr(task.payload, "analysis_backend_mode", "deepseek_only")
            or "deepseek_only"
        )
        if not self.activate_backend_mode(mode):
            raise RuntimeError(f"analysis backend is not configured: {mode}")
        frozen_profile = self._frozen_profile_for_task(task)
        settings = self._settings_for_profile(frozen_profile)
        frozen_identity = dict(getattr(task.payload, "analysis_profile_identity", {}) or {})
        profile_identity = frozen_identity or self._profile_identity_for(frozen_profile)
        if frozen_identity and "decision_stance" not in frozen_identity:
            # Old accepted tasks did not expose stance in Profile. Keep their
            # configured baseline, rather than silently adding a new selection.
            settings.general.decision_stance = self._settings.general.decision_stance
        if mode == "deepseek_only":
            return self._build_direct(settings=settings, profile_identity=profile_identity,
                                      execution_policy=task.payload.execution_policy)
        assert self._codex_backend_factory is not None
        primary = self._build_orchestrator(
            settings=settings,
            client=None,
            analysis_backend=self._codex_backend_factory(task, settings),
            profile_identity=profile_identity,
            execution_policy=task.payload.execution_policy,
        )
        if mode == "codex_only":
            return primary
        return FullChainFallbackOrchestrator(
            primary=primary,
            cold_fallback=self._build_direct(
                settings=settings.model_copy(deep=True),
                profile_identity=profile_identity,
                execution_policy=task.payload.execution_policy,
            ),
            pipeline_identity=settings.analysis_pipeline,
        )

    def _frozen_profile_for_task(self, task: AnalysisTask) -> AnalysisProfile:
        identity = getattr(task.payload, "analysis_profile_identity", {}) or {}
        if not identity:
            return self._analysis_profile
        values = dict(identity)
        expected = str(values.pop("profile_fingerprint", "") or "")
        profile = validate_analysis_profile(values)
        if expected != profile_fingerprint(profile):
            raise ValueError("frozen analysis profile fingerprint mismatch")
        return profile

    def _build_direct(
        self, *, client: Any | None = None, settings: Any | None = None,
        profile_identity: Mapping[str, Any] | None = None,
        execution_policy: str | None = None,
    ) -> TwoStageOrchestrator:
        settings = settings or self._profile_settings.model_copy(deep=True)
        profile_identity = profile_identity or self._profile_identity
        if client is None:
            client = create_ai_client(settings.provider, logger_=self._logger)
        return self._build_orchestrator(
            settings=settings,
            client=client,
            analysis_backend=DirectAnalysisRoleBackend(
                client=client,
                settings=settings,
                profile_identity=profile_identity,
                logger=self._logger,
            ),
            profile_identity=profile_identity,
            execution_policy=execution_policy,
        )

    def _build_orchestrator(
        self,
        *,
        settings: Any,
        client: Any | None,
        analysis_backend: Any,
        profile_identity: Mapping[str, Any] | None = None,
        execution_policy: str | None = None,
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
            profile_identity=profile_identity,
            execution_policy=execution_policy,
        )
