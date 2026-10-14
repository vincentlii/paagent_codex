"""Demo-only execution values; no model, credentials or database authority."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pa_agent.operations.demo_settings import DemoExecutionPolicy

Positive = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
MAPPINGS = {"BTCUSDT": "BTC-USDT-SWAP", "ETHUSDT": "ETH-USDT-SWAP", "XAUUSDT": "XAU-USDT-SWAP"}


def canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class DemoConfig(StrictModel):
    enabled: bool = False
    entry_enabled: bool = False
    execution_target: Literal["paper", "okx_demo"] = "paper"
    notional_usdt: Positive = Decimal("500")
    leverage: Positive = Decimal("3")
    symbols: tuple[str, ...] = ("BTC-USDT-SWAP",)
    cutover_id: str = ""
    cutover_started_at_ms: int = 0
    # Must be pinned by explicit private read-only preflight, never inferred on entry.
    account_key: str = ""
    accepted_symbols: tuple[str, ...] = ()

    @model_validator(mode="after")
    def check(self) -> Self:
        if self.notional_usdt != 500 or self.leverage != 3:
            raise ValueError("fixed_500_leverage_3_protocol")
        if any(s not in MAPPINGS.values() for s in self.symbols + self.accepted_symbols):
            raise ValueError("unsupported_instrument")
        if self.entry_enabled and (
            not self.enabled
            or self.execution_target != "okx_demo"
            or not self.cutover_id
            or not self.account_key
        ):
            raise ValueError("entry_requires_explicit_cutover_and_account")
        return self


@dataclass(frozen=True)
class DemoPaths:
    root: Path

    @property
    def intents(self) -> Path:
        return self.root / "intents"

    @property
    def receipts(self) -> Path:
        return self.root / "receipts"

    @property
    def state_db(self) -> Path:
        return self.root / "state" / "execution.sqlite3"

    @property
    def projection(self) -> Path:
        return self.root / "projection" / "demo.json"

    @property
    def control(self) -> Path:
        return self.intents / "control.json"


def resolve_demo_paths(paths: object, *, environ: Mapping[str, str]) -> DemoPaths:
    return DemoPaths(
        Path(environ.get("PA_AGENT_EXECUTION_HOME") or (paths.state_root / "execution"))
    )


class DemoIntent(StrictModel):
    schema_version: Literal["okx_demo_intent_v1"] = "okx_demo_intent_v1"
    cutover_id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=160)
    run_id: str = Field(min_length=1, max_length=160)
    pipeline: Literal["pa_vpa_joint", "pa_only", "pa_shadow"]
    source_identity: dict[str, str]
    source_spec: dict[str, str | float]
    market_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    record_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_bar_ms: int = Field(gt=0)
    timeframe: Literal["1h"] = "1h"
    generated_at_ms: int = Field(gt=0)
    fresh_until_ms: int = Field(gt=0)
    expires_at_ms: int = Field(gt=0)
    inst_id: str
    disposition: Literal["limit", "no_trade", "rejected"]
    side: Literal["buy", "sell"] | None = None
    entry: Positive | None = None
    stop: Positive | None = None
    tp1: Positive | None = None
    source_reason: str = Field(default="", max_length=2400)
    demo_execution_policy: DemoExecutionPolicy | None = None
    content_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    def digest_payload(self):
        # v1 null price fields participate in the original digest; only this
        # optional extension is omitted for pre-settings intents.
        excluded = {"content_digest"}
        if self.demo_execution_policy is None:
            excluded.add("demo_execution_policy")
        return self.model_dump(mode="json", exclude=excluded)

    @classmethod
    def seal(cls, values: dict) -> Self:
        # Validate and normalize Decimal representations before hashing.
        parsed = cls.model_validate(
            values | {"content_digest": "0" * 64}, context={"sealing": True}
        )
        payload = parsed.digest_payload()
        return cls.model_validate(payload | {"content_digest": digest(payload)})

    @model_validator(mode="after")
    def check(self, info) -> Self:
        if self.inst_id != MAPPINGS.get(self.source_identity.get("symbol")):
            raise ValueError("source_target_mapping_mismatch")
        if self.fresh_until_ms <= self.generated_at_ms or self.expires_at_ms < self.fresh_until_ms:
            raise ValueError("invalid_expiry")
        if self.disposition == "limit" and any(
            v is None for v in (self.side, self.entry, self.stop, self.tp1)
        ):
            raise ValueError("missing_limit_prices")
        if self.disposition != "limit" and any(
            v is not None for v in (self.side, self.entry, self.stop, self.tp1)
        ):
            raise ValueError("non_limit_has_prices")
        if not (info.context or {}).get("sealing"):
            if digest(self.digest_payload()) != self.content_digest:
                raise ValueError("intent_digest_mismatch")
        return self


class DemoContract(StrictModel):
    inst_id: str
    ct_type: Literal["linear"] = "linear"
    settle_ccy: Literal["USDT"] = "USDT"
    tick: Positive
    lot: Positive
    minimum: Positive
    maximum_limit: Positive
    maximum_market: Positive
    ct_val: Positive
    ct_mult: Positive
    observed_at_ms: int


class DemoOrderRequest(StrictModel):
    environment: Literal["okx_demo_v1"] = "okx_demo_v1"
    account_key: str = Field(min_length=1)
    inst_id: str
    side: Literal["buy", "sell"]
    client_id: str = Field(pattern=r"^pad[a-f0-9]{29}$")
    px: Positive
    sz: Positive
    stop: Positive
    tp1: Positive
    notional: Positive
    margin: Positive
    exp_time_ms: int
    contract_digest: str


class DemoSnapshot(StrictModel):
    observed_at_ms: int
    server_time_ms: int
    account_key: str
    account_mode: str
    position_mode: str
    equity: Decimal | None = None
    available: Decimal | None = None
    contracts: tuple[DemoContract, ...] = ()
    orders: tuple[dict[str, str], ...] = ()
    algos: tuple[dict[str, str], ...] = ()
    positions: tuple[dict[str, str], ...] = ()
    fills: tuple[dict[str, str], ...] = ()
    bills: tuple[dict[str, str], ...] = ()
    leverage: dict[str, str] = {}
    quotes: dict[str, dict[str, str]] = {}
    coverage: dict[str, bool] = {}


class DemoReceipt(StrictModel):
    intent_digest: str
    task_id: str
    cutover_id: str
    disposition: str
    reason: str = ""
    accepted_at_ms: int


def client_id(account: str, intent: DemoIntent, leg: str, generation: int) -> str:
    return (
        "pad"
        + digest(["okx_demo_v1", account, intent.cutover_id, intent.task_id, leg, generation])[:29]
    )
