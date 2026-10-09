from __future__ import annotations

import time

import pytest
from jsonschema import ValidationError, validate

from pa_agent.agent_runtime.evidence_mcp import _tools
from pa_agent.agent_runtime.stage_challenge import ChildExecution, StageChallengeHost, challenge_rpc
from pa_agent.ai.deepseek_client import AIUsage
from tests.unit.test_frozen_agent_job import _job


@pytest.mark.parametrize("resource", [
    {"operation": "get_htf_evidence", "arguments": {"evidence_ids": ["HTF.H1"]}},
    {"operation": "get_pa_evidence", "arguments": {"fact_ids": ["PA.BAR.K1"], "extra": True}},
    {"operation": "get_role_baseline", "arguments": {}},
])
def test_public_delegation_schema_rejects_wrong_nested_contract(resource):
    schema = next(t["inputSchema"] for t in _tools() if t["name"] == "start_stage_challenge")
    with pytest.raises(ValidationError):
        validate({"question": "Check this boundary", "resources": [resource]}, schema)


def test_public_delegation_schema_accepts_exact_htf_argument():
    schema = next(t["inputSchema"] for t in _tools() if t["name"] == "start_stage_challenge")
    validate({"question": "Check this boundary", "resources": [
        {"operation": "get_htf_evidence", "arguments": {"fact_ids": ["HTF.H1"]}},
    ]}, schema)


def test_rejected_delegation_does_not_allow_a_second_attempt():
    launched = []
    def execute(*args):
        launched.append(True)
        return ChildExecution(None, AIUsage(), "not_expected")
    with StageChallengeHost(_job(), execute=execute, parent_ready=lambda: None,
                            deadline=time.monotonic() + 5) as host:
        with pytest.raises(ValueError):
            host.start("Check", [{"operation": "get_role_baseline", "arguments": {}}])
        with pytest.raises(ValueError, match="one child"):
            host.start("Retry", [{"operation": "get_pa_evidence",
                                  "arguments": {"fact_ids": ["PA.BAR.K1"]}}])
    assert not launched


def test_scoped_batch_delivers_each_original_resource_once_and_keeps_audit():
    def execute(context, endpoint, token, *args):
        batch = challenge_rpc(endpoint, token, {"action": "read_all"})
        assert [row["resource_index"] for row in batch["resources"]] == [0, 1]
        assert batch["resources"][0]["payload"]["facts"][0]["fact_id"] == "PA.BAR.K1"
        assert batch["resources"][1]["payload"]["bars"][0]["seq"] == 1
        return ChildExecution({"source_digest": context["job_digest"],
                               "evidence_refs": ["PA.BAR.K1"], "support": "known",
                               "challenge": "", "unknown": ""}, AIUsage())
    with StageChallengeHost(_job(), execute=execute, parent_ready=lambda: None,
                            deadline=time.monotonic() + 5) as host:
        host.start("Check one closed sequence", [
            {"operation": "get_pa_evidence", "arguments": {"fact_ids": ["PA.BAR.K1"]}},
            {"operation": "get_bars", "arguments": {"older_seq": 1, "newer_seq": 1}},
        ])
        assert host.collect(3)["status"] == "completed"
        assert [e["operation"] for e in host.audit()["evidence_events"]] == ["get_pa_evidence", "get_bars"]
