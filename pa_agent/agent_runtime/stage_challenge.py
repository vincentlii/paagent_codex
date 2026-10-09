"""Host-owned, single-child collaboration over capability-scoped loopback RPC.

The child never receives a Frozen Job path or the parent's evidence endpoint.
Its sole tool selects from a finite list of Host-validated frozen read requests.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from jsonschema import ValidationError, validate

from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
from pa_agent.agent_runtime.execution_contract import (
    STAGE_CHALLENGE_PROTOCOL as CHALLENGE_PROTOCOL, STAGE_CHALLENGE_LIMITS as CHALLENGE_LIMITS,
)
from pa_agent.agent_runtime.frozen_job import AgentJobBudget, FrozenAgentJob
from pa_agent.ai.deepseek_client import AIUsage

CHILD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["source_digest", "evidence_refs", "support", "challenge", "unknown"],
    "properties": {
        "source_digest": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
        **{name: {"type": "string", "maxLength": 3000} for name in ("support", "challenge", "unknown")},
    },
}


@dataclass(frozen=True)
class ChildExecution:
    content: dict[str, Any] | None
    usage: AIUsage
    failure_code: str = ""


def challenge_rpc(endpoint: str, token: str, message: dict[str, Any]) -> dict[str, Any]:
    """No redirects/proxies or remote hosts; credentials never enter a tool result."""
    parsed = urlparse(endpoint)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
            or parsed.path != "/challenge" or parsed.query or parsed.fragment or parsed.username):
        raise ValueError("invalid challenge endpoint")
    request = Request(endpoint, json.dumps(message, allow_nan=False).encode("utf-8"),
                      {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        # Loopback Host emits no redirects. Reject any unexpected redirect before forwarding a token.
        from urllib.request import HTTPRedirectHandler
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=12) as response:
            value = json.loads(response.read(524289))
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise ValueError("challenge Host unavailable") from exc
    if not isinstance(value, dict) or "error" in value:
        raise ValueError(str(value.get("error", "invalid challenge response")) if isinstance(value, dict)
                         else "invalid challenge response")
    return value


class StageChallengeHost:
    """One run's async child, scoped evidence, lifecycle and auditable result."""

    def __init__(self, job: FrozenAgentJob, *, execute: Callable[..., ChildExecution],
                 parent_ready: Callable[[], None], deadline: float) -> None:
        from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer
        self.job = job
        self._execute = execute
        self._parent_ready = parent_ready
        self._deadline = deadline
        self._parent_token = secrets.token_urlsafe(32)
        self._child_token = secrets.token_urlsafe(32)
        budget = AgentJobBudget(**CHALLENGE_LIMITS, max_wall_seconds=job.budget.max_wall_seconds)
        self._gateway = FrozenEvidenceGateway(job, child_budget=budget)
        self._evidence = EvidenceMcpServer(self._gateway, job_digest=job.manifest.job_digest)
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._resources: list[dict[str, Any]] = []
        self._delivered_ids: set[str] = set()
        self._collect_wait_ms = 0.0
        self._result: dict[str, Any] | None = None
        self._usage = AIUsage()
        self._status = "not_requested"
        self._failure = ""
        self._started_at = self._completed_at = self._collected_at = 0
        self._scope_digest = ""
        self._scope: dict[str, Any] | None = None
        self._child_access_denials = 0
        self._start_attempts = self._start_rejections = self._last_start_attempt_at = 0
        self._last_start_rejection = ""
        self._closed = False
        host = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if self.path != "/challenge" or not 0 < size <= 32768:
                        raise ValueError("invalid challenge request")
                    payload = json.loads(self.rfile.read(size))
                    token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                    result = host._dispatch(token, payload)
                except (ValueError, TypeError, KeyError):
                    result = {"error": "challenge request not authorized or invalid"}
                encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                try:
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.endpoint = f"http://127.0.0.1:{self._server.server_port}/challenge"
        self._server_thread = threading.Thread(target=self._server.serve_forever,
                                               kwargs={"poll_interval": 0.05}, daemon=True)

    def __enter__(self):
        self._server_thread.start()
        return self

    def __exit__(self, *args):
        self.close()

    def binding(self) -> dict[str, str]:
        return {"protocol": CHALLENGE_PROTOCOL, "job_digest": self.job.manifest.job_digest,
                "endpoint": self.endpoint, "token": self._parent_token}

    def _dispatch(self, token: str, message: Any) -> dict[str, Any]:
        if not isinstance(message, dict):
            raise ValueError("invalid request")
        action = message.get("action")
        if hmac.compare_digest(token, self._parent_token):
            if action == "start":
                return self.start(message["question"], message["resources"])
            if action == "collect":
                return self.collect(message.get("wait_seconds", 0))
        elif hmac.compare_digest(token, self._child_token):
            if action == "read_all":
                return {"scope_digest": self._scope_digest,
                        "resources": [self._read(index) for index in range(len(self._resources))]}
            if action == "read":
                return self._read(message.get("resource_index"))
            with self._lock:
                self._child_access_denials += 1
        raise ValueError("capability not authorized")

    def start(self, question: str, resources: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            self._start_attempts += 1
            self._last_start_attempt_at = int(time.time()*1000)
            try:
                return self._start(question, resources)
            except (ValueError, TypeError) as exc:
                self._start_rejections += 1
                # Errors below contain no request text, credentials or raw evidence.
                self._last_start_rejection = str(exc)[:160]
                if self._status == "not_requested":
                    self._status = "start_rejected"
                raise

    def _start(self, question: str, resources: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            if self._closed or self._status != "not_requested":
                raise ValueError("only one child is allowed per stage")
            if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
                raise ValueError("challenge requires one bounded question")
            if not isinstance(resources, list) or not 1 <= len(resources) <= 8:
                raise ValueError("challenge requires 1 to 8 scoped resources")
            if len(json.dumps(resources, allow_nan=False).encode("utf-8")) > 30000:
                raise ValueError("resource scope exceeds byte limit")
            from pa_agent.agent_runtime.evidence_mcp import _tools
            schemas = {item["name"]: item["inputSchema"] for item in _tools()}
            allowed = set(self._gateway.available_tool_names())
            for resource in resources:
                if (not isinstance(resource, dict) or set(resource) != {"operation", "arguments"}
                        or resource["operation"] not in allowed):
                    raise ValueError("resource operation not permitted")
                try:
                    validate(resource["arguments"], schemas[resource["operation"]])
                except ValidationError as exc:
                    raise ValueError("invalid resource arguments") from exc
            # The callback validates *parent* reads only, including same-session handoff.
            self._parent_ready()
            if time.monotonic() >= self._deadline:
                raise ValueError("stage deadline exhausted")
            self._resources = json.loads(json.dumps(resources, allow_nan=False))
            scope = {"job_digest": self.job.manifest.job_digest, "question": question,
                     "resources": self._resources, "limits": CHALLENGE_LIMITS}
            self._scope = scope
            self._scope_digest = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
            context = {**scope, "scope_digest": self._scope_digest, "role": self.job.manifest.role,
                       "snapshot_identity": self._gateway.snapshot_identity}
            self._status = "running"
            self._started_at = int(time.time()*1000)
            self._thread = threading.Thread(target=self._run, args=(context,), daemon=True)
            self._thread.start()
            return {"status": "running", "scope_digest": self._scope_digest}

    def _read(self, index: Any) -> dict[str, Any]:
        with self._lock:
            if (self._status != "running" or self._cancel.is_set() or time.monotonic() >= self._deadline
                    or isinstance(index, bool) or not isinstance(index, int)
                    or not 0 <= index < len(self._resources)):
                self._child_access_denials += 1
                raise ValueError("resource outside active child scope")
            resource = self._resources[index]
            result = self._evidence._call_tool(resource["operation"], resource["arguments"])
            if result.get("isError"):
                return {"error": "scoped evidence denied or budget exhausted"}
            payload = result["structuredContent"]
            # A nested reference is not delivery of that referenced record.
            rows = payload.get("facts", payload.get("bars", payload.get("evidence", payload.get("sections", []))))
            rows = [*rows, *(payload.get("measurement") or {}).get("source_bars", [])]
            delivered = {str(row[key]) for row in rows if isinstance(row, dict)
                         for key in ("fact_id", "evidence_id", "section_id") if row.get(key)}
            self._delivered_ids.update(delivered)
            return {"scope_digest": self._scope_digest, "resource_index": index,
                    "citable_ids": sorted(delivered), "payload": payload}

    def _run(self, context: dict[str, Any]) -> None:
        try:
            execution = self._execute(context, self.endpoint, self._child_token,
                                      self._cancel, max(0.0, self._deadline-time.monotonic()))
            with self._lock:
                self._usage = execution.usage
                if self._failure == "child_shutdown_timeout":
                    pass  # Never overwrite a failed lifecycle audit with a late result.
                elif self._cancel.is_set():
                    self._status, self._failure = "cancelled", "parent_closed"
                elif execution.failure_code:
                    self._status, self._failure = "failed", execution.failure_code
                else:
                    validate(execution.content, CHILD_SCHEMA)
                    result = execution.content
                    if (result["source_digest"] != self.job.manifest.job_digest
                            or set(result["evidence_refs"]) - self._delivered_ids
                            or self._child_access_denials
                            or not self._gateway.audit_events
                            or any(event.status != "ok" for event in self._gateway.audit_events)
                            or (not result["evidence_refs"] and not result["unknown"].strip())):
                        raise ValueError("invalid child evidence grounding")
                    self._result, self._status = result, "completed"
        except (ValueError, ValidationError, TypeError, KeyError):
            with self._lock:
                self._status, self._failure = "failed", "invalid_child_output"
        except Exception:
            with self._lock:
                self._status, self._failure = "failed", "child_execution_error"
        finally:
            with self._lock:
                self._completed_at = int(time.time()*1000)
                self._done.set()

    def collect(self, wait_seconds: int = 0) -> dict[str, Any]:
        if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, int) or not 0 <= wait_seconds <= 10:
            raise ValueError("wait_seconds must be 0 to 10")
        if wait_seconds and self._status == "running":
            wait_started = time.monotonic()
            self._done.wait(min(wait_seconds, max(0, self._deadline-time.monotonic())))
            with self._lock:
                self._collect_wait_ms += (time.monotonic()-wait_started)*1000
        with self._lock:
            response = {"status": self._status, "scope_digest": self._scope_digest}
            if self._status in {"completed", "failed", "cancelled"}:
                self._collected_at = self._collected_at or int(time.time()*1000)
                response.update({"result": self._result, "failure_code": self._failure})
            return response

    def audit(self) -> dict[str, Any]:
        with self._lock:
            return {"protocol": CHALLENGE_PROTOCOL, "job_digest": self.job.manifest.job_digest,
                    "scope_digest": self._scope_digest, "status": self._status,
                    "collected": bool(self._collected_at), "failure_code": self._failure,
                    "child_access_denials": self._child_access_denials,
                    "start_attempts": self._start_attempts, "start_rejections": self._start_rejections,
                    "last_start_rejection": self._last_start_rejection,
                    "last_start_attempt_at_epoch_ms": self._last_start_attempt_at,
                    "delivered_value_ids": sorted(self._delivered_ids), "collect_wait_ms": self._collect_wait_ms,
                    "started_at_epoch_ms": self._started_at, "completed_at_epoch_ms": self._completed_at,
                    "collected_at_epoch_ms": self._collected_at, "child_usage": asdict(self._usage),
                    "evidence_events": [asdict(event) for event in self._gateway.audit_events]}

    def review_artifact(self) -> dict[str, Any]:
        """Bounded task and accepted opinion for replay/quality audit, never credentials.

        Keep this in the worker sidecar rather than duplicating tool content in
        the stage metrics ledger. This is explicit output, not hidden reasoning.
        """
        with self._lock:
            return json.loads(json.dumps({"scope": self._scope, "accepted_result": self._result}))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancel.set()
        try:
            if self._thread is not None:
                self._thread.join(timeout=15)
                if self._thread.is_alive():
                    with self._lock:
                        self._status, self._failure = "failed", "child_shutdown_timeout"
                        self._usage.reported_fields = ()
        finally:
            # Revoke capabilities even if execution cleanup fails. close() must
            # not mask a parent exception or skip the worker's remaining cleanup.
            if self._server_thread.is_alive():
                self._server.shutdown()
            self._server.server_close()
            self._server_thread.join(timeout=2) if self._server_thread.ident else None
