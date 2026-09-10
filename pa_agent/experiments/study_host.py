"""Trusted Host wiring for preregistered analysis studies.

This module reuses the production TwoStageOrchestrator.  It does not own market
data, validation, routing, risk, persistence, or any trading decision.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from pa_agent.ai.analysis_role_backend import DirectAnalysisRoleBackend
from pa_agent.ai.client_factory import create_ai_client
from pa_agent.ai.json_validator import JsonValidator
from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.ai.router import route_strategy_files
from pa_agent.config.analysis_pipeline import AnalysisPipelineIdentity
from pa_agent.experiments.study_protocol import Condition
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.records.experience_reader import ExperienceReader
from pa_agent.records.pending_writer import PendingWriter
from pa_agent.util.threading import OrchestratorEvent
from pa_agent.experiments.study_resources import (
    domain_resources, input_from_evidence, study_domain_fingerprint,
)
from pa_agent.experiments.study_protocol import digest


class StudyHostExecutor:
    """Execute one registered arm through a fresh production analysis stack."""

    def __init__(
        self,
        *,
        settings: object,
        prompt_dir: Path,
        experience_dir: Path,
        state_dir: Path,
        domain_fingerprint: str,
        cancel_token: object,
        codex_backend_factory: object | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._prompt_dir = Path(prompt_dir)
        self._experience_dir = Path(experience_dir)
        self._state_dir = Path(state_dir)
        self._domain_fingerprint = str(domain_fingerprint)
        self._cancel_token = cancel_token
        self._codex_backend_factory = codex_backend_factory
        self._logger = logger or logging.getLogger(__name__)

    def __call__(self, request: dict) -> dict:
        if len(str(request.get("case_id", ""))) != 24 or any(
            c not in "0123456789abcdef" for c in str(request["case_id"])
        ):
            raise ValueError("invalid registered study case identity")
        condition = Condition.model_validate(request["condition"])
        actual_domain = study_domain_fingerprint(self._prompt_dir, self._settings)
        if request.get("domain_fingerprint") != actual_domain or actual_domain != self._domain_fingerprint:
            raise ValueError("study request domain fingerprint drift")
        evidence = request["evidence"]
        actual_evidence = digest(evidence)
        if actual_evidence != request["evidence_digest"]:
            raise ValueError("study evidence digest mismatch")
        if domain_resources(self._prompt_dir) != evidence["knowledge"]:
            raise ValueError("current prompt resources differ from registered knowledge")
        frozen = input_from_evidence(evidence)

        settings = self._settings.model_copy(deep=True)
        settings.analysis_pipeline = AnalysisPipelineIdentity(pipeline=condition.pipeline)
        settings.general.higher_timeframe_enabled = True
        settings.prompt.vpa_mode = {
            "pa_only": "off",
            "pa_shadow": "shadow",
            "pa_vpa_joint": "off",
        }[condition.pipeline]
        settings.prompt.vpa_enabled = condition.pipeline != "pa_only"
        settings.provider.thinking = True
        settings.provider.reasoning_effort = condition.reasoning
        # A registered global effort must not inherit hidden GUI role overrides.
        settings.provider.reasoning_profiles = type(settings.provider.reasoning_profiles)()

        experience = ExperienceReader(
            experience_dir=self._experience_dir,
            logger=self._logger,
        )
        assembler = PromptAssembler(
            prompt_dir=self._prompt_dir,
            experience_reader=experience,
            prompt_settings=settings.prompt,
        )
        if condition.backend == "deepseek_direct":
            settings.provider.model = condition.model
            client = create_ai_client(settings.provider, logger_=self._logger)
            backend = DirectAnalysisRoleBackend(
                client=client,
                settings=settings,
                logger=self._logger,
                allow_provider_fallback=False,
            )
            runtime_identity = client
        else:
            if condition.model != "gpt-5.6-luna" or condition.reasoning != "max":
                raise ValueError("phase 7 Codex execution requires gpt-5.6-luna/max")
            if not callable(self._codex_backend_factory):
                raise RuntimeError("Codex study backend factory is unavailable")
            spec = SimpleNamespace(**condition.model_dump())
            built = self._codex_backend_factory(spec, frozen, settings)
            if not isinstance(built, tuple) or len(built) != 2:
                raise TypeError("Codex study factory must return backend and runtime identity")
            backend, observed_runtime = built
            if not isinstance(observed_runtime, Mapping) or (
                observed_runtime.get("model") != condition.model
                or observed_runtime.get("reasoning_effort") != condition.reasoning
                or observed_runtime.get("investigation_policy") != condition.investigation
            ):
                raise ValueError("Codex study runtime did not deliver the registered condition")
            client = None
            runtime_identity = backend

        orchestrator = TwoStageOrchestrator(
            client=client,
            analysis_backend=backend,
            assembler=assembler,
            router=route_strategy_files,
            validator=JsonValidator(settings, prompt_dir=self._prompt_dir),
            pending_writer=PendingWriter(
                pending_dir=self._state_dir / "records" / str(request["case_id"]),
                api_key=settings.provider.api_key,
            ),
            exp_reader=experience,
            settings=settings,
            call_arm="pa",
            allow_provider_fallback=False,
        )
        frame = getattr(frozen, "frame", None)
        if frame is None:
            raise TypeError("trusted frozen sample has no frame")
        if settings.prompt.experience_max_entries > 0 and frame.data_metadata.get("experience_pool") is None:
            raise ValueError("study experience requires a pre-frozen pool")
        events = []
        record = orchestrator.submit(
            frame,
            self._cancel_token,
            events.append,
            previous_record=None,
            incremental_new_bar_count=None,
            htf_context=getattr(frozen, "htf_context", None),
            frozen_vpa_catalog=(frozen.vpa_catalog if condition.pipeline != "pa_only" else None),
        )
        if any(event in (OrchestratorEvent.RecordSaveFailed, OrchestratorEvent.RecordAuditFailed) for event in events):
            raise ValueError("study result persistence failed")
        if study_domain_fingerprint(self._prompt_dir, self._settings) != actual_domain:
            raise ValueError("domain resources changed during study arm")
        if record.analysis_timing is not None and record.analysis_timing.result_available_at_ms is None:
            raise ValueError("study result has no durable delivery receipt")
        for call in record.call_attempts:
            if call.get("response_hash") and (
                call.get("effective_model") != condition.model
                or call.get("effective_reasoning_effort") != condition.reasoning
            ):
                raise ValueError("provider response model or effort differs from registered treatment")
        return {
            "condition": condition.model_dump(),
            "client_token": uuid4().hex,
            "runtime_identity": runtime_identity,
            "output": record.model_dump(mode="json"),
            "evidence_digest": actual_evidence,
            "domain_fingerprint": actual_domain,
        }


__all__ = ["StudyHostExecutor"]
