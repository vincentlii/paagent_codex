"""Identity-checked execution of one frozen asset analysis."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pa_agent.monitoring.identity import canonical_timeframe
from pa_agent.monitoring.scheduler import AnalysisTask


@dataclass(frozen=True)
class FrozenAnalysisPayload:
    """Inputs already frozen by the data layer before entering the queue."""

    frame: Any
    htf_context: Mapping[str, Any] = field(default_factory=dict)
    previous_record: Any | None = field(default=None, compare=False, repr=False)
    incremental_new_bar_count: int | None = None


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
        return orchestrator.submit(
            payload.frame,
            cancel_token,
            lambda _event: None,
            previous_record=payload.previous_record,
            incremental_new_bar_count=payload.incremental_new_bar_count,
            htf_context=dict(payload.htf_context),
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
