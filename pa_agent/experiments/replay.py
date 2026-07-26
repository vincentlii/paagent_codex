"""Reconstruct immutable experiment inputs from a saved analysis record."""

from __future__ import annotations

import copy
from typing import Any

from pa_agent.demo.record_loader import frame_from_record_klines
from pa_agent.records.schema import AnalysisRecord


def _current_metadata(record: AnalysisRecord) -> dict[str, Any]:
    context = record.snapshot_context if isinstance(record.snapshot_context, dict) else {}
    metadata = {
        key: copy.deepcopy(context.get(key))
        for key in (
            "source",
            "venue",
            "volume_kind",
            "volume_quality",
            "timezone_basis",
            "continuous_market",
            "seasonality_history_bars",
            "seasonality_context_hash",
        )
        if context.get(key) is not None
    }
    if isinstance(context.get("source_identity"), dict):
        metadata["source_identity"] = copy.deepcopy(context["source_identity"])
    return metadata


def frozen_input_from_record(
    record: AnalysisRecord,
) -> tuple[Any, dict[str, Any] | None, dict[str, Any]]:
    """Return current frame, exact HTF context, and the original VPA catalog."""
    catalog = record.vpa_catalog if isinstance(record.vpa_catalog, dict) else None
    if not isinstance(catalog, dict):
        raise ValueError("source record does not contain a frozen VPA evidence catalog")
    snapshot_ms = (
        int(record.snapshot_context.get("snapshot_ts_local_ms"))
        if isinstance(record.snapshot_context, dict)
        and record.snapshot_context.get("snapshot_ts_local_ms") is not None
        else record.meta.timestamp_local_ms
    )
    frame = frame_from_record_klines(
        record.kline_data,
        symbol=record.meta.symbol,
        timeframe=record.meta.timeframe,
        snapshot_ts_local_ms=snapshot_ms,
        data_metadata=_current_metadata(record),
    )
    compact_htf = copy.deepcopy(record.htf_context)
    if not isinstance(compact_htf, dict):
        compact_htf = None
    if compact_htf is not None and compact_htf.get("status") == "available":
        if not record.htf_kline_data:
            raise ValueError(
                "source record declares available HTF context but has no full HTF bars; "
                "run one new Shadow or Soft analysis before starting A/A/B/B"
            )
        timeframe = str(compact_htf.get("timeframe") or "").strip()
        if not timeframe:
            raise ValueError("available HTF context has no timeframe")
        htf_metadata = compact_htf.get("metadata")
        htf_frame = frame_from_record_klines(
            record.htf_kline_data,
            symbol=str(compact_htf.get("symbol") or record.meta.symbol),
            timeframe=timeframe,
            snapshot_ts_local_ms=snapshot_ms,
            data_metadata=(copy.deepcopy(htf_metadata) if isinstance(htf_metadata, dict) else {}),
        )
        compact_htf["frame"] = htf_frame
    return frame, compact_htf, copy.deepcopy(catalog)
