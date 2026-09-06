"""Consistent, credential-free backups for durable PA Agent VPS state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

from pa_agent.config.paths import resolve_runtime_paths

_BACKUP_PREFIX = "pa-agent-"
_SECRET_KEYS = {
    "api_key",
    "api_key_encrypted",
    "app_secret",
    "credential",
    "dashboard_token",
    "password",
    "secret",
    "token",
    "webhook_url",
}
_SECRET_SUFFIXES = ("_api_key", "_credential", "_password", "_secret", "_token")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_sqlite(source: Path, target: Path) -> bool:
    if not source.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True, timeout=10.0) as input_db:
        with sqlite3.connect(target) as output_db:
            input_db.backup(output_db)
            check = output_db.execute("PRAGMA quick_check").fetchone()
            if check is None or str(check[0]).lower() != "ok":
                raise sqlite3.DatabaseError(f"backup integrity check failed: {source.name}")
    return True


def _credential_free_json(value: object) -> object:
    """Remove secret-bearing fields while preserving the restorable structure."""
    if isinstance(value, dict):
        return {
            str(key): _credential_free_json(nested)
            for key, nested in value.items()
            if not _is_secret_key(str(key))
        }
    if isinstance(value, list):
        return [_credential_free_json(item) for item in value]
    return value


def _is_secret_key(key: str) -> bool:
    normalized = key.casefold()
    return normalized in _SECRET_KEYS or normalized.endswith(_SECRET_SUFFIXES)


def _backup_json(source: Path, target: Path) -> bool:
    if not source.is_file():
        return False
    raw = json.loads(source.read_text(encoding="utf-8"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            _credential_free_json(raw),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return True


def create_backup(output_root: Path, *, keep: int = 14) -> Path:
    """Create one atomic backup and retain only exact PA Agent backup directories."""
    if keep < 1:
        raise ValueError("keep must be positive")
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = resolve_runtime_paths()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    destination = root / f"{_BACKUP_PREFIX}{stamp}"
    temporary = root / f".{destination.name}.{uuid4().hex}.tmp"
    if destination.exists():
        raise FileExistsError(f"backup already exists: {destination.name}")
    temporary.mkdir(mode=0o700)
    files: list[dict[str, object]] = []
    try:
        database_sources = (
            ("analysis.sqlite3", paths.analysis_audit_db_path),
            ("monitoring.sqlite3", paths.monitoring_audit_db_path),
            ("paper.sqlite3", paths.paper_trading_db_path),
        )
        for name, source in database_sources:
            target = temporary / "databases" / name
            if _backup_sqlite(source, target):
                files.append(
                    {"path": target.relative_to(temporary).as_posix(), "sha256": _sha256(target)}
                )
        for name, source in (
            ("settings.json", paths.settings_json_path),
            ("control.json", paths.runtime_control_path),
        ):
            target = temporary / "configuration" / name
            if not _backup_json(source, target):
                continue
            files.append(
                {"path": target.relative_to(temporary).as_posix(), "sha256": _sha256(target)}
            )
        manifest = {
            "schema_version": 1,
            "created_at_utc": stamp,
            "credential_files_included": False,
            "configuration_credentials_removed": True,
            "files": sorted(files, key=lambda item: str(item["path"])),
        }
        manifest_path = temporary / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    backups = sorted(
        (
            item
            for item in root.iterdir()
            if item.is_dir() and item.name.startswith(_BACKUP_PREFIX)
        ),
        key=lambda item: item.name,
        reverse=True,
    )
    for expired in backups[keep:]:
        if expired.parent == root and expired.name.startswith(_BACKUP_PREFIX):
            shutil.rmtree(expired)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up durable PA Agent VPS state")
    parser.add_argument("--output-root", type=Path, default=Path("/var/backups/pa-agent"))
    parser.add_argument("--keep", type=int, default=14)
    args = parser.parse_args(argv)
    print(create_backup(args.output_root, keep=args.keep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
