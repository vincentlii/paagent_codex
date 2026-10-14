"""Load and fingerprint the packaged Codex analysis rules and role skills."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from types import MappingProxyType
from typing import Mapping

_SKILL_PATHS = {
    "stage1": "skills/stage1/SKILL.md",
    "stage2": "skills/stage2/SKILL.md",
    "joint_stage2": "skills/joint_stage2/SKILL.md",
    "vpa_evidence": "skills/vpa_evidence/SKILL.md",
    "joint_arbiter": "skills/joint_arbiter/SKILL.md",
    "draft_pointer_repair": "skills/draft_pointer_repair/SKILL.md",
}
GUIDANCE_PATHS = {
    "role_contracts": "guidance/role_contracts.toml",
    "stage_challenge": "guidance/stage_challenge.md",
    "layered_knowledge": "guidance/layered_stage2.md",
}


@dataclass(frozen=True)
class CodexRuntimeAssets:
    runtime_version: str
    asset_digest: str
    agents_text: str
    skill_documents: Mapping[str, str]
    config_text: str
    rules_text: str
    challenger_text: str
    guidance_documents: Mapping[str, str]


def load_codex_runtime_assets() -> CodexRuntimeAssets:
    root = files("pa_agent.agent_runtime").joinpath("assets")
    agents_text = root.joinpath("AGENTS.md").read_text(encoding="utf-8")
    config_text = root.joinpath("config.toml").read_text(encoding="utf-8")
    rules_text = root.joinpath("rules/pa-agent.rules").read_text(encoding="utf-8")
    challenger_text = root.joinpath("pa_challenger.toml").read_text(encoding="utf-8")
    skills = {
        role: root.joinpath(path).read_text(encoding="utf-8")
        for role, path in _SKILL_PATHS.items()
    }
    guidance = {key: root.joinpath(path).read_text(encoding="utf-8")
                for key, path in GUIDANCE_PATHS.items()}
    payload = {
        "AGENTS.md": agents_text,
        "config.toml": config_text,
        "rules/pa-agent.rules": rules_text,
        "pa_challenger.toml": challenger_text,
        **{path: skills[role] for role, path in _SKILL_PATHS.items()},
        **{path: guidance[key] for key, path in GUIDANCE_PATHS.items()},
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return CodexRuntimeAssets(
        runtime_version=f"codex-runtime-v1-{digest[:16]}",
        asset_digest=digest,
        agents_text=agents_text,
        skill_documents=MappingProxyType(skills),
        config_text=config_text,
        rules_text=rules_text,
        challenger_text=challenger_text,
        guidance_documents=MappingProxyType(guidance),
    )
