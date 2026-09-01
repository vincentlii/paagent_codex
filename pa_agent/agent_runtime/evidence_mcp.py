"""Minimal stdio MCP server exposing one frozen job's read-only evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
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
            "name": "read_knowledge",
            "description": "Read one PA-selected knowledge document from this role's allowlist.",
            "inputSchema": {
                "type": "object",
                "properties": {"knowledge_id": {"type": "string"}},
                "required": ["knowledge_id"],
                "additionalProperties": False,
            },
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
            if name == "list_fact_ids":
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
            elif name == "read_knowledge":
                knowledge_id = str(arguments["knowledge_id"])
                payload = {
                    "knowledge_id": knowledge_id,
                    "text": self._gateway.read_knowledge(knowledge_id),
                }
            else:
                raise ValueError(f"unknown tool: {name}")
        except (EvidenceAccessDenied, EvidenceBudgetExceeded) as exc:
            self._publish_audit()
            return {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            }
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    job = FrozenAgentJobStore(args.job_root).open(args.job_digest)
    server = EvidenceMcpServer(
        FrozenEvidenceGateway(job),
        job_digest=job.manifest.job_digest,
        audit_output=args.audit_output,
    )
    for line in sys.stdin:
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise TypeError("message must be an object")
            response = server.handle(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            response = EvidenceMcpServer._error(None, -32700, f"parse error: {exc}")
        if response is not None:
            sys.stdout.write(
                json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
