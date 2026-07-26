from __future__ import annotations

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
