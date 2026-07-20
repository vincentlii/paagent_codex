"""Immutable contracts for frozen market data and its provenance."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

QualitySeverity = Literal["hard_error", "warning", "info"]


@dataclass(frozen=True)
class ResolvedInstrumentIdentity:
    """Provider identity resolved once for every timeframe in one analysis."""

    provider: str
    requested_symbol: str
    resolved_symbol: str
    venue: str = ""
    market: str = ""
    timezone_basis: str = "UTC"
    volume_kind: str = "unknown"
    volume_quality: str = "unknown"

    def __post_init__(self) -> None:
        provider = str(self.provider or "").strip().lower()
        requested = str(self.requested_symbol or "").strip()
        resolved = str(self.resolved_symbol or requested).strip()
        if not provider:
            raise ValueError("resolved instrument identity requires provider")
        if not requested or not resolved:
            raise ValueError("resolved instrument identity requires symbol")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "requested_symbol", requested)
        object.__setattr__(self, "resolved_symbol", resolved)
        object.__setattr__(self, "venue", str(self.venue or "").strip().upper())
        object.__setattr__(self, "market", str(self.market or "").strip().lower())
        object.__setattr__(
            self,
            "timezone_basis",
            str(self.timezone_basis or "UTC").strip() or "UTC",
        )
        object.__setattr__(
            self,
            "volume_kind",
            str(self.volume_kind or "unknown").strip().lower(),
        )
        object.__setattr__(
            self,
            "volume_quality",
            str(self.volume_quality or "unknown").strip().lower(),
        )

    @property
    def key(self) -> tuple[str, str, str]:
        return (
            self.provider.casefold(),
            self.venue.casefold(),
            self.resolved_symbol.casefold(),
        )

    def same_instrument(self, other: ResolvedInstrumentIdentity) -> bool:
        return self.key == other.key

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "requested_symbol": self.requested_symbol,
            "resolved_symbol": self.resolved_symbol,
            "venue": self.venue,
            "market": self.market,
            "timezone_basis": self.timezone_basis,
            "volume_kind": self.volume_kind,
            "volume_quality": self.volume_quality,
        }

    def to_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "source": self.provider,
            "venue": self.venue,
            "volume_kind": self.volume_kind,
            "volume_quality": self.volume_quality,
            "timezone_basis": self.timezone_basis,
            "source_identity": self.to_dict(),
        }
        if self.market == "crypto":
            metadata["continuous_market"] = True
        return metadata

    @classmethod
    def from_value(
        cls,
        value: object,
    ) -> ResolvedInstrumentIdentity | None:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            return None
        nested = value.get("source_identity")
        raw = nested if isinstance(nested, Mapping) else value
        provider = str(raw.get("provider") or raw.get("source") or "").strip()
        requested = str(
            raw.get("requested_symbol")
            or raw.get("resolved_symbol")
            or raw.get("symbol")
            or ""
        ).strip()
        resolved = str(raw.get("resolved_symbol") or requested).strip()
        if not provider or not requested:
            return None
        return cls(
            provider=provider,
            requested_symbol=requested,
            resolved_symbol=resolved,
            venue=str(raw.get("venue") or ""),
            market=str(raw.get("market") or ""),
            timezone_basis=str(raw.get("timezone_basis") or "UTC"),
            volume_kind=str(raw.get("volume_kind") or "unknown"),
            volume_quality=str(raw.get("volume_quality") or "unknown"),
        )


@dataclass(frozen=True)
class DataQualityIssue:
    code: str
    severity: QualitySeverity
    message: str
    timeframe: str = ""
    bar_seqs: tuple[int, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "timeframe": self.timeframe,
            "bar_seqs": list(self.bar_seqs),
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class DataQualityReport:
    issues: tuple[DataQualityIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "hard_error" for issue in self.issues)

    @property
    def hard_errors(self) -> tuple[DataQualityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "hard_error")

    @property
    def warnings(self) -> tuple[DataQualityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def failed_check(self) -> str | None:
        return self.hard_errors[0].code if self.hard_errors else None

    @property
    def reason(self) -> str:
        return self.hard_errors[0].message if self.hard_errors else ""

    def merged(self, *others: DataQualityReport) -> DataQualityReport:
        issues = list(self.issues)
        for report in others:
            issues.extend(report.issues)
        return DataQualityReport(tuple(issues))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "hard_error_count": len(self.hard_errors),
            "warning_count": len(self.warnings),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class AnalysisSnapshotBundle:
    """Frozen same-source input passed to all analysis stages."""

    current_frame: Any
    higher_frame: Any | None
    source_identity: ResolvedInstrumentIdentity | None
    data_quality: DataQualityReport
    captured_at_ms: int
