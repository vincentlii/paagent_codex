"""Host-owned execution settings, independent of model identity and cutover."""

from decimal import Decimal
import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, field_serializer


class DemoExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schema_version: Literal[1] = 1
    notional_usdt: Annotated[Decimal, Field(gt=0, le=1_000_000_000, decimal_places=8)] = Decimal(
        "500"
    )
    limit_validity_hours: Annotated[int, Field(strict=True, ge=1, le=24)] = 3
    generation: Annotated[int, Field(strict=True, ge=0)] = 0

    @field_validator("notional_usdt", mode="before")
    @classmethod
    def reject_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("notional_must_be_decimal")
        return value

    @field_validator("notional_usdt")
    @classmethod
    def canonical_decimal(cls, value):
        return value.normalize()

    @field_serializer("notional_usdt")
    def serialize_decimal(self, value):
        return format(value.normalize(), "f")

    @property
    def fingerprint(self):
        raw = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


def validate_demo_settings(value):
    if not isinstance(value, dict) or set(value) != {"notional_usdt", "limit_validity_hours"}:
        raise ValueError("invalid_demo_execution_settings")
    return DemoExecutionPolicy.model_validate(value)
