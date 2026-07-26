"""PA Agent utility package."""

from pa_agent.util.logging import configure_logging, update_api_key
from pa_agent.util.threading import CancelToken, OrchestratorEvent

__all__ = ["CancelToken", "OrchestratorEvent", "EventBus", "configure_logging", "update_api_key"]


def __getattr__(name: str):
    """Keep the desktop EventBus export without loading Qt in headless paths."""
    if name == "EventBus":
        from pa_agent.util.event_bus import EventBus

        return EventBus
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
