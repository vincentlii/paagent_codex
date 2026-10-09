"""Crash reconciliation without repeating uncertain model work."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pa_agent.data.snapshot_bundle import compact_snapshot_context, serialize_frame_bars
from pa_agent.monitoring.frozen_task_codec import decode_task
from pa_agent.records.analysis_history import list_record_paths
from pa_agent.records.codec import load_record

_SUCCESS = frozenset({"completed", "completed_with_warnings", "degraded_pa_only"})


def reconcile_saved_tasks(
    scheduler_audit: Any,
    records_dir: Path,
    analysis_audit: Any,
) -> list[str]:
    """Reconcile proven saved results; return task IDs needing human review."""
    unresolved = scheduler_audit.list_unresolved_tasks()
    if not unresolved:
        return []
    records: dict[str, list[tuple[Any, Path]]] = {}
    for path in list_record_paths(records_dir):
        try:
            record = load_record(path).record
        except (OSError, ValueError, TypeError):
            continue
        task_id = record.monitoring_task_id
        if task_id:
            records.setdefault(task_id, []).append((record, path))

    blocked: list[str] = []
    for row in unresolved:
        task_id = row["task_id"]
        if row["started_at_ms"] is None:
            if not row["payload_json"] or not row["payload_digest"]:
                blocked.append(task_id)
            continue
        try:
            task = decode_task(row["payload_json"], row["payload_digest"])
        except (KeyError, TypeError, ValueError):
            blocked.append(task_id)
            continue
        matches = records.get(task_id, [])
        if len(matches) != 1:
            blocked.append(task_id)
            continue
        record, path = matches[0]
        outcome = record.run_outcome if isinstance(record.run_outcome, dict) else {}
        status = str(outcome.get("status") or "")
        timing = record.analysis_timing
        indexed = analysis_audit.get_run(record.meta.run_id)
        if not (
            status in _SUCCESS
            and record.exception is None
            and record.role_stop is None
            and record.stage1_diagnosis is not None
            and record.stage2_decision is not None
            and timing is not None
            and timing.result_available_at_ms is not None
            and record.meta.symbol == task.symbol
            and record.meta.timeframe == task.timeframe
            and record.kline_data == serialize_frame_bars(task.payload.frame)
            and record.snapshot_context == compact_snapshot_context(task.payload.frame)
            and indexed is not None
            and Path(indexed["record_path"]).resolve() == path.resolve()
            and indexed["run_status"] == status
            and scheduler_audit.record_reconciled_success(
                task_id, record.meta.run_id, status,
            )
        ):
            blocked.append(task_id)
    return blocked
