"""Optional higher-timeframe background retrieval for PA analysis."""
from __future__ import annotations

import logging
from typing import Any

from pa_agent.data.base import DataSource
from pa_agent.data.contracts import ResolvedInstrumentIdentity
from pa_agent.data.factory import volume_metadata_for_source
from pa_agent.data.snapshot import INDICATOR_WARMUP_BARS, build_display_frame
from pa_agent.data.validation import validate_analysis_frame

logger = logging.getLogger(__name__)

# One background timeframe is intentionally enough.  The entry timeframe still
# owns the setup; this mapping only supplies a slower structural backdrop.
_DEFAULT_HIGHER_TIMEFRAME: dict[str, str | None] = {
    "1m": "15m",
    "2m": "15m",
    "3m": "15m",
    "5m": "30m",
    "10m": "1h",
    "15m": "1h",
    "30m": "2h",
    "45m": "4h",
    "1h": "4h",
    "2h": "1d",
    "3h": "1d",
    "4h": "1d",
    "6h": "1w",
    "8h": "1w",
    "12h": "1w",
    "1d": "1w",
    "1w": "1M",
    "1M": None,
}


def resolve_higher_timeframe(current_timeframe: str, override: str = "") -> str | None:
    """Return a configured higher timeframe without introducing a router."""
    chosen = (override or "").strip()
    if chosen:
        return None if chosen == current_timeframe else chosen
    return _DEFAULT_HIGHER_TIMEFRAME.get(current_timeframe)


def disabled_higher_timeframe_context(
    current_timeframe: str,
    *,
    reason: str = "disabled_by_setting",
) -> dict[str, Any]:
    return {
        "status": "disabled",
        "timeframe": None,
        "current_timeframe": current_timeframe,
        "reason": reason,
        "frame": None,
        "metadata": {},
    }


def fetch_higher_timeframe_context(
    *,
    source: DataSource | None,
    source_kind: str,
    symbol: str,
    current_timeframe: str,
    bar_count: int = 60,
    override: str = "",
    now_ms: int | None = None,
    resolved_identity: ResolvedInstrumentIdentity | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch one optional background snapshot without changing chart subscription.

    Failure is intentionally non-fatal.  PA continues on the current timeframe
    and receives an explicit unavailable marker rather than fabricated HTF text.
    """
    target = resolve_higher_timeframe(current_timeframe, override)
    if target is None:
        return disabled_higher_timeframe_context(
            current_timeframe,
            reason="no_higher_timeframe_mapping",
        )
    if source is None:
        return {
            "status": "unavailable",
            "timeframe": target,
            "current_timeframe": current_timeframe,
            "reason": "data_source_unavailable",
            "frame": None,
            "metadata": {},
        }
    identity = ResolvedInstrumentIdentity.from_value(resolved_identity)
    try:
        supported = set(source.supported_timeframes())
        if target not in supported:
            return {
                "status": "unavailable",
                "timeframe": target,
                "current_timeframe": current_timeframe,
                "reason": "source_does_not_support_higher_timeframe",
                "frame": None,
                "metadata": {},
            }
        usable_count = max(20, min(int(bar_count), 300))
        if identity is not None:
            raw = source.fetch_snapshot_for_identity(
                identity,
                target,
                usable_count + INDICATOR_WARMUP_BARS + 5,
            )
        else:
            raw = source.fetch_snapshot_for(
                symbol,
                target,
                usable_count + INDICATOR_WARMUP_BARS + 5,
            )
        metadata = volume_metadata_for_source(
            source_kind,
            source,
            identity=identity,
            symbol=symbol,
        )
        frame = build_display_frame(
            raw,
            usable_count,
            symbol,
            target,
            now_ms=now_ms,
            data_metadata=metadata,
        )
        if frame is None:
            return {
                "status": "unavailable",
                "timeframe": target,
                "current_timeframe": current_timeframe,
                "reason": "insufficient_closed_higher_timeframe_bars",
                "frame": None,
                "metadata": metadata,
            }
        validation = validate_analysis_frame(
            frame,
            minimum_bars=20,
            now_ms=now_ms,
            expected_identity=identity,
        )
        if not validation.ok:
            return {
                "status": "unavailable",
                "timeframe": target,
                "current_timeframe": current_timeframe,
                "reason": validation.failed_check or "invalid_higher_timeframe_frame",
                "frame": None,
                "metadata": metadata,
            }
        return {
            "status": "available",
            "timeframe": target,
            "current_timeframe": current_timeframe,
            "reason": None,
            "frame": frame,
            "metadata": metadata,
            "source_identity": identity.to_dict() if identity is not None else None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "Higher timeframe fetch unavailable for %s %s -> %s: %s",
            symbol,
            current_timeframe,
            target,
            exc,
        )
        return {
            "status": "unavailable",
            "timeframe": target,
            "current_timeframe": current_timeframe,
            "reason": "higher_timeframe_fetch_failed",
            "frame": None,
            "metadata": {},
        }
