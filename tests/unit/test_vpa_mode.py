from __future__ import annotations

from pa_agent.config.settings import PromptSettings
from pa_agent.config.vpa import resolve_vpa_mode, vpa_is_enabled
from pa_agent.ai.json_validator import JsonValidator
from pa_agent.config.settings import Settings


def test_vpa_mode_prefers_explicit_mode_over_legacy_boolean() -> None:
    settings = PromptSettings(vpa_mode="off", vpa_enabled=True)

    assert resolve_vpa_mode(settings) == "off"
    assert vpa_is_enabled(settings) is False


def test_vpa_mode_migrates_legacy_enabled_to_shadow() -> None:
    settings = PromptSettings(vpa_enabled=True)

    assert resolve_vpa_mode(settings) == "shadow"
    assert vpa_is_enabled(settings) is True


def test_shadow_defaults_to_full_prompt_ab_design() -> None:
    settings = PromptSettings(vpa_mode="shadow")

    assert settings.vpa_shadow_design == "full_prompt_ab"


def test_validator_observes_runtime_mode_changes() -> None:
    settings = Settings(prompt=PromptSettings(vpa_mode="off"))
    validator = JsonValidator(settings)
    source = {"cycle_position": "normal_channel", "vpa_context": {"summary": "old"}}

    assert "vpa_context" not in validator.normalize_parsed("stage1", source)
    settings.prompt.vpa_mode = "shadow"
    assert "vpa_context" in validator.normalize_parsed("stage1", source)
