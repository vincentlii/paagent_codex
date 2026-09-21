import pytest

from pa_agent.ai.analysis_metrics import call_metrics, metric, summarize_call_metrics
from pa_agent.ai.deepseek_client import AIUsage, _reported_usage_fields
from pa_agent.agent_runtime.codex_worker import CodexCliWorker
from types import SimpleNamespace


def test_missing_usage_is_unknown_but_reported_zero_is_actual():
    missing = call_metrics(AIUsage(), {})["values"]
    zero = call_metrics(AIUsage(reported_fields=("prompt_tokens",)), {})["values"]
    assert missing["prompt_tokens"]["value"] is None
    assert zero["prompt_tokens"]["status"] == "actual"
    assert zero["prompt_tokens"]["value"] == 0
    assert zero["cached_prompt_tokens"]["status"] == "unknown"


def test_estimates_and_unknown_coverage_are_not_summed_as_actual():
    metrics = call_metrics(AIUsage(), {"initial_context_tokens": 123, "evidence_call_count": 0})
    result = summarize_call_metrics([{"metrics": metrics}, {}])["values"]
    assert result["initial_context_tokens"]["estimated_sum"] == 123
    assert result["initial_context_tokens"]["actual_sum"] is None
    assert result["initial_context_tokens"]["unknown_count"] == 1
    assert not result["evidence_call_count"]["complete_actual"]


def test_cache_zero_requires_a_provider_field():
    assert "cached_prompt_tokens" not in _reported_usage_fields(SimpleNamespace(prompt_tokens=10))
    assert "cached_prompt_tokens" in _reported_usage_fields(SimpleNamespace(prompt_cache_hit_tokens=0))
    _, usage = CodexCliWorker._parse_events('{"type":"turn.completed","usage":{"input_tokens":8,"output_tokens":2}}')
    assert usage.total_tokens == 10
    assert "cached_prompt_tokens" not in usage.reported_fields


def test_batched_evidence_counts_resources_separately_from_mcp_roundtrips():
    runtime = {"evidence_call_count": 3, "evidence_events": [
        {"mcp_call_index": 1}, {"mcp_call_index": 1}, {"mcp_call_index": 2},
    ]}
    values = call_metrics(AIUsage(), runtime)["values"]
    assert values["evidence_call_count"]["value"] == 3
    assert values["evidence_mcp_call_count"]["value"] == 2
    runtime["evidence_events"].append({"operation": "legacy_read"})
    assert call_metrics(AIUsage(), runtime)["values"]["evidence_mcp_call_count"]["status"] == "unknown"


def test_legacy_evidence_missing_measurements_cannot_be_actual_zero():
    measured = call_metrics(AIUsage(), {"evidence_events": [{"status": "ok"}],
        "evidence_response_bytes": 0, "retrieval_context_tokens": 0, "evidence_latency_ms": 0})
    for name in ("evidence_response_bytes", "retrieval_context_tokens", "evidence_latency_ms"):
        assert measured["values"][name]["status"] == "unknown"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_measurements_rejected(value):
    with pytest.raises(ValueError):
        metric(value, "tokens", "test")


def test_retries_expose_incomplete_usage():
    measured = call_metrics(AIUsage(), {"transport_attempts": [{}, {}]})
    assert measured["usage_scope"] == "final_response_only"
    assert not measured["prior_attempt_usage_known"]


def test_usage_coverage_requires_actual_token_fields_not_just_no_retries():
    assert not summarize_call_metrics([{"metrics": call_metrics(AIUsage(), {})}])["usage_coverage_complete"]
    measured = call_metrics(AIUsage(reported_fields=("prompt_tokens", "completion_tokens", "total_tokens", "cached_prompt_tokens")), {})
    assert summarize_call_metrics([{"metrics": measured}])["usage_coverage_complete"]


def test_codex_multiple_turns_sum_usage_but_missing_fields_remain_unknown():
    _, usage = CodexCliWorker._parse_events(
        '{"type":"turn.completed","usage":{"input_tokens":8,"output_tokens":2,"cached_input_tokens":1}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":3}}'
    )
    assert usage.total_tokens == 25
    assert "cached_prompt_tokens" not in usage.reported_fields


def test_required_mcp_bootstrap_failure_is_availability_not_model_output():
    from pa_agent.agent_runtime.codex_worker import _classify_failed_process, CodexWorkerUnavailable
    failure = _classify_failed_process(1, "", "Failed to create session: required MCP servers failed to initialize: pa_evidence")
    assert isinstance(failure, CodexWorkerUnavailable)
