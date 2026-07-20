"""Shared parsing rules for explicit K/H/L references in model text."""
from __future__ import annotations

import re

# Chinese characters are Unicode word characters, so ``\b`` would miss
# natural phrases such as ``K1区间``. Restrict only ASCII identifier context.
# A dot is also identifier context here because PA fact IDs use dotted names.
K_REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9_.])K\s*(\d+)(?![A-Za-z0-9_.])",
    re.IGNORECASE,
)
K_RANGE_RE = re.compile(
    r"(?<![A-Za-z0-9_.])K\s*(\d+)\s*[-\u2013\u2014\uFF0D]\s*K?\s*(\d+)"
    r"(?![A-Za-z0-9_.])",
    re.IGNORECASE,
)
K_BAR_RANGE_RE = re.compile(
    r"^\s*K\s*(?P<first>\d+)"
    r"(?:\s*[-\u2013\u2014\uFF0D]\s*K?\s*(?P<second>\d+))?\s*$",
    re.IGNORECASE,
)
HTF_REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9_.])HTF\.H\s*(\d+)(?![A-Za-z0-9_.])",
    re.IGNORECASE,
)
HTF_RANGE_RE = re.compile(
    r"(?<![A-Za-z0-9_.])HTF\.H\s*(\d+)\s*[-\u2013\u2014\uFF0D]\s*"
    r"(?:(?:HTF\.)?H\s*)?(\d+)(?![A-Za-z0-9_.])",
    re.IGNORECASE,
)
REFERENCE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:HTF\.H|K|H|L)\s*\d+(?![A-Za-z0-9_.])",
    re.IGNORECASE,
)


def parse_k_bar_range_bounds(value: object) -> tuple[int, int] | None:
    """Return inclusive newest/oldest bounds for one structured K range."""
    match = K_BAR_RANGE_RE.fullmatch(str(value or ""))
    if match is None:
        return None
    first = int(match.group("first"))
    second = int(match.group("second") or first)
    return min(first, second), max(first, second)


def extract_htf_reference_seqs(value: object) -> set[int]:
    """Extract explicit HTF bar IDs, including compact range endpoints."""
    text = str(value or "")
    references = {
        int(match.group(1)) for match in HTF_REFERENCE_RE.finditer(text)
    }
    for match in HTF_RANGE_RE.finditer(text):
        references.update((int(match.group(1)), int(match.group(2))))
    return references
