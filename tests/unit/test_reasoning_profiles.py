from __future__ import annotations

from pa_agent.ai.reasoning_profiles import resolve_reasoning_profile
from pa_agent.config.settings import (
    AIProviderSettings,
    ReasoningProfileOverride,
    Settings,
)


def test_unconfigured_role_inherits_global_provider_settings() -> None:
    provider = AIProviderSettings(thinking=True, reasoning_effort="max")

    resolved = resolve_reasoning_profile(provider, "stage2")

    assert resolved.role == "stage2"
    assert resolved.thinking is True
    assert resolved.reasoning_effort == "max"
    assert resolved.source == "global"


def test_role_override_can_disable_thinking_without_changing_global_default() -> None:
    provider = AIProviderSettings(
        thinking=True,
        reasoning_effort="high",
        reasoning_profiles={
            "stage2": ReasoningProfileOverride(thinking=False),
        },
    )

    stage1 = resolve_reasoning_profile(provider, "stage1")
    stage2 = resolve_reasoning_profile(provider, "stage2")

    assert stage1.thinking is True
    assert stage1.reasoning_effort == "high"
    assert stage1.source == "global"
    assert stage2.thinking is False
    assert stage2.reasoning_effort == "high"
    assert stage2.source == "role"


def test_settings_round_trip_keeps_role_overrides(tmp_path) -> None:
    from pa_agent.config.settings import load_settings, save_settings

    path = tmp_path / "settings.json"
    settings = Settings()
    settings.provider.reasoning_profiles.stage1 = ReasoningProfileOverride(
        thinking=True,
        reasoning_effort="max",
    )
    settings.provider.reasoning_profiles.vpa_evidence = ReasoningProfileOverride(
        thinking=False,
    )

    save_settings(settings, path)
    loaded = load_settings(path)

    assert loaded.provider.reasoning_profiles.stage1.reasoning_effort == "max"
    assert loaded.provider.reasoning_profiles.vpa_evidence.thinking is False
