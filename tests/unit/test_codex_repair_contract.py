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
