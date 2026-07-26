"""Deterministic parsing helpers for model-proposed structure levels."""
from __future__ import annotations

import re

_PRICE_LEVEL_RE = re.compile(
    r"^\s*(?P<first>\d+(?:\.\d+)?)"
    r"(?:\s*[-~\uFF0D\u2014\u5230\u81F3]\s*(?P<second>\d+(?:\.\d+)?))?\s*$"
)


def parse_price_level_bounds(raw: object) -> tuple[float, float] | None:
    """Return sorted positive bounds for one strict price or price range."""
    match = _PRICE_LEVEL_RE.fullmatch(str(raw or ""))
    if match is None:
        return None
    first = float(match.group("first"))
    second = float(match.group("second") or first)
    if first <= 0 or second <= 0:
        return None
    return tuple(sorted((first, second)))


def price_level_midpoint(raw: object) -> float | None:
    """Return the reproducible midpoint used only for canonical ordering."""
    bounds = parse_price_level_bounds(raw)
    if bounds is None:
        return None
    return (bounds[0] + bounds[1]) / 2.0
