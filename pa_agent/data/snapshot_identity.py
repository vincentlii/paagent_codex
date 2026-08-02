"""Canonical identity for persisted current/HTF market snapshots."""
from __future__ import annotations

import hashlib
import json
from typing import Any

LEGACY_SNAPSHOT_HASH_ALGORITHM = "v5-canonical-json"
SNAPSHOT_HASH_ALGORITHM = "v6-market-facts-json"
_SUPPORTED_ALGORITHMS = frozenset(
    {LEGACY_SNAPSHOT_HASH_ALGORITHM, SNAPSHOT_HASH_ALGORITHM}
)
_VOLATILE_CONTEXT_FIELDS = frozenset({"captured_at_ms", "snapshot_ts_local_ms"})


def _canonical_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical: list[dict[str, Any]] = []
    for bar in bars:
        payload = dict(bar)
        if payload.get("volume_state") is None:
            payload.pop("volume_state", None)
        canonical.append(payload)
    return canonical


def _canonical_context(value: Any) -> Any:
    """Remove observation-time metadata that is not a market fact."""
    if isinstance(value, dict):
        return {
            key: _canonical_context(item)
            for key, item in value.items()
            if key not in _VOLATILE_CONTEXT_FIELDS
        }
    if isinstance(value, list):
        return [_canonical_context(item) for item in value]
    return value


def canonical_market_snapshot_hash(
    *,
    symbol: str,
    timeframe: str,
    kline_data: list[dict[str, Any]],
    snapshot_context: dict[str, Any] | None,
    htf_context: dict[str, Any] | None,
    htf_kline_data: list[dict[str, Any]],
    algorithm: str = SNAPSHOT_HASH_ALGORITHM,
) -> str:
    normalized_algorithm = str(algorithm or "").strip()
    if normalized_algorithm not in _SUPPORTED_ALGORITHMS:
        raise ValueError(f"unsupported market snapshot hash algorithm: {algorithm}")
    market_facts_only = normalized_algorithm == SNAPSHOT_HASH_ALGORITHM
    payload = {
        "algorithm": normalized_algorithm,
        "symbol": symbol,
        "timeframe": timeframe,
        "kline_data": _canonical_bars(kline_data),
        "snapshot_context": (
            _canonical_context(snapshot_context or {})
            if market_facts_only
            else snapshot_context or {}
        ),
        "htf_context": (
            _canonical_context(htf_context) if market_facts_only else htf_context
        ),
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
