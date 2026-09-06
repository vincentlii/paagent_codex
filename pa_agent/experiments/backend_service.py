"""Application service for one full Direct-versus-Codex Backend A/A/B/B run."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from pa_agent.experiments.backend_aabb import (
    BackendAabbRunResult,
    BackendAabbRunner,
    backend_contract_fingerprint,
    freeze_backend_experiment_input,
)
from pa_agent.experiments.executor import BackendOrchestratorArmExecutor
from pa_agent.experiments.pa_vpa_aabb import analysis_runtime_fingerprint
from pa_agent.experiments.replay import frozen_input_from_record
from pa_agent.experiments.store import AnalysisExperimentStore
from pa_agent.records.audit_store import AnalysisAuditStore
from pa_agent.records.codec import load_record
from pa_agent.records.pending_writer import PendingWriter


@dataclass(frozen=True)
class PreparedBackendAabbSource:
    source_path: Path
    source_run_id: str
    frozen: Any


class BackendAabbExperimentService:
    """Freeze one mature record and compare complete PA+VPA backend systems."""

    def __init__(
        self,
        *,
        settings: Any,
        prompt_dir: Path,
        experience_dir: Path,
        pending_dir: Path,
        db_path: Path,
        codex_backend_factory: Callable[..., Any],
        codex_runtime_config: Mapping[str, Any],
        logger: logging.Logger | None = None,
    ) -> None:
        audit_store = AnalysisAuditStore(
            db_path,
            simulation_horizon_bars=settings.general.counterfactual_horizon_bars,
        )
        writer = PendingWriter(
            pending_dir=pending_dir,
            api_key=settings.provider.api_key,
            audit_store=audit_store,
            counterfactual_horizon_bars=settings.general.counterfactual_horizon_bars,
        )
        self._settings = settings
        self._direct_runtime = self._provider_config(settings.provider)
        self._codex_runtime = dict(codex_runtime_config)
        self._runner = BackendAabbRunner(
            execute_arm=BackendOrchestratorArmExecutor(
                settings=settings,
                prompt_dir=prompt_dir,
                experience_dir=experience_dir,
                codex_backend_factory=codex_backend_factory,
                logger=logger or logging.getLogger(__name__),
            ),
            pending_writer=writer,
            experiment_store=AnalysisExperimentStore(db_path),
        )

    def prepare_source(self, source_path: Path) -> PreparedBackendAabbSource:
        path = Path(source_path).resolve()
        source = load_record(path).record
        if source.experiment_context:
            raise ValueError("an experiment arm cannot be reused as a source snapshot")
        frame, htf_context, catalog = frozen_input_from_record(source)
        shared_settings = {
            "general": self._model_dump(self._settings.general),
            "prompt": self._model_dump(self._settings.prompt),
            "validation": self._model_dump(self._settings.validation),
            "vpa_mode": "shadow",
            "higher_timeframe_enabled": True,
        }
        frozen = freeze_backend_experiment_input(
            frame,
            htf_context,
            direct_backend=self._direct_runtime,
            codex_backend=self._codex_runtime,
            shared_settings=shared_settings,
            contract_fingerprint=backend_contract_fingerprint(),
            analysis_fingerprint=analysis_runtime_fingerprint(),
            vpa_catalog=catalog,
        )
        return PreparedBackendAabbSource(
            source_path=path,
            source_run_id=source.meta.run_id,
            frozen=frozen,
        )

    def run(self, source: PreparedBackendAabbSource, cancel_token: Any) -> BackendAabbRunResult:
        return self._runner.run(
            source.frozen,
            cancel_token,
            source_run_id=source.source_run_id,
            source_record_path=str(source.source_path),
        )

    @staticmethod
    def _model_dump(value: Any) -> dict[str, Any]:
        return dict(value.model_dump() if hasattr(value, "model_dump") else vars(value))

    @classmethod
    def _provider_config(cls, provider: Any) -> dict[str, Any]:
        return cls._model_dump(provider)
