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


@pytest.fixture(autouse=True)
def disable_external_post_order_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GUI smoke tests do not own production trade history or notifications."""
    from pa_agent.gui import main_window

    monkeypatch.setattr(
        main_window.MainWindow,
        "_spawn_post_order_followup",
        lambda _self, _inner, _decision: None,
    )
