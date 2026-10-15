"""Reuse official qualification; bind source prices to separate Demo constraints."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from decimal import Decimal, ROUND_FLOOR
from collections.abc import Mapping

from pa_agent.data.instrument import InstrumentSpec
from pa_agent.data.contracts import ResolvedInstrumentIdentity
from pa_agent.execution.plans import official_paper_plan, decision_bar_ts, _field, _decision
from pa_agent.execution.demo_contracts import (
    DemoConfig,
    DemoIntent,
    DemoContract,
    DemoOrderRequest,
    MAPPINGS,
    digest,
    client_id,
)


def _json_value(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {k: _json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(v) for v in value]
    if hasattr(value, "__dict__"):
        return _json_value(vars(value))
    return value


def compile_demo_intent(
    record: object,
    source_spec: InstrumentSpec,
    *,
    task_id: str,
    config: DemoConfig,
    frozen_execution_epoch: str,
    now_ms: int,
    market_snapshot_hash: str,
    demo_execution_policy=None,
) -> DemoIntent:
    if not config.enabled or config.execution_target != "okx_demo":
        raise ValueError("demo_disabled")
    if not config.cutover_id or frozen_execution_epoch != config.cutover_id:
        raise ValueError("skipped_cutover")
    identity = ResolvedInstrumentIdentity.from_value(_field(record, "snapshot_context", {}))
    if identity is None or identity.key != (
        source_spec.provider.casefold(),
        source_spec.venue.casefold(),
        source_spec.symbol.casefold(),
        source_spec.instrument_type.casefold(),
    ):
        raise ValueError("source_identity_mismatch")
    frozen_spec = InstrumentSpec.from_value(
        _field(record, "snapshot_context", {}).get("instrument_spec")
    )
    if frozen_spec != source_spec:
        raise ValueError("source_spec_mismatch")
    if source_spec.provider != "binance_usdm" or source_spec.venue != "BINANCE":
        raise ValueError("unsupported_source")
    meta = _field(record, "meta")
    if _field(meta, "symbol") != source_spec.symbol or _field(meta, "timeframe") != "1h":
        raise ValueError("source_market_mismatch")
    target = MAPPINGS.get(source_spec.symbol)
    if target not in config.symbols:
        raise ValueError("instrument_disabled")
    pipeline = _field(record, "analysis_pipeline")
    timing = _field(record, "analysis_timing")
    if pipeline is None or timing is None:
        raise ValueError("new_delivery_required")
    plan = official_paper_plan(record, source_spec, dedupe_key=task_id, now_ms=now_ms)
    bar_ms = decision_bar_ts(record)
    if bar_ms < 10**12:
        bar_ms *= 1000
    available = _field(timing, "result_available_at_ms")
    if available < config.cutover_started_at_ms:
        raise ValueError("skipped_cutover")
    values = dict(
        schema_version="okx_demo_intent_v2",
        cutover_id=config.cutover_id,
        task_id=task_id,
        run_id=_field(meta, "run_id"),
        pipeline=_field(pipeline, "pipeline"),
        source_identity={
            "provider": source_spec.provider,
            "venue": source_spec.venue,
            "symbol": source_spec.symbol,
            "instrument_type": source_spec.instrument_type,
        },
        source_spec=source_spec.to_dict(),
        market_digest=market_snapshot_hash,
        record_digest=digest(_json_value(record)),
        source_bar_ms=bar_ms,
        generated_at_ms=now_ms,
        fresh_until_ms=_field(timing, "decision_expires_at_ms"),
        expires_at_ms=plan.expires_at_ms if plan else _field(timing, "decision_expires_at_ms"),
        inst_id=target,
        disposition="no_trade" if plan is None else "limit",
    )
    if demo_execution_policy is not None:
        from pa_agent.operations.demo_settings import DemoExecutionPolicy

        policy = DemoExecutionPolicy.model_validate(demo_execution_policy)
        values["demo_execution_policy"] = policy.model_dump(mode="json")
        if plan is not None and plan.order_type == "limit":
            values["expires_at_ms"] = bar_ms + (1 + policy.limit_validity_hours) * 3_600_000
    if plan is not None:
        if plan.order_type != "limit":
            values.update(disposition="rejected", source_reason="unsupported_order_type")
        else:
            decision = _decision(record)
            values.update(
                side="buy" if plan.side == "long" else "sell",
                entry=str(decision["entry_price"]),
                stop=str(decision["stop_loss_price"]),
                tp1=str(decision["take_profit_price"]),
            )
    return DemoIntent.seal(values)


def bind_demo_limit(
    intent: DemoIntent, contract: DemoContract, *, account_key: str, available_margin: Decimal
) -> DemoOrderRequest:
    if intent.disposition != "limit" or contract.inst_id != intent.inst_id:
        raise ValueError("invalid_limit_binding")
    entry, stop, tp1 = intent.entry, intent.stop, intent.tp1
    if any(price % contract.tick != 0 for price in (entry, stop, tp1)):
        raise ValueError("target_tick_mismatch")
    if not (stop < entry < tp1 if intent.side == "buy" else tp1 < entry < stop):
        raise ValueError("invalid_geometry")
    per_contract = entry * contract.ct_val * contract.ct_mult
    target_notional = (
        intent.demo_execution_policy.notional_usdt if intent.demo_execution_policy else Decimal(500)
    )
    quantity = (target_notional / per_contract / contract.lot).to_integral_value(
        rounding=ROUND_FLOOR
    ) * contract.lot
    if quantity < contract.minimum or quantity > min(
        contract.maximum_limit, contract.maximum_market
    ):
        raise ValueError("quantity_bounds")
    notional = quantity * per_contract
    margin = notional / 3
    if not available_margin.is_finite() or available_margin < margin:
        raise ValueError("insufficient_margin")
    return DemoOrderRequest(
        account_key=account_key,
        inst_id=intent.inst_id,
        side=intent.side,
        client_id=client_id(account_key, intent, "entry", 1),
        px=entry,
        sz=quantity,
        stop=stop,
        tp1=tp1,
        notional=notional,
        margin=margin,
        exp_time_ms=intent.fresh_until_ms,
        contract_digest=digest(contract.model_dump(mode="json")),
    )
