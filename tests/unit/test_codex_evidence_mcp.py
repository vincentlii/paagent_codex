from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
from tests.unit.test_frozen_agent_job import _job


def test_concurrent_tool_calls_cannot_overwrite_newer_audit(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from pa_agent.agent_runtime import evidence_mcp
    from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway

    job = _job()
    audit = tmp_path / "audit.json"
    server = evidence_mcp.EvidenceMcpServer(
        FrozenEvidenceGateway(job), job_digest=job.manifest.job_digest, audit_output=audit,
    )
    first_snapshot = Event()
    newer_written = Event()
    write = evidence_mcp._atomic_write_json

    def delayed_write(path, payload):
        if len(payload["events"]) == 1:
            first_snapshot.set()
            newer_written.wait(0.2)
            write(path, payload)
        else:
            write(path, payload)
            newer_written.set()

    monkeypatch.setattr(evidence_mcp, "_atomic_write_json", delayed_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(server._call_tool, "get_bars", {"older_seq": 1, "newer_seq": 1})
        assert first_snapshot.wait(2)
        second = pool.submit(server._call_tool, "get_bars", {"older_seq": 2, "newer_seq": 2})
        assert not first.result(timeout=3)["isError"]
        assert not second.result(timeout=3)["isError"]
    events = json.loads(audit.read_text("utf-8"))["events"]
    assert [event["call_index"] for event in events] == [1, 2]


def test_bound_server_rejects_rebinding_to_unrelated_frozen_job(tmp_path: Path) -> None:
    from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
    from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer

    job_root = tmp_path / "jobs"
    store = FrozenAgentJobStore(job_root)
    first_path = store.publish(_job())
    unrelated_path = store.publish(_job(core_context={"unrelated": True}))
    first = store.open(first_path.name)
    binding = tmp_path / "runtime" / "binding.json"
    binding.parent.mkdir()
    audit = tmp_path / "outbox" / "stage1-audit.json"
    audit.parent.mkdir()

    def write(job_digest: str) -> None:
        binding.write_text(json.dumps({
            "schema_version": 1,
            "job_root": str(job_root.resolve()),
            "job_digest": job_digest,
            "audit_output": str(audit.resolve()),
        }), encoding="utf-8")

    write(first_path.name)
    server = EvidenceMcpServer(
        FrozenEvidenceGateway(first),
        job_digest=first_path.name,
        audit_output=audit,
        binding_file=binding,
        job_root=job_root,
    )
    write(unrelated_path.name)

    with pytest.raises(ValueError, match="authorized PA Stage 2 transition"):
        server._call_tool("get_job_manifest", {})


def _request(process: subprocess.Popen[str], payload: dict) -> dict:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
    process.stdin.flush()
    return json.loads(process.stdout.readline())


def _initialize(process: subprocess.Popen[str]) -> dict:
    response = _request(process, {
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "pa-agent-test", "version": "1"}},
    })
    assert "result" in response, response
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    process.stdin.flush()
    return response


def test_stdio_mcp_serves_only_budgeted_frozen_evidence(tmp_path: Path) -> None:
    job_root = tmp_path / "jobs"
    published = FrozenAgentJobStore(job_root).publish(_job())
    audit_path = tmp_path / "audit.json"
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-m",
            "pa_agent.agent_runtime.evidence_mcp",
            "--job-root",
            str(job_root),
            "--job-digest",
            published.name,
            "--audit-output",
            str(audit_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        initialized = _initialize(process)
        listed = _request(
            process,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        called = _request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "get_facts",
                    "arguments": {"fact_ids": ["PA.BAR.K1"]},
                },
            },
        )
        unavailable = _request(
            process,
            {
                "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "get_vpa_evidence", "arguments": {"evidence_ids": []}},
            },
        )
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=5)

    assert "tools" in initialized["result"]["capabilities"]
    for tool in listed["result"]["tools"]:
        assert tool["annotations"]["readOnlyHint"] is (tool["name"] != "start_stage_challenge")
        assert tool["annotations"]["destructiveHint"] is False
        assert tool["annotations"]["openWorldHint"] is False
    from jsonschema import ValidationError, validate
    challenge_schema = next(tool["inputSchema"] for tool in listed["result"]["tools"]
                            if tool["name"] == "start_stage_challenge")
    validate({"question": "Check boundary", "resources": [{"operation": "get_htf_evidence",
             "arguments": {"fact_ids": ["HTF.H1"]}}]}, challenge_schema)
    with pytest.raises(ValidationError):
        validate({"question": "Check boundary", "resources": [{"operation": "get_htf_evidence",
                 "arguments": {"evidence_ids": ["HTF.H1"]}}]}, challenge_schema)
    # The SDK registers a static schema for Stage 1 -> Stage 2 rebinding;
    # dispatch authorization is enforced by the server, not tools/list.
    assert set(FrozenEvidenceGateway(_job()).available_tool_names()) <= {
        item["name"] for item in listed["result"]["tools"]
    }
    assert called["result"]["isError"] is False
    assert called["result"]["structuredContent"]["facts"][0]["fact_id"] == "PA.BAR.K1"
    assert "error" in unavailable or unavailable["result"]["isError"] is True
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["job_digest"] == published.name
    assert audit["events"][0]["operation"] == "get_facts"
    assert (audit["events"][1]["operation"], audit["events"][1]["status"]) == (
        "get_vpa_evidence", "denied",
    )


def test_mcp_rejects_unlisted_tool_even_when_gateway_has_method(tmp_path: Path) -> None:
    from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer

    job = _job(role="stage1")
    audit = tmp_path / "audit.json"
    server = EvidenceMcpServer(
        FrozenEvidenceGateway(job), job_digest=job.manifest.job_digest,
        audit_output=audit,
    )
    denied = server._call_tool("get_vpa_evidence", {"evidence_ids": []})
    assert denied["isError"] is True
    assert "not available" in denied["content"][0]["text"]
    events = json.loads(audit.read_text(encoding="utf-8"))["events"]
    assert [(event["operation"], event["status"]) for event in events] == [
        ("get_vpa_evidence", "denied"),
    ]


def test_stdio_mcp_returns_tool_error_for_role_boundary(tmp_path: Path) -> None:
    job_root = tmp_path / "jobs"
    published = FrozenAgentJobStore(job_root).publish(_job())
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-m",
            "pa_agent.agent_runtime.evidence_mcp",
            "--job-root",
            str(job_root),
            "--job-digest",
            published.name,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        _initialize(process)
        denied = _request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "read_allowed_knowledge",
                    "arguments": {
                        "knowledge_id": "vpa/effort-result",
                        "section_ids": ["vpa/effort-result#s001"],
                    },
                },
            },
        )
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=5)

    assert denied["result"]["isError"] is True
    assert "outside role allowlist" in denied["result"]["content"][0]["text"]
