from __future__ import annotations

import json
from dataclasses import dataclass

from pa_agent.ai.role_validator import RoleValidationResult
from pa_agent.orchestrator.role_call import run_structured_role


@dataclass
class _Reply:
    content: str
    reasoning_content: str = ""


def test_role_repair_replays_invalid_draft_without_demanding_verbatim_prose() -> None:
    replies = iter(
        (
            _Reply('{"stable":"keep-me","broken":false}'),
            _Reply('{"stable":"keep-me","broken":true}'),
        )
    )
    calls: list[list[dict[str, object]]] = []

    def call_api(messages: list[dict[str, object]]) -> _Reply:
        calls.append(messages)
        return next(replies)

    def validate(raw: str) -> RoleValidationResult:
        if '"broken":true' in raw:
            return RoleValidationResult({"stable": "keep-me", "broken": True})
        return RoleValidationResult(
            None,
            ("broken: True was expected",),
        )

    result = run_structured_role(
        role="vpa_evidence",
        messages=[{"role": "user", "content": "Return JSON."}],
        call_api=call_api,
        validate=validate,
    )

    assert result.ok
    assert len(calls) == 2
    assert calls[1][-2] == {
        "role": "assistant",
        "content": '{"stable":"keep-me","broken":false}',
    }
    feedback = str(calls[1][-1]["content"])
    assert "优先保持未涉及的专业判断稳定" in feedback
    assert "逐字不变" not in feedback


def test_authority_violation_fails_closed_without_generative_repair() -> None:
    calls = 0

    def call_api(messages: list[dict[str, object]]) -> _Reply:
        nonlocal calls
        calls += 1
        return _Reply('{"decision":{"order_type":"market"}}')

    result = run_structured_role(
        role="vpa_evidence",
        messages=[{"role": "user", "content": "Return JSON."}],
        call_api=call_api,
        validate=lambda raw: RoleValidationResult(
            None,
            ("VPA may not output authority field decision.order_type",),
            authority_violations=("vpa_forbidden_field:decision.order_type",),
        ),
    )

    assert not result.ok
    assert calls == 1
    assert result.validation_events[-1]["retry_policy"] == "none"


def test_repeated_prose_warnings_are_one_event_with_full_occurrence_count() -> None:
    result = run_structured_role(
        role="vpa_evidence",
        messages=[{"role": "user", "content": "Return JSON."}],
        call_api=lambda messages: _Reply('{"ok":true}'),
        validate=lambda raw: RoleValidationResult(
            {"ok": True},
            warnings=("first path warning", "second path warning"),
        ),
    )

    assert result.ok
    warnings = [
        event
        for event in result.validation_events
        if event["code"] == "professional_prose_warning"
    ]
    assert len(warnings) == 1
    assert warnings[0]["occurrence_count"] == 2
    assert warnings[0]["findings"] == [
        "first path warning",
        "second path warning",
    ]


def test_identical_warnings_keep_true_occurrence_count_without_duplicate_text() -> None:
    result = run_structured_role(
        role="vpa_evidence",
        messages=[{"role": "user", "content": "Return JSON."}],
        call_api=lambda messages: _Reply('{"ok":true}'),
        validate=lambda raw: RoleValidationResult(
            {"ok": True},
            warnings=("same path warning", "same path warning"),
        ),
    )

    warnings = [
        event
        for event in result.validation_events
        if event["code"] == "professional_prose_warning"
    ]
    assert warnings[0]["occurrence_count"] == 2
    assert warnings[0]["findings"] == ["same path warning"]


def test_final_failed_role_attempt_does_not_claim_another_repair() -> None:
    result = run_structured_role(
        role="vpa_evidence",
        messages=[{"role": "user", "content": "Return JSON."}],
        call_api=lambda messages: _Reply('{"broken":false}'),
        validate=lambda raw: RoleValidationResult(None, ("broken: true expected",)),
        max_repairs=1,
    )

    hard = [
        event
        for event in result.validation_events
        if event["severity"] == "hard_error"
    ]
    assert [event["retry_policy"] for event in hard] == [
        "bounded_repair",
        "none",
    ]


def test_role_repair_restores_unrelated_structured_judgment() -> None:
    replies = iter(
        (
            _Reply(
                '{"joint_arbitration":{"decision_effect":"lower_confidence"},'
                '"broken":false}'
            ),
            _Reply(
                '{"joint_arbitration":{"decision_effect":"reject_current_attempt"},'
                '"broken":true}'
            ),
        )
    )

    def validate(raw: str) -> RoleValidationResult:
        payload = json.loads(raw)
        if (
            payload.get("broken") is True
            and payload.get("joint_arbitration", {}).get("decision_effect")
            == "lower_confidence"
        ):
            return RoleValidationResult({"ok": True})
        return RoleValidationResult(None, ("broken: true expected",))

    result = run_structured_role(
        role="joint_arbiter",
        messages=[{"role": "user", "content": "Return JSON."}],
        call_api=lambda messages: next(replies),
        validate=validate,
    )

    assert result.ok
    assert any(
        event.get("code") == "repair_stability_restore"
        and event["normalization"]["path"]
        == "joint_arbitration.decision_effect"
        for event in result.validation_events
    )


def test_role_repair_restores_a_removed_structured_judgment() -> None:
    replies = iter(
        (
            _Reply(
                '{"joint_arbitration":{"decision_effect":"lower_confidence"},'
                '"broken":false}'
            ),
            _Reply('{"joint_arbitration":{},"broken":true}'),
        )
    )

    def validate(raw: str) -> RoleValidationResult:
        payload = json.loads(raw)
        if (
            payload.get("broken") is True
            and payload.get("joint_arbitration", {}).get("decision_effect")
            == "lower_confidence"
        ):
            return RoleValidationResult({"ok": True})
        return RoleValidationResult(None, ("broken: true expected",))

    result = run_structured_role(
        role="joint_arbiter",
        messages=[{"role": "user", "content": "Return JSON."}],
        call_api=lambda messages: next(replies),
        validate=validate,
    )

    assert result.ok
    assert any(
        event.get("code") == "repair_stability_restore"
        and event["normalization"]["before"] == "missing_from_repair"
        for event in result.validation_events
    )


def test_role_repair_allows_citation_metadata_to_be_corrected() -> None:
    replies = iter(
        (
            _Reply(
                '{"joint_arbitration":{"decision_effect":"lower_confidence",'
                '"evidence_used":["UNKNOWN"]}}'
            ),
            _Reply(
                '{"joint_arbitration":{"decision_effect":"lower_confidence",'
                '"evidence_used":["VPA.K1"]}}'
            ),
        )
    )

    def validate(raw: str) -> RoleValidationResult:
        payload = json.loads(raw)
        arbitration = payload.get("joint_arbitration", {})
        if arbitration.get("evidence_used") == ["VPA.K1"]:
            return RoleValidationResult({"ok": True})
        return RoleValidationResult(None, ("unknown evidence ID",))

    result = run_structured_role(
        role="joint_arbiter",
        messages=[{"role": "user", "content": "Return JSON."}],
        call_api=lambda messages: next(replies),
        validate=validate,
    )

    assert result.ok
    assert not any(
        event.get("code") == "repair_stability_restore"
        and event["normalization"]["path"]
        == "joint_arbitration.evidence_used"
        for event in result.validation_events
    )
