"""Load pending analysis JSON records for demo replay."""
from __future__ import annotations

import logging
import random
from pathlib import Path

from pa_agent.config.paths import RECORDS_PENDING_DIR
from pa_agent.data.base import KlineBar, KlineFrame, normalize_kline_bar
from pa_agent.data.snapshot import compute_indicators
from pa_agent.records.codec import load_record
from pa_agent.records.schema import AnalysisRecord
from pa_agent.util.timefmt import now_local_ms

logger = logging.getLogger(__name__)


def list_pending_record_paths(directory: Path | None = None) -> list[Path]:
    """Return ``*.json`` analysis records under *directory* (sorted newest first)."""
    root = directory or RECORDS_PENDING_DIR
    if not root.is_dir():
        return []
    files = [p for p in root.glob("*.json") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def pick_random_record_path(directory: Path | None = None) -> Path | None:
    paths = list_pending_record_paths(directory)
    return random.choice(paths) if paths else None


def load_analysis_record(path: Path) -> AnalysisRecord:
    """Parse one pending record file."""
    return load_record(path).record


def try_load_analysis_record(path: Path) -> AnalysisRecord | None:
    """Load *path*; return None if the file is unreadable or invalid."""
    try:
        return load_analysis_record(path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Skip demo record %s: %s", path.name, exc)
        return None


def is_demo_playable(record: AnalysisRecord) -> bool:
    """True when at least one stage result exists for replay."""
    return bool(record.stage1_diagnosis or record.stage2_decision)


def has_flow_viz_payload(record: AnalysisRecord) -> bool:
    s2 = record.stage2_decision
    if not isinstance(s2, dict):
        return False
    return bool(s2.get("decision_trace") or s2.get("terminal"))


def pick_playable_demo_record(
    *,
    directory: Path | None = None,
    exclude: Path | str | None = None,
    prefer_flow_viz: bool = True,
) -> tuple[Path, AnalysisRecord] | None:
    """Return a random playable record, skipping broken or incomplete files."""
    paths = list_pending_record_paths(directory)
    if not paths:
        return None

    exclude_key = str(exclude) if exclude is not None else None
    order = paths.copy()
    random.shuffle(order)

    def _eligible(path: Path, record: AnalysisRecord) -> bool:
        if exclude_key is not None and str(path) == exclude_key:
            return False
        return is_demo_playable(record)

    if prefer_flow_viz:
        for path in order:
            record = try_load_analysis_record(path)
            if record is not None and _eligible(path, record) and has_flow_viz_payload(record):
                return path, record

    for path in order:
        record = try_load_analysis_record(path)
        if record is not None and _eligible(path, record):
            return path, record

    # Newest-first fallback (deterministic) when shuffle missed valid files
    for path in paths:
        if exclude_key is not None and str(path) == exclude_key:
            continue
        record = try_load_analysis_record(path)
        if record is not None and is_demo_playable(record):
            return path, record

    return None


def frame_from_record_klines(
    kline_data: list[dict],
    *,
    symbol: str,
    timeframe: str,
    snapshot_ts_local_ms: int | None = None,
    data_metadata: dict | None = None,
) -> KlineFrame:
    """Build a chart/analysis frame from persisted ``kline_data`` (newest-first)."""
    rebased: list[KlineBar] = []
    for i, b in enumerate(kline_data):
        rebased.append(
            normalize_kline_bar(
                KlineBar(
                    seq=int(b.get("seq", i + 1)),
                    ts_open=b["ts_open"],
                    open=b["open"],
                    high=b["high"],
                    low=b["low"],
                    close=b["close"],
                    volume=b.get("volume", 0),
                    volume_state=b.get("volume_state"),
                    amount=b.get("amount", 0) or 0,
                    pct_chg=b.get("pct_chg"),
                    closed=bool(b.get("closed", True)),
                )
            )
        )
    if not rebased:
        raise ValueError("Record has no kline_data")
    return KlineFrame(
        symbol=symbol,
        timeframe=timeframe,
        bars=tuple(rebased),
        indicators=compute_indicators(rebased),
        snapshot_ts_local_ms=snapshot_ts_local_ms or now_local_ms(),
        data_metadata=dict(data_metadata or {}),
    )
