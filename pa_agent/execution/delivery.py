"""Host-owned delivery observations; never infer a historical model finish."""

from pa_agent.data.bar_close_wait import timeframe_to_seconds
from pa_agent.data.datetime_ts import ts_open_to_ms
from pa_agent.records.schema import AnalysisTiming


def validated_delivery(record, *, now_ms: int) -> AnalysisTiming | None:
    if record.analysis_pipeline is None:
        return None
    as_of = record.role_runtime.get("analysis_as_of_ms")
    duration = timeframe_to_seconds(record.meta.timeframe)
    responses = [
        call.get("created_at_ms") for call in record.call_attempts if call.get("response_hash")
    ]
    if (
        type(as_of) is not int
        or "M" in record.meta.timeframe
        or not duration
        or not responses
        or not record.kline_data
    ):
        return None
    if any(type(value) is not int for value in responses):
        return None
    completed = max(responses)
    if not as_of <= completed <= now_ms:
        return None  # Clock conflict stays unknown, never clamp observations.
    latest_open = max(int(ts_open_to_ms(bar["ts_open"])) for bar in record.kline_data)
    expiry = latest_open + 2 * duration * 1000
    if expiry <= as_of:
        return None
    return AnalysisTiming(
        analysis_as_of_ms=as_of,
        model_completed_at_ms=completed,
        validated_at_ms=now_ms,
        decision_expires_at_ms=expiry,
    )
