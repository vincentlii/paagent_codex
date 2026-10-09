"""Consistent, credential-free backups for durable PA Agent VPS state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from pa_agent.config.paths import resolve_runtime_paths

_BACKUP_PREFIX = "pa-agent-"
_BACKUP_NAME = re.compile(r"pa-agent-\d{8}T\d{6}Z")
_REQUIRED_FILES = {
    "databases/analysis.sqlite3",
    "databases/monitoring.sqlite3",
    "databases/paper.sqlite3",
    "configuration/settings.json",
    "configuration/control.json",
}
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
    with closing(sqlite3.connect(source_uri, uri=True, timeout=10.0)) as input_db:
        with closing(sqlite3.connect(target)) as output_db:
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


def _backup_tree(source: Path, target_root: Path, temporary: Path) -> list[dict[str, str]]:
    """Copy immutable/raw state without following links outside the state root."""
    if not source.exists():
        return []
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"backup source is not a regular directory: {source}")
    files: list[dict[str, str]] = []
    for item in sorted(source.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"backup source contains a symlink: {item}")
        if item.is_dir():
            continue
        if not item.is_file():
            raise ValueError(f"backup source is not a regular file: {item}")
        target = target_root / item.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)
        files.append({
            "path": target.relative_to(temporary).as_posix(),
            "sha256": _sha256(target),
        })
    return files


def verify_backup(directory: Path) -> list[str]:
    """Check the manifest and every backup file before a restore attempt."""
    root = Path(directory).resolve()
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        entries = manifest["files"]
        if manifest["schema_version"] != 1 or not isinstance(entries, list):
            raise ValueError("invalid backup manifest")
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return ["backup manifest is missing or invalid"]
    errors: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("backup manifest contains an invalid file entry")
            continue
        name = str(entry.get("path") or "")
        relative = Path(name)
        if (
            not name
            or name in seen
            or relative.is_absolute()
            or ".." in relative.parts
            or name == "manifest.json"
        ):
            errors.append(f"backup manifest contains an unsafe or duplicate path: {name}")
            continue
        seen.add(name)
        file = root / relative
        if not file.resolve().is_relative_to(root) or file.is_symlink() or not file.is_file():
            errors.append(f"backup file is missing or unsafe: {name}")
        elif _sha256(file) != str(entry.get("sha256") or ""):
            errors.append(f"backup file checksum differs: {name}")
    for name in sorted(_REQUIRED_FILES - seen):
        errors.append(f"backup required file is missing: {name}")
    return errors


def create_backup(output_root: Path, *, keep: int = 14) -> Path:
    """Create one atomic backup and retain only exact PA Agent backup directories."""
    if keep < 1:
        raise ValueError("keep must be positive")
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = resolve_runtime_paths()
    codex_root = Path(
        os.environ.get("PA_AGENT_CODEX_STATE_DIR") or (paths.state_root / "codex")
    ).expanduser().resolve()
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
            if not _backup_sqlite(source, target):
                raise FileNotFoundError(f"required backup database is missing: {source}")
            files.append(
                {"path": target.relative_to(temporary).as_posix(), "sha256": _sha256(target)}
            )
        for name, source in (
            ("settings.json", paths.settings_json_path),
            ("control.json", paths.runtime_control_path),
        ):
            target = temporary / "configuration" / name
            if not _backup_json(source, target):
                raise FileNotFoundError(f"required backup configuration is missing: {source}")
            files.append(
                {"path": target.relative_to(temporary).as_posix(), "sha256": _sha256(target)}
            )
        for target_name, source in (
            ("records/pending", paths.records_pending_dir),
            ("experience", paths.experience_dir),
            ("frozen_jobs", codex_root / "jobs"),
            ("codex_acceptance", codex_root / "results" / ".accepted"),
        ):
            if root.is_relative_to(source):
                raise ValueError(f"backup destination is inside its source: {source}")
            files.extend(_backup_tree(source, temporary / target_name, temporary))
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
        errors = verify_backup(temporary)
        if errors:
            raise ValueError("backup verification failed: " + "; ".join(errors))
        os.replace(temporary, destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    backups = sorted(
        (
            item
            for item in root.iterdir()
            if item.is_dir() and _BACKUP_NAME.fullmatch(item.name)
        ),
        key=lambda item: item.name,
        reverse=True,
    )
    for expired in backups[keep:]:
        if expired.parent == root and _BACKUP_NAME.fullmatch(expired.name):
            shutil.rmtree(expired)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up durable PA Agent VPS state")
    parser.add_argument("--output-root", type=Path, default=Path("/var/backups/pa-agent"))
    parser.add_argument("--keep", type=int, default=14)
    parser.add_argument("--verify", type=Path, help="verify one backup without modifying state")
    args = parser.parse_args(argv)
    if args.verify is not None:
        errors = verify_backup(args.verify)
        for error in errors:
            print(error)
        if not errors:
            print("backup verification OK")
        return int(bool(errors))
    print(create_backup(args.output_root, keep=args.keep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
