"""Lossless, typed serialization of accepted headless analysis tasks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.data.instrument import InstrumentSpec
from pa_agent.monitoring.analysis_runner import FrozenAnalysisPayload
from pa_agent.monitoring.scheduler import AnalysisTask
from pa_agent.monitoring.task_factory import (
    _incremental_identity_payload,
    _snapshot_hash,
)
from pa_agent.records.schema import AnalysisRecord


def _frame_to_dict(frame: KlineFrame) -> dict[str, Any]:
    if not isinstance(frame, KlineFrame):
        raise TypeError("frozen task requires a KlineFrame")
    return asdict(frame)


def _frame_from_dict(value: dict[str, Any]) -> KlineFrame:
    return KlineFrame(
        symbol=value["symbol"],
        timeframe=value["timeframe"],
        bars=tuple(KlineBar(**bar) for bar in value["bars"]),
        indicators=IndicatorBundle(
            ema20=tuple(value["indicators"]["ema20"]),
            atr14=tuple(value["indicators"]["atr14"]),
        ),
        snapshot_ts_local_ms=value["snapshot_ts_local_ms"],
        data_metadata=value["data_metadata"],
    )


def encode_task(task: AnalysisTask) -> tuple[str, str]:
    payload = task.payload
    if not isinstance(payload, FrozenAnalysisPayload):
        raise TypeError("frozen task requires FrozenAnalysisPayload")
    htf = dict(payload.htf_context)
    if isinstance(htf.get("frame"), KlineFrame):
        htf["frame"] = _frame_to_dict(htf["frame"])
    previous = payload.previous_record
    if previous is not None and not isinstance(previous, AnalysisRecord):
        raise TypeError("frozen previous record must be AnalysisRecord")
    spec = payload.instrument_spec
    if spec is not None and not isinstance(spec, InstrumentSpec):
        raise TypeError("frozen instrument spec must be InstrumentSpec")
    value = {
        "version": 1,
        "identity": {
            name: getattr(task, name) for name in (
                "task_id", "source", "venue", "symbol", "timeframe",
                "snapshot_hash", "cohort_id", "created_at_ms",
            )
        },
        "payload": {
            "frame": _frame_to_dict(payload.frame),
            "htf_context": htf,
            "previous_record": (
                previous.model_dump(mode="json") if previous is not None else None
            ),
            "incremental_new_bar_count": payload.incremental_new_bar_count,
            "instrument_spec": spec.to_dict() if spec is not None else None,
            "analysis_backend_mode": payload.analysis_backend_mode,
            "analysis_backend_identity": dict(payload.analysis_backend_identity),
            "analysis_profile_fingerprint": payload.analysis_profile_fingerprint,
            "analysis_profile_identity": dict(payload.analysis_profile_identity),
        },
    }
    if payload.execution_policy is not None:
        value["payload"]["execution_policy"] = payload.execution_policy
    if payload.demo_execution_policy is not None:
        from pa_agent.operations.demo_settings import DemoExecutionPolicy
        if payload.execution_target != "okx_demo":
            raise ValueError("demo_settings_require_demo_binding")
        value["payload"]["demo_execution_policy"] = DemoExecutionPolicy.model_validate(dict(payload.demo_execution_policy)).model_dump(mode="json")
    if payload.execution_target is not None or payload.execution_epoch is not None:
        if payload.execution_target != "okx_demo" or not payload.execution_epoch:
            raise ValueError("invalid frozen execution binding")
        value["payload"].update(execution_target=payload.execution_target, execution_epoch=payload.execution_epoch)
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def decode_task(encoded: str, digest: str) -> AnalysisTask:
    if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != digest:
        raise ValueError("frozen task payload digest mismatch")
    value = json.loads(encoded)
    if value.get("version") != 1:
        raise ValueError("unsupported frozen task version")
    identity = value["identity"]
    source = value["payload"]
    if source.get("execution_policy") not in {None, "limit_3bar_v1"}:
        raise ValueError("unknown frozen execution policy")
    target, epoch = source.get("execution_target"), source.get("execution_epoch")
    demo_policy = source.get("demo_execution_policy")
    if demo_policy is not None:
        from pa_agent.operations.demo_settings import DemoExecutionPolicy
        if target != "okx_demo":
            raise ValueError("demo_settings_require_demo_binding")
        demo_policy = DemoExecutionPolicy.model_validate(demo_policy).model_dump(mode="json")
    if (target is not None or epoch is not None) and (target != "okx_demo" or not isinstance(epoch, str) or not epoch):
        raise ValueError("invalid frozen execution binding")
    htf = source["htf_context"]
    if isinstance(htf.get("frame"), dict):
        htf["frame"] = _frame_from_dict(htf["frame"])
    previous = source["previous_record"]
    payload = FrozenAnalysisPayload(
        frame=_frame_from_dict(source["frame"]),
        htf_context=htf,
        previous_record=(
            AnalysisRecord.model_validate(previous) if previous is not None else None
        ),
        incremental_new_bar_count=source["incremental_new_bar_count"],
        instrument_spec=InstrumentSpec.from_value(source["instrument_spec"]),
        analysis_backend_mode=source["analysis_backend_mode"],
        analysis_backend_identity=source["analysis_backend_identity"],
        analysis_profile_fingerprint=source["analysis_profile_fingerprint"],
        analysis_profile_identity=source["analysis_profile_identity"],
        execution_policy=source.get("execution_policy"),
        execution_target=target,
        execution_epoch=epoch,
        demo_execution_policy=demo_policy,
    )
    task = AnalysisTask.create(
        source=identity["source"], venue=identity["venue"],
        symbol=identity["symbol"], timeframe=identity["timeframe"],
        snapshot_hash=identity["snapshot_hash"], cohort_id=identity["cohort_id"],
        created_at_ms=identity["created_at_ms"], payload=payload,
    )
    if task.task_id != identity["task_id"]:
        raise ValueError("frozen task identity mismatch")
    incremental = _incremental_identity_payload(
        payload.previous_record, payload.incremental_new_bar_count
    )
    if _snapshot_hash(payload.frame, dict(payload.htf_context), incremental) != task.snapshot_hash:
        raise ValueError("frozen market snapshot mismatch")
    if source["instrument_spec"] is not None and payload.instrument_spec is None:
        raise ValueError("frozen instrument spec is invalid")
    return task
