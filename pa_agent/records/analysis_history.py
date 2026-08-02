"""Helpers for locating prior analysis records for incremental runs."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pa_agent.config.paths import RECORDS_PENDING_DIR
from pa_agent.data.base import KlineFrame
from pa_agent.data.contracts import ResolvedInstrumentIdentity
from pa_agent.data.datetime_ts import format_epoch_for_display, ts_open_to_ms
from pa_agent.records.codec import load_record as load_record_file
from pa_agent.records.schema import AnalysisRecord

_TS_EPS_MS = 1.0  # milliseconds tolerance for bar open time matching
_SUCCESSFUL_RUN_STATUSES = frozenset(
    {"completed", "completed_with_warnings", "degraded_pa_only"}
)


@dataclass(frozen=True)
class IncrementalBarDelta:
    """How many closed bars appeared since a previous record."""

    new_count: int
    anchor_ts_open: float
    new_bar_ts_opens: tuple[float, ...]


def format_bar_ts(ts_open: float) -> str:
    """Format bar open time for logs/UI (server-time epoch, no local TZ shift)."""
    return format_epoch_for_display(ts_open, short=False)


def list_record_paths(directory: Path | None = None) -> list[Path]:
    """Return saved analysis record paths, newest modified first."""
    root = directory or RECORDS_PENDING_DIR
    if not root.is_dir():
        return []
    paths = [p for p in root.glob("*.json") if p.is_file()]
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return paths


def load_record(path: Path) -> AnalysisRecord | None:
    """Load one AnalysisRecord, returning None for unreadable legacy files."""
    try:
        return load_record_file(path).record
    except Exception:
        return None


def find_latest_successful_record(
    *,
    symbol: str,
    timeframe: str,
    identity: ResolvedInstrumentIdentity | None = None,
    directory: Path | None = None,
) -> AnalysisRecord | None:
    """Find the newest successful record for one resolved instrument."""
    root = directory or RECORDS_PENDING_DIR
    identity_key = identity.key if identity is not None else ("", "", "", "")
    cache_key = (str(root.resolve()), symbol, timeframe, *identity_key)
    try:
        dir_mtime = root.stat().st_mtime if root.is_dir() else 0.0
    except OSError:
        dir_mtime = 0.0
    cached = _LATEST_RECORD_CACHE.get(cache_key)
    if cached is not None and cached[0] == dir_mtime:
        return cached[1]

    result: AnalysisRecord | None = None
    for path in list_record_paths(directory):
        record = load_record(path)
        if record is None:
            continue
        if record.meta.symbol != symbol or record.meta.timeframe != timeframe:
            continue
        if identity is not None:
            record_identity = ResolvedInstrumentIdentity.from_value(
                record.snapshot_context
            )
            if record_identity is None or not record_identity.same_instrument(identity):
                continue
        if record.exception is not None:
            continue
        outcome = record.run_outcome if isinstance(record.run_outcome, dict) else {}
        if str(outcome.get("status") or "") not in _SUCCESSFUL_RUN_STATUSES:
            continue
        if str(outcome.get("official_path") or "none") == "none":
            continue
        if not record.stage1_diagnosis or not record.stage2_decision:
            continue
        if not record.kline_data:
            continue
        result = record
        break
    _LATEST_RECORD_CACHE[cache_key] = (dir_mtime, result)
    return result


def find_latest_successful_record_for_frame(
    frame: KlineFrame,
    *,
    directory: Path | None = None,
) -> AnalysisRecord | None:
    """Identity-safe lookup for a frozen frame; missing identity disables reuse."""
    identity = ResolvedInstrumentIdentity.from_value(
        getattr(frame, "data_metadata", {}) or {}
    )
    if identity is None:
        return None
    return find_latest_successful_record(
        symbol=frame.symbol,
        timeframe=frame.timeframe,
        identity=identity,
        directory=directory,
    )


_LATEST_RECORD_CACHE: dict[
    tuple[str, str, str, str, str, str, str],
    tuple[float, AnalysisRecord | None],
] = {}


def invalidate_latest_record_cache() -> None:
    """Clear cached latest-record lookups (call after saving a new record)."""
    _LATEST_RECORD_CACHE.clear()


def compute_incremental_bar_delta(
    frame: KlineFrame,
    previous_record: AnalysisRecord,
) -> IncrementalBarDelta | None:
    """Return bars newer than the previous record's latest closed bar.

    ``frame.bars`` and ``previous_record.kline_data`` are newest-first. The anchor
    is ``kline_data[0]`` (K1 at the time of the previous analysis). New bars are
    those with ``ts_open`` strictly greater than the anchor — not merely bars
    appearing before the anchor index in the current window.
    """
    if not previous_record.kline_data:
        return None

    anchor_raw = previous_record.kline_data[0]["ts_open"]
    anchor = ts_open_to_ms(anchor_raw)

    anchor_seen = False
    new_ts: list[float] = []
    for bar in frame.bars:
        ts = ts_open_to_ms(bar.ts_open)
        if abs(ts - anchor) <= _TS_EPS_MS:
            anchor_seen = True
            continue
        if ts > anchor + _TS_EPS_MS:
            new_ts.append(bar.ts_open)

    if not anchor_seen:
        return None

    return IncrementalBarDelta(
        new_count=len(new_ts),
        anchor_ts_open=float(anchor_raw),
        new_bar_ts_opens=tuple(new_ts),
    )


def count_new_bars_since_record(
    frame: KlineFrame,
    previous_record: AnalysisRecord,
) -> int | None:
    """Backward-compatible wrapper returning only the new bar count."""
    delta = compute_incremental_bar_delta(frame, previous_record)
    if delta is None:
        return None
    return delta.new_count
