"""Canonical identities used only by the multi-asset runtime."""

from __future__ import annotations


def canonical_timeframe(value: object) -> str:
    """Normalize timeframe spelling without merging minutes and months."""
    text = str(value or "").strip()
    if text.endswith("M"):
        return f"{text[:-1].lower()}M"
    return text.lower()
