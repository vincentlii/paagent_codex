"""Canonical identity for persisted current/HTF market snapshots."""
from __future__ import annotations

import hashlib
import json
from typing import Any

SNAPSHOT_HASH_ALGORITHM = "v5-canonical-json"


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
        "kline_data": kline_data,
        "snapshot_context": snapshot_context or {},
        "htf_context": htf_context,
        "htf_kline_data": htf_kline_data,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
