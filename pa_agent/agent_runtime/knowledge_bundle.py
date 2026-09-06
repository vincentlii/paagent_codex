"""Deterministic section bundles for PA-selected runtime knowledge."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable

from pa_agent.ai.prompt_assembler import select_agent_knowledge_text

_HEADING = re.compile(r"^\s*(#{1,6}\s+.+|§\s*\S.+)$")


def build_knowledge_bundle(
    *,
    prompt_dir: Path,
    knowledge_ids: Iterable[str],
    role: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load only selected documents and publish searchable immutable sections."""
    modules: dict[str, Any] = {}
    manifest_entries: list[dict[str, Any]] = []
    root = Path(prompt_dir).resolve()
    for load_order, raw_id in enumerate(
        dict.fromkeys(str(item) for item in knowledge_ids),
        start=1,
    ):
        relative = Path(raw_id)
        if relative.is_absolute() or relative.as_posix() != raw_id.replace("\\", "/"):
            raise ValueError(f"invalid knowledge document id: {raw_id}")
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(f"unknown knowledge document: {raw_id}")
        source_text = path.read_text(encoding="utf-8")
        text = select_agent_knowledge_text(
            role=role,
            module_id=raw_id,
            text=source_text,
        )
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        sections = _split_sections(raw_id, text)
        framework = "vpa" if raw_id.startswith("wyckoff_vpa/") else "brooks"
        modules[raw_id] = {
            "knowledge_module_id": raw_id,
            "version": digest[:16],
            "sha256": digest,
            "sections": sections,
        }
        manifest_entries.append(
            {
                "knowledge_module_id": raw_id,
                "framework": framework,
                "role_allowlist": [role],
                # The allowlist has already been narrowed by Stage 1 or the
                # Brooks Router.  In the semantic-parity release every selected
                # module is mandatory; later experiments may demote modules to
                # on-demand only after non-inferiority is demonstrated.
                "loading_policy": "mandatory",
                "load_order": load_order,
                "context_tags": _context_tags(raw_id),
                "version": digest[:16],
                "sha256": digest,
                "source_sha256": source_digest,
                "content_bytes": len(text.encode("utf-8")),
                "selection_profile": f"{role}_semantic_parity_v1",
                "source_note": raw_id,
                "section_ids": [item["section_id"] for item in sections],
            }
        )
    return modules, {"schema_version": 1, "modules": manifest_entries}


def _split_sections(module_id: str, text: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    chunks: list[tuple[str, list[str]]] = []
    title = "Overview"
    body: list[str] = []
    for line in lines:
        if _HEADING.match(line) and body:
            chunks.append((title, body))
            title = line.strip().lstrip("#").strip()
            body = []
        elif _HEADING.match(line):
            title = line.strip().lstrip("#").strip()
        else:
            body.append(line)
    if body or not chunks:
        chunks.append((title, body))

    sections: list[dict[str, str]] = []
    for index, (heading, section_lines) in enumerate(chunks, start=1):
        content = "\n".join(section_lines).strip()
        section_id = f"{module_id}#s{index:03d}"
        sections.append(
            {
                "section_id": section_id,
                "title": heading,
                "text": content,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )
    return sections


def _context_tags(module_id: str) -> list[str]:
    stem = Path(module_id).stem.lower()
    return [part for part in re.split(r"[^a-z0-9]+", stem) if part]
