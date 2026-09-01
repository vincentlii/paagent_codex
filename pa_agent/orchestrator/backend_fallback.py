"""Whole-chain backend fallback without mixing role outputs."""

from __future__ import annotations

from typing import Any

from pa_agent.ai.codex_analysis_backend import FullChainFallbackRequired

_VISIBLE_CALLBACKS = (
    "on_stage1_reasoning",
    "on_stage1_content",
    "on_stage2_reasoning",
    "on_stage2_content",
    "on_stage_prompt",
    "on_stage2_files",
)


class FullChainFallbackOrchestrator:
    """Retry the same frozen cycle from Stage 1 on a cold Direct orchestrator."""

    def __init__(self, *, primary: Any, cold_fallback: Any) -> None:
        self._primary = primary
        self._cold_fallback = cold_fallback

    def submit(self, *args: Any, **kwargs: Any) -> Any:
        on_event = kwargs.get("on_event")
        event_position = None
        if on_event is None and len(args) >= 3:
            event_position = 2
            on_event = args[event_position]
        if on_event is None:
            raise TypeError("submit requires on_event")

        buffered: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []
        primary_args = list(args)
        primary_kwargs = dict(kwargs)

        def buffered_callback(callback: Any) -> Any:
            def capture(*callback_args: Any, **callback_kwargs: Any) -> None:
                buffered.append((callback, callback_args, callback_kwargs))

            return capture

        if event_position is None:
            primary_kwargs["on_event"] = buffered_callback(on_event)
        else:
            primary_args[event_position] = buffered_callback(on_event)
        for name in _VISIBLE_CALLBACKS:
            callback = kwargs.get(name)
            if callback is not None:
                primary_kwargs[name] = buffered_callback(callback)
        try:
            result = self._primary.submit(*primary_args, **primary_kwargs)
        except FullChainFallbackRequired as exc:
            fallback_args = list(args)
            fallback_kwargs = dict(kwargs)
            if event_position is None:
                fallback_kwargs["on_event"] = on_event
            else:
                fallback_args[event_position] = on_event
            fallback_context = {
                "from": "codex_agent",
                "to": "direct",
                "restart_scope": "full_pa_chain",
                "failure_kind": exc.failure.kind,
                "attempts": [dict(attempt) for attempt in exc.attempts],
            }
            fallback_kwargs["backend_fallback_context"] = fallback_context
            result = self._cold_fallback.submit(*fallback_args, **fallback_kwargs)
            runtime = dict(getattr(result, "role_runtime", {}) or {})
            runtime["backend_fallback"] = fallback_context
            result.role_runtime = runtime
            return result
        for callback, callback_args, callback_kwargs in buffered:
            callback(*callback_args, **callback_kwargs)
        return result
