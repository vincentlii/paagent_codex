"""Compact repeated validation warnings without losing their audit detail."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable


def group_warning_messages(
    items: Iterable[tuple[str, str]],
) -> list[dict[str, object]]:
    """Group messages by warning code while retaining every underlying finding."""
    grouped: dict[str, list[str]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    order: list[str] = []
    for code, message in items:
        if code not in grouped:
            order.append(code)
        counts[code] += 1
        if message not in grouped[code]:
            grouped[code].append(message)

    payloads: list[dict[str, object]] = []
    for code in order:
        findings = grouped[code]
        preview = "; ".join(findings[:3])
        if len(findings) > 3:
            preview += f"; ... ({len(findings) - 3} more)"
        payloads.append(
            {
                "code": code,
                "occurrence_count": counts[code],
                "message": preview,
                "findings": findings,
            }
        )
    return payloads
