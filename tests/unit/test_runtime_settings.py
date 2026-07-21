from __future__ import annotations

import pytest

from pa_agent.config.runtime import RuntimeConfigurationError, load_runtime_settings
from pa_agent.config.settings import Settings, save_settings


def _settings_file(tmp_path, *, api_key: str = "config-key"):
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.provider.api_key = api_key
    save_settings(settings, path)
    return path


def test_direct_environment_secret_has_highest_precedence_and_is_not_persisted(tmp_path) -> None:
    settings_path = _settings_file(tmp_path)
    key_file = tmp_path / "api-key"
    key_file.write_text("file-key\n", encoding="utf-8")

    loaded = load_runtime_settings(
        settings_path,
        environ={
            "PA_AGENT_API_KEY": "env-key",
            "PA_AGENT_API_KEY_FILE": str(key_file),
            "PA_AGENT_MODEL": "runtime-model",
            "PA_AGENT_BASE_URL": "https://gateway.example/v1",
        },
    )

    assert loaded.settings.provider.api_key == "env-key"
    assert loaded.api_key_source == "environment"
    assert loaded.settings.provider.model == "runtime-model"
    assert loaded.settings.provider.base_url == "https://gateway.example/v1"
    assert "env-key" not in settings_path.read_text(encoding="utf-8")
    assert load_runtime_settings(settings_path, environ={}).settings.provider.api_key == "config-key"


def test_secret_file_precedes_systemd_credential_and_config(tmp_path) -> None:
    settings_path = _settings_file(tmp_path)
    key_file = tmp_path / "explicit-key"
    key_file.write_text("explicit-key\n", encoding="utf-8")
    credential_dir = tmp_path / "credentials"
    credential_dir.mkdir()
    (credential_dir / "pa_agent_api_key").write_text("credential-key", encoding="utf-8")

    loaded = load_runtime_settings(
        settings_path,
        environ={
            "PA_AGENT_API_KEY_FILE": str(key_file),
            "CREDENTIALS_DIRECTORY": str(credential_dir),
        },
    )

    assert loaded.settings.provider.api_key == "explicit-key"
    assert loaded.api_key_source == "file"


def test_systemd_credential_is_used_when_no_explicit_secret_exists(tmp_path) -> None:
    settings_path = _settings_file(tmp_path)
    credential_dir = tmp_path / "credentials"
    credential_dir.mkdir()
    (credential_dir / "pa_agent_api_key").write_text("credential-key\n", encoding="utf-8")

    loaded = load_runtime_settings(
        settings_path,
        environ={"CREDENTIALS_DIRECTORY": str(credential_dir)},
    )

    assert loaded.settings.provider.api_key == "credential-key"
    assert loaded.api_key_source == "systemd_credential"


def test_empty_explicit_secret_file_is_a_configuration_error(tmp_path) -> None:
    settings_path = _settings_file(tmp_path)
    key_file = tmp_path / "empty-key"
    key_file.write_text("\n", encoding="utf-8")

    with pytest.raises(RuntimeConfigurationError, match="empty"):
        load_runtime_settings(
            settings_path,
            environ={"PA_AGENT_API_KEY_FILE": str(key_file)},
        )


def test_invalid_settings_schema_is_reported_as_runtime_configuration_error(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        '{"provider": {"context_window": 0}}',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeConfigurationError, match="settings"):
        load_runtime_settings(settings_path, environ={})


def test_malformed_settings_json_is_reported_as_runtime_configuration_error(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(RuntimeConfigurationError, match="settings"):
        load_runtime_settings(settings_path, environ={})
