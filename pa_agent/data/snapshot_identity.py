"""Canonical identity for persisted current/HTF market snapshots."""
from __future__ import annotations

import hashlib
import json
from typing import Any

SNAPSHOT_HASH_ALGORITHM = "v5-canonical-json"


def _canonical_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical: list[dict[str, Any]] = []
    for bar in bars:
        payload = dict(bar)
        if payload.get("volume_state") is None:
            payload.pop("volume_state", None)
        canonical.append(payload)
    return canonical


def canonical_market_snapshot_hash(
    *,
    symbol: str,
    timeframe: str,
    kline_data: list[dict[str, Any]],
    snapshot_context: dict[str, Any] | None,
    htf_context: dict[str, Any] | None,
    htf_kline_data: list[dict[str, Any]],
) -> str:
    payload = {
        "algorithm": SNAPSHOT_HASH_ALGORITHM,
        "symbol": symbol,
        "timeframe": timeframe,
        "kline_data": _canonical_bars(kline_data),
        "snapshot_context": snapshot_context or {},
        "htf_context": htf_context,
        "htf_kline_data": _canonical_bars(htf_kline_data),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
