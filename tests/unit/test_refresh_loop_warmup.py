"""RefreshLoop fetch policy keeps warmup and optional history bounded."""
from __future__ import annotations

from pa_agent.data.refresh_loop import RefreshLoop


def test_refresh_loop_snapshot_count_includes_indicator_warmup() -> None:
    loop = RefreshLoop(object(), 20)
    assert loop._next_fetch_count() == 75


def test_refresh_loop_uses_large_history_only_for_initial_backfill() -> None:
    loop = RefreshLoop(object(), 20, initial_fetch_bars=800)
    assert loop._next_fetch_count() == 800
    merged = loop._snapshot_with_history([])
    assert merged == []
    assert loop._next_fetch_count() == 800


def test_refresh_loop_exposes_pause_control_before_fetching() -> None:
    loop = RefreshLoop(object(), 20)
    loop.set_paused(True)
    assert loop._paused.is_set()
