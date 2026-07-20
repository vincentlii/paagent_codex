"""Assemble a frozen, same-source analysis snapshot bundle."""
from __future__ import annotations

import time
from typing import Any

from pa_agent.data.contracts import (
    AnalysisSnapshotBundle,
    ResolvedInstrumentIdentity,
)
from pa_agent.data.validation import validate_snapshot_identities


def build_snapshot_bundle(
    current_frame: Any,
    htf_context: dict[str, Any] | None,
    *,
    expected_identity: ResolvedInstrumentIdentity | dict[str, Any] | None = None,
    captured_at_ms: int | None = None,
) -> AnalysisSnapshotBundle:
    identity = ResolvedInstrumentIdentity.from_value(
        expected_identity
        or getattr(current_frame, "data_metadata", {})
        or {}
    )
    higher_frame = None
    if isinstance(htf_context, dict) and htf_context.get("status") == "available":
        higher_frame = htf_context.get("frame")
    captured = int(captured_at_ms if captured_at_ms is not None else time.time() * 1000)
    report = validate_snapshot_identities(
        current_frame,
        higher_frame,
        expected_identity=identity,
        now_ms=captured,
    )
    return AnalysisSnapshotBundle(
        current_frame=current_frame,
        higher_frame=higher_frame,
        source_identity=identity,
        data_quality=report,
        captured_at_ms=captured,
    )


def compact_snapshot_context(frame: Any) -> dict[str, Any]:
    """Persist provenance and availability without copying seasonal samples."""
    metadata = getattr(frame, "data_metadata", {}) or {}
    metadata = metadata if isinstance(metadata, dict) else {}
    identity = ResolvedInstrumentIdentity.from_value(metadata)
    bars = tuple(getattr(frame, "bars", ()) or ())
    return {
        "source_identity": identity.to_dict() if identity is not None else None,
        "source": metadata.get("source"),
        "venue": metadata.get("venue"),
        "volume_kind": metadata.get("volume_kind", "unknown"),
        "volume_quality": metadata.get("volume_quality", "unknown"),
        "timezone_basis": metadata.get("timezone_basis", "UTC"),
        "continuous_market": metadata.get("continuous_market"),
        "seasonality_history_bars": int(
            metadata.get("seasonality_history_bars", 0) or 0
        ),
        "snapshot_ts_local_ms": getattr(frame, "snapshot_ts_local_ms", None),
        "latest_closed_ts_open": bars[0].ts_open if bars else None,
    }
