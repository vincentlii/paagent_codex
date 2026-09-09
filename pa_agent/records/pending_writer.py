"""PendingWriter — persists AnalysisRecord and FollowupTurn to disk.

File naming convention:
    {YYYY-MM-DD_HH-mm-ss}_{symbol}_{timeframe}.json
    {YYYY-MM-DD_HH-mm-ss}_{symbol}_{timeframe}.followups.jsonl

Disk failures are logged and emitted to the event bus but never propagated.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from pa_agent.records.audit_store import AnalysisAuditStore
from pa_agent.records.codec import dump_record
from pa_agent.records.schema import AnalysisRecord, FollowupTurn
from pa_agent.util.mask_secret import mask_secret


def _default_logger() -> logging.Logger:
    return logging.getLogger(__name__)


def _ms_to_local_datetime(ms: int) -> datetime:
    """Convert epoch milliseconds to local datetime."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()


def _build_basename(record: AnalysisRecord) -> str:
    """Build the filename stem (without extension) for a record."""
    dt = _ms_to_local_datetime(record.meta.timestamp_local_ms)
    ts_str = dt.strftime("%Y-%m-%d_%H-%M-%S-%f")
    symbol = _safe_filename_part(record.meta.symbol)
    timeframe = _safe_filename_part(record.meta.timeframe)
    return f"{ts_str}_{symbol}_{timeframe}_{record.meta.run_id[:12]}"


def _safe_filename_part(value: object) -> str:
    """Return a portable, non-empty filename component."""
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    return text.strip(".-") or "unknown"


@dataclass(frozen=True)
class PersistResult:
    """Outcome of one record persistence attempt."""

    ok: bool
    path: Path
    audit_ok: bool = True
    audit_error: str | None = None

    def __fspath__(self) -> str:
        return str(self.path)

    def __getattr__(self, name: str):
        """Keep existing callers that consume the returned path compatible."""
        return getattr(self.path, name)


