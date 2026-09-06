"""Minimal stdio MCP server exposing one frozen job's read-only evidence."""

from __future__ import annotations

import argparse
import json
import os
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
            "description": "List fact IDs visible to this role in the frozen snapshot.",
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
            "description": "Read selected deterministic fact records by exact fact ID.",
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
            "description": "Read deterministic indicator measurements from the frozen catalog.",
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
            "description": "Read exact PA.* evidence IDs selected from the frozen fact catalog.",
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
    ) -> None:
        self._gateway = gateway
        self._job_digest = job_digest
        self._audit_output = audit_output

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
        try:
            if name == "get_job_manifest":
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
        payload = {
            "schema_version": 1,
            "job_digest": self._job_digest,
            "events": [asdict(item) for item in self._gateway.audit_events],
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
    parser.add_argument("--job-root", type=Path, required=True)
    parser.add_argument("--job-digest", required=True)
    parser.add_argument("--audit-output", type=Path)
    return parser


def _run_official_sdk(server: EvidenceMcpServer) -> None:
    """Serve the bounded gateway through the pinned official MCP Python SDK."""
    from mcp.server import MCPServer

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
            raise RuntimeError(str(message))
        return dict(result.get("structuredContent") or {})

    @mcp.tool(structured_output=True)
    def get_job_manifest() -> dict[str, Any]:
        """Read immutable job identity, cutoff, hashes, and expiry."""
        return payload("get_job_manifest", {})

    @mcp.tool(structured_output=True)
    def get_market_core() -> dict[str, Any]:
        """Read the bounded core context selected by PA Agent."""
        return payload("get_market_core", {})

    @mcp.tool(structured_output=True)
    def list_fact_ids(prefix: str = "", limit: int = 100) -> dict[str, Any]:
        """List visible fact IDs in this frozen role."""
        return payload("list_fact_ids", {"prefix": prefix, "limit": limit})

    @mcp.tool(structured_output=True)
    def get_facts(fact_ids: list[str]) -> dict[str, Any]:
        """Read deterministic facts by exact ID."""
        return payload("get_facts", {"fact_ids": fact_ids})

    @mcp.tool(structured_output=True)
    def get_bars(older_seq: int, newer_seq: int) -> dict[str, Any]:
        """Read an inclusive bar range inside the frozen cutoff."""
        return payload("get_bars", {"older_seq": older_seq, "newer_seq": newer_seq})

    @mcp.tool(structured_output=True)
    def get_measurements(limit: int = 100) -> dict[str, Any]:
        """Read deterministic measurement facts."""
        return payload("get_measurements", {"limit": limit})

    @mcp.tool(structured_output=True)
    def get_price_frame(older_seq: int, newer_seq: int) -> dict[str, Any]:
        """Read up to 100 frozen PA-only bars and aligned measurements."""
        return payload(
            "get_price_frame",
            {"older_seq": older_seq, "newer_seq": newer_seq},
        )

    @mcp.tool(structured_output=True)
    def get_pa_evidence(fact_ids: list[str]) -> dict[str, Any]:
        """Read exact PA evidence IDs from this role."""
        return payload("get_pa_evidence", {"fact_ids": fact_ids})

    @mcp.tool(structured_output=True)
    def get_htf_evidence(fact_ids: list[str]) -> dict[str, Any]:
        """Read exact HTF evidence IDs from this Frozen Snapshot."""
        return payload("get_htf_evidence", {"fact_ids": fact_ids})

    @mcp.tool(structured_output=True)
    def get_vpa_evidence(evidence_ids: list[str]) -> dict[str, Any]:
        """Read exact VPA evidence IDs from this role catalog."""
        return payload("get_vpa_evidence", {"evidence_ids": evidence_ids})

    @mcp.tool(structured_output=True)
    def list_allowed_knowledge() -> dict[str, Any]:
        """List PA-selected knowledge modules and section IDs."""
        return payload("list_allowed_knowledge", {})

    @mcp.tool(structured_output=True)
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

    @mcp.tool(structured_output=True)
    def read_allowed_knowledge(
        knowledge_id: str,
        section_ids: list[str],
    ) -> dict[str, Any]:
        """Read exact authorized sections from one knowledge module."""
        return payload(
            "read_allowed_knowledge",
            {"knowledge_id": knowledge_id, "section_ids": section_ids},
        )

    @mcp.tool(structured_output=True)
    def read_knowledge_module(knowledge_id: str) -> dict[str, Any]:
        """Read one complete PA-selected mandatory knowledge module."""
        return payload("read_knowledge_module", {"knowledge_id": knowledge_id})

    @mcp.tool(structured_output=True)
    def get_upstream_artifact() -> dict[str, Any]:
        """Read the validated upstream artifact authorized for this role."""
        return payload("get_upstream_artifact", {})

    @mcp.tool(structured_output=True)
    def get_experience_entries() -> dict[str, Any]:
        """Read the bounded historical experience selected for Stage 2."""
        return payload("get_experience_entries", {})

    @mcp.tool(structured_output=True)
    def get_repair_findings() -> dict[str, Any]:
        """Read the prior draft and Validator-authorized repair pointers."""
        return payload("get_repair_findings", {})

    mcp.run()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    job = FrozenAgentJobStore(args.job_root).open(args.job_digest)
    server = EvidenceMcpServer(
        FrozenEvidenceGateway(job),
        job_digest=job.manifest.job_digest,
        audit_output=args.audit_output,
    )
    _run_official_sdk(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
