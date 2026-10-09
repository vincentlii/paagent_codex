"""Convert validated record fields into execution plans without text inference."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from pa_agent.data.base import KlineBar
from pa_agent.data.contracts import ResolvedInstrumentIdentity
from pa_agent.data.instrument import InstrumentSpec
from pa_agent.execution.paper import PaperTradePlan
from pa_agent.experiments.pa_vpa_aabb import SUPPORTED_AABB_PROTOCOL_VERSIONS

_SUCCESS_STATUSES = frozenset({"completed", "completed_with_warnings", "degraded_pa_only"})
_ORDER_TYPES = {
    "\u9650\u4ef7\u5355": "limit",
    "\u7a81\u7834\u5355": "breakout",
    "\u5e02\u4ef7\u5355": "market",
}
_SIDES = {"\u505a\u591a": "long", "\u505a\u7a7a": "short"}
_NO_TRADE = "\u4e0d\u4e0b\u5355"


def instrument_spec_from_record(record: object) -> InstrumentSpec | None:
    context = _field(record, "snapshot_context", {})
    if not isinstance(context, Mapping):
        return None
    return InstrumentSpec.from_value(context.get("instrument_spec"))


def market_observed_at_ms(record: object) -> int | None:
    """Use the frozen observation time, never the delayed model completion time."""
    timing = _field(record, "analysis_timing")
    runtime = _field(record, "role_runtime", {}) or {}
    value = _field(timing, "analysis_as_of_ms")
    if value is None:
        value = _field(runtime, "analysis_as_of_ms")
    if value is None:
        value = _field(_field(record, "meta"), "timestamp_local_ms")
    return int(value) if value is not None else None


def closed_bars_from_record(record: object) -> tuple[KlineBar, ...]:
    result: list[KlineBar] = []
    for raw in _field(record, "kline_data", []) or []:
        if not isinstance(raw, Mapping) or raw.get("closed") is False:
            continue
        try:
            result.append(
                KlineBar(
                    seq=int(raw.get("seq") or 1),
                    ts_open=float(raw["ts_open"]),
                    open=float(raw["open"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    close=float(raw["close"]),
                    volume=float(raw.get("volume") or 0.0),
                    amount=float(raw.get("amount") or 0.0),
                    closed=True,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("analysis record contains an invalid closed bar") from exc
    return tuple(result)


def decision_bar_ts(record: object) -> int:
    bars = closed_bars_from_record(record)
    if not bars:
        raise ValueError("analysis record has no closed-bar timestamp")
    return max(int(bar.ts_open) for bar in bars)


def official_paper_plan(
    record: object,
    spec: InstrumentSpec,
    *,
    dedupe_key: str,
    now_ms: int | None = None,
) -> PaperTradePlan | None:
    """Return only a production PA decision; Shadow VPA never becomes authority."""
    outcome = _outcome(record)
    pipeline = _field(record, "analysis_pipeline")
    pipeline_name = _field(pipeline, "pipeline")
    joint = pipeline_name == "pa_vpa_joint"
    if outcome.get("official_path") != "pa_only" and not (joint and outcome.get("official_path") == "joint"):
        raise ValueError("official paper execution requires PA-only authority")
    if str(outcome.get("effective_vpa_mode") or "off") == "soft":
        raise ValueError("research soft output cannot enter the official paper account")
    if _field(record, "experiment_context"):
        raise ValueError("experiment arm cannot enter the official paper account")
    if _field(record, "joint_stage2") or _field(record, "joint_decision"):
        raise ValueError("Joint fields cannot enter the official paper account")
    if joint and outcome.get("official_path") == "pa_only" and _decision(record).get("order_type") != _NO_TRADE:
        raise ValueError("joint pipeline cannot execute a PA-only fallback candidate")
    stage1 = _field(record, "stage1_diagnosis", {})
    if not isinstance(stage1, Mapping) or stage1.get("gate_result") != "proceed":
        decision = _decision(record)
        if str(decision.get("order_type") or "") != _NO_TRADE:
            raise ValueError("official order requires a proceeded PA gate")
    delivery = None
    if pipeline is not None:
        from pa_agent.records.schema import AnalysisTiming
        from pa_agent.config.analysis_pipeline import AnalysisPipelineIdentity
        from pa_agent.data.bar_close_wait import timeframe_to_seconds
        from pa_agent.data.datetime_ts import ts_open_to_ms
        AnalysisPipelineIdentity.model_validate(pipeline.model_dump() if hasattr(pipeline, "model_dump") else pipeline)
        timing = _field(record, "analysis_timing")
        if timing is None:
            raise ValueError("new pipeline requires persisted delivery timing")
        timing = AnalysisTiming.model_validate(timing.model_dump() if hasattr(timing, "model_dump") else timing)
        now = int(datetime.now(timezone.utc).timestamp() * 1000) if now_ms is None else now_ms
        timeframe = _field(_field(record, "meta"), "timeframe")
        duration = None if "M" in str(timeframe) else timeframe_to_seconds(timeframe)
        if timing.result_available_at_ms is None or timing.decision_expires_at_ms is None or not duration:
            raise ValueError("new pipeline requires complete delivery timing")
        expected_expiry = int(ts_open_to_ms(decision_bar_ts(record))) + 2 * duration * 1000
        if timing.decision_expires_at_ms != expected_expiry:
            raise ValueError("delivery expiry does not match frozen snapshot")
        if now < timing.result_available_at_ms:
            raise ValueError("delivery result is not yet available")
        if now >= timing.decision_expires_at_ms:
            raise ValueError("delivery decision expired")
        if _field(record, "role_stop") is not None:
            raise ValueError("stopped role is not executable")
        if joint and outcome.get("official_path") == "joint":
            stage2 = _field(record, "stage2_decision", {})
            if (stage2.get("analysis_basis", {}).get("protocol_version") != "pa_vpa_stage2_draft_v1"
                    or not _field(record, "vpa_evidence")):
                raise ValueError("joint decision provenance is missing")
        delivery = (now, timing.decision_expires_at_ms)
    plan = _plan_from_decision(
        record,
        spec,
        arm=pipeline_name or "pa_official",
        dedupe_key=dedupe_key,
    )
    if plan is not None and delivery is not None:
        return replace(plan, available_at_ms=delivery[0], expires_at_ms=delivery[1], take_profit_price_2=None)
    return plan


def research_arm_plan(
    record: object,
    spec: InstrumentSpec,
    *,
    market_snapshot_hash: str,
) -> PaperTradePlan | None:
    """Return one validated A/A/B/B arm for its isolated research account."""
    context = _field(record, "experiment_context", {})
    if not isinstance(context, Mapping):
        raise ValueError("research paper plan requires experiment context")
    if context.get("protocol_version") not in SUPPORTED_AABB_PROTOCOL_VERSIONS:
        raise ValueError("research paper plan requires the A/A/B/B protocol")
    arm_id = str(context.get("arm_id") or "")
    group = str(context.get("group") or "")
    if arm_id not in {"A1", "A2", "B1", "B2"}:
        raise ValueError("research paper plan has an invalid arm")
    outcome = _outcome(record)
    expected_mode = "off" if group == "A" else "soft"
    received_path = str(outcome.get("official_path") or "")
    if group == "A" and received_path != "pa_only":
        raise ValueError("research arm did not receive its assigned decision path")
    if group == "B" and received_path not in {"joint", "pa_only"}:
        raise ValueError("research B arm has no executable ITT decision path")
    if outcome.get("effective_vpa_mode") != expected_mode:
        raise ValueError("research arm did not receive its assigned mode")
    return _plan_from_decision(
        record,
        spec,
        arm=arm_id,
        dedupe_key=str(market_snapshot_hash),
    )


def _plan_from_decision(
    record: object,
    spec: InstrumentSpec,
    *,
    arm: str,
    dedupe_key: str,
) -> PaperTradePlan | None:
    if _field(record, "exception") is not None:
        raise ValueError("failed analysis cannot enter paper execution")
    outcome = _outcome(record)
    if str(outcome.get("status") or "") not in _SUCCESS_STATUSES:
        raise ValueError("analysis status is not executable")
    stage2 = _field(record, "stage2_decision", {})
    decision = _decision(record)
    if not decision:
        raise ValueError("validated stage2 decision is unavailable")
    from pa_agent.ai.execution_policy import effective_order_type

    order_label = effective_order_type(stage2)
    if order_label == _NO_TRADE:
        return None
    order_type = _ORDER_TYPES.get(order_label)
    side = _SIDES.get(str(decision.get("order_direction") or ""))
    if order_type is None or side is None:
        raise ValueError("validated decision has unsupported order semantics")
    meta = _field(record, "meta")
    symbol = str(_field(meta, "symbol", "") or "").strip().upper()
    timeframe = str(_field(meta, "timeframe", "") or "").strip()
    run_id = str(_field(meta, "run_id", "") or "").strip()
    identity = ResolvedInstrumentIdentity.from_value(
        _field(record, "snapshot_context", {})
    )
    if identity is None or identity.key != (
        spec.provider.casefold(), spec.venue.casefold(), spec.symbol.casefold(),
        spec.instrument_type.casefold(),
    ):
        raise ValueError("analysis identity does not match execution instrument")
    created_bar_ts = decision_bar_ts(record)
    if not run_id or not symbol or not timeframe:
        raise ValueError("analysis record identity or closed-bar timestamp is incomplete")
    return PaperTradePlan(
        run_id=run_id,
        arm=arm,
        symbol=symbol,
        timeframe=timeframe,
        side=side,
        order_type=order_type,
        entry_price=float(decision["entry_price"]),
        stop_price=float(decision["stop_loss_price"]),
        take_profit_price=float(decision["take_profit_price"]),
        take_profit_price_2=(
            float(decision["take_profit_price_2"])
            if decision.get("take_profit_price_2") is not None
            else None
        ),
        created_bar_ts=created_bar_ts,
        dedupe_key=str(dedupe_key),
    )


def _outcome(record: object) -> Mapping[str, Any]:
    value = _field(record, "run_outcome", {})
    return value if isinstance(value, Mapping) else {}


def decision_path(record: object) -> str:
    return str(_outcome(record).get("official_path") or "")


def _decision(record: object) -> Mapping[str, Any]:
    stage2 = _field(record, "stage2_decision", {})
    value = stage2.get("decision") if isinstance(stage2, Mapping) else None
    return value if isinstance(value, Mapping) else {}


def _field(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)