class PendingWriter:
    """Writes analysis records and followup turns to the pending directory."""

    def __init__(
        self,
        pending_dir: Optional[Path] = None,
        event_bus=None,
        logger: Optional[logging.Logger] = None,
        api_key: str = "",
        audit_store: AnalysisAuditStore | None = None,
        counterfactual_horizon_bars: int = 20,
    ) -> None:
        if pending_dir is None:
            from pa_agent.config.paths import RECORDS_PENDING_DIR
            pending_dir = RECORDS_PENDING_DIR

        self._pending_dir = pending_dir
        self._event_bus = event_bus
        self._logger = logger or _default_logger()
        self._api_key = api_key
        self._audit_store = audit_store or AnalysisAuditStore(
            self._pending_dir.parent / "audit" / "analysis.sqlite3",
            simulation_horizon_bars=counterfactual_horizon_bars,
        )

        try:
            self._pending_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._logger.error(
                "PendingWriter: failed to create pending directory %s: %s",
                self._pending_dir,
                exc,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_full(self, record: AnalysisRecord) -> PersistResult:
        """Serialize and save a complete analysis record.

        Returns the actual write outcome.
        """
        basename = _build_basename(record)
        path = self._pending_dir / f"{basename}.json"
        data = dump_record(record)
        data = self._sanitize(data, self._api_key)
        ok = self._write_json(path, data)
        audit_ok = True
        audit_error = None
        if ok:
            self._invalidate_latest_record_cache()
            audit = self._audit_store.record_analysis(record, record_path=path)
            audit_ok = audit.ok
            audit_error = audit.error
            timing = record.analysis_timing
            if audit_ok and timing is not None and timing.validated_at_ms is not None and timing.result_available_at_ms is None:
                # The full candidate and its index are durable before availability
                # is observed. Publish the receipt before releasing the caller.
                from pa_agent.records.schema import AnalysisTiming
                available = int(datetime.now(timezone.utc).timestamp() * 1000)
                try:
                    record.analysis_timing = AnalysisTiming.model_validate({
                        **timing.model_dump(), "result_available_at_ms": available,
                    })
                except ValueError:
                    return PersistResult(ok=False, path=path, audit_ok=False, audit_error="delivery clock conflict")
                ok = self._write_json(path, self._sanitize(dump_record(record), self._api_key))
                if ok:
                    audit = self._audit_store.record_analysis(record, record_path=path)
                    audit_ok, audit_error = audit.ok, audit.error
                if not ok or not audit_ok:
                    record.analysis_timing = timing
        return PersistResult(ok=ok, path=path, audit_ok=audit_ok, audit_error=audit_error)

    def save_partial(self, record: AnalysisRecord | None, reason: str) -> PersistResult | None:
        """Serialize and save a partial analysis record with a reason field.

        The ``_partial_reason`` key is injected into the serialized dict
        (it is not part of the Pydantic model). When ``record.exception`` is
        set, ``partial_reason`` is also copied into that dict for easier
        filtering without reading ``_partial_reason``.

        Returns the actual write outcome. A missing record is not persisted.
        """
        if record is None:
            self._logger.warning("PendingWriter: skipped partial save without record (%s)", reason)
            return None
        basename = _build_basename(record)
        path = self._pending_dir / f"{basename}.json"
        data = dump_record(record)
        data["_partial_reason"] = reason
        if isinstance(data.get("exception"), dict):
            data["exception"] = {**data["exception"], "partial_reason": reason}
        data = self._sanitize(data, self._api_key)
        ok = self._write_json(path, data)
        audit_ok = True
        audit_error = None
        if ok:
            self._invalidate_latest_record_cache()
            audit = self._audit_store.record_analysis(
                record,
                record_path=path,
                partial_reason=reason,
            )
            audit_ok = audit.ok
            audit_error = audit.error
        return PersistResult(ok=ok, path=path, audit_ok=audit_ok, audit_error=audit_error)

    def append_followup(self, record_id: str, turn: FollowupTurn) -> None:
        """Append a single followup turn to the JSONL sidecar file.

        ``record_id`` is the basename (without extension) of the record file,
        e.g. ``"2026-05-18_14-00-13_XAUUSD_1h"``.
        """
        path = self._pending_dir / f"{record_id}.followups.jsonl"
        line = json.dumps(turn.model_dump(), ensure_ascii=False)
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            self._handle_disk_error(exc, path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize(data: dict, api_key: str) -> dict:
        """Recursively replace any occurrence of *api_key* in string values.

        If *api_key* is empty, returns *data* unchanged.
        Handles nested dicts, lists, and plain string values at any depth.
        """
        if not api_key:
            return data

        masked = mask_secret(api_key)

        def _walk(node):
            if isinstance(node, str):
                return node.replace(api_key, masked)
            if isinstance(node, dict):
                return {k: _walk(v) for k, v in node.items()}
            if isinstance(node, list):
                return [_walk(item) for item in node]
            return node

        return _walk(data)

    def _write_json(self, path: Path, data: dict) -> bool:
        """Atomically write *data* and return whether it reached the target path."""
        temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            text = json.dumps(data, ensure_ascii=False, indent=2)
            with temp_path.open("x", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            return True
        except (OSError, TypeError, ValueError) as exc:
            self._handle_disk_error(exc, path)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    @staticmethod
    def _invalidate_latest_record_cache() -> None:
        try:
            from pa_agent.records.analysis_history import invalidate_latest_record_cache

            invalidate_latest_record_cache()
        except Exception:  # noqa: BLE001
            pass

    def _handle_disk_error(self, exc: Exception, path: Path) -> None:
        """Log a persistence error and optionally emit it to the event bus."""
        self._logger.error(
            "PendingWriter: disk error writing %s: %s", path, exc
        )
        if self._event_bus is not None:
            try:
                self._event_bus.emit("disk_error", {"path": str(path), "error": str(exc)})
            except Exception as bus_exc:  # noqa: BLE001
                self._logger.error(
                    "PendingWriter: event_bus emit failed: %s", bus_exc
                )
