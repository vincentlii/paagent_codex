"""Create a compact replay inventory without copying prompts or market payloads."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pa_agent.quality.replay_manifest import write_replay_manifest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build PA Agent replay manifest")
    parser.add_argument("records_dir", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    manifest = write_replay_manifest(args.records_dir, args.output)
    print(
        json.dumps(
            {
                "case_count": manifest["case_count"],
                "invalid_count": len(manifest["invalid_records"]),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if not manifest["invalid_records"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

