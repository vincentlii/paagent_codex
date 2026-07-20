"""E2E fixtures that keep modal error reporting observable and non-blocking."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def validation_dialog_spy(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the modal validation dialog so failed analyses cannot hang CI."""
    from pa_agent.gui import main_window

    spy = MagicMock(name="show_validation_debug_dialog")
    monkeypatch.setattr(main_window, "show_validation_debug_dialog", spy)
    return spy
