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
    knowledge = gateway.read_allowed_knowledge(
        knowledge_id="brooks/context",
        section_ids=("brooks/context#s001",),
    )

    assert facts[0]["value"]["close"] == 101.0
    assert [bar["seq"] for bar in bars] == [1, 2]
    assert "Brooks" in knowledge["sections"][0]["text"]
    assert len(gateway.audit_events) == 3
    assert all(event.snapshot_hash == "a" * 64 for event in gateway.audit_events)


def test_mandatory_knowledge_can_be_read_as_one_complete_module(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)

    knowledge = gateway.read_knowledge_module(knowledge_id="brooks/context")

    assert knowledge["knowledge_module_id"] == "brooks/context"
    assert [item["section_id"] for item in knowledge["sections"]] == [
        "brooks/context#s001"
    ]
    assert gateway.audit_events[-1].operation == "read_knowledge_module"


def test_stage1_cannot_read_vpa_fact_or_unselected_knowledge(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)

    with pytest.raises(EvidenceAccessDenied):
        gateway.get_facts(("VPA.BAR.K1",))
    with pytest.raises(EvidenceAccessDenied):
        gateway.read_allowed_knowledge(
            knowledge_id="vpa/effort-result",
            section_ids=("vpa/effort-result#s001",),
        )


def test_evidence_budget_is_fail_closed_and_audited(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    gateway.get_facts(("PA.BAR.K1",))
    gateway.get_bars(older_seq=1, newer_seq=1)
    gateway.read_allowed_knowledge(
        knowledge_id="brooks/context",
        section_ids=("brooks/context#s001",),
    )

    with pytest.raises(EvidenceBudgetExceeded):
        gateway.get_facts(("PA.BAR.K1",))

    assert gateway.audit_events[-1].status == "denied"
    assert gateway.audit_events[-1].reason == "call_budget_exceeded"


def test_bar_request_cannot_cross_snapshot_cutoff(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)

    with pytest.raises(EvidenceAccessDenied, match="cutoff"):
        gateway.get_bars(older_seq=99, newer_seq=1)


def test_full_core_context_is_retrieved_on_demand(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)

    core = gateway.get_market_core()
    experiences = gateway.get_experience_entries()

    assert "experience_entries" not in core
    assert experiences == [{"summary": "bounded history"}]


def test_pa_price_frame_is_complete_and_contains_no_volume(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)

    rows = gateway.get_price_frame(older_seq=2, newer_seq=1)

    assert [item["seq"] for item in rows] == [1, 2]
    assert all("volume" not in item for item in rows)


def test_vpa_core_exposes_frozen_catalog_semantics_without_copying_rows():
    from dataclasses import replace
    from pa_agent.agent_runtime.frozen_job import AgentJobBudget

    catalog = {
        "catalog_id": "vpa-test", "volume_kind": "tick",
        "volume_provenance": {"raw_field": "tick_volume"},
        "data_status": "limited", "comparators": {"seasonality": "unavailable"},
        "evidence": [{"evidence_id": "vpa-e001", "volume": 12}],
    }
    job = _job()
    job = replace(job, manifest=replace(job.manifest, role="vpa_evidence"),
                  fact_projection={**job.fact_projection, "role_evidence": catalog},
                  budget=AgentJobBudget(max_evidence_calls=3, max_evidence_bytes=4096))
    core = FrozenEvidenceGateway(job).get_market_core()
    assert core["vpa_catalog_context"] == {key: value for key, value in catalog.items() if key != "evidence"}
    pa = replace(job, manifest=replace(job.manifest, role="stage1"))
    assert "vpa_catalog_context" not in FrozenEvidenceGateway(pa).get_market_core()
