"""Minimal stdio MCP server exposing one frozen job's read-only evidence."""

from __future__ import annotations

import argparse
import json
import os
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from pa_agent.agent_runtime.evidence import (
    EvidenceAccessDenied,
    EvidenceBudgetExceeded,
    FrozenEvidenceGateway,
)
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore

_PROTOCOL_VERSION = "2025-06-18"
_TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "get_role_baseline",
            "description": "Read the complete Host-selected mandatory baseline in frozen order, without summarizing or dropping resources. Not for repair jobs.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "measure_price_window",
            "description": "Measure explicit frozen price window geometry (1-100 bars); no market labels. Receipt is not a catalog fact ID.",
            "inputSchema": {"type": "object", "additionalProperties": False,
                            "properties": {key: {"type": "integer", "minimum": 1} for key in ("older_seq", "newer_seq")},
                            "required": ["older_seq", "newer_seq"]},
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "project_price_leg",
            "description": "Compute origin + (end - start) from explicit frozen OHLC anchors in chronological order within 100 bars. Not a target recommendation.",
            "inputSchema": {"type": "object", "additionalProperties": False,
                            "properties": {
                                **{key: {"type": "integer", "minimum": 1} for key in ("start_seq", "end_seq", "origin_seq")},
                                **{key: {"type": "string", "enum": ["open", "high", "low", "close"]} for key in ("start_field", "end_field", "origin_field")},
                            }, "required": ["start_seq", "start_field", "end_seq", "end_field", "origin_seq", "origin_field"]},
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "get_job_manifest",
            "description": "Read the immutable job identity, cutoff, hashes, and expiry.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "get_market_core",
            "description": "Read the bounded role core context selected by PA Agent.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "list_fact_ids",
            "description": "List visible frozen fact IDs. PA role baselines already include the complete fact_id_index in core_context; use that index directly without listing it again.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prefix": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "additionalProperties": False,
            },
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "get_facts",
            "description": "Read exact fact IDs whose values are missing from the baseline, or deliberately recheck a disputed record. Batch independent IDs in one call; an index is not a value read.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "fact_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 100,
                    }
                },
                "required": ["fact_ids"],
                "additionalProperties": False,
            },
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "get_bars",
            "description": "Read an inclusive K-line sequence range from the frozen snapshot.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "older_seq": {"type": "integer", "minimum": 1},
                    "newer_seq": {"type": "integer", "minimum": 1},
                },
                "required": ["older_seq", "newer_seq"],
                "additionalProperties": False,
            },
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "get_measurements",
            "description": "Read deterministic indicator records. PA baseline price_frame already includes aligned indicator values; use it first, and call this only for a missing field or explicit recheck.",
            "inputSchema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}},
                "additionalProperties": False,
            },
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "get_price_frame",
            "description": (
                "Read up to 100 frozen PA-only bars with aligned deterministic "
                "indicator measurements; volume is never exposed to PA roles."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "older_seq": {"type": "integer", "minimum": 1},
                    "newer_seq": {"type": "integer", "minimum": 1},
                },
                "required": ["older_seq", "newer_seq"],
                "additionalProperties": False,
            },
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "get_pa_evidence",
            "description": "Read exact PA.* IDs from the supplied fact_id_index, preferably batching missing/disputed records. baseline delivery_index distinguishes delivered values from indexed-only facts.",
            "inputSchema": {
                "type": "object",
                "properties": {"fact_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100}},
                "required": ["fact_ids"],
                "additionalProperties": False,
            },
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "get_htf_evidence",
            "description": "Read exact HTF.* evidence IDs visible in this frozen role.",
            "inputSchema": {
                "type": "object",
                "properties": {"fact_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100}},
                "required": ["fact_ids"],
                "additionalProperties": False,
            },
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "get_vpa_evidence",
            "description": "Read exact VPA evidence IDs from the frozen role catalog.",
            "inputSchema": {
                "type": "object",
                "properties": {"evidence_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100}},
                "required": ["evidence_ids"],
                "additionalProperties": False,
            },
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "list_allowed_knowledge",
            "description": "List PA-selected knowledge modules and section identities for this role.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "search_allowed_knowledge",
            "description": "Search only PA-selected knowledge sections and return bounded snippets.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "knowledge_id": {"type": ["string", "null"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "read_allowed_knowledge",
            "description": "Read exact authorized sections from one PA-selected knowledge module.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "knowledge_id": {"type": "string"},
                    "section_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                },
                "required": ["knowledge_id", "section_ids"],
                "additionalProperties": False,
            },
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "read_knowledge_module",
            "description": (
                "Read one complete authorized knowledge module. Use this for every "
                "module marked loading_policy=mandatory before forming a diagnosis."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"knowledge_id": {"type": "string"}},
                "required": ["knowledge_id"],
                "additionalProperties": False,
            },
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "get_upstream_artifact",
            "description": "Read only the validated upstream artifact authorized for this role.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "get_experience_entries",
            "description": "Read the bounded historical experience selected for Stage 2.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": _TOOL_ANNOTATIONS,
        },
        {
            "name": "get_repair_findings",
            "description": "Read the prior draft and Validator-authorized JSON Pointers for this repair job.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": _TOOL_ANNOTATIONS,
        },
    ]


