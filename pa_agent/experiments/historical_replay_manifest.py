"""Integrity checks for the preregistered 20-case Windows failure cohort."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from importlib.resources import files
from pathlib import Path
from typing import Any

_RESOURCE = "fixtures/historical_failures_manifest.json"


def load_historical_failure_manifest() -> dict[str, Any]:
    resource = files("pa_agent.experiments").joinpath(_RESOURCE)
    raw = json.loads(resource.read_text(encoding="utf-8"))
    cases = raw.get("cases") or []
    if raw.get("schema_version") != 1 or raw.get("case_count") != 20:
        raise ValueError("historical failure manifest identity is invalid")
    if not isinstance(cases, list) or len(cases) != 20:
        raise ValueError("historical failure manifest must contain exactly 20 cases")
    names = [str(item.get("record") or "") for item in cases if isinstance(item, dict)]
    if len(names) != 20 or len(set(names)) != 20:
        raise ValueError("historical failure manifest record identities are invalid")
    if not isinstance(raw.get("baseline_status_counts"), dict) or not isinstance(
        raw.get("baseline_audit_code_counts"), dict
    ):
        raise ValueError("historical failure baseline taxonomy is missing")
    return raw


def verify_historical_failure_records(record_root: Path) -> list[Path]:
    """Resolve and hash every preregistered record; never silently skip a case."""
    root = Path(record_root).expanduser().resolve()
    manifest = load_historical_failure_manifest()
    verified: list[Path] = []
    status_counts: Counter[str] = Counter()
    audit_counts: Counter[str] = Counter()
    for case in manifest["cases"]:
        path = (root / str(case["record"])).resolve()
        if path.parent != root or not path.is_file():
            raise FileNotFoundError(f"historical replay record unavailable: {case['record']}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != str(case["sha256"]):
            raise ValueError(f"historical replay record digest mismatch: {case['record']}")
        record = json.loads(path.read_text(encoding="utf-8"))
        status = str((record.get("run_outcome") or {}).get("status") or "unknown")
        status_counts[status] += 1
        for event in record.get("validation_audit") or []:
            if isinstance(event, dict):
                audit_counts[str(event.get("code") or "unknown")] += 1
        verified.append(path)
    if dict(sorted(status_counts.items())) != manifest["baseline_status_counts"]:
        raise ValueError("historical replay status taxonomy mismatch")
    if dict(sorted(audit_counts.items())) != manifest["baseline_audit_code_counts"]:
        raise ValueError("historical replay audit taxonomy mismatch")
    return verified
