"""Resolve per-role model thinking settings without changing analysis semantics."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pa_agent.config.settings import AIProviderSettings, ReasoningEffort


@dataclass(frozen=True)
class ResolvedReasoningProfile:
    """Effective request controls used for one logical model role."""

    role: str
    thinking: bool
    reasoning_effort: ReasoningEffort
    source: Literal["role", "global", "default"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "thinking": self.thinking,
            "reasoning_effort": self.reasoning_effort,
            "source": self.source,
        }


def resolve_reasoning_profile(
    provider: AIProviderSettings | Any | None,
    role: str,
) -> ResolvedReasoningProfile:
    """Resolve *role* with explicit override, then global, then safe defaults."""
    role_name = str(role or "").strip() or "unknown"
    if provider is None:
        return ResolvedReasoningProfile(
            role=role_name,
            thinking=True,
            reasoning_effort="high",
            source="default",
        )

    global_thinking = bool(getattr(provider, "thinking", True))
    global_effort = _effort(getattr(provider, "reasoning_effort", "high"))
    profiles = getattr(provider, "reasoning_profiles", None)
    override = getattr(profiles, role_name, None) if profiles is not None else None
    has_override = override is not None and (
        getattr(override, "thinking", None) is not None
        or getattr(override, "reasoning_effort", None) is not None
    )
    if not has_override:
        return ResolvedReasoningProfile(
            role=role_name,
            thinking=global_thinking,
            reasoning_effort=global_effort,
            source="global",
        )
    return ResolvedReasoningProfile(
        role=role_name,
        thinking=(
            bool(override.thinking)
            if override.thinking is not None
            else global_thinking
        ),
        reasoning_effort=(
            _effort(override.reasoning_effort)
            if override.reasoning_effort is not None
            else global_effort
        ),
        source="role",
    )


def _effort(value: Any) -> ReasoningEffort:
    text = str(value or "high").strip().lower()
    if text in {"low", "medium", "high", "max"}:
        return text  # type: ignore[return-value]
    return "high"
