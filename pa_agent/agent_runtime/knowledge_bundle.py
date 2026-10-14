"""Deterministic section bundles for PA-selected runtime knowledge."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable

from pa_agent.ai.prompt_assembler import select_agent_knowledge_text

_HEADING = re.compile(r"^\s*(#{1,6}\s+.+|§\s*\S.+|[一二三四五六七八九十]+、.+|(?:步骤|规则)\d+[：:].+|【[^】]+】)\s*$")
_SECTION = re.compile(r"<!-- knowledge: ([a-z0-9_-]+)(?:; aliases: ([^<>\n]*))? -->")


def build_knowledge_bundle(
    *,
    prompt_dir: Path,
    knowledge_ids: Iterable[str],
    role: str,
    joint_stage2: bool = False,
    layered_stage2_research: bool = False,
    knowledge_selection: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load only selected documents and publish searchable immutable sections."""
    if layered_stage2_research and (role != "stage2" or not joint_stage2):
        raise ValueError("layered knowledge is limited to joint Stage 2 research")
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
            joint_stage2=joint_stage2,
        )
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        sections = _split_sections(raw_id, text)
        research_candidate = layered_stage2_research and (
            raw_id.endswith("交易策略.txt")
            or raw_id in {"文件13-窄通道与宽通道策略.txt", "文件15-二次入场机会.txt"}
        )
        # Keep recognition documents and universal rules complete. For the
        # research-only strategy candidates, front-load the opening principles
        # and every explicitly headed exception; the rest remains frozen and
        # available by exact section ID. No production Job uses this branch.
        baseline_section_ids = [
            section["section_id"] for index, section in enumerate(sections)
            if index < 3 or any(term in section["title"] for term in (
                "例外", "陷阱", "反证", "风险", "禁止", "失败", "局限",
            ))
        ] if research_candidate else []
        layered = research_candidate and len(baseline_section_ids) < len(sections)
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
                **((knowledge_selection or {}).get(raw_id, {})),
                # The allowlist has already been narrowed by Stage 1 or the
                # Brooks Router.  In the semantic-parity release every selected
                # module is mandatory; later experiments may demote modules to
                # on-demand only after non-inferiority is demonstrated.
                "loading_policy": "on_demand" if layered else "mandatory",
                **({"baseline_section_ids": baseline_section_ids} if layered else {}),
                "load_order": load_order,
                "context_tags": _context_tags(raw_id),
                "version": digest[:16],
                "sha256": digest,
                "source_sha256": source_digest,
                "content_bytes": len(text.encode("utf-8")),
                "selection_profile": (
                    "layered_stage2_research_v1" if layered_stage2_research
                    else "joint_stage2_semantic_parity_v1" if joint_stage2
                    else f"{role}_semantic_parity_v1"
                ),
                "source_note": raw_id,
                "section_ids": [item["section_id"] for item in sections],
            }
        )
    return modules, {"schema_version": 2, "modules": manifest_entries}


def _split_sections(module_id: str, text: str) -> list[dict[str, Any]]:
    # Keep every character, including headings and fenced examples. Stable
    # anchors live in the source, so reorder/rename does not invalidate IDs.
    lines = text.splitlines(keepends=True)
    chunks: list[tuple[str, list[str]]] = []
    title = "Overview"
    body: list[str] = []
    fence: str | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
        if fence is None and _HEADING.match(line):
            if body:
                chunks.append((title, body))
            title = line.strip().lstrip("#").strip()
            body = []
        body.append(line)
    if body or not chunks:
        chunks.append((title, body))

    sections: list[dict[str, Any]] = []
    seen: set[str] = set()
    for heading, section_lines in chunks:
        content = "".join(section_lines)
        # Metadata belongs at the section start, never inside a quoted example.
        metadata_line = 1 if section_lines and _HEADING.match(section_lines[0]) else 0
        anchor = (
            _SECTION.fullmatch(section_lines[metadata_line].strip())
            if len(section_lines) > metadata_line else None
        )
        # Custom legacy prompt packs need no migration. Their unanchored IDs
        # are title-derived. Indistinguishable legacy repeats need an occurrence
        # suffix; only explicit source anchors promise cross-revision stability.
        key = anchor.group(1) if anchor else hashlib.sha256(heading.encode()).hexdigest()[:16]
        section_id = f"{module_id}#{key}"
        if section_id in seen and not anchor:
            base_id = section_id
            occurrence = 2
            while section_id in seen:
                section_id = f"{base_id}-{occurrence}"
                occurrence += 1
        if section_id in seen:
            raise ValueError(f"duplicate knowledge section id: {section_id}")
        seen.add(section_id)
        aliases = [value.strip() for value in (anchor.group(2) or "").split("|") if value.strip()] if anchor else []
        sections.append(
            {
                "section_id": section_id,
                "title": heading,
                "text": content,
                "aliases": aliases,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )
    return sections


def _context_tags(module_id: str) -> list[str]:
    stem = Path(module_id).stem.lower()
    return [part for part in re.split(r"[^a-z0-9]+", stem) if part]
