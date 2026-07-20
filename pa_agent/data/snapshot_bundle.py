"""Assemble a frozen, same-source analysis snapshot bundle."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from collections.abc import Mapping
from typing import Any

from pa_agent.data.contracts import (
    AnalysisSnapshotBundle,
    ResolvedInstrumentIdentity,
)
from pa_agent.data.validation import validate_snapshot_identities


def seasonality_context_hash(metadata: object) -> str | None:
    """Identify raw seasonality inputs without persisting their full payload."""
    values = metadata if isinstance(metadata, dict) else {}
    samples = values.get("seasonality_samples")
    if samples:
        payload = {
            "samples": samples,
            "history_bars": int(values.get("seasonality_history_bars", 0) or 0),
            "minimum_samples": int(values.get("seasonality_min_samples", 8) or 8),
            "timezone_basis": values.get("timezone_basis", "UTC"),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            default=str,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    existing = str(values.get("seasonality_context_hash") or "").strip().lower()
    if len(existing) == 64 and all(character in "0123456789abcdef" for character in existing):
        return existing
    return None


def serialize_frame_bars(frame: Any | None) -> list[dict[str, Any]]:
    """Serialize one immutable frame without changing provider facts."""
    if frame is None:
        return []
    result: list[dict[str, Any]] = []
    for bar in tuple(getattr(frame, "bars", ()) or ()):
        if dataclasses.is_dataclass(bar) and not isinstance(bar, type):
            result.append(dataclasses.asdict(bar))
        elif isinstance(bar, Mapping):
            result.append(dict(bar))
        elif hasattr(bar, "__dict__"):
            result.append(dict(vars(bar)))
        else:
            raise TypeError(f"unsupported bar type: {type(bar).__name__}")
    return result


def build_snapshot_bundle(
    current_frame: Any,
    htf_context: dict[str, Any] | None,
    *,
    expected_identity: ResolvedInstrumentIdentity | dict[str, Any] | None = None,
    captured_at_ms: int | None = None,
) -> AnalysisSnapshotBundle:
    identity = ResolvedInstrumentIdentity.from_value(
        expected_identity or getattr(current_frame, "data_metadata", {}) or {}
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
        "seasonality_history_bars": int(metadata.get("seasonality_history_bars", 0) or 0),
        "seasonality_context_hash": seasonality_context_hash(metadata),
        "snapshot_ts_local_ms": getattr(frame, "snapshot_ts_local_ms", None),
        "latest_closed_ts_open": bars[0].ts_open if bars else None,
    }
