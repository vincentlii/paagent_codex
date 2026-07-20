"""Build a compact, deterministic replay inventory from analysis records."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


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
    identity = {
        "symbol": meta.get("symbol"),
        "timeframe": meta.get("timeframe"),
        "kline_data": record.get("kline_data") or [],
        "htf_context": record.get("htf_context"),
    }
    return _canonical_hash(identity)


def build_replay_manifest(paths: Iterable[str | Path]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    run_ids: set[str] = set()
    duplicate_run_ids: set[str] = set()

    for raw_path in sorted((Path(path) for path in paths), key=lambda item: item.name):
        try:
            record = json.loads(raw_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
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
        sidecar = record.get("vpa_sidecar")
        cases.append(
            {
                "run_id": run_id,
                "symbol": str(meta.get("symbol") or ""),
                "timeframe": str(meta.get("timeframe") or ""),
                "snapshot_id": _snapshot_identity(record),
                "record_sha256": _canonical_hash(record),
                "has_htf": bool(record.get("htf_context") or record.get("htf_text")),
                "has_vpa": isinstance(sidecar, dict) and bool(sidecar),
                "outcome": "internal_error" if record.get("exception") else "completed",
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
