"""Host-owned contract for bounded Codex draft repair."""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Iterable

_MARKER = "PA_REPAIR_CONTEXT_JSON="

PATCH_ENVELOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "status",
        "changed_pointers",
        "replacement_fragments",
        "supporting_evidence_ids",
        "unrepairable_reason",
    ],
    "properties": {
        "status": {"type": "string", "enum": ["repaired", "unrepairable"]},
        "changed_pointers": {
            "type": "array",
            "items": {"type": "string"},
        },
        "replacement_fragments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["pointer", "operation", "value_json"],
                "properties": {
                    "pointer": {"type": "string"},
                    "operation": {"type": "string", "enum": ["replace", "remove"]},
                    "value_json": {"type": ["string", "null"]},
                },
            },
        },
        "supporting_evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "unrepairable_reason": {"type": ["string", "null"]},
    },
}


def append_repair_context(
    feedback: str,
    *,
    original_draft: str,
    findings: Iterable[str],
    editable_pointers: Iterable[str],
) -> str:
    """Append a machine-readable, size-bounded repair authority block."""
    finding_list = [str(item) for item in findings]
    payload = {
        "schema_version": 1,
        "original_draft": str(original_draft),
        "findings": finding_list,
        "typed_findings": _typed_repair_findings(finding_list, str(feedback)),
        "editable_pointers": sorted({str(item) for item in editable_pointers}),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"{feedback.rstrip()}\n\n{_MARKER}{encoded}"


def extract_repair_context(messages: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(list(messages)):
        if str(message.get("role") or "") != "user":
            continue
        content = str(message.get("content") or "")
        marker_index = content.rfind(_MARKER)
        if marker_index < 0:
            continue
        try:
            payload = json.loads(content[marker_index + len(_MARKER) :].strip())
        except json.JSONDecodeError as exc:
            raise ValueError("repair context is malformed") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("repair context schema is unsupported")
        draft = payload.get("original_draft")
        pointers = payload.get("editable_pointers")
        findings = payload.get("findings")
        typed_findings = payload.get("typed_findings") or []
        if (
            not isinstance(draft, str)
            or not isinstance(pointers, list)
            or not isinstance(findings, list)
            or not isinstance(typed_findings, list)
            or any(not isinstance(item, dict) for item in typed_findings)
        ):
            raise ValueError("repair context is incomplete")
        try:
            parsed = json.loads(draft)
        except json.JSONDecodeError as exc:
            raise ValueError("repair draft must be a parseable JSON object") from exc
        if not isinstance(parsed, dict):
            raise ValueError("repair draft must be a JSON object")
        normalized = sorted({str(item) for item in pointers})
        if not normalized or any(not item.startswith("/") for item in normalized):
            raise ValueError("repair context has no authorized JSON Pointer")
        return {
            "schema_version": 1,
            "original_draft": draft,
            "findings": [str(item) for item in findings],
            "typed_findings": [dict(item) for item in typed_findings],
            "editable_pointers": normalized,
        }
    return None


def strip_repair_context(content: str) -> str:
    """Remove the host-only machine block before constructing model core context."""
    return str(content).split(_MARKER, 1)[0].rstrip()


_EXPLICIT_PRICE_FINDING_RE = re.compile(
    r"(?P<path>[A-Za-z_][A-Za-z0-9_.\[\]]*)\s+"
    r"K(?P<seq>\d+)\.(?P<field>open|high|low|close)\s+states\s+"
    r"(?P<stated>[-+]?\d+(?:\.\d+)?),\s+but frozen bar value is\s+"
    r"(?P<actual>[-+]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def _typed_repair_findings(
    findings: Iterable[str],
    feedback: str,
) -> list[dict[str, Any]]:
    """Project deterministic repair facts without changing Validator authority."""
    typed: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    combined = "\n".join([*(str(item) for item in findings), str(feedback)])
    for match in _EXPLICIT_PRICE_FINDING_RE.finditer(combined):
        path = match.group("path")
        pointer = field_path_to_pointer(path)
        key = ("explicit_bar_price_mismatch", pointer or path)
        if key in seen:
            continue
        seen.add(key)
        seq = int(match.group("seq"))
        typed.append(
            {
                "code": key[0],
                "path": path,
                "pointer": pointer,
                "message": match.group(0),
                "fact_refs": [f"PA.BAR.K{seq}"],
                "stated_value": float(match.group("stated")),
                "frozen_value": float(match.group("actual")),
            }
        )
    for finding in findings:
        text = str(finding)
        pointer = field_path_to_pointer(text)
        key = ("validator_finding", pointer or text)
        if key in seen:
            continue
        seen.add(key)
        typed.append(
            {
                "code": key[0],
                "path": text if pointer else None,
                "pointer": pointer,
                "message": text,
                "fact_refs": [],
                "stated_value": None,
                "frozen_value": None,
            }
        )
    return typed


def apply_patch_envelope(
    repair_context: dict[str, Any],
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Apply only explicitly authorized replacements to the frozen prior draft."""
    if envelope.get("status") != "repaired":
        raise ValueError(
            str(envelope.get("unrepairable_reason") or "Codex declared repair unrepairable")
        )
    allowed = set(map(str, repair_context["editable_pointers"]))
    changed = list(map(str, envelope.get("changed_pointers") or []))
    fragments = envelope.get("replacement_fragments") or []
    if not changed:
        raise ValueError("repaired envelope must contain an authorized change")
    if len(changed) != len(set(changed)) or set(changed) - allowed:
        raise ValueError("repair changed_pointers exceed Validator authority")
    if not isinstance(fragments, list):
        raise ValueError("repair replacement_fragments must be an array")
    fragment_pointers = [
        str(item.get("pointer") or "") for item in fragments if isinstance(item, dict)
    ]
    if len(fragment_pointers) != len(fragments) or fragment_pointers != changed:
        raise ValueError("repair fragment pointers do not match changed_pointers")
    original = json.loads(str(repair_context["original_draft"]))
    result = copy.deepcopy(original)
    for fragment in fragments:
        pointer = str(fragment["pointer"])
        if pointer not in allowed:
            raise ValueError("repair fragment exceeds Validator authority")
        operation = str(fragment.get("operation") or "")
        if operation == "remove":
            _remove_pointer(result, pointer)
            continue
        if operation != "replace" or not isinstance(fragment.get("value_json"), str):
            raise ValueError("repair fragment operation is invalid")
        try:
            value = json.loads(fragment["value_json"])
        except json.JSONDecodeError as exc:
            raise ValueError("repair value_json is invalid") from exc
        _assign_pointer(result, pointer, value)
    # A field/array edit is not permission to select a different decision-tree
    # question. In particular, renaming duplicates can hide a semantic mismatch.
    for name in ("gate_trace", "decision_trace"):
        before = {str(item.get("node_id")) for item in original.get(name, [])
                  if isinstance(item, dict)}
        after = {str(item.get("node_id")) for item in result.get(name, [])
                 if isinstance(item, dict)}
        if after - before:
            raise ValueError("bounded repair cannot introduce new trace node identities")
    return result


def dotted_path_to_pointer(path: tuple[str | int, ...]) -> str:
    return "/" + "/".join(_escape_pointer_part(str(item)) for item in path)


def field_path_to_pointer(value: str) -> str | None:
    """Convert a validator field path to JSON Pointer; reject prose findings."""
    prefix = str(value).partition(":")
    candidate = prefix[2] if prefix[1] and prefix[0] in {
        "s1", "s2", "gate", "trace", "vpa", "metrics", "signal_chain", "breakout_price"
    } else str(value)
    candidate = candidate.strip().split(None, 1)[0].rstrip(":")
    if not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*(?:(?:\.[A-Za-z_][A-Za-z0-9_]*)|(?:\[\d+\]))*",
        candidate,
    ):
        return None
    parts: list[str] = []
    for match in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]", candidate):
        parts.append(match.group(1) or match.group(2))
    return "/" + "/".join(_escape_pointer_part(item) for item in parts)


def _parts(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise ValueError("repair path must be an absolute JSON Pointer")
    return [item.replace("~1", "/").replace("~0", "~") for item in pointer[1:].split("/")]


def _assign_pointer(root: Any, pointer: str, value: Any) -> None:
    parts = _parts(pointer)
    if not parts:
        raise ValueError("root replacement is not allowed")
    parent = root
    for part in parts[:-1]:
        if isinstance(parent, list) and part.isdigit() and int(part) < len(parent):
            parent = parent[int(part)]
        elif isinstance(parent, dict) and part in parent:
            parent = parent[part]
        else:
            raise ValueError("repair pointer parent does not exist")
    leaf = parts[-1]
    if isinstance(parent, list) and leaf.isdigit() and int(leaf) < len(parent):
        parent[int(leaf)] = value
    elif isinstance(parent, dict):
        parent[leaf] = value
    else:
        raise ValueError("repair pointer target is invalid")


def _remove_pointer(root: Any, pointer: str) -> None:
    parts = _parts(pointer)
    if not parts:
        raise ValueError("root removal is not allowed")
    parent = root
    for part in parts[:-1]:
        if isinstance(parent, list) and part.isdigit() and int(part) < len(parent):
            parent = parent[int(part)]
        elif isinstance(parent, dict) and part in parent:
            parent = parent[part]
        else:
            raise ValueError("repair pointer parent does not exist")
    leaf = parts[-1]
    if isinstance(parent, list) and leaf.isdigit() and int(leaf) < len(parent):
        parent.pop(int(leaf))
    elif isinstance(parent, dict) and leaf in parent:
        del parent[leaf]
    else:
        raise ValueError("repair pointer target does not exist")


def _escape_pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")
