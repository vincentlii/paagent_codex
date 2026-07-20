"""Record writer adapter for nested and offline orchestrator runs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SuppressedPersistResult:
    ok: bool = True
    audit_ok: bool = True
    path: str = ""
    audit_error: str | None = None


class SuppressedRecordWriter:
    """Acknowledge writes while a parent runner owns final persistence."""

    def save_full(self, _record: object) -> SuppressedPersistResult:
        return SuppressedPersistResult()

    def save_partial(self, _record: object, _reason: str) -> SuppressedPersistResult:
        return SuppressedPersistResult()
