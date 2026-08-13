"""Run the opt-in continuous PA/VPA A/A/B/B research monitor."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pa_agent.experiments.cli import continuous_main  # noqa: E402, I001


if __name__ == "__main__":
    raise SystemExit(continuous_main())
