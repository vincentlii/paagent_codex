from __future__ import annotations

import json
import hashlib

import pytest
from pydantic import ValidationError

from pa_agent.operations.analysis_profile import (
    AnalysisProfile,
    analysis_capabilities,
    profile_fingerprint,
    validate_analysis_profile,
)


def test_default_profile_fixes_initial_market_scope_and_models() -> None:
    profile = AnalysisProfile()

    assert profile.deepseek_model == "deepseek-v4-flash"
    assert profile.deepseek_thinking is True
    assert profile.deepseek_reasoning_effort == "high"
    assert profile.codex_model == "gpt-5.6-sol"
    assert profile.codex_reasoning_effort == "high"
    assert profile.source == "binance_usdm"
    assert profile.exchange == "BINANCE"
    assert profile.symbol == "XAUUSDT"
    assert profile.timeframe == "1h"
    assert profile.vpa_mode == "shadow"
    assert profile.higher_timeframe_enabled is True
    assert profile.higher_timeframe_override == "4h"
    assert profile.higher_timeframe_bar_count == 60
    assert profile.max_concurrency == 1


def test_profile_is_strict_and_contains_no_secret_or_path_fields() -> None:
    with pytest.raises(ValidationError):
        AnalysisProfile.model_validate({"api_key": "secret"})

    encoded = json.dumps(AnalysisProfile().model_dump(), sort_keys=True)
    for fragment in ("api_key", "secret", "password", "path", "base_url"):
        assert fragment not in encoded


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "deepseek-v4-pro"])
def test_deepseek_model_catalog_accepts_only_supported_models(model: str) -> None:
    assert validate_analysis_profile({"deepseek_model": model}).deepseek_model == model


def test_codex_model_effort_pairs_are_allowlisted() -> None:
    assert AnalysisProfile(
        codex_model="gpt-6-luna", codex_reasoning_effort="max"
    ).codex_reasoning_effort == "max"
    with pytest.raises(ValidationError):
        AnalysisProfile(codex_model="gpt-6-luna", codex_reasoning_effort="ultra")
    assert AnalysisProfile(
        codex_model="gpt-5.6-terra", codex_reasoning_effort="max"
    ).codex_reasoning_effort == "max"
    with pytest.raises(ValidationError):
        AnalysisProfile(codex_model="gpt-5.4-mini", codex_reasoning_effort="max")
    assert AnalysisProfile(
        codex_model="gpt-5.6-sol", codex_reasoning_effort="ultra"
    ).codex_reasoning_effort == "ultra"
    with pytest.raises(ValidationError):
        AnalysisProfile(deepseek_model="deepseek-chat")


def test_capability_catalog_is_json_safe_and_keeps_labels_separate() -> None:
    capabilities = analysis_capabilities()
    dumped = json.dumps(capabilities, ensure_ascii=False)

    assert "DeepSeek V4 Flash" in dumped
    assert "deepseek-v4-flash" in dumped
    codex = {item["id"]: item for item in capabilities["codex_models"]}
    assert codex["gpt-6-luna"]["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert codex["gpt-6-astra"]["efforts"] == [
        "low", "medium", "high", "xhigh", "max", "ultra"
    ]
    assert codex["gpt-5.6-sol"]["efforts"] == [
        "low", "medium", "high", "xhigh", "max", "ultra"
    ]


def test_profile_fingerprint_is_canonical_sha256() -> None:
    original = AnalysisProfile()
    reordered = validate_analysis_profile(dict(reversed(list(original.model_dump().items()))))

    assert profile_fingerprint(original) == profile_fingerprint(reordered)
    assert len(profile_fingerprint(original)) == 64


def test_new_default_fields_preserve_legacy_frozen_profile_fingerprint() -> None:
    legacy = AnalysisProfile().model_dump(mode="json")
    legacy.pop("symbols", None)
    legacy.pop("decision_stance", None)
    expected = hashlib.sha256(json.dumps(legacy, ensure_ascii=True, allow_nan=False,
        separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    assert profile_fingerprint(legacy) == expected
    assert profile_fingerprint({**legacy, "symbols": ["XAUUSDT"], "decision_stance": "balanced"}) == expected


@pytest.mark.parametrize("concurrency", [2, 3])
def test_multi_asset_scope_and_stance_are_explicit_allowlisted_and_hashed(concurrency) -> None:
    profile = AnalysisProfile(symbols=("XAUUSDT", "BTCUSDT", "ETHUSDT"),
        decision_stance="conservative", max_concurrency=concurrency)
    assert profile.enabled_symbols == ("XAUUSDT", "BTCUSDT", "ETHUSDT")
    assert profile_fingerprint(profile) != profile_fingerprint(AnalysisProfile())
    for values in ({"symbols": []}, {"symbols": ["BTCUSDT", "BTCUSDT"]},
                   {"symbols": ["SOLUSDT"]}, {"symbols": [{}]},
                   {"symbol": "XAUUSDT", "symbols": ["BTCUSDT"]},
                   {"decision_stance": "extreme_aggressive"},
                   {"max_concurrency": 4}):
        with pytest.raises(ValidationError):
            AnalysisProfile.model_validate(values)
    capabilities = analysis_capabilities()
    assert [item["id"] for item in capabilities["market_symbols"]] == ["XAUUSDT", "BTCUSDT", "ETHUSDT"]
    assert [item["id"] for item in capabilities["decision_stances"]] == ["conservative", "balanced", "aggressive"]
    assert [item["id"] for item in capabilities["concurrency_choices"]] == ["1", "2", "3"]
