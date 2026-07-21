from __future__ import annotations

from pa_agent.config.paths import resolve_runtime_paths


def test_runtime_paths_separate_read_only_assets_from_writable_state(tmp_path) -> None:
    project_root = tmp_path / "checkout"
    state_root = tmp_path / "state"
    prompt_root = tmp_path / "prompts"
    settings_path = tmp_path / "private" / "settings.json"

    paths = resolve_runtime_paths(
        project_root=project_root,
        environ={
            "PA_AGENT_HOME": str(state_root),
            "PA_AGENT_PROMPT_DIR": str(prompt_root),
            "PA_AGENT_SETTINGS_PATH": str(settings_path),
        },
    )

    assert paths.project_root == project_root.resolve()
    assert paths.prompt_dir == prompt_root.resolve()
    assert paths.state_root == state_root.resolve()
    assert paths.settings_json_path == settings_path.resolve()
    assert paths.records_pending_dir == state_root.resolve() / "records" / "pending"
    assert paths.analysis_audit_db_path == state_root.resolve() / "records" / "audit" / "analysis.sqlite3"
    assert paths.monitoring_audit_db_path == state_root.resolve() / "records" / "audit" / "monitoring.sqlite3"
    assert paths.trade_records_dir == state_root.resolve() / "trade_records"


def test_runtime_paths_preserve_checkout_layout_without_overrides(tmp_path) -> None:
    project_root = tmp_path / "checkout"

    paths = resolve_runtime_paths(project_root=project_root, environ={})

    assert paths.state_root == project_root.resolve()
    assert paths.prompt_dir == project_root.resolve() / "prompt_engineering"
    assert paths.settings_json_path == project_root.resolve() / "config" / "settings.json"
