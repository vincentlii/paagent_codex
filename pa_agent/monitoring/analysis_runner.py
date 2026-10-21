"""Identity-checked execution of one frozen asset analysis."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pa_agent.monitoring.identity import canonical_timeframe
from pa_agent.monitoring.scheduler import AnalysisTask
from pa_agent.util.threading import OrchestratorEvent

_SUCCESSFUL_ANALYSIS_STATUSES = frozenset(
    {"completed", "completed_with_warnings", "degraded_pa_only"}
)
_RETRYABLE_ANALYSIS_STATUSES = frozenset(
    {"unavailable_provider", "internal_error"}
)


@dataclass(frozen=True)
class AnalysisRunResult:
    """Separate scheduler execution from the domain result it returned."""

    record: Any = field(compare=False, repr=False)
    analysis_status: str
    analysis_success: bool
    retryable: bool
    record_persisted: bool
    audit_ok: bool


@dataclass(frozen=True)
class FrozenAnalysisPayload:
    """Inputs already frozen by the data layer before entering the queue."""

    frame: Any
    htf_context: Mapping[str, Any] = field(default_factory=dict)
    previous_record: Any | None = field(default=None, compare=False, repr=False)
    incremental_new_bar_count: int | None = None
    instrument_spec: Any | None = field(default=None, compare=True, repr=False)
    analysis_backend_mode: str = "deepseek_only"
    analysis_backend_identity: Mapping[str, Any] = field(default_factory=dict)
    analysis_profile_fingerprint: str = ""
    analysis_profile_identity: Mapping[str, Any] = field(default_factory=dict)
    execution_policy: str | None = None
    execution_target: str | None = None
    execution_epoch: str | None = None
    demo_execution_policy: Mapping[str, Any] | None = None
    take_profit_tick_size: str | None = None


class IndependentAnalysisHandler:
    """Create an independent orchestrator for each scheduled model run."""

    def __init__(
        self,
        orchestrator_factory: Callable[[AnalysisTask], Any],
    ) -> None:
        if not callable(orchestrator_factory):
            raise TypeError("orchestrator_factory must be callable")
        self._orchestrator_factory = orchestrator_factory

    def __call__(self, task: AnalysisTask, cancel_token: Any) -> Any:
        payload = task.payload
        if not isinstance(payload, FrozenAnalysisPayload):
            raise TypeError("task payload must be FrozenAnalysisPayload")
        self._validate_identity(task, payload.frame)
        orchestrator = self._orchestrator_factory(task)
        if orchestrator is None or not callable(getattr(orchestrator, "submit", None)):
            raise TypeError("orchestrator_factory must return an orchestrator")
        events: list[OrchestratorEvent] = []
        record = orchestrator.submit(
            payload.frame,
            cancel_token,
            events.append,
            previous_record=payload.previous_record,
            incremental_new_bar_count=payload.incremental_new_bar_count,
            htf_context=dict(payload.htf_context),
            monitoring_task_id=task.task_id,
        )
        outcome = getattr(record, "run_outcome", None)
        outcome = outcome if isinstance(outcome, dict) else {}
        status = str(outcome.get("status") or "internal_error")
        persisted = OrchestratorEvent.RecordSaved in events
        save_failed = OrchestratorEvent.RecordSaveFailed in events
        success = (
            status in _SUCCESSFUL_ANALYSIS_STATUSES
            and persisted
            and not save_failed
            and OrchestratorEvent.RecordAuditFailed not in events
        )
        return AnalysisRunResult(
            record=record,
            analysis_status=status,
            analysis_success=success,
            retryable=(
                status in _RETRYABLE_ANALYSIS_STATUSES
                or (status in _SUCCESSFUL_ANALYSIS_STATUSES and not success)
            ),
            record_persisted=persisted,
            audit_ok=OrchestratorEvent.RecordAuditFailed not in events,
        )

    @staticmethod
    def _validate_identity(task: AnalysisTask, frame: Any) -> None:
        symbol = str(getattr(frame, "symbol", "") or "").strip().upper()
        timeframe = canonical_timeframe(getattr(frame, "timeframe", ""))
        if symbol != task.symbol:
            raise ValueError(
                f"frame symbol {symbol or '<missing>'} does not match task {task.symbol}"
            )
        if timeframe != task.timeframe:
            raise ValueError(
                "frame timeframe "
                f"{timeframe or '<missing>'} does not match task {task.timeframe}"
            )
