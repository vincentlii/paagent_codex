"""Validation retry policy: which errors may retry and immutable field guards."""
from __future__ import annotations

from typing import Any, Literal

StageName = Literal["stage1", "stage2"]

# Format-like invalid field prefixes — safe to retry without changing trade thesis.
_FORMAT_PREFIXES: tuple[str, ...] = (
    "gate_trace",
    "decision_trace",
    "bar_by_bar_summary",
    "bar_range",
    "incremental",
    "next_bar_prediction",
    "next_cycle_prediction",
    "decision.reasoning",
)

# Semantic errors that must not be hidden by a formatting retry.
_NO_RETRY_PREFIXES: tuple[str, ...] = (
    "trace:§14",
    "s2:order_direction",
)

_SEMANTIC_STATE_FIELDS: frozenset[str] = frozenset(
    {
        "spike_stage",
        "market_phase",
        "transition_risk",
    }
)


IMMUTABLE_FIELDS: dict[StageName, tuple[str, ...]] = {
    "stage1": (
        "direction",
        "cycle_position",
        "gate_result",
        "pa_evidence.fact_refs",
        "pa_evidence.theory_rule_ids",
        "pa_evidence.spike_analysis",
    ),
    "stage2": (
        "decision.order_type",
        "decision.order_direction",
        "decision.entry_price",
        "decision.stop_loss_price",
        "decision.take_profit_price",
        "decision.take_profit_price_2",
    ),
}


def max_retries_for_category(category: str, settings: Any) -> int:
    """Return allowed retry count for a validation category."""
    if category == "e":
        return 0
    if not getattr(settings, "retry_enabled", True):
        return 0
    base = int(getattr(settings, "retry_max", 3) or 0)
    if category in ("a", "b", "d"):
        return base
    if category == "c":
        return min(base, int(getattr(settings, "retry_max_semantic", 1) or 1))
    return 0


def should_retry(
    category: str,
    invalid_fields: list[str],
    missing_fields: list[str],
    *,
    attempt: int,
    settings: Any,
) -> bool:
    """Whether another API call is warranted."""
    if category == "e":
        return False
    if attempt >= max_retries_for_category(category, settings):
        return False
    if category in ("a", "b", "d"):
        return True
    if category != "c":
        return False
    fields = list(invalid_fields or []) + list(missing_fields or [])
    if not fields:
        return False
    if any(_starts_any(f, _NO_RETRY_PREFIXES) for f in fields):
        return False
    if any(_starts_any(f, _FORMAT_PREFIXES) for f in fields):
        return True
    if any(str(field) in _SEMANTIC_STATE_FIELDS for field in fields):
        return attempt < int(getattr(settings, "retry_max_semantic", 1) or 1)
    if any(
        f.startswith(
            (
                "s1:",
                "s2:",
                "gate:",
                "trace:",
                "breakout_price:",
                "signal_chain:",
                "metrics:",
            )
        )
        for f in fields
    ):
        # Default: one semantic retry if enabled
        return attempt < int(getattr(settings, "retry_max_semantic", 1) or 1)
    return False


def _starts_any(field: str, prefixes: tuple[str, ...]) -> bool:
    text = str(field or "")
    return any(text.startswith(p) or p in text for p in prefixes)


def _get_path(obj: dict[str, Any], path: str) -> Any:
    cur: Any = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def detect_immutable_field_changes(
    stage: StageName,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    *,
    feedback_mentioned: set[str] | None = None,
) -> list[str]:
    """Report retry changes to decision fields unrelated to the validation feedback."""
    if not before or not isinstance(after, dict):
        return []
    mentioned = feedback_mentioned or set()
    violations: list[str] = []
    for key in IMMUTABLE_FIELDS.get(stage, ()):
        if key in mentioned:
            continue
        b = _get_path(before, key)
        a = _get_path(after, key)
        if b is not None and a is not None and str(b) != str(a):
            violations.append(f"{key}: {b!r} → {a!r}")

    return violations


def extract_feedback_targets(invalid_fields: list[str], missing_fields: list[str]) -> set[str]:
    """Map error lines to field paths mentioned in retry feedback."""
    targets: set[str] = set()
    for raw in list(missing_fields or []) + list(invalid_fields or []):
        text = str(raw)
        if "order_type" in text:
            targets.add("decision.order_type")
        if (
            "unbounded new-high/new-low" in text
            or "unbounded negated new-high/new-low" in text
            or "must cite PA.WINDOW instead of restating" in text
        ):
            targets.add("pa_evidence.fact_refs")
        for key in (
            "direction",
            "cycle_position",
            "gate_result",
            "pa_evidence.fact_refs",
            "pa_evidence.theory_rule_ids",
            "pa_evidence.spike_analysis",
            "decision.order_type",
            "decision.order_direction",
            "decision.entry_price",
            "decision.stop_loss_price",
            "decision.take_profit_price",
            "decision.take_profit_price_2",
            "next_bar_prediction",
            "next_cycle_prediction",
        ):
            if key in text:
                targets.add(key)
    return targets
