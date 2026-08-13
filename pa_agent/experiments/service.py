"""Application service for one frozen PA/VPA A/A/B/B experiment."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pa_agent.execution.coordinator import ExecutionAction, PaperExecutionCoordinator
from pa_agent.experiments.continuous import (
    IneligibleContinuousSource,
    PreparedAabbSource,
    is_continuous_source_record,
)
from pa_agent.experiments.executor import OrchestratorArmExecutor
from pa_agent.experiments.pa_vpa_aabb import (
    AabbRunResult,
    PaVpaAabbRunner,
    freeze_experiment_input,
    validate_aabb_records,
)
from pa_agent.experiments.replay import frozen_input_from_record
from pa_agent.experiments.store import AnalysisExperimentStore
from pa_agent.records.audit_store import AnalysisAuditStore
from pa_agent.records.codec import load_record
from pa_agent.records.pending_writer import PendingWriter


@dataclass(frozen=True)
class AabbServiceResult:
    experiment_id: str
    status: str
    records: tuple[Any, ...]
    protocol_report: dict[str, Any]
    external_comparison: dict[str, Any]
    paper_actions: tuple[ExecutionAction, ...] = ()
    error: str | None = None
    retryable: bool = False
    reused_experiment: bool = False


class AabbExperimentService:
    """Own replay, four-arm execution, persistence and isolated research Paper."""

    def __init__(
        self,
        *,
        settings: Any,
        prompt_dir: Path,
        experience_dir: Path,
        pending_dir: Path,
        db_path: Path,
        paper_db_path: Path,
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
        self._store = AnalysisExperimentStore(db_path)
        self._runner = PaVpaAabbRunner(
            execute_arm=OrchestratorArmExecutor(
                settings=settings,
                prompt_dir=prompt_dir,
                experience_dir=experience_dir,
                logger=logger or logging.getLogger(__name__),
            ),
            pending_writer=writer,
            experiment_store=self._store,
        )
        self._paper = PaperExecutionCoordinator(paper_db_path, settings.paper_trading)

    def prepare_source(
        self,
        source_path: Path,
        *,
        production_shadow_only: bool = False,
    ) -> PreparedAabbSource:
        path = Path(source_path).resolve()
        source = load_record(path).record
        if source.experiment_context:
            raise IneligibleContinuousSource(
                "an A/A/B/B arm cannot be used as a source snapshot"
            )
        if production_shadow_only and not is_continuous_source_record(source):
            raise IneligibleContinuousSource(
                "source is not an eligible production Shadow record"
            )
        frame, htf_context, catalog = frozen_input_from_record(source)
        frozen = freeze_experiment_input(
            frame,
            htf_context,
            self._settings,
            catalog=catalog,
        )
        return PreparedAabbSource(
            source_path=path,
            source_run_id=source.meta.run_id,
            frozen=frozen,
        )

    def run(
        self,
        source: PreparedAabbSource,
        cancel_token: Any,
        *,
        reuse_completed: bool = False,
    ) -> AabbServiceResult:
        reused = False
        result: AabbRunResult | None = None
        if reuse_completed:
            manifest = self._store.find_completed_experiment(
                cohort_id=source.frozen.cohort_id,
                market_snapshot_hash=source.frozen.market_snapshot_hash,
            )
            if manifest is not None:
                result = self._result_from_manifest(manifest)
                reused = True
        if result is None:
            result = self._runner.run(
                source.frozen,
                cancel_token,
                source_run_id=source.source_run_id,
                source_record_path=str(source.source_path),
            )

        paper_actions: tuple[ExecutionAction, ...] = ()
        paper_error: str | None = None
        retryable = False
        if self._paper.enabled and result.status == "completed":
            try:
                paper_actions = self._paper.record_aabb(result, source.frozen)
            except (OSError, sqlite3.Error) as exc:
                paper_error = f"research_paper_persistence_failed:{type(exc).__name__}"
                retryable = True
            except ValueError as exc:
                paper_error = f"research_paper_contract_failed:{exc}"

        return AabbServiceResult(
            experiment_id=result.experiment_id,
            status=result.status,
            records=result.records,
            protocol_report=result.protocol_report,
            external_comparison=result.external_comparison,
            paper_actions=paper_actions,
            error=paper_error,
            retryable=retryable,
            reused_experiment=reused,
        )

    @staticmethod
    def _result_from_manifest(manifest: dict[str, Any]) -> AabbRunResult:
        records = []
        for arm in sorted(
            manifest.get("arms") or (),
            key=lambda value: int(value.get("sequence_position") or 0),
        ):
            record_path = str(arm.get("record_path") or "").strip()
            if not record_path:
                raise ValueError("completed A/A/B/B manifest has an arm without a record")
            records.append(load_record(Path(record_path)).record)
        protocol = validate_aabb_records(records)
        if len(records) != 4 or not protocol["valid"]:
            raise ValueError(
                "completed A/A/B/B artifacts failed replay validation: "
                + ",".join(protocol.get("errors") or ())
            )
        return AabbRunResult(
            experiment_id=str(manifest["experiment_id"]),
            status="completed",
            records=tuple(records),
            protocol_report=protocol,
            external_comparison=dict(manifest.get("external_comparison") or {}),
        )
