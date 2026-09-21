"""Versioned call measurements; legacy zero-filled counters are not evidence."""
from __future__ import annotations

from pa_agent.ai.role_contracts import AnalysisMetric


def metric(value, unit, source, *, estimated=False):
    return AnalysisMetric(
        status="unknown" if value is None else "estimated" if estimated else "actual",
        value=value, unit=unit, source=source,
    ).model_dump(mode="json")


def call_metrics(usage, runtime, *, wall_ms=None):
    reported = set(getattr(usage, "reported_fields", ()) or ())
    values = {
        name: metric(getattr(usage, name, None) if name in reported else None,
                     "tokens", "provider_usage")
        for name in ("prompt_tokens", "cached_prompt_tokens", "completion_tokens", "total_tokens")
    }
    for name, unit, estimated in (
        ("initial_context_bytes", "bytes", False),
        ("initial_context_tokens", "tokens", True),
        ("retrieval_context_tokens", "tokens", True),
        ("evidence_response_bytes", "bytes", False),
        ("evidence_call_count", "count", False),
        ("evidence_denied_count", "count", False),
        ("evidence_latency_ms", "milliseconds", False),
    ):
        value = runtime.get(name)
        event_field = {"evidence_response_bytes": "response_bytes",
                       "retrieval_context_tokens": "response_tokens",
                       "evidence_latency_ms": "latency_ms"}.get(name)
        if event_field and any(event.get(event_field) is None for event in runtime.get("evidence_events", ())):
            value = None
        values[name] = metric(value, unit, "host_evidence_accounting", estimated=estimated)
    events = runtime.get("evidence_events")
    mcp_calls = None
    if events is not None and all(
        type(event.get("mcp_call_index")) is int and event["mcp_call_index"] > 0
        for event in events
    ):
        mcp_calls = len({event["mcp_call_index"] for event in events})
    elif runtime.get("analysis_backend") == "direct":
        mcp_calls = 0
    values["evidence_mcp_call_count"] = metric(mcp_calls, "count", "host_mcp_audit")
    timings = runtime.get("timings") or {}
    for name in ("process_ms", "queue_ms", "worker_wall_ms", "spool_wall_ms"):
        values[name] = metric(timings.get(name), "milliseconds", "host_clock")
    values["backend_wall_ms"] = metric(wall_ms, "milliseconds", "host_monotonic")
    values["cost_usd"] = metric(None, "usd", "billing_not_reported")
    transport_count = len(runtime.get("transport_attempts") or ())
    values["transport_attempt_count"] = metric(transport_count or None, "count", "host_attempt_ledger")
    return {"protocol_version": "analysis_metrics_v1", "scope": "role_call",
            "usage_scope": "final_response_only" if transport_count > 1 else "reported_response",
            "prior_attempt_usage_known": transport_count <= 1, "values": values}


def summarize_call_metrics(calls):
    """Preserve missing coverage; never convert partial totals into full costs."""
    calls = list(calls)
    keys = set().union(*(set((c.get("metrics") or {}).get("values", {})) for c in calls))
    result = {}
    for key in sorted(keys):
        items = [(c.get("metrics") or {}).get("values", {}).get(key) for c in calls]
        known = [AnalysisMetric.model_validate(v) for v in items if v is not None]
        actual = [v.value for v in known if v.status == "actual"]
        estimated = [v.value for v in known if v.status == "estimated"]
        result[key] = {"actual_count": len(actual), "estimated_count": len(estimated),
                       "unknown_count": len(calls) - len(actual) - len(estimated),
                       "actual_sum": sum(actual) if actual else None,
                       "estimated_sum": sum(estimated) if estimated else None,
                       "complete_actual": len(actual) == len(calls) and bool(calls)}
    return {"protocol_version": "analysis_metrics_v1", "calls": len(calls),
            "usage_coverage_complete": bool(calls) and all(
                (c.get("metrics") or {}).get("prior_attempt_usage_known", False) for c in calls)
            and all(result.get(key, {}).get("complete_actual", False) for key in
                    ("prompt_tokens", "completion_tokens", "total_tokens", "cached_prompt_tokens")),
            "values": result}
