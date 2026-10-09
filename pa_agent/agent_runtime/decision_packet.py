"""Deterministic, replayable decision packets for bounded Codex analysis."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

STAGE1_PACKET_VERSION = "bounded_stage1_packet_v2"
_SPINE_IDS = frozenset({
    *(f"PA.WINDOW.K{size}" for size in (5, 8, 10, 20, 40)),
    *(f"PA.COMPARE.K1.PRIOR{size}" for size in (5, 10, 20)),
    *(f"PA.PAIR.K{seq}.K{seq + 1}" for seq in range(1, 6)),
})


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False,
                      separators=(",", ":"), sort_keys=True).encode("utf-8")


def _measurement_tables(facts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Lossless tabular encoding: share repeated keys, never round or summarize."""
    groups: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = {}
    for fact in facts:
        if not str(fact.get("fact_id", "")).startswith(("PA.BAR.", "PA.INDICATOR.")):
            continue
        value = fact["value"]
        columns = tuple(value)
        key = (fact["domain"], fact["fact_type"], columns)
        table = groups.setdefault(key, {
            "domain": fact["domain"], "fact_type": fact["fact_type"],
            "columns": ["fact_id", "bar_range", *columns], "rows": [],
        })
        table["rows"].append([fact["fact_id"], fact["bar_range"],
                              *(value[column] for column in columns)])
    return list(groups.values())


def build_stage1_decision_packet(
    *,
    snapshot_hash: str,
    cutoff_ts_open: int,
    bars: Sequence[Mapping[str, Any]],
    facts: Sequence[Mapping[str, Any]],
    core_context: Mapping[str, Any],
    knowledge_documents: Mapping[str, Any],
    knowledge_manifest: Mapping[str, Any],
    role_skill: str = "",
) -> dict[str, Any]:
    """Compile facts without interpreting market structure or choosing a setup."""
    columns = ("seq", "ts_open", "open", "high", "low", "close", "closed")
    ordered_bars = sorted((dict(item) for item in bars), key=lambda item: int(item["seq"]))
    fact_rows = [dict(item) for item in facts]
    fact_by_id = {str(item.get("fact_id")): item for item in fact_rows if item.get("fact_id")}
    spine = [fact_by_id[fact_id] for fact_id in sorted(_SPINE_IDS) if fact_id in fact_by_id]

    foundation: list[dict[str, Any]] = []
    section_index: list[dict[str, Any]] = []
    for module in knowledge_manifest.get("modules", []):
        if not isinstance(module, Mapping):
            continue
        module_id = str(module.get("knowledge_module_id") or "")
        document = knowledge_documents.get(module_id) or {}
        sections = document.get("sections", []) if isinstance(document, Mapping) else []
        for section in sections:
            if not isinstance(section, Mapping):
                continue
            mandatory = module.get("loading_policy", "mandatory") == "mandatory"
            # An embedded section already carries its ID, title, digest and
            # exact text. Index only sections whose text is not in this packet.
            if not mandatory:
                section_index.append({
                    "knowledge_module_id": module_id,
                    "section_id": section.get("section_id"),
                    "title": section.get("title"),
                    "sha256": section.get("sha256"),
                    "aliases": list(section.get("aliases") or []),
                })
            # The Host manifest owns knowledge selection. A second filename
            # allowlist here silently omitted mandatory signal/counterexample
            # modules while the Skill still required their complete delivery.
            if mandatory:
                foundation.append({
                    "knowledge_module_id": module_id,
                    "section_id": section.get("section_id"),
                    "title": section.get("title"),
                    "sha256": section.get("sha256"),
                    "aliases": list(section.get("aliases") or []),
                    "text": section.get("text"),
                })

    protocol = dict(core_context.get("role_protocol") or {})
    # These include executable Windows semantics, not just duplicate prose.
    # Keep them until an explicit rule-by-rule projection proves equivalence.
    protocol.pop("semantic_parity_policy", None)
    htf = core_context.get("higher_timeframe") or {}
    packet: dict[str, Any] = {
        "packet_version": STAGE1_PACKET_VERSION,
        "identity": {
            "snapshot_hash": str(snapshot_hash),
            "cutoff_ts_open": int(cutoff_ts_open),
            "role": "stage1",
        },
        "role_contract": protocol,
        "role_skill": str(role_skill),
        "price_frame": {
            "columns": list(columns),
            "rows": [[item.get(key) for key in columns] for item in ordered_bars],
            "ordering": "K1 newest closed; increasing seq is older",
        },
        "pa_fact_spine": spine,
        "bar_measurements": _measurement_tables(fact_rows),
        "available_fact_ids": sorted(fact_by_id),
        "higher_timeframe": htf,
        "previous_analysis": core_context.get("previous_analysis"),
        "knowledge": {
            "foundation_exact_sections": foundation,
            "section_index": section_index,
            "retrieval_rule": "Read exact indexed sections only when they can change a named unresolved conclusion.",
        },
        "investigation_envelope": {
            "max_optional_calls": 2,
            "max_fact_batches": 1,
            "max_knowledge_batches": 1,
        },
    }
    packet["packet_digest"] = hashlib.sha256(_canonical_bytes(packet)).hexdigest()
    packet["packet_bytes"] = len(_canonical_bytes(packet))
    return packet


def verify_stage1_decision_packet(packet: Mapping[str, Any]) -> None:
    candidate = dict(packet)
    expected = str(candidate.pop("packet_digest", ""))
    candidate.pop("packet_bytes", None)
    actual = hashlib.sha256(_canonical_bytes(candidate)).hexdigest()
    if not expected or expected != actual:
        raise ValueError("Stage 1 decision packet digest mismatch")