class EvidenceMcpServer:
    def __init__(
        self,
        gateway: FrozenEvidenceGateway,
        *,
        job_digest: str,
        audit_output: Path | None = None,
        binding_file: Path | None = None,
        job_root: Path | None = None,
    ) -> None:
        self._gateway = gateway
        self._job = gateway.frozen_job
        self._job_digest = job_digest
        self._audit_output = audit_output
        self._binding_file = binding_file
        self._binding_job_root = Path(job_root).resolve() if job_root is not None else None
        self._binding_audit_root = (
            audit_output.resolve().parent if audit_output is not None else None
        )
        if binding_file is not None and (
            self._binding_job_root is None or self._binding_audit_root is None
        ):
            raise ValueError("bound evidence server requires fixed job and audit roots")
        self._binding_state = (
            job_digest,
            str(audit_output.resolve()) if audit_output is not None else None,
        )
        self._call_lock = threading.Lock()
        self._mcp_calls = 0
        self._audit_call_indices: list[int] = []

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        if request_id is None:
            return None
        try:
            if method == "initialize":
                requested = str(
                    (message.get("params") or {}).get("protocolVersion")
                    or _PROTOCOL_VERSION
                )
                result = {
                    "protocolVersion": requested,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "pa-agent-frozen-evidence",
                        "version": "1.0",
                    },
                    "instructions": (
                        "Only frozen, role-authorized evidence is available. "
                        "Call and byte budgets are enforced; no live data is exposed."
                    ),
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": _tools()}
            elif method == "tools/call":
                params = message.get("params") or {}
                result = self._call_tool(
                    str(params.get("name") or ""),
                    params.get("arguments") or {},
                )
            else:
                return self._error(request_id, -32601, f"method not found: {method}")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (TypeError, ValueError, KeyError) as exc:
            return self._error(request_id, -32602, f"invalid params: {exc}")

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        # The SDK runs synchronous tools in a thread pool. Budget accounting and
        # its durable receipt must be one transaction, including denied calls.
        with self._call_lock:
            self._refresh_binding()
            self._mcp_calls += 1
            return self._dispatch_tool(name, arguments)

    def _refresh_binding(self) -> None:
        """Rebind a resumed PA session before serving any Stage 2 evidence."""
        if self._binding_file is None:
            return
        job_root, job_digest, audit_output = _read_binding_file(self._binding_file)
        if (
            job_root.resolve() != self._binding_job_root
            or audit_output.resolve().parent != self._binding_audit_root
        ):
            raise ValueError("evidence binding escaped its fixed runtime roots")
        state = (job_digest, str(audit_output.resolve()))
        if state == self._binding_state:
            return
        job = FrozenAgentJobStore(job_root).open(job_digest)
        self._validate_binding_transition(job)
        self._gateway = FrozenEvidenceGateway(job)
        self._job = job
        self._job_digest = job.manifest.job_digest
        self._audit_output = audit_output
        self._binding_state = state
        self._mcp_calls = 0
        self._audit_call_indices = []

    def _validate_binding_transition(self, job: Any) -> None:
        current = self._job
        continuation = job.core_context.get("pa_session_continuation")
        if (
            current.manifest.role != "stage1"
            or current.manifest.attempt != 1
            or job.manifest.role != "stage2"
            or job.manifest.attempt != 1
            or not isinstance(continuation, dict)
            or continuation.get("protocol_version") != "pa_session_continuation_v1"
            or continuation.get("parent_job_digest") != current.manifest.job_digest
            or job.manifest.task_id != current.manifest.task_id
            or job.manifest.snapshot_hash != current.manifest.snapshot_hash
            or job.manifest.cutoff_ts_open != current.manifest.cutoff_ts_open
            or dict(job.runtime.get("backend_runtime_identity") or {})
            != dict(current.runtime.get("backend_runtime_identity") or {})
        ):
            raise ValueError("evidence binding is not an authorized PA Stage 2 transition")

    def _dispatch_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "get_role_baseline":
                if arguments:
                    raise ValueError("role baseline accepts no arguments")
                resources = []
                for operation, resource_arguments in self._gateway.mandatory_read_plan():
                    result = self._dispatch_tool(operation, resource_arguments)
                    if result.get("isError"):
                        return result
                    resources.append({"operation": operation, "arguments": resource_arguments,
                                      "payload": result["structuredContent"]})
                continuation = self._gateway.pa_continuation
                payload = {
                    "baseline_version": (
                        "mandatory_pa_delta_v1" if continuation else "mandatory_baseline_v1"
                    ),
                    "resources": resources,
                }
                if continuation:
                    payload["pa_session_continuation"] = continuation
                if self._gateway.analysis_execution_enabled:
                    payload["delivery_index"] = self._gateway.baseline_delivery_index(resources)
            elif name == "measure_price_window":
                payload = {"measurement": self._gateway.measure_price_window(**arguments)}
            elif name == "project_price_leg":
                payload = {"measurement": self._gateway.project_price_leg(**arguments)}
            elif name == "get_job_manifest":
                payload = {"manifest": self._gateway.get_job_manifest()}
            elif name == "get_market_core":
                payload = {"core_context": self._gateway.get_market_core()}
            elif name == "list_fact_ids":
                payload = {
                    "fact_ids": self._gateway.list_fact_ids(
                        prefix=str(arguments.get("prefix") or ""),
                        limit=int(arguments.get("limit", 100)),
                    )
                }
            elif name == "get_facts":
                payload = {
                    "facts": self._gateway.get_facts(
                        tuple(str(item) for item in arguments["fact_ids"])
                    )
                }
            elif name == "get_bars":
                payload = {
                    "bars": self._gateway.get_bars(
                        older_seq=int(arguments["older_seq"]),
                        newer_seq=int(arguments["newer_seq"]),
                    )
                }
            elif name == "get_measurements":
                payload = {
                    "measurements": self._gateway.get_measurements(
                        limit=int(arguments.get("limit", 100))
                    )
                }
            elif name == "get_price_frame":
                payload = {
                    "price_frame": self._gateway.get_price_frame(
                        older_seq=int(arguments["older_seq"]),
                        newer_seq=int(arguments["newer_seq"]),
                    )
                }
            elif name == "get_pa_evidence":
                payload = {
                    "facts": self._gateway.get_pa_evidence(
                        tuple(str(item) for item in arguments["fact_ids"])
                    )
                }
            elif name == "get_htf_evidence":
                payload = {
                    "facts": self._gateway.get_htf_evidence(
                        tuple(str(item) for item in arguments["fact_ids"])
                    )
                }
            elif name == "get_vpa_evidence":
                payload = {
                    "evidence": self._gateway.get_vpa_evidence(
                        tuple(str(item) for item in arguments["evidence_ids"])
                    )
                }
            elif name == "list_allowed_knowledge":
                payload = {"modules": self._gateway.list_allowed_knowledge()}
            elif name == "search_allowed_knowledge":
                payload = {
                    "matches": self._gateway.search_allowed_knowledge(
                        query=str(arguments["query"]),
                        knowledge_id=(
                            str(arguments["knowledge_id"])
                            if arguments.get("knowledge_id") is not None
                            else None
                        ),
                        limit=int(arguments.get("limit", 8)),
                    )
                }
            elif name == "read_allowed_knowledge":
                payload = self._gateway.read_allowed_knowledge(
                    knowledge_id=str(arguments["knowledge_id"]),
                    section_ids=tuple(str(item) for item in arguments["section_ids"]),
                )
            elif name == "read_knowledge_module":
                payload = self._gateway.read_knowledge_module(
                    knowledge_id=str(arguments["knowledge_id"]),
                )
            elif name == "get_upstream_artifact":
                payload = {"upstream_artifact": self._gateway.get_upstream_artifact()}
            elif name == "get_experience_entries":
                payload = {"experience_entries": self._gateway.get_experience_entries()}
            elif name == "get_repair_findings":
                payload = {"repair": self._gateway.get_repair_findings()}
            else:
                raise ValueError(f"unknown tool: {name}")
        except (EvidenceAccessDenied, EvidenceBudgetExceeded) as exc:
            self._publish_audit()
            return {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            }
        payload.setdefault("snapshot_identity", self._gateway.snapshot_identity)
        self._publish_audit()
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return {
            "content": [{"type": "text", "text": encoded}],
            "structuredContent": payload,
            "isError": False,
        }

    def _publish_audit(self) -> None:
        if self._audit_output is None:
            return
        events = self._gateway.audit_events
        self._audit_call_indices.extend(
            [self._mcp_calls] * (len(events) - len(self._audit_call_indices))
        )
        payload = {
            "schema_version": 1,
            "job_digest": self._job_digest,
            "events": [{**asdict(item), "mcp_call_index": call_index}
                       for item, call_index in zip(events, self._audit_call_indices, strict=True)],
        }
        _atomic_write_json(self._audit_output, payload)

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve one frozen PA Agent evidence job")
    parser.add_argument("--job-root", type=Path)
    parser.add_argument("--job-digest")
    parser.add_argument("--audit-output", type=Path)
    parser.add_argument("--binding-file", type=Path)
    return parser


