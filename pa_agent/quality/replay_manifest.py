"""Build a compact, deterministic replay inventory from analysis records."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pa_agent.data.snapshot_identity import (
    LEGACY_SNAPSHOT_HASH_ALGORITHM,
    canonical_market_snapshot_hash,
)
from pa_agent.records.codec import load_record


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _snapshot_identity(record: dict[str, Any]) -> str:
    meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
    experiment = (
        record.get("experiment_context")
        if isinstance(record.get("experiment_context"), dict)
        else {}
    )
    return canonical_market_snapshot_hash(
        symbol=str(meta.get("symbol") or ""),
        timeframe=str(meta.get("timeframe") or ""),
        kline_data=record.get("kline_data") or [],
        snapshot_context=record.get("snapshot_context") or {},
        htf_context=record.get("htf_context"),
        htf_kline_data=record.get("htf_kline_data") or [],
        algorithm=str(
            experiment.get("market_snapshot_hash_algorithm")
            or LEGACY_SNAPSHOT_HASH_ALGORITHM
        ),
    )


def build_replay_manifest(paths: Iterable[str | Path]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    run_ids: set[str] = set()
    duplicate_run_ids: set[str] = set()

    for raw_path in sorted((Path(path) for path in paths), key=lambda item: item.name):
        try:
            loaded = load_record(raw_path)
            model = loaded.record
            record = model.model_dump(mode="json", exclude_none=True)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            invalid.append({"path": raw_path.name, "error": type(exc).__name__})
            continue
        if not isinstance(record, dict):
            invalid.append({"path": raw_path.name, "error": "record_not_object"})
            continue
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        run_id = str(meta.get("run_id") or raw_path.stem)
        if run_id in run_ids:
            duplicate_run_ids.add(run_id)
        run_ids.add(run_id)
        cases.append(
            {
                "run_id": run_id,
                "symbol": str(meta.get("symbol") or ""),
                "timeframe": str(meta.get("timeframe") or ""),
                "snapshot_id": _snapshot_identity(record),
                "record_sha256": _canonical_hash(record),
                "has_htf": bool(record.get("htf_context") or record.get("htf_text")),
                "has_vpa": isinstance(record.get("vpa_catalog"), dict),
                "outcome": str((record.get("run_outcome") or {}).get("status") or (
                    "internal_error" if record.get("exception") else "completed"
                )),
            }
        )

    duplicate_snapshots = sorted(
        snapshot_id
        for snapshot_id in {case["snapshot_id"] for case in cases}
        if sum(case["snapshot_id"] == snapshot_id for case in cases) > 1
    )
    return {
        "schema_version": 1,
        "case_count": len(cases),
        "invalid_records": invalid,
        "duplicate_run_ids": sorted(duplicate_run_ids),
        "duplicate_snapshot_ids": duplicate_snapshots,
        "cases": cases,
    }


def write_replay_manifest(
    records_dir: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    manifest = build_replay_manifest(Path(records_dir).glob("*.json"))
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest
