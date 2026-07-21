"""Runtime-only configuration overlays for unattended deployments."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pa_agent.config.settings import Settings, load_settings

_MAX_SECRET_BYTES = 64 * 1024


class RuntimeConfigurationError(ValueError):
    """An explicit runtime override cannot be used safely."""


@dataclass(frozen=True)
class LoadedRuntimeSettings:
    settings: Settings
    api_key_source: str


def _read_secret(path: Path, *, source: str) -> str:
    try:
        size = path.stat().st_size
        if size > _MAX_SECRET_BYTES:
            raise RuntimeConfigurationError(f"{source} secret file is too large: {path}")
        value = path.read_text(encoding="utf-8").strip()
    except RuntimeConfigurationError:
        raise
    except (OSError, UnicodeError) as exc:
        raise RuntimeConfigurationError(
            f"cannot read {source} secret file {path}: {exc}"
        ) from exc
    if not value:
        raise RuntimeConfigurationError(f"{source} secret file is empty: {path}")
    return value


def load_runtime_settings(
    path: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> LoadedRuntimeSettings:
    """Load persisted settings, then apply non-persistent process overrides.

    API-key precedence is direct environment value, explicit credential file,
    systemd credential, then the existing local configuration value.
    """
    env = os.environ if environ is None else environ
    settings = load_settings(Path(path))
    source = "configuration" if settings.provider.api_key.strip() else "unset"

    direct_key = str(env.get("PA_AGENT_API_KEY") or "").strip()
    explicit_file = str(env.get("PA_AGENT_API_KEY_FILE") or "").strip()
    credential_dir = str(env.get("CREDENTIALS_DIRECTORY") or "").strip()
    if direct_key:
        settings.provider.api_key = direct_key
        source = "environment"
    elif explicit_file:
        settings.provider.api_key = _read_secret(
            Path(explicit_file).expanduser(),
            source="PA_AGENT_API_KEY_FILE",
        )
        source = "file"
    elif credential_dir:
        credential_path = Path(credential_dir) / "pa_agent_api_key"
        if credential_path.is_file():
            settings.provider.api_key = _read_secret(
                credential_path,
                source="systemd credential",
            )
            source = "systemd_credential"

    model = str(env.get("PA_AGENT_MODEL") or "").strip()
    base_url = str(env.get("PA_AGENT_BASE_URL") or "").strip()
    if model:
        settings.provider.model = model
    if base_url:
        settings.provider.base_url = base_url
    return LoadedRuntimeSettings(settings=settings, api_key_source=source)
