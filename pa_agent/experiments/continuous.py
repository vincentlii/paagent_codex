"""Bounded, recoverable sampling of production Shadow records for A/A/B/B research."""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pa_agent.ai.provider_failures import classify_provider_exception

logger = logging.getLogger(__name__)

CONTINUOUS_AABB_PIPELINE_ID = "continuous-aabb-v1"
_SUCCESS_STATUSES = frozenset({"completed", "completed_with_warnings"})


class IneligibleContinuousSource(ValueError):
    """The record is valid but not a production Shadow sampling source."""


@dataclass(frozen=True)
class ContinuousAabbPolicy:
    max_records_per_cycle: int = 1
    max_attempts: int = 2
    retry_backoff_seconds: float = 300.0
    lease_seconds: float = 7200.0
    pipeline_id: str = CONTINUOUS_AABB_PIPELINE_ID

    def __post_init__(self) -> None:
        if self.max_records_per_cycle < 1:
            raise ValueError("max_records_per_cycle must be at least 1")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds cannot be negative")
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if not self.pipeline_id.strip():
            raise ValueError("pipeline_id is required")


@dataclass(frozen=True)
class PreparedAabbSource:
    source_path: Path
    source_run_id: str
    frozen: Any


@dataclass(frozen=True)
class ContinuousAabbOutcome:
    source_path: Path
    source_run_id: str | None
    status: str
    experiment_id: str | None = None
    detail: str | None = None


def is_continuous_source_record(record: Any) -> bool:
    """Accept only complete production Shadow records, never experiment arms."""
    if getattr(record, "experiment_context", None):
        return False
    if getattr(record, "exception", None) is not None:
        return False
    if not getattr(record, "stage1_diagnosis", None):
        return False
    if not getattr(record, "stage2_decision", None):
        return False
    if not getattr(record, "kline_data", None):
        return False
    if not isinstance(getattr(record, "vpa_catalog", None), dict):
        return False
    outcome = getattr(record, "run_outcome", None)
    if not isinstance(outcome, dict):
        return False
    return (
        str(outcome.get("status") or "") in _SUCCESS_STATUSES
        and str(outcome.get("official_path") or "") == "pa_only"
        and str(outcome.get("effective_vpa_mode") or "") == "shadow"
    )


