"""Load and validate the machine-readable release quality contract."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class QualityContract:
    version: int
    invariants: dict[str, int]
    structured_output: dict[str, float | int]
    performance: dict[str, float | int]
    research: dict[str, float | int | bool]

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("quality contract version must be positive")
        if not self.invariants:
            raise ValueError("quality contract must define invariants")
        for name, value in self.invariants.items():
            if not name.startswith("max_") or int(value) < 0:
                raise ValueError(f"invalid invariant threshold: {name}")
        _require_fraction(
            self.structured_output,
            "certified_provider_first_pass_rate",
            lower_open=True,
        )
        _require_fraction(
            self.structured_output,
            "max_false_hard_reject_rate",
        )
        _require_fraction(
            self.research,
            "max_unexplained_trade_frequency_decline",
        )


def _require_fraction(
    section: dict[str, Any],
    key: str,
    *,
    lower_open: bool = False,
) -> None:
    value = float(section[key])
    valid_lower = value > 0 if lower_open else value >= 0
    if not valid_lower or value > 1:
        raise ValueError(f"{key} must be within {'(0, 1]' if lower_open else '[0, 1]'}")


def load_quality_contract(path: str | Path) -> QualityContract:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    required = {
        "version",
        "invariants",
        "structured_output",
        "performance",
        "research",
    }
    missing = required.difference(raw)
    if missing:
        raise ValueError(f"quality contract missing sections: {sorted(missing)}")
    return QualityContract(
        version=int(raw["version"]),
        invariants={key: int(value) for key, value in raw["invariants"].items()},
        structured_output=dict(raw["structured_output"]),
        performance=dict(raw["performance"]),
        research=dict(raw["research"]),
    )
