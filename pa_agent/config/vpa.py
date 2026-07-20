"""Shared VPA mode helpers.

The legacy ``vpa_enabled`` setting remains readable so existing settings files
continue to work.  New code must resolve the mode through this module instead
of interpreting the legacy boolean independently.
"""
from __future__ import annotations

from typing import Literal

VpaMode = Literal["off", "shadow", "soft"]
_VALID_VPA_MODES = frozenset(("off", "shadow", "soft"))
VPA_PAIR_IDENTITY_VERSION = "initial_stage1_requests_v1"


def resolve_vpa_mode(prompt_settings: object | None) -> VpaMode:
    """Return the effective VPA mode with a backward-compatible fallback."""
    raw_mode = getattr(prompt_settings, "vpa_mode", None)
    if raw_mode in _VALID_VPA_MODES:
        return raw_mode
    return "shadow" if bool(getattr(prompt_settings, "vpa_enabled", False)) else "off"


def vpa_is_enabled(prompt_settings: object | None) -> bool:
    """Whether VPA fields and prompts belong to this analysis path."""
    return resolve_vpa_mode(prompt_settings) != "off"
