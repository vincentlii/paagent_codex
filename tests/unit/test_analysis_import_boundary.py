"""Use a fresh interpreter so test collection order cannot mask import cycles."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_orchestrator_import_does_not_eagerly_load_research_service():
    result = subprocess.run(
        [sys.executable, "-B", "-c",
         "import sys; from pa_agent.orchestrator.two_stage import TwoStageOrchestrator; "
         "assert 'pa_agent.experiments.backend_service' not in sys.modules; "
         "from pa_agent.experiments.backend_service import BackendAabbExperimentService"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
