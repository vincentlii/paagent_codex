"""Static and live deployment audit for the constrained Codex runtime."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence
from uuid import uuid4

from pa_agent.agent_runtime.execution_contract import SKILL_NAMES
from pa_agent.agent_runtime.runtime_assets import GUIDANCE_PATHS, load_codex_runtime_assets

_RESOURCE_PATHS = {
    "AGENTS.md": None,
    "config.toml": None,
    "rules/pa-agent.rules": None,
    "pa_challenger.toml": None,
    "skills/stage1/SKILL.md": "stage1",
    "skills/stage2/SKILL.md": "stage2",
    "skills/joint_stage2/SKILL.md": "joint_stage2",
    "skills/vpa_evidence/SKILL.md": "vpa_evidence",
    "skills/joint_arbiter/SKILL.md": "joint_arbiter",
    "skills/draft_pointer_repair/SKILL.md": "draft_pointer_repair",
    **{path: None for path in GUIDANCE_PATHS.values()},
}
_SKILL_NAMES = SKILL_NAMES
_REQUIRED_RULES = {
    "guidance/role_contracts.toml": ("result_draft_only", "analysis_role_stop_v1", "unrepairable", "fixed"),
    "guidance/stage_challenge.md": ("host_stage_challenge_v1", "resources", "子意见是待核解释"),
    "guidance/layered_stage2.md": ("layered_stage2_research_v1", "on_demand_sections", "完整原文"),
    "AGENTS.md": (
        "Brooks Price Action 是 Primary Market Framework",
        "Frozen Snapshot",
        "禁止 Web",
        "实时行情",
        "外部 App/Plugin",
        "数据库",
        "不修改文件",
        "不运行 shell",
        "不下单",
        "不得改变 Validator",
        "Trader Equation",
        "loading_policy=mandatory",
    ),
    "config.toml": (
        'approval_policy = "never"',
        'sandbox_mode = "read-only"',
        'forced_login_method = "chatgpt"',
        'web_search = "disabled"',
        "shell_tool = false",
        "apps = false",
        "multi_agent = false",
        "multi_agent_v2 = false",
        "memories = false",
        "hooks = false",
        "goals = false",
        "remote_plugin = false",
        "plugins = false",
        "tool_suggest = false",
        "shell_snapshot = false",
        "browser_use = false",
        "browser_use_external = false",
        "browser_use_full_cdp_access = false",
        "computer_use = false",
        "image_generation = false",
        "view_image = false",
        "workspace_dependencies = false",
        "sleep_tool = false",
    ),
    "rules/pa-agent.rules": ("decision = \"forbidden\"", "curl", "git", "ssh"),
    "pa_challenger.toml": (
        'name = "pa_challenger"', 'sandbox_mode = "read-only"',
        'web_search = "disabled"', 'protocol_version = "host_stage_challenge_v1"',
        'read_challenge_resource',
    ),
    "skills/stage1/SKILL.md": ("禁止读取或引用 VPA", "不得生成 Setup 订单", "loading_policy=mandatory"),
    "skills/stage2/SKILL.md": ("Brooks Router", "不计算或修改 position size", "loading_policy=mandatory"),
    "skills/joint_stage2/SKILL.md": ("pa_vpa_stage2_draft_v1", "loading_policy=mandatory", "Host fail-closed", "vpa_evidence_refs"),
    "skills/vpa_evidence/SKILL.md": ("独立的量价证据", "不创建或翻转 Brooks Setup", "loading_policy=mandatory"),
    "skills/joint_arbiter/SKILL.md": ("research-only", "不创建新 Setup", "不影响正式生产记录"),
    "skills/draft_pointer_repair/SKILL.md": (
        "JSON Pointers",
        "Patch Envelope",
        "不重新分析市场",
    ),
}


@dataclass(frozen=True)
class RuntimeAuditIssue:
    code: str
    resource: str
    detail: str = ""


@dataclass(frozen=True)
class RuntimeAuditReport:
    asset_digest: str
    resource_names: tuple[str, ...]
    issues: tuple[RuntimeAuditIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "ok": self.ok,
            "asset_digest": self.asset_digest,
            "resource_names": list(self.resource_names),
            "issues": [asdict(issue) for issue in self.issues],
        }


@dataclass(frozen=True)
class PosixPermissionExpectation:
    key: str
    path: str
    owner: str
    group: str
    mode: int
    codex_access: str

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "mode": f"{self.mode:04o}"}


@dataclass(frozen=True)
class LiveCanary:
    sentinel: str
    workspace: Path
    output_path: Path
    command: tuple[str, ...]


def _resource_documents(root: Path | None = None) -> dict[str, str]:
    if root is not None:
        return {
            name: (root / name).read_text(encoding="utf-8")
            for name in sorted(_RESOURCE_PATHS)
        }
    assets = load_codex_runtime_assets()
    result: dict[str, str] = {}
    for name, role in sorted(_RESOURCE_PATHS.items()):
        if name == "AGENTS.md":
            result[name] = assets.agents_text
        elif name == "config.toml":
            result[name] = assets.config_text
        elif name == "rules/pa-agent.rules":
            result[name] = assets.rules_text
        elif name == "pa_challenger.toml":
            result[name] = assets.challenger_text
        elif name in GUIDANCE_PATHS.values():
            key = next(key for key, path in GUIDANCE_PATHS.items() if path == name)
            result[name] = assets.guidance_documents[key]
        else:
            result[name] = assets.skill_documents[str(role)]
    return result


def _frontmatter_name(document: str) -> str | None:
    lines = document.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, separator, value = line.partition(":")
        if separator and key.strip() == "name":
            return value.strip()
    return None


def _validate_documents(documents: Mapping[str, str]) -> list[RuntimeAuditIssue]:
    issues: list[RuntimeAuditIssue] = []
    for resource in sorted(_RESOURCE_PATHS):
        text = documents.get(resource, "")
        if not text.strip():
            issues.append(RuntimeAuditIssue("missing_resource", resource))
            continue
        role = _RESOURCE_PATHS[resource]
        if role is not None:
            actual_name = _frontmatter_name(text)
            expected_name = _SKILL_NAMES[role]
            if actual_name != expected_name:
                issues.append(
                    RuntimeAuditIssue(
                        "invalid_skill_frontmatter",
                        resource,
                        f"expected name {expected_name}",
                    )
                )
        for required in _REQUIRED_RULES[resource]:
            if required not in text:
                issues.append(
                    RuntimeAuditIssue(
                        "missing_required_rule",
                        resource,
                        required,
                    )
                )
    return issues


def audit_runtime_assets(*, source_assets: Path | None = None) -> RuntimeAuditReport:
    """Validate installed package data and optionally compare it with a source tree."""
    try:
        installed = _resource_documents()
        assets = load_codex_runtime_assets()
    except (KeyError, OSError, UnicodeError) as exc:
        return RuntimeAuditReport(
            asset_digest="",
            resource_names=(),
            issues=(
                RuntimeAuditIssue(
                    "installed_resources_unreadable",
                    "pa_agent.agent_runtime.assets",
                    type(exc).__name__,
                ),
            ),
        )
    issues = _validate_documents(installed)
    if source_assets is not None:
        try:
            source = _resource_documents(Path(source_assets))
        except (OSError, UnicodeError) as exc:
            issues.append(
                RuntimeAuditIssue(
                    "source_resources_unreadable",
                    str(source_assets),
                    type(exc).__name__,
                )
            )
        else:
            issues.extend(_validate_documents(source))
            for resource in sorted(_RESOURCE_PATHS):
                if installed.get(resource) != source.get(resource):
                    issues.append(RuntimeAuditIssue("installed_resource_mismatch", resource))
    return RuntimeAuditReport(
        asset_digest=assets.asset_digest,
        resource_names=tuple(sorted(installed)),
        issues=tuple(issues),
    )


def posix_permission_contract() -> tuple[PosixPermissionExpectation, ...]:
    """Return the credential-free deployment ownership and access contract."""
    return (
        PosixPermissionExpectation(
            "source", "/opt/pa-agent", "root", "root", 0o755, "read_only"
        ),
        PosixPermissionExpectation(
            "codex_state",
            "/var/lib/pa-agent-codex",
            "root",
            "pa-agent-codex-spool",
            0o750,
            "read_only",
        ),
        PosixPermissionExpectation(
            "codex_home",
            "/var/lib/pa-agent-codex/home",
            "pa-agent-codex",
            "pa-agent-codex",
            0o700,
            "read_write",
        ),
        PosixPermissionExpectation(
            "codex_auth",
            "/var/lib/pa-agent-codex/home/auth.json",
            "pa-agent-codex",
            "pa-agent-codex",
            0o600,
            "read_write",
        ),
        PosixPermissionExpectation(
            "codex_config",
            "/var/lib/pa-agent-codex/home/config.toml",
            "root",
            "pa-agent-codex",
            0o440,
            "read_only",
        ),
        PosixPermissionExpectation(
            "codex_rules_dir",
            "/var/lib/pa-agent-codex/home/rules",
            "root",
            "pa-agent-codex",
            0o750,
            "read_only",
        ),
        PosixPermissionExpectation(
            "codex_rules",
            "/var/lib/pa-agent-codex/home/rules/pa-agent.rules",
            "root",
            "pa-agent-codex",
            0o440,
            "read_only",
        ),
        PosixPermissionExpectation(
            "job_root",
            "/var/lib/pa-agent-codex/jobs",
            "pa-agent",
            "pa-agent-codex-spool",
            0o2750,
            "read_only",
        ),
        PosixPermissionExpectation(
            "inbox_root",
            "/var/lib/pa-agent-codex/inbox",
            "root",
            "pa-agent-codex-spool",
            0o2770,
            "read_write",
        ),
        PosixPermissionExpectation(
            "results_root",
            "/var/lib/pa-agent-codex/results",
            "root",
            "pa-agent-codex-spool",
            0o2770,
            "read_write",
        ),
        PosixPermissionExpectation(
            "runtime_root",
            "/var/lib/pa-agent-codex/runtime",
            "pa-agent-codex",
            "pa-agent-codex",
            0o700,
            "read_write",
        ),
        PosixPermissionExpectation(
            "outbox_root",
            "/var/lib/pa-agent-codex/outbox",
            "pa-agent-codex",
            "pa-agent-codex",
            0o700,
            "read_write",
        ),
        PosixPermissionExpectation(
            "provider_credential",
            "/etc/pa-agent/api_key",
            "root",
            "pa-agent",
            0o640,
            "none",
        ),
        PosixPermissionExpectation(
            "codex_deepseek_credential",
            "/etc/pa-agent/pa-agent-codex-secret.env",
            "root",
            "root",
            0o600,
            "none",
        ),
        PosixPermissionExpectation(
            "production_database",
            "/var/lib/pa-agent/records",
            "pa-agent",
            "pa-agent",
            0o700,
            "none",
        ),
        PosixPermissionExpectation(
            "paper_ledger",
            "/var/lib/pa-agent/trade_records",
            "pa-agent",
            "pa-agent",
            0o700,
            "none",
        ),
    )


def audit_posix_permissions() -> tuple[RuntimeAuditIssue, ...]:
    """Inspect metadata only; never open credential or database contents."""
    if os.name != "posix":
        return (RuntimeAuditIssue("unsupported_platform", "permissions", os.name),)
    import grp
    import pwd

    issues: list[RuntimeAuditIssue] = []
    try:
        codex = pwd.getpwnam("pa-agent-codex")
        codex_groups = {codex.pw_gid}
        codex_groups.update(
            group.gr_gid for group in grp.getgrall() if codex.pw_name in group.gr_mem
        )
    except KeyError:
        return (RuntimeAuditIssue("missing_identity", "pa-agent-codex"),)
    for expectation in posix_permission_contract():
        path = Path(expectation.path)
        try:
            metadata = path.stat()
            owner = pwd.getpwuid(metadata.st_uid).pw_name
            group = grp.getgrgid(metadata.st_gid).gr_name
        except (OSError, KeyError) as exc:
            issues.append(
                RuntimeAuditIssue(
                    "permission_metadata_unavailable",
                    expectation.key,
                    type(exc).__name__,
                )
            )
            continue
        mode = stat.S_IMODE(metadata.st_mode)
        if (owner, group, mode) != (expectation.owner, expectation.group, expectation.mode):
            issues.append(
                RuntimeAuditIssue(
                    "permission_metadata_mismatch",
                    expectation.key,
                    f"owner={owner} group={group} mode={mode:04o}",
                )
            )
        shift = (
            6
            if metadata.st_uid == codex.pw_uid
            else 3
            if metadata.st_gid in codex_groups
            else 0
        )
        bits = (mode >> shift) & 0o7
        actual_access = (
            "read_write" if bits & 0o2 else "read_only" if bits & 0o4 else "none"
        )
        if actual_access != expectation.codex_access:
            issues.append(
                RuntimeAuditIssue(
                    "codex_access_mismatch",
                    expectation.key,
                    f"expected={expectation.codex_access} actual={actual_access}",
                )
            )
    source_root = Path("/opt/pa-agent")
    if source_root.is_dir():
        for path in source_root.rglob("*"):
            try:
                metadata = path.lstat()
            except OSError as exc:
                issues.append(
                    RuntimeAuditIssue(
                        "permission_metadata_unavailable",
                        "source_tree",
                        type(exc).__name__,
                    )
                )
                continue
            relative = str(path.relative_to(source_root))
            if stat.S_ISLNK(metadata.st_mode):
                issues.append(RuntimeAuditIssue("source_symlink_forbidden", relative))
                continue
            mode = stat.S_IMODE(metadata.st_mode)
            if metadata.st_uid != 0 or mode & 0o022:
                issues.append(
                    RuntimeAuditIssue(
                        "source_tree_not_root_read_only",
                        relative,
                        f"mode={mode:04o}",
                    )
                )
            required = 0o005 if stat.S_ISDIR(metadata.st_mode) else 0o004
            if mode & required != required:
                issues.append(
                    RuntimeAuditIssue(
                        "source_tree_not_codex_readable",
                        relative,
                        f"mode={mode:04o}",
                    )
                )
    return tuple(issues)


def prepare_live_canary(
    *,
    workspace: Path,
    output_path: Path,
    codex_binary: str,
    model: str,
    reasoning_effort: str,
) -> LiveCanary:
    """Create a harmless schema-bound job whose answer is known only to a role Skill."""
    sentinel = f"pa-runtime-{uuid4().hex}"
    workspace = Path(workspace).resolve()
    output_path = Path(output_path).resolve()
    workspace.mkdir(parents=True, exist_ok=False)
    skill_dir = workspace / ".agents" / "skills" / "pa-runtime-audit-canary"
    skill_dir.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text(
        "# Runtime canary\n\n"
        "For this harmless audit, you must use $pa-runtime-audit-canary and return only "
        "the schema-bound JSON. Do not use tools or inspect any other path.\n",
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        "---\nname: pa-runtime-audit-canary\n"
        "description: Prove workspace AGENTS and this role Skill are discovered.\n---\n\n"
        f"Set `agents_sentinel` to exactly `{sentinel}`. Set `skill_observed` to true.\n",
        encoding="utf-8",
    )
    schema_path = workspace / "output_schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": False,
                "required": ["agents_sentinel", "skill_observed"],
                "properties": {
                    "agents_sentinel": {"type": "string"},
                    "skill_observed": {"type": "boolean"},
                },
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    prompt = (
        "Use $pa-runtime-audit-canary. "
        "Follow the workspace AGENTS instructions for the runtime audit. "
        "Return only the required JSON object."
    )
    command = (
        codex_binary,
        "exec",
        prompt,
        "--ephemeral",
        "--strict-config",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--model",
        model,
        "--cd",
        str(workspace),
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "--config",
        'approval_policy="never"',
        "--config",
        f'model_reasoning_effort="{reasoning_effort}"',
        "--config",
        'web_search="disabled"',
        "--config",
        "features.shell_tool=false",
        "--config",
        "tools.web_search=false",
    )
    return LiveCanary(sentinel, workspace, output_path, command)


def run_live_canary(
    *,
    codex_binary: str,
    model: str,
    reasoning_effort: str,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[bool, str]:
    """Run the explicit online canary; any mismatch blocks deployment."""
    with tempfile.TemporaryDirectory(prefix="pa-agent-codex-canary-") as temporary:
        root = Path(temporary)
        canary = prepare_live_canary(
            workspace=root / "workspace",
            output_path=root / "result.json",
            codex_binary=codex_binary,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        environment = dict(os.environ)
        environment.pop("OPENAI_API_KEY", None)
        environment.pop("CODEX_API_KEY", None)
        completed = process_runner(
            list(canary.command),
            cwd=canary.workspace,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )
        if completed.returncode != 0 or not canary.output_path.is_file():
            return False, f"Codex live canary failed (exit status {completed.returncode})"
        try:
            result = json.loads(canary.output_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False, "Codex live canary returned invalid JSON"
        if result != {"agents_sentinel": canary.sentinel, "skill_observed": True}:
            return False, "Codex live canary did not observe workspace AGENTS and role Skill"
        return True, "Codex live canary observed workspace AGENTS and role Skill"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit packaged Codex runtime assets")
    parser.add_argument("--source-assets", type=Path)
    parser.add_argument("--check-permissions", action="store_true")
    parser.add_argument("--live-canary", action="store_true")
    parser.add_argument("--codex-binary", default="codex")
    parser.add_argument("--model", default="")
    parser.add_argument("--reasoning-effort", default="high")
    args = parser.parse_args(argv)
    report = audit_runtime_assets(source_assets=args.source_assets)
    payload = report.to_dict()
    payload["permission_contract"] = [item.to_dict() for item in posix_permission_contract()]
    permission_issues = audit_posix_permissions() if args.check_permissions else ()
    if permission_issues:
        payload["permission_issues"] = [asdict(issue) for issue in permission_issues]
    canary_ok = True
    if args.live_canary:
        if not args.model:
            canary_ok, message = False, "--model is required for the live canary"
        else:
            canary_ok, message = run_live_canary(
                codex_binary=args.codex_binary,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
            )
        payload["live_canary"] = {"ok": canary_ok, "message": message}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if report.ok and not permission_issues and canary_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
