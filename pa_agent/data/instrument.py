"""Provider-declared instrument constraints used by execution math."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentSpec:
    provider: str
    venue: str
    symbol: str
    instrument_type: str
    tick_size: float
    quantity_step: float
    minimum_quantity: float
    maximum_quantity: float
    minimum_notional: float
    contract_multiplier: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", str(self.provider).strip().lower())
        object.__setattr__(self, "venue", str(self.venue).strip().upper())
        object.__setattr__(self, "symbol", str(self.symbol).strip().upper())
        object.__setattr__(
            self,
            "instrument_type",
            str(self.instrument_type).strip().lower(),
        )
        if not self.provider or not self.venue or not self.symbol or not self.instrument_type:
            raise ValueError("instrument spec identity is incomplete")
        if self.tick_size <= 0 or self.quantity_step <= 0:
            raise ValueError("instrument tick and quantity step must be positive")
        if self.minimum_quantity <= 0 or self.maximum_quantity < self.minimum_quantity:
            raise ValueError("instrument quantity bounds are invalid")
        if self.minimum_notional < 0:
            raise ValueError("instrument minimum notional cannot be negative")
        if self.contract_multiplier <= 0:
            raise ValueError("instrument contract multiplier must be positive")

    def to_dict(self) -> dict[str, str | float]:
        return {
            "provider": self.provider,
            "venue": self.venue,
            "symbol": self.symbol,
            "instrument_type": self.instrument_type,
            "tick_size": self.tick_size,
            "quantity_step": self.quantity_step,
            "minimum_quantity": self.minimum_quantity,
            "maximum_quantity": self.maximum_quantity,
            "minimum_notional": self.minimum_notional,
            "contract_multiplier": self.contract_multiplier,
        }

    @classmethod
    def from_value(cls, value: object) -> InstrumentSpec | None:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            return None
        try:
            return cls(
                provider=str(value["provider"]),
                venue=str(value["venue"]),
                symbol=str(value["symbol"]),
                instrument_type=str(value["instrument_type"]),
                tick_size=float(value["tick_size"]),
                quantity_step=float(value["quantity_step"]),
                minimum_quantity=float(value["minimum_quantity"]),
                maximum_quantity=float(value["maximum_quantity"]),
                minimum_notional=float(value["minimum_notional"]),
                contract_multiplier=float(value.get("contract_multiplier", 1.0)),
            )
        except (KeyError, TypeError, ValueError):
            return None