def _resolve_job_binding(args: argparse.Namespace) -> tuple[Path, str, Path | None]:
    if args.binding_file is not None:
        if args.job_root is not None or args.job_digest is not None or args.audit_output is not None:
            raise ValueError("binding-file cannot be combined with direct job arguments")
        return _read_binding_file(args.binding_file)
    if args.job_root is None or not args.job_digest:
        raise ValueError("job-root and job-digest are required without binding-file")
    return args.job_root, str(args.job_digest), args.audit_output


def _read_binding_file(path: Path) -> tuple[Path, str, Path]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("evidence binding is unreadable or malformed") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "job_root", "job_digest", "audit_output",
    } or value.get("schema_version") != 1:
        raise ValueError("evidence binding schema is invalid")
    job_root = Path(str(value.get("job_root") or ""))
    audit_output = Path(str(value.get("audit_output") or ""))
    if not job_root.is_absolute() or not audit_output.is_absolute():
        raise ValueError("evidence binding paths must be absolute")
    return job_root, str(value.get("job_digest") or ""), audit_output


def _run_official_sdk(server: EvidenceMcpServer) -> None:
    """Serve the bounded gateway through the pinned official MCP Python SDK."""
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations
    from mcp.server.mcpserver.exceptions import ToolError

    mcp = MCPServer(
        "pa-agent-frozen-evidence",
        instructions=(
            "Only frozen, role-authorized evidence is available. Calls and bytes are "
            "budgeted; no live data, database, network, or write capability is exposed."
        ),
    )

    def payload(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = server._call_tool(name, arguments)
        if result.get("isError"):
            content = result.get("content") or []
            message = content[0].get("text") if content and isinstance(content[0], dict) else name
            # Only gateway permission/budget errors reach this branch. Preserve
            # their public stop guidance; unexpected exceptions stay SDK-masked.
            raise ToolError(str(message))
        return dict(result.get("structuredContent") or {})

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def get_role_baseline() -> dict[str, Any]:
        """Read all Host-selected mandatory resources, in order and without loss, in one call."""
        return payload("get_role_baseline", {})

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def get_job_manifest() -> dict[str, Any]:
        """Read immutable job identity, cutoff, hashes, and expiry."""
        return payload("get_job_manifest", {})

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def measure_price_window(older_seq: int, newer_seq: int) -> dict[str, Any]:
        """Measure an explicit 1-100 bar frozen window; no trend/Setup classification."""
        return payload("measure_price_window", {"older_seq": older_seq, "newer_seq": newer_seq})

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def project_price_leg(start_seq: int, start_field: str, end_seq: int, end_field: str,
                          origin_seq: int, origin_field: str) -> dict[str, Any]:
        """Compute origin + (end - start) using frozen OHLC anchors, never recommend a target."""
        return payload("project_price_leg", {
            "start_seq": start_seq, "start_field": start_field, "end_seq": end_seq,
            "end_field": end_field, "origin_seq": origin_seq, "origin_field": origin_field,
        })

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def get_market_core() -> dict[str, Any]:
        """Read the bounded core context selected by PA Agent."""
        return payload("get_market_core", {})

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def list_fact_ids(prefix: str = "", limit: int = 100) -> dict[str, Any]:
        """List visible fact IDs. PA baselines already supply the complete fact_id_index
        in core_context; use that index directly without listing it again.
        """
        return payload("list_fact_ids", {"prefix": prefix, "limit": limit})

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def get_facts(fact_ids: list[str]) -> dict[str, Any]:
        """Read missing/disputed deterministic records by exact ID, batching independent IDs.
        An index or upstream reference is not a value read; prefer already delivered values.
        """
        return payload("get_facts", {"fact_ids": fact_ids})

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def get_bars(older_seq: int, newer_seq: int) -> dict[str, Any]:
        """Read an inclusive bar range inside the frozen cutoff."""
        return payload("get_bars", {"older_seq": older_seq, "newer_seq": newer_seq})

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def get_measurements(limit: int = 100) -> dict[str, Any]:
        """Read deterministic indicator records. PA baseline price_frame already includes
        aligned values; use those first, and call only for a missing field or explicit recheck.
        """
        return payload("get_measurements", {"limit": limit})

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def get_price_frame(older_seq: int, newer_seq: int) -> dict[str, Any]:
        """Read up to 100 frozen PA-only bars and aligned measurements."""
        return payload(
            "get_price_frame",
            {"older_seq": older_seq, "newer_seq": newer_seq},
        )

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def get_pa_evidence(fact_ids: list[str]) -> dict[str, Any]:
        """Read missing/disputed PA IDs from fact_id_index, batching independent records.
        baseline delivery_index distinguishes delivered values from indexed-only facts.
        """
        return payload("get_pa_evidence", {"fact_ids": fact_ids})

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def get_htf_evidence(fact_ids: list[str]) -> dict[str, Any]:
        """Read exact HTF evidence IDs from this Frozen Snapshot."""
        return payload("get_htf_evidence", {"fact_ids": fact_ids})

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def get_vpa_evidence(evidence_ids: list[str]) -> dict[str, Any]:
        """Read exact VPA evidence IDs from this role catalog."""
        return payload("get_vpa_evidence", {"evidence_ids": evidence_ids})

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def list_allowed_knowledge() -> dict[str, Any]:
        """List PA-selected knowledge modules and section IDs."""
        return payload("list_allowed_knowledge", {})

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def search_allowed_knowledge(
        query: str,
        knowledge_id: str | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        """Search only PA-selected knowledge sections."""
        return payload(
            "search_allowed_knowledge",
            {"query": query, "knowledge_id": knowledge_id, "limit": limit},
        )

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def read_allowed_knowledge(
        knowledge_id: str,
        section_ids: list[str],
    ) -> dict[str, Any]:
        """Read exact authorized sections from one knowledge module."""
        return payload(
            "read_allowed_knowledge",
            {"knowledge_id": knowledge_id, "section_ids": section_ids},
        )

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def read_knowledge_module(knowledge_id: str) -> dict[str, Any]:
        """Read one complete PA-selected mandatory knowledge module."""
        return payload("read_knowledge_module", {"knowledge_id": knowledge_id})

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def get_upstream_artifact() -> dict[str, Any]:
        """Read the validated upstream artifact authorized for this role."""
        return payload("get_upstream_artifact", {})

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def get_experience_entries() -> dict[str, Any]:
        """Read the bounded historical experience selected for Stage 2."""
        return payload("get_experience_entries", {})

    @mcp.tool(structured_output=True, annotations=ToolAnnotations(**_TOOL_ANNOTATIONS))
    def get_repair_findings() -> dict[str, Any]:
        """Read the prior draft and Validator-authorized repair pointers."""
        return payload("get_repair_findings", {})

    mcp.run()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        job_root, job_digest, audit_output = _resolve_job_binding(args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    job = FrozenAgentJobStore(job_root).open(job_digest)
    server = EvidenceMcpServer(
        FrozenEvidenceGateway(job),
        job_digest=job.manifest.job_digest,
        audit_output=audit_output,
        binding_file=args.binding_file,
        job_root=job_root,
    )
    _run_official_sdk(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
