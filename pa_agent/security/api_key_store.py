"""Small local encryption boundary for the persisted AI API key."""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def secret_key_path(settings_path: Path) -> Path:
    return Path(settings_path).parent / "secret.key"


def _read_key(path: Path) -> bytes:
    value = path.read_bytes().strip()
    Fernet(value)
    return value


def _load_or_create_key(path: Path) -> bytes:
    try:
        return _read_key(path)
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        return _read_key(path)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
            handle.flush()
            os.fsync(handle.fileno())
        with suppress(OSError):
            path.chmod(0o600)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return key


def encrypt_api_key(api_key: str, settings_path: Path) -> str:
    """Encrypt a non-empty key with the installation-local Fernet key."""
    value = str(api_key or "")
    if not value:
        return ""
    key = _load_or_create_key(secret_key_path(settings_path))
    return Fernet(key).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_api_key(ciphertext: str, settings_path: Path) -> str:
    """Return plaintext or an empty value when key/ciphertext is unavailable."""
    value = str(ciphertext or "").strip()
    if not value:
        return ""
    try:
        key = _read_key(secret_key_path(settings_path))
        return Fernet(key).decrypt(value.encode("ascii")).decode("utf-8")
    except (FileNotFoundError, InvalidToken, OSError, UnicodeError, ValueError):
        return ""
