"""Centralised read-only resource and writable runtime paths for PA Agent."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def _resolved_path(value: object) -> Path:
    return Path(str(value)).expanduser().resolve()


@dataclass(frozen=True)
class RuntimePaths:
    """Resolved project resources and process-writable state locations."""

    project_root: Path
    prompt_dir: Path
    state_root: Path
    records_pending_dir: Path
    experience_dir: Path
    config_dir: Path
    logs_dir: Path
    trade_records_dir: Path
    settings_json_path: Path
    feishu_json_legacy_path: Path
    log_file_path: Path
    crash_log_path: Path
    analysis_audit_db_path: Path
    monitoring_audit_db_path: Path
    paper_trading_db_path: Path


def resolve_runtime_paths(
    *,
    project_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> RuntimePaths:
    """Resolve paths without requiring the checkout itself to be writable.

    With no overrides, the legacy checkout layout is preserved. Linux services
    should set ``PA_AGENT_HOME`` to a writable state directory.
    """
    env = os.environ if environ is None else environ
    project = _resolved_path(
        project_root or Path(__file__).resolve().parent.parent.parent
    )
    state = _resolved_path(env.get("PA_AGENT_HOME") or project)
    prompt = _resolved_path(
        env.get("PA_AGENT_PROMPT_DIR") or project / "prompt_engineering"
    )
    config = state / "config"
    settings = _resolved_path(
        env.get("PA_AGENT_SETTINGS_PATH") or config / "settings.json"
    )
    records = state / "records"
    logs = state / "logs"
    return RuntimePaths(
        project_root=project,
        prompt_dir=prompt,
        state_root=state,
        records_pending_dir=records / "pending",
        experience_dir=state / "experience",
        config_dir=config,
        logs_dir=logs,
        trade_records_dir=state / "trade_records",
        settings_json_path=settings,
        feishu_json_legacy_path=config / "feishu.json",
        log_file_path=logs / "pa_agent.log",
        crash_log_path=logs / "crash.log",
        analysis_audit_db_path=records / "audit" / "analysis.sqlite3",
        monitoring_audit_db_path=records / "audit" / "monitoring.sqlite3",
        paper_trading_db_path=state / "trade_records" / "paper.sqlite3",
    )


RUNTIME_PATHS = resolve_runtime_paths()

# Compatibility constants used by the desktop application.
PROJECT_ROOT: Path = RUNTIME_PATHS.project_root
STATE_ROOT: Path = RUNTIME_PATHS.state_root
PROMPT_DIR: Path = RUNTIME_PATHS.prompt_dir
PA_AGENT_DIR: Path = PROJECT_ROOT
RECORDS_PENDING_DIR: Path = RUNTIME_PATHS.records_pending_dir
EXPERIENCE_DIR: Path = RUNTIME_PATHS.experience_dir
CONFIG_DIR: Path = RUNTIME_PATHS.config_dir
LOGS_DIR: Path = RUNTIME_PATHS.logs_dir
TRADE_RECORDS_DIR: Path = RUNTIME_PATHS.trade_records_dir
FEISHU_JSON_LEGACY_PATH: Path = RUNTIME_PATHS.feishu_json_legacy_path
SETTINGS_JSON_PATH: Path = RUNTIME_PATHS.settings_json_path
LOG_FILE_PATH: Path = RUNTIME_PATHS.log_file_path
CRASH_LOG_PATH: Path = RUNTIME_PATHS.crash_log_path
ANALYSIS_AUDIT_DB_PATH: Path = RUNTIME_PATHS.analysis_audit_db_path
MONITORING_AUDIT_DB_PATH: Path = RUNTIME_PATHS.monitoring_audit_db_path
PAPER_TRADING_DB_PATH: Path = RUNTIME_PATHS.paper_trading_db_path
