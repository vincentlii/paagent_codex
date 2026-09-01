from __future__ import annotations

from pathlib import Path

import pytest

from pa_agent.agent_runtime.evidence import (
    EvidenceAccessDenied,
    EvidenceBudgetExceeded,
    FrozenEvidenceGateway,
)
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from tests.unit.test_frozen_agent_job import _job


def _gateway(tmp_path: Path) -> FrozenEvidenceGateway:
    store = FrozenAgentJobStore(tmp_path)
    published = store.publish(_job())
    return FrozenEvidenceGateway(store.open(published.name))


def test_stage1_can_read_price_facts_bars_and_allowed_brooks_knowledge(
    tmp_path: Path,
) -> None:
    gateway = _gateway(tmp_path)

    facts = gateway.get_facts(("PA.BAR.K1",))
    bars = gateway.get_bars(older_seq=2, newer_seq=1)
    knowledge = gateway.read_knowledge("brooks/context")

    assert facts[0]["value"]["close"] == 101.0
    assert [bar["seq"] for bar in bars] == [1, 2]
    assert "Brooks" in knowledge
    assert len(gateway.audit_events) == 3
    assert all(event.snapshot_hash == "a" * 64 for event in gateway.audit_events)


def test_stage1_cannot_read_vpa_fact_or_unselected_knowledge(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)

    with pytest.raises(EvidenceAccessDenied):
        gateway.get_facts(("VPA.BAR.K1",))
    with pytest.raises(EvidenceAccessDenied):
        gateway.read_knowledge("vpa/effort-result")


def test_evidence_budget_is_fail_closed_and_audited(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    gateway.get_facts(("PA.BAR.K1",))
    gateway.get_bars(older_seq=1, newer_seq=1)
    gateway.read_knowledge("brooks/context")

    with pytest.raises(EvidenceBudgetExceeded):
        gateway.get_facts(("PA.BAR.K1",))

    assert gateway.audit_events[-1].status == "denied"
    assert gateway.audit_events[-1].reason == "call_budget_exceeded"


def test_bar_request_cannot_cross_snapshot_cutoff(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)

    with pytest.raises(EvidenceAccessDenied, match="cutoff"):
        gateway.get_bars(older_seq=99, newer_seq=1)
