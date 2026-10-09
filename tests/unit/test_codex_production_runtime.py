from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

from pa_agent.agent_runtime.production import (
    CodexProductionConfig,
    build_codex_backend_factory,
    validate_codex_production_config,
)
from pa_agent.agent_runtime.runtime_assets import load_codex_runtime_assets
from tests.integration.conftest import make_frame


def test_codex_factory_is_disabled_by_default(tmp_path: Path) -> None:
    assert build_codex_backend_factory(
        state_dir=tmp_path,
        prompt_dir=tmp_path,
        environment={},
    ) is None


def test_enabled_codex_factory_uses_state_only_runtime_roots(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    environment = {
        "PA_AGENT_CODEX_ENABLED": "1",
        "PA_AGENT_CODEX_MODEL": "gpt-5.6-sol",
        "PA_AGENT_CODEX_REASONING": "high",
        "PA_AGENT_CODEX_BINARY": sys.executable,
    }

    factory = build_codex_backend_factory(
        state_dir=tmp_path / "state",
        prompt_dir=prompt_dir,
        environment=environment,
    )
    config = CodexProductionConfig.from_environment(
        state_dir=tmp_path / "state",
        environment=environment,
    )
    backend = factory(
        SimpleNamespace(
            payload=SimpleNamespace(
                frame=make_frame(),
                analysis_backend_mode="auto",
                analysis_backend_identity=config.public_runtime_identity(),
            )
        ),
        object(),
    )

    assert backend.backend_id == "codex_agent"
    assert backend._request_full_chain_fallback is True
    assert backend._worker._config.job_root == tmp_path / "state" / "codex" / "jobs"
    assert backend._worker._config.inbox_root == tmp_path / "state" / "codex" / "inbox"
    assert backend._runtime_identity["codex_binary_digest"]
    assert (
        backend._runtime_identity["runtime_asset_digest"]
        == load_codex_runtime_assets().asset_digest
    )

    fast_backend = factory(
        SimpleNamespace(
            payload=SimpleNamespace(
                frame=make_frame(),
                analysis_backend_mode="codex_only",
                analysis_backend_identity={
                    **config.public_runtime_identity(),
                    "service_tier": "fast",
                },
            )
        ),
        object(),
    )
    assert fast_backend._runtime_identity["service_tier"] == "fast"
    assert fast_backend._publish_job._runtime_identity["service_tier"] == "fast"


def test_enabled_codex_preflight_fails_closed_without_model_or_binary(tmp_path: Path) -> None:
    config = CodexProductionConfig.from_environment(
        state_dir=tmp_path,
        environment={"PA_AGENT_CODEX_ENABLED": "1", "PA_AGENT_CODEX_BINARY": "missing-codex"},
    )

    errors = validate_codex_production_config(config, prompt_dir=tmp_path / "missing")

    assert "PA_AGENT_CODEX_MODEL is required when Codex is enabled" in errors
    assert "Codex binary is unavailable or not executable" in errors
    assert "Codex prompt directory is unavailable" in errors


def test_enabled_codex_preflight_rejects_unknown_model_and_invalid_pair(
    tmp_path: Path,
) -> None:
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    for model, effort in (("gpt-unknown", "high"), ("gpt-5.4-mini", "max")):
        config = CodexProductionConfig.from_environment(
            state_dir=tmp_path / model,
            environment={
                "PA_AGENT_CODEX_ENABLED": "1",
                "PA_AGENT_CODEX_MODEL": model,
                "PA_AGENT_CODEX_REASONING": effort,
                "PA_AGENT_CODEX_BINARY": sys.executable,
            },
        )

        errors = validate_codex_production_config(config, prompt_dir=prompt_dir)

        assert any("pair is unsupported" in error for error in errors)


def test_deepseek_codex_preflight_requires_read_only_model_catalog(
    tmp_path: Path,
) -> None:
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    config = CodexProductionConfig.from_environment(
        state_dir=tmp_path / "state",
        environment={
            "PA_AGENT_CODEX_ENABLED": "1",
            "PA_AGENT_CODEX_MODEL": "deepseek-flash",
            "PA_AGENT_CODEX_REASONING": "high",
            "PA_AGENT_CODEX_BINARY": sys.executable,
        },
    )

    errors = validate_codex_production_config(config, prompt_dir=prompt_dir)

    assert "PA_AGENT_CODEX_MODEL_CATALOG is required for deepseek-flash" in errors