class ContinuousAabbMonitor:
    """Discover frozen Shadow snapshots and run isolated paired research once."""

    def __init__(
        self,
        *,
        records_dir: Path,
        prepare_source: Callable[[Path], PreparedAabbSource],
        execute_source: Callable[[PreparedAabbSource, Any], Any],
        experiment_store: Any,
        policy: ContinuousAabbPolicy | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._records_dir = Path(records_dir)
        self._prepare_source = prepare_source
        self._execute_source = execute_source
        self._store = experiment_store
        self._policy = policy or ContinuousAabbPolicy()
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._prepared_cache: dict[
            Path,
            tuple[tuple[int, int], PreparedAabbSource | None],
        ] = {}

    def run_cycle(self, cancel_token: Any) -> tuple[ContinuousAabbOutcome, ...]:
        outcomes: list[ContinuousAabbOutcome] = []
        prepared_sources: list[PreparedAabbSource] = []
        for path in self._source_paths():
            if cancel_token.is_set():
                break
            prepared = self._prepare(path)
            if prepared is not None:
                prepared_sources.append(prepared)
        prepared_sources.sort(key=self._market_order_key)

        for prepared in prepared_sources:
            if cancel_token.is_set() or len(outcomes) >= self._policy.max_records_per_cycle:
                break
            frozen = prepared.frozen
            claim = self._store.claim_continuous_snapshot(
                pipeline_id=self._policy.pipeline_id,
                cohort_id=str(frozen.cohort_id),
                market_snapshot_hash=str(frozen.market_snapshot_hash),
                source_run_id=prepared.source_run_id,
                source_record_path=str(prepared.source_path),
                max_attempts=self._policy.max_attempts,
                lease_seconds=self._policy.lease_seconds,
                now_ms=self._now_ms(),
            )
            if claim.reason == "store_error":
                outcomes.append(
                    ContinuousAabbOutcome(
                        source_path=prepared.source_path,
                        source_run_id=prepared.source_run_id,
                        status="store_error",
                        detail="continuous snapshot claim failed",
                    )
                )
                continue
            if not claim.acquired or not claim.lease_token:
                continue
            outcomes.append(self._execute_claimed(prepared, claim.lease_token, cancel_token))
        return tuple(outcomes)

    def _source_paths(self) -> tuple[Path, ...]:
        if not self._records_dir.is_dir():
            return ()
        paths = tuple(path for path in self._records_dir.glob("*.json") if path.is_file())
        return tuple(sorted(paths, key=lambda path: path.name))

    @staticmethod
    def _market_order_key(source: PreparedAabbSource) -> tuple[float, str]:
        frame = getattr(source.frozen, "frame", None)
        closed = [
            float(bar.ts_open)
            for bar in tuple(getattr(frame, "bars", ()) or ())
            if bool(getattr(bar, "closed", False))
        ]
        if not closed:
            raise ValueError("continuous A/A/B/B source has no closed market bar")
        return max(closed), str(source.source_path)

    def _prepare(self, path: Path) -> PreparedAabbSource | None:
        try:
            stat = path.stat()
        except OSError as exc:
            logger.warning("Cannot inspect continuous A/A/B/B source %s: %s", path, exc)
            return None
        signature = (int(stat.st_mtime_ns), int(stat.st_size))
        cached = self._prepared_cache.get(path)
        if cached is not None and cached[0] == signature:
            return cached[1]
        try:
            prepared = self._prepare_source(path)
        except IneligibleContinuousSource as exc:
            logger.debug("Skip ineligible continuous A/A/B/B source %s: %s", path, exc)
            prepared = None
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("Invalid continuous A/A/B/B source %s: %s", path, exc)
            prepared = None
        self._prepared_cache[path] = (signature, prepared)
        return prepared

    def _execute_claimed(
        self,
        source: PreparedAabbSource,
        lease_token: str,
        cancel_token: Any,
    ) -> ContinuousAabbOutcome:
        experiment_id: str | None = None
        try:
            result = self._execute_source(source, cancel_token)
            experiment_id = str(getattr(result, "experiment_id", "") or "") or None
            protocol = getattr(result, "protocol_report", {}) or {}
            status = str(getattr(result, "status", "") or "incomplete")
            operation_error = str(getattr(result, "error", "") or "") or None
            completed = (
                status == "completed"
                and bool(protocol.get("valid"))
                and operation_error is None
            )
            retryable = (
                bool(getattr(result, "retryable", False))
                if operation_error is not None
                else status in {"cancelled", "incomplete"}
            )
            error = (
                operation_error
                or (None if completed else ",".join(protocol.get("errors") or ()) or status)
            )
            if operation_error is not None:
                status = "paper_failed"
        except (OSError, sqlite3.OperationalError) as exc:
            status = "failed"
            completed = False
            retryable = True
            error = f"operational:{type(exc).__name__}"
            logger.warning("Continuous A/A/B/B operational failure: %s (%s)", source.source_path, error)
        except Exception as exc:
            failure = classify_provider_exception(exc)
            status = "failed"
            completed = False
            retryable = failure.retryable
            error = f"{failure.kind}:{type(exc).__name__}"
            logger.warning("Continuous A/A/B/B source failed: %s (%s)", source.source_path, error)

        finished = self._store.finish_continuous_snapshot(
            pipeline_id=self._policy.pipeline_id,
            cohort_id=str(source.frozen.cohort_id),
            market_snapshot_hash=str(source.frozen.market_snapshot_hash),
            lease_token=lease_token,
            completed=completed,
            retryable=retryable,
            experiment_id=experiment_id,
            error=error,
            max_attempts=self._policy.max_attempts,
            retry_backoff_seconds=self._policy.retry_backoff_seconds,
            now_ms=self._now_ms(),
        )
        if not finished.ok:
            raise RuntimeError(f"continuous A/A/B/B lease finalization failed: {finished.error}")
        return ContinuousAabbOutcome(
            source_path=source.source_path,
            source_run_id=source.source_run_id,
            status=status,
            experiment_id=experiment_id,
            detail=error,
        )
