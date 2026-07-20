"""Helpers for keeping optional Wyckoff VPA fields behind one switch."""
from __future__ import annotations

import copy
import re
from typing import Any

from pa_agent.ai.kline_references import parse_k_bar_range_bounds

VPA_KEY_PREFIXES = ("vpa_", "wyckoff_")
VPA_FEATURE_SOURCES = frozenset(
    {
        "vpa_context",
        "vpa_features",
        "vpa_measurements",
        "wyckoff_vpa",
    }
)

VPA_PROMPT_START = "[[VPA_BLOCK_START]]"
VPA_PROMPT_END = "[[VPA_BLOCK_END]]"
_VPA_PROMPT_RE = re.compile(
    rf"\n?{re.escape(VPA_PROMPT_START)}.*?{re.escape(VPA_PROMPT_END)}\n?",
    re.DOTALL,
)
def is_vpa_key(key: object) -> bool:
    text = str(key or "").strip().lower()
    return any(text.startswith(prefix) for prefix in VPA_KEY_PREFIXES)


def strip_vpa_fields(value: Any) -> Any:
    """Return a copy of *value* with VPA/Wyckoff keys and feature sources removed."""
    if isinstance(value, dict):
        return {
            key: strip_vpa_fields(item)
            for key, item in value.items()
            if not is_vpa_key(key)
        }
    if isinstance(value, list):
        out: list[Any] = []
        for item in value:
            if isinstance(item, str) and item.strip().lower() in VPA_FEATURE_SOURCES:
                continue
            out.append(strip_vpa_fields(item))
        return out
    if isinstance(value, tuple):
        return tuple(strip_vpa_fields(list(value)))
    return copy.deepcopy(value)


def wrap_vpa_prompt_block(content: str) -> str:
    if not content:
        return ""
    return f"{VPA_PROMPT_START}\n{content.strip()}\n{VPA_PROMPT_END}"


def strip_vpa_prompt_sections(text: str) -> str:
    """Remove VPA prompt sections inserted by this package."""
    if not isinstance(text, str) or not text:
        return text
    return _VPA_PROMPT_RE.sub("\n", text)


def materialize_vpa_observation_ranges(
    stage1_obj: dict[str, Any],
    evidence_catalog: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project observation ranges from cited immutable evidence IDs.

    ``bar_range`` is reproducible citation metadata, so the program owns it.
    Unknown IDs remain untouched and are rejected by the contract validator.
    """
    out = copy.deepcopy(stage1_obj)
    context = out.get("vpa_context")
    catalog = evidence_catalog if isinstance(evidence_catalog, dict) else {}
    if not isinstance(context, dict):
        return out
    evidence = {
        str(item.get("evidence_id")): item
        for item in catalog.get("evidence", [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    for observation in context.get("observations", []):
        if not isinstance(observation, dict):
            continue
        bounds = [
            parsed
            for evidence_id in observation.get("evidence_ids", [])
            for parsed in (
                _range_bounds((evidence.get(str(evidence_id)) or {}).get("bar_range")),
            )
            if parsed is not None
        ]
        if not bounds:
            continue
        newest = min(item[0] for item in bounds)
        oldest = max(item[1] for item in bounds)
        observation["bar_range"] = (
            f"K{oldest}-K{newest}" if oldest != newest else f"K{oldest}"
        )
    return out


def _range_bounds(value: object) -> tuple[int, int] | None:
    return parse_k_bar_range_bounds(value)
