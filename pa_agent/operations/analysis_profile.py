"""Credential-free, versioned model choices for the VPS analysis runtime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, model_validator

DeepSeekModel = Literal["deepseek-v4-flash", "deepseek-v4-pro"]
DeepSeekEffort = Literal["low", "medium", "high", "max"]
CodexModel = Literal[
    "gpt-6-astra",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
]
CodexEffort = Literal["low", "medium", "high", "xhigh", "max", "ultra"]

_CODEX_EFFORTS: Mapping[str, tuple[str, ...]] = {
    "gpt-6-astra": ("low", "medium", "high", "xhigh", "max", "ultra"),
    "gpt-5.6-sol": ("low", "medium", "high", "xhigh", "max", "ultra"),
    "gpt-5.6-terra": ("low", "medium", "high", "xhigh", "max", "ultra"),
    "gpt-5.6-luna": ("low", "medium", "high", "xhigh", "max"),
    "gpt-5.5": ("low", "medium", "high", "xhigh"),
    "gpt-5.4": ("low", "medium", "high", "xhigh"),
    "gpt-5.4-mini": ("low", "medium", "high", "xhigh"),
}


@dataclass(frozen=True)
class ModelCapability:
    id: str
    label: str
    efforts: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["efforts"] = list(self.efforts)
        return payload


class AnalysisProfile(BaseModel):
    """Only model choices vary; market and evidence scope are fixed in v1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    deepseek_model: DeepSeekModel = "deepseek-v4-flash"
    deepseek_thinking: bool = True
    deepseek_reasoning_effort: DeepSeekEffort = "high"
    codex_model: CodexModel = "gpt-5.6-sol"
    codex_reasoning_effort: CodexEffort = "high"
    source: Literal["binance_usdm"] = "binance_usdm"
    exchange: Literal["BINANCE"] = "BINANCE"
    symbol: Literal["XAUUSDT"] = "XAUUSDT"
    timeframe: Literal["1h"] = "1h"
    vpa_mode: Literal["shadow"] = "shadow"
    higher_timeframe_enabled: Literal[True] = True
    higher_timeframe_override: Literal["4h"] = "4h"
    higher_timeframe_bar_count: Literal[60] = 60
    max_concurrency: Literal[1] = 1

    @model_validator(mode="after")
    def _supported_codex_pair(self) -> AnalysisProfile:
        if self.codex_reasoning_effort not in _CODEX_EFFORTS[self.codex_model]:
            raise ValueError(
                f"unsupported Codex model/effort pair: "
                f"{self.codex_model}/{self.codex_reasoning_effort}"
            )
        return self


def validate_analysis_profile(value: AnalysisProfile | Mapping[str, object]) -> AnalysisProfile:
    if isinstance(value, AnalysisProfile):
        return value
    return AnalysisProfile.model_validate(value)


def codex_model_effort_supported(model: str, effort: str) -> bool:
    """Return whether a credential-free Codex selection is in the release catalog."""
    # Explicit engineering backend; not yet promoted to the Dashboard catalog.
    if model == "deepseek-flash":
        return effort in {"low", "high", "max"}
    return effort in _CODEX_EFFORTS.get(model, ())


def profile_fingerprint(profile: AnalysisProfile | Mapping[str, object]) -> str:
    validated = validate_analysis_profile(profile)
    encoded = json.dumps(
        validated.model_dump(mode="json"),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def analysis_capabilities() -> dict[str, list[dict[str, object]]]:
    deepseek = (
        ModelCapability(
            id="deepseek-v4-flash",
            label="DeepSeek V4 Flash",
            efforts=("low", "medium", "high", "max"),
        ),
        ModelCapability(
            id="deepseek-v4-pro",
            label="DeepSeek V4 Pro",
            efforts=("low", "medium", "high", "max"),
        ),
    )
    labels = {
        "gpt-6-astra": "GPT-6 Astra",
        "gpt-5.6-sol": "GPT-5.6 Sol",
        "gpt-5.6-terra": "GPT-5.6 Terra",
        "gpt-5.6-luna": "GPT-5.6 Luna",
        "gpt-5.5": "GPT-5.5",
        "gpt-5.4": "GPT-5.4",
        "gpt-5.4-mini": "GPT-5.4 Mini",
    }
    codex = tuple(
        ModelCapability(id=model, label=labels[model], efforts=efforts)
        for model, efforts in _CODEX_EFFORTS.items()
    )
    return {
        "deepseek_models": [item.to_dict() for item in deepseek],
        "codex_models": [item.to_dict() for item in codex],
    }
