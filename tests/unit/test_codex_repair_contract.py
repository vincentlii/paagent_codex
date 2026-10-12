from __future__ import annotations

import json

import pytest

from pa_agent.agent_runtime.repair_contract import (
    PATCH_ENVELOPE_SCHEMA,
    append_repair_context,
    apply_patch_envelope,
    extract_repair_context,
)
from pa_agent.ai.provider_capabilities import (
    project_strict_output_schema,
    strict_schema_compatible,
)


def _context() -> dict:
    messages = [
        {
            "role": "user",
            "content": append_repair_context(
                "Repair only the cited field.",
                original_draft=json.dumps(
                    {"order": {"direction": "long", "entry": 100.0}, "confidence": 0.7}
                ),
                findings=("order.entry must cite a frozen price",),
                editable_pointers=("/order/entry",),
            ),
        }
    ]
    context = extract_repair_context(messages)
    assert context is not None
    return context


@pytest.mark.parametrize("pointers", [["/support_levels/0", "/support_levels/2"], ["/support_levels/2", "/support_levels/0"]])
def test_patch_removals_keep_original_array_item_identity(pointers) -> None:
    context = {
        "original_draft": json.dumps({"support_levels": ["100", "90", "80"]}),
        "editable_pointers": pointers,
    }
    candidate = apply_patch_envelope(context, {
        "status": "repaired", "changed_pointers": pointers,
        "replacement_fragments": [{"pointer": p, "operation": "remove", "value_json": "null"} for p in pointers],
    })
    assert candidate == {"support_levels": ["90"]}


def test_repair_rejects_canonical_only_path_before_dispatch() -> None:
    from pa_agent.ai.pa_provider_contracts import schema_for_provider_draft

    message = {"role": "user", "content": append_repair_context(
        "unknown fact", original_draft='{"pa_evidence":{"spike_analysis":null}}',
        findings=["pa_evidence.fact_refs"], editable_pointers=["/pa_evidence/fact_refs"],
    )}
    with pytest.raises(ValueError, match="outside Provider Draft"):
        extract_repair_context([message], output_schema=schema_for_provider_draft("stage1"))


def test_patch_envelope_changes_only_validator_authorized_pointer() -> None:
    repaired = apply_patch_envelope(
        _context(),
        {
            "status": "repaired",
            "changed_pointers": ["/order/entry"],
            "replacement_fragments": [
                {"pointer": "/order/entry", "operation": "replace", "value_json": "101.5"}
            ],
            "supporting_evidence_ids": ["PA.BAR.K1"],
            "unrepairable_reason": None,
        },
    )

    assert repaired == {
        "order": {"direction": "long", "entry": 101.5},
        "confidence": 0.7,
    }


def test_patch_envelope_rejects_semantic_rewrite_outside_authority() -> None:
    with pytest.raises(ValueError, match="authority"):
        apply_patch_envelope(
            _context(),
            {
                "status": "repaired",
                "changed_pointers": ["/order/direction"],
                "replacement_fragments": [
                    {
                        "pointer": "/order/direction",
                        "operation": "replace",
                        "value_json": '"short"',
                    }
                ],
                "supporting_evidence_ids": [],
                "unrepairable_reason": None,
            },
        )


def test_patch_envelope_projects_to_strict_codex_output_schema() -> None:
    projected = project_strict_output_schema(PATCH_ENVELOPE_SCHEMA)

    assert strict_schema_compatible(projected)
    assert projected["properties"]["replacement_fragments"]["items"][
        "additionalProperties"
    ] is False


@pytest.mark.parametrize("pointer", ["/decision_trace/1/node_id", "/decision_trace"])
def test_repair_cannot_relabel_duplicate_trace_as_a_new_decision_node(pointer) -> None:
    trace = [{"node_id": "10.1", "reason": "structural stop"},
             {"node_id": "10.1", "reason": "trader equation"}]
    context = {"original_draft": json.dumps({"decision_trace": trace}),
               "editable_pointers": [pointer]}
    replacement = "11.1" if pointer.endswith("node_id") else [trace[0], {**trace[1], "node_id": "11.1"}]
    with pytest.raises(ValueError, match="trace node identities"):
        apply_patch_envelope(context, {
            "status": "repaired", "changed_pointers": [pointer],
            "replacement_fragments": [{"pointer": pointer, "operation": "replace",
                                       "value_json": json.dumps(replacement)}],
        })


def test_repair_can_remove_authorized_duplicate_without_renaming_nodes() -> None:
    trace = [{"node_id": "10.1", "reason": "structural stop"},
             {"node_id": "10.1", "reason": "trader equation"}]
    repaired = apply_patch_envelope({
        "original_draft": json.dumps({"decision_trace": trace}),
        "editable_pointers": ["/decision_trace"],
    }, {"status": "repaired", "changed_pointers": ["/decision_trace"],
        "replacement_fragments": [{"pointer": "/decision_trace", "operation": "replace",
                                   "value_json": json.dumps(trace[:1])}]})
    assert repaired == {"decision_trace": trace[:1]}


def test_repair_context_projects_explicit_price_mismatch_as_typed_fact() -> None:
    feedback = (
        "s2:decision.watch_points[1] K2.high states 4379.78, "
        "but frozen bar value is 4380.13"
    )
    context = extract_repair_context([{
        "role": "user",
        "content": append_repair_context(
            feedback,
            original_draft=json.dumps({"decision": {"watch_points": ["a", "b"]}}),
            findings=["decision.watch_points[1]"],
            editable_pointers=["/decision/watch_points/1"],
        ),
    }])

    assert context is not None
    price_finding = next(
        item for item in context["typed_findings"]
        if item["code"] == "explicit_bar_price_mismatch"
    )
    assert price_finding["pointer"] == "/decision/watch_points/1"
    assert price_finding["fact_refs"] == ["PA.BAR.K2"]
    assert price_finding["stated_value"] == 4379.78
    assert price_finding["frozen_value"] == 4380.13
