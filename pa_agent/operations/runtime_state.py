"""Atomic, credential-free state exchange for the VPS runtime and control plane."""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

DesiredState = Literal["running", "paused"]
AnalysisBackendMode = Literal["deepseek_only", "auto", "codex_only"]
_FORBIDDEN_STATUS_KEYS = (
    "api_key",
    "password",
    "secret",
    "credential",
    "raw_prompt",
    "raw_response",
)


@dataclass(frozen=True)
class RuntimeControlState:
    schema_version: int = 2
    desired_state: DesiredState = "running"
    desired_backend: AnalysisBackendMode = "deepseek_only"
    generation: int = 0
    updated_at_ms: int = 0
    actor: str = "default"
    error: str | None = None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        with suppress(OSError):
            temporary.chmod(0o640)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


class RuntimeControlStore:
    """Persist desired runtime state without reaching into the analysis process."""

    def __init__(self, path: Path, *, now_ms=None) -> None:
        self._path = Path(path)
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.Lock()

    def read(self) -> RuntimeControlState:
        if not self._path.is_file():
            return RuntimeControlState()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            schema_version = int(raw.get("schema_version", 1))
            if schema_version not in {1, 2}:
                raise ValueError("unsupported control schema")
            desired = str(raw["desired_state"])
            if desired not in {"running", "paused"}:
                raise ValueError("invalid desired_state")
            desired_backend = str(raw.get("desired_backend") or "deepseek_only")
            if desired_backend not in {"deepseek_only", "auto", "codex_only"}:
                raise ValueError("invalid desired_backend")
            generation = int(raw.get("generation", 0))
            updated_at_ms = int(raw.get("updated_at_ms", 0))
            if generation < 0 or updated_at_ms < 0:
                raise ValueError("negative control metadata")
            return RuntimeControlState(
                schema_version=2,
                desired_state=desired,  # type: ignore[arg-type]
                desired_backend=desired_backend,  # type: ignore[arg-type]
                generation=generation,
                updated_at_ms=updated_at_ms,
                actor=str(raw.get("actor") or "unknown")[:64],
            )
        except (KeyError, OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            return RuntimeControlState(
                desired_state="paused",
                updated_at_ms=int(self._now_ms()),
                actor="fail_closed",
                error="invalid_control_state",
            )

    def set_desired_state(self, desired_state: DesiredState, *, actor: str) -> RuntimeControlState:
        if desired_state not in {"running", "paused"}:
            raise ValueError("desired_state must be running or paused")
        with self._lock:
            current = self.read()
            state = RuntimeControlState(
                desired_state=desired_state,
                desired_backend=current.desired_backend,
                generation=current.generation + 1,
                updated_at_ms=int(self._now_ms()),
                actor=(str(actor).strip() or "unknown")[:64],
            )
            _atomic_write_json(self._path, asdict(state))
            return state

    def set_desired_backend(
        self,
        desired_backend: AnalysisBackendMode,
        *,
        actor: str,
    ) -> RuntimeControlState:
        if desired_backend not in {"deepseek_only", "auto", "codex_only"}:
            raise ValueError("unsupported analysis backend")
        with self._lock:
            current = self.read()
            state = RuntimeControlState(
                desired_state=current.desired_state,
                desired_backend=desired_backend,
                generation=current.generation + 1,
                updated_at_ms=int(self._now_ms()),
                actor=(str(actor).strip() or "unknown")[:64],
            )
            _atomic_write_json(self._path, asdict(state))
            return state


class RuntimeStatusStore:
    """Publish a small status projection that cannot carry provider credentials."""

    def __init__(self, path: Path, *, now_ms=None) -> None:
        self._path = Path(path)
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

    def write(self, status: dict[str, Any]) -> dict[str, Any]:
        self._assert_credential_free(status)
        payload = {
            "schema_version": 1,
            **status,
            "updated_at_ms": int(self._now_ms()),
        }
        self._validate_payload(payload)
        _atomic_write_json(self._path, payload)
        return payload

    def read(self) -> dict[str, Any] | None:
        if not self._path.is_file():
            return None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        try:
            self._assert_credential_free(raw)
            self._validate_payload(raw)
        except (TypeError, ValueError):
            return None
        return raw

    @staticmethod
    def _validate_payload(raw: dict[str, Any]) -> None:
        if raw.get("schema_version") != 1:
            raise ValueError("unsupported runtime status schema")
        updated_at_ms = raw.get("updated_at_ms")
        if isinstance(updated_at_ms, bool) or not isinstance(updated_at_ms, int):
            raise TypeError("updated_at_ms must be an integer")
        if updated_at_ms < 0:
            raise ValueError("updated_at_ms cannot be negative")
        state = raw.get("service_state")
        if state not in {"running", "paused", "stopping"}:
            raise ValueError("invalid service_state")
        stale_after = raw.get("heartbeat_stale_after_seconds", 90)
        if isinstance(stale_after, bool) or not isinstance(stale_after, (int, float)):
            raise TypeError("heartbeat_stale_after_seconds must be numeric")
        if not 1 <= float(stale_after) <= 86_400:
            raise ValueError("heartbeat_stale_after_seconds is out of range")
        expected_containers = {
            "assets": list,
            "scheduler": dict,
            "mode": dict,
            "recent_runs": list,
            "paper": dict,
        }
        for key, expected_type in expected_containers.items():
            value = raw.get(key, expected_type())
            if not isinstance(value, expected_type):
                raise TypeError(f"{key} must be {expected_type.__name__}")
        for key in ("assets", "recent_runs"):
            if any(not isinstance(item, dict) for item in raw.get(key, [])):
                raise TypeError(f"{key} entries must be objects")
        paper = raw.get("paper", {})
        accounts = paper.get("accounts", [])
        if not isinstance(accounts, list):
            raise TypeError("paper.accounts must be a list")
        if any(not isinstance(item, dict) for item in accounts):
            raise TypeError("paper.accounts entries must be objects")

    @classmethod
    def _assert_credential_free(cls, value: Any, *, path: str = "status") -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                lowered = str(key).lower()
                if any(fragment in lowered for fragment in _FORBIDDEN_STATUS_KEYS):
                    raise ValueError(f"secret-bearing status field is forbidden: {path}.{key}")
                cls._assert_credential_free(nested, path=f"{path}.{key}")
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                cls._assert_credential_free(nested, path=f"{path}[{index}]")
