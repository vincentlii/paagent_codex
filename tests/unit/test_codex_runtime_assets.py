from __future__ import annotations

import shutil
from pathlib import Path

from pa_agent.agent_runtime.runtime_audit import (
    audit_runtime_assets,
    posix_permission_contract,
)
from pa_agent.agent_runtime.runtime_assets import load_codex_runtime_assets


def test_runtime_assets_are_complete_and_deterministically_versioned() -> None:
    first = load_codex_runtime_assets()
    second = load_codex_runtime_assets()

    assert first.runtime_version == second.runtime_version
    assert first.runtime_version.startswith("codex-runtime-v1-")
    assert set(first.skill_documents) == {
        "stage1",
        "stage2",
        "vpa_evidence",
        "joint_arbiter",
    }
    assert "禁止 Web" in first.agents_text
    assert "Brooks Price Action 是主框架" in first.agents_text
    assert all(text.startswith("---\n") for text in first.skill_documents.values())


def test_runtime_audit_verifies_authority_frontmatter_and_prohibited_tools() -> None:
    report = audit_runtime_assets()

    assert report.ok is True
    assert report.asset_digest == load_codex_runtime_assets().asset_digest
    assert report.resource_names == (
        "AGENTS.md",
        "skills/joint_arbiter/SKILL.md",
        "skills/stage1/SKILL.md",
        "skills/stage2/SKILL.md",
        "skills/vpa_evidence/SKILL.md",
    )
    assert report.issues == ()


def test_runtime_audit_fails_on_rule_drift_and_installed_resource_mismatch(
    tmp_path: Path,
) -> None:
    source_assets = Path(__file__).resolve().parents[2] / "pa_agent" / "agent_runtime" / "assets"
    candidate = tmp_path / "assets"
    shutil.copytree(source_assets, candidate)
    agents_path = candidate / "AGENTS.md"
    agents_path.write_text(
        agents_path.read_text(encoding="utf-8").replace("禁止 Web", "允许 Web"),
        encoding="utf-8",
    )

    report = audit_runtime_assets(source_assets=candidate)

    assert report.ok is False
    assert {issue.code for issue in report.issues} >= {
        "installed_resource_mismatch",
        "missing_required_rule",
    }


def test_posix_permission_contract_separates_code_secrets_jobs_and_outputs() -> None:
    contract = {item.key: item for item in posix_permission_contract()}

    assert contract["source"].codex_access == "read_only"
    assert contract["codex_home"].codex_access == "read_write"
    assert contract["codex_auth"].mode == 0o600
    assert contract["job_root"].codex_access == "read_only"
    assert contract["inbox_root"].codex_access == "read_write"
    assert contract["results_root"].codex_access == "read_write"
    assert contract["runtime_root"].codex_access == "read_write"
    assert contract["outbox_root"].codex_access == "read_write"
    assert contract["provider_credential"].codex_access == "none"
    assert contract["production_database"].codex_access == "none"
    assert contract["paper_ledger"].codex_access == "none"
