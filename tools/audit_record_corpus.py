"""Read-only migration and replay audit for a directory of analysis records."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pa_agent.ai.vpa_measurements import validate_frozen_vpa_catalog  # noqa: E402
from pa_agent.experiments.replay import frozen_input_from_record  # noqa: E402
from pa_agent.records.codec import load_record  # noqa: E402


def audit_record_corpus(directory: Path) -> dict[str, object]:
    files = sorted(directory.glob("*.json"))
    issue_counts: Counter[str] = Counter()
    invalid: list[dict[str, str]] = []
    non_replayable: list[dict[str, object]] = []
    catalog_validated = 0
    replay_validated = 0

    for path in files:
        try:
            loaded = load_record(path)
        except Exception as exc:  # CLI must inventory every corrupt artifact.
            invalid.append({"path": path.name, "error": type(exc).__name__})
            continue
        for issue in loaded.migration_issues:
            issue_counts[issue.code] += 1
        if not loaded.replayable:
            non_replayable.append(
                {
                    "path": path.name,
                    "issues": [
                        issue.code
                        for issue in loaded.migration_issues
                        if issue.severity == "error"
                    ],
                }
            )
            continue
        if not isinstance(loaded.record.vpa_catalog, dict):
            continue
        try:
            frame, _htf, catalog = frozen_input_from_record(loaded.record)
            validate_frozen_vpa_catalog(frame, catalog)
        except (TypeError, ValueError) as exc:
            non_replayable.append(
                {
                    "path": path.name,
                    "issues": [str(exc)],
                }
            )
            continue
        catalog_validated += 1
        replay_validated += 1

    return {
        "directory": str(directory.resolve()),
        "record_count": len(files),
        "loaded_count": len(files) - len(invalid),
        "invalid_records": invalid,
        "migration_issue_counts": dict(sorted(issue_counts.items())),
        "catalog_validated_count": catalog_validated,
        "replay_validated_count": replay_validated,
        "non_replayable_records": non_replayable,
        "passed": not invalid and not non_replayable,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("records_dir", type=Path)
    args = parser.parse_args()
    report = audit_record_corpus(args.records_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
