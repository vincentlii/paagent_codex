"""Provider-declared instrument constraints used by execution math."""
from __future__ import annotations

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

