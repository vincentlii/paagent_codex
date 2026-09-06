from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from tests.unit.test_frozen_agent_job import _job


def _request(process: subprocess.Popen[str], payload: dict) -> dict:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
    process.stdin.flush()
    return json.loads(process.stdout.readline())


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
        initialized = _request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
        )
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
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=5)

    assert initialized["result"]["capabilities"]["tools"]["listChanged"] is False
    assert {item["name"] for item in listed["result"]["tools"]} == {
        "get_job_manifest",
        "get_market_core",
        "list_fact_ids",
        "get_facts",
        "get_bars",
        "get_measurements",
        "get_price_frame",
        "get_pa_evidence",
        "get_htf_evidence",
        "get_vpa_evidence",
        "list_allowed_knowledge",
        "search_allowed_knowledge",
        "read_allowed_knowledge",
        "read_knowledge_module",
        "get_upstream_artifact",
        "get_experience_entries",
        "get_repair_findings",
    }
    assert called["result"]["isError"] is False
    assert called["result"]["structuredContent"]["facts"][0]["fact_id"] == "PA.BAR.K1"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["job_digest"] == published.name
    assert audit["events"][0]["operation"] == "get_facts"


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
