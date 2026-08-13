"""Compatibility wrapper for one offline PA/VPA A/A/B/B experiment."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pa_agent.experiments.cli import single_main  # noqa: E402


def main() -> int:
    return single_main()


if __name__ == "__main__":
    raise SystemExit(main())
