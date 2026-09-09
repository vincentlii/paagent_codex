"""Helpers for keeping optional Wyckoff VPA fields behind one switch."""
from __future__ import annotations

import copy
import re
from typing import Any

from pa_agent.ai.kline_references import K_REFERENCE_RE, parse_k_bar_range_bounds

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


def price_only_history(value: Any) -> Any:
    """Do not relabel a prior joint judgment as independent PA evidence.

    Joint rationales can contain VPA in ordinary decision fields. Keep the prior
    Stage 1 separately; stripping a few keys cannot safely unmix that judgment.
    The stored record itself is never edited.
    """
    if isinstance(value, dict):
        basis = value.get("analysis_basis") or {}
        if (value.get("protocol_version") == "pa_vpa_stage2_draft_v1"
                or isinstance(basis, dict) and basis.get("protocol_version") == "pa_vpa_stage2_draft_v1"):
            return {}
    return strip_vpa_fields(value or {})


def wrap_vpa_prompt_block(content: str) -> str:
    if not content:
        return ""
    return f"{VPA_PROMPT_START}\n{content.strip()}\n{VPA_PROMPT_END}"


def strip_vpa_prompt_sections(text: str) -> str:
    """Remove VPA prompt sections inserted by this package."""
    if not isinstance(text, str) or not text:
        return text
    return _VPA_PROMPT_RE.sub("\n", text)


def materialize_vpa_report_observation_ranges(
    report_obj: dict[str, Any],
    evidence_catalog: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project report observation ranges from cited immutable evidence IDs.

    ``bar_range`` is reproducible citation metadata, so the program owns it.
    Unknown IDs remain untouched and are rejected by the contract validator.
    """
    out = copy.deepcopy(report_obj)
    catalog = evidence_catalog if isinstance(evidence_catalog, dict) else {}
    if not isinstance(out, dict):
        return out
    evidence = {
        str(item.get("evidence_id")): item
        for item in catalog.get("evidence", [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    for observation in out.get("observations", []):
        if not isinstance(observation, dict):
            continue
        bounds = [
            parsed
            for evidence_id in [
                *observation.get("evidence_ids", []),
                *observation.get("auto_bound_evidence_ids", []),
            ]
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


def materialize_vpa_reference_bindings(
    report_obj: dict[str, Any],
    evidence_catalog: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Materialize unique K# bindings without mutating declared citations.

    This is citation normalization, not interpretation. References already
    covered by a cited range are unchanged; unknown or ambiguous references
    remain unbound so the validator can reject them.
    """
    out = copy.deepcopy(report_obj)
    catalog = evidence_catalog if isinstance(evidence_catalog, dict) else {}
    exact_by_bar: dict[int, list[str]] = {}
    ranges_by_id: dict[str, tuple[int, int]] = {}
    for item in catalog.get("evidence", []):
        if not isinstance(item, dict) or not item.get("evidence_id"):
            continue
        evidence_id = str(item["evidence_id"])
        bounds = _range_bounds(item.get("bar_range"))
        if bounds is None:
            continue
        ranges_by_id[evidence_id] = bounds
        if bounds[0] == bounds[1]:
            exact_by_bar.setdefault(bounds[0], []).append(evidence_id)

    targets: list[tuple[str, dict[str, Any], str]] = []
    for index, observation in enumerate(out.get("observations", [])):
        if isinstance(observation, dict):
            targets.append(
                (f"vpa_report.observations[{index}]", observation, "interpretation")
            )
    for index, assessment in enumerate(out.get("hypothesis_assessments", [])):
        if isinstance(assessment, dict):
            targets.append(
                (f"vpa_report.hypothesis_assessments[{index}]", assessment, "reason")
            )

    events: list[dict[str, Any]] = []
    for path, item, text_key in targets:
        declared = [str(value) for value in item.get("evidence_ids", [])]
        auto_bound = [
            str(value) for value in item.get("auto_bound_evidence_ids", [])
        ]
        before = list(auto_bound)
        for claim in item.get("sequence_claims", []):
            if not isinstance(claim, dict):
                continue
            for key in ("earlier_evidence_id", "later_evidence_id"):
                evidence_id = str(claim.get(key) or "")
                if (
                    evidence_id in ranges_by_id
                    and evidence_id not in declared
                    and evidence_id not in auto_bound
                ):
                    auto_bound.append(evidence_id)
        covered: set[int] = set()
        for evidence_id in [*declared, *auto_bound]:
            bounds = ranges_by_id.get(evidence_id)
            if bounds is not None:
                covered.update(range(bounds[0], bounds[1] + 1))
        for match in K_REFERENCE_RE.finditer(str(item.get(text_key) or "")):
            seq = int(match.group(1))
            if seq in covered:
                continue
            candidates = exact_by_bar.get(seq, [])
            if len(candidates) != 1:
                continue
            evidence_id = candidates[0]
            if evidence_id not in declared and evidence_id not in auto_bound:
                auto_bound.append(evidence_id)
            covered.add(seq)
        if auto_bound == before:
            continue
        item["auto_bound_evidence_ids"] = auto_bound
        events.append(
            {
                "path": f"{path}.auto_bound_evidence_ids",
                "before": before,
                "after": list(auto_bound),
                "reason": "bound_explicit_model_reference",
            }
        )
    return out, events


def _range_bounds(value: object) -> tuple[int, int] | None:
    return parse_k_bar_range_bounds(value)
