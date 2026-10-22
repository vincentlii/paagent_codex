"""Freeze analysis inputs and derive reproducible production task identities."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pa_agent.ai.higher_timeframe import serialize_higher_timeframe_context
from pa_agent.ai.decision_continuity import HISTORY_CONTEXT_VERSION
from pa_agent.config.vpa import resolve_production_vpa_mode
from pa_agent.data.base import KlineFrame
from pa_agent.data.snapshot_bundle import compact_snapshot_context, serialize_frame_bars
from pa_agent.monitoring.analysis_runner import FrozenAnalysisPayload
from pa_agent.monitoring.scheduler import AnalysisTask
from pa_agent.operations.analysis_profile import (
    AnalysisProfile,
    profile_fingerprint,
    profile_identity_payload,
    validate_analysis_profile,
)
from pa_agent.quality.runtime_fingerprint import (
    analysis_runtime_fingerprint as _analysis_runtime_fingerprint,
)

_ANALYSIS_GENERAL_FIELDS = (
    "analysis_bar_count",
    "higher_timeframe_enabled",
    "higher_timeframe_override",
    "higher_timeframe_bar_count",
    "incremental_max_new_bars",
    "decision_stance",
    "decision_confidence_threshold",
    "counterfactual_horizon_bars",
    "enable_next_bar_prediction",
)


def analysis_runtime_fingerprint(*, project_root: Path, prompt_dir: Path) -> str:
    """Compatibility export for production callers."""
    return _analysis_runtime_fingerprint(project_root, prompt_dir=prompt_dir)


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validated_digest(value: str, name: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    return text


def _frame_identity_payload(frame: KlineFrame) -> dict[str, Any]:
    from pa_agent.records.experience_reader import experience_pool_binding

    context = compact_snapshot_context(frame)
    context.pop("snapshot_ts_local_ms", None)
    return {
        "symbol": frame.symbol,
        "timeframe": frame.timeframe,
        "bars": serialize_frame_bars(frame),
        "ema20": list(frame.indicators.ema20),
        "atr14": list(frame.indicators.atr14),
        "context": context,
        **experience_pool_binding(frame.data_metadata),
    }


def frame_snapshot_hash(frame: KlineFrame) -> str:
    """Hash current-frame market facts for cache invalidation."""
    return _stable_hash(_frame_identity_payload(frame))


def _snapshot_hash(
    frame: KlineFrame,
    htf_context: dict[str, Any] | None,
    incremental_context: dict[str, Any] | None,
) -> str:
    higher_frame = None
    if isinstance(htf_context, dict) and htf_context.get("status") == "available":
        candidate = htf_context.get("frame")
        if isinstance(candidate, KlineFrame):
            higher_frame = candidate
    return _stable_hash(
        {
            "current": _frame_identity_payload(frame),
            "htf_context": serialize_higher_timeframe_context(htf_context),
            "higher": (
                _frame_identity_payload(higher_frame)
                if higher_frame is not None
                else None
            ),
            "incremental_context": incremental_context,
        }
    )


def _record_field(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _plain_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    if isinstance(value, Mapping):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_value(item) for item in value]
    return value


def _incremental_identity_payload(
    previous_record: Any | None,
    new_bar_count: int | None,
    history_context_version: str | None = None,
) -> dict[str, Any] | None:
    if history_context_version not in (None, HISTORY_CONTEXT_VERSION):
        raise ValueError("unsupported history context version")
    if previous_record is None or new_bar_count is None or int(new_bar_count) <= 0:
        return None
    response = _record_field(previous_record, "stage1_response", {})
    response_content = (
        response.get("content") if isinstance(response, Mapping) else None
    )
    kline_data = _record_field(previous_record, "kline_data", [])
    latest_bar = kline_data[0] if isinstance(kline_data, list) and kline_data else None
    identity = {
        "new_bar_count": int(new_bar_count),
        "previous": {
            "meta": _plain_value(_record_field(previous_record, "meta")),
            "latest_bar": _plain_value(latest_bar),
            "stage1_messages": _plain_value(
                _record_field(previous_record, "stage1_messages", [])
            ),
            "stage1_response_content": response_content,
            "stage1_diagnosis": _plain_value(
                _record_field(previous_record, "stage1_diagnosis")
            ),
            "stage2_decision": _plain_value(
                _record_field(previous_record, "stage2_decision")
            ),
            "strategy_files_used": _plain_value(
                _record_field(previous_record, "strategy_files_used", [])
            ),
        },
    }
    if history_context_version is not None:
        identity["history_context_version"] = history_context_version
        identity["previous"].update({
            name: _plain_value(_record_field(previous_record, name))
            for name in (
                "snapshot_context", "analysis_pipeline", "run_outcome", "exception",
                "role_stop", "analysis_timing", "kline_data",
            )
        })
    return identity


def _analysis_settings_payload(settings: Any) -> dict[str, Any]:
    provider = settings.provider.model_dump()
    provider.pop("api_key", None)
    provider.pop("api_key_encrypted", None)
    prompt = settings.prompt.model_dump()
    prompt.pop("vpa_enabled", None)
    prompt["vpa_mode"] = resolve_production_vpa_mode(settings.prompt)
    general = settings.general.model_dump()
    payload = {
        "provider": provider,
        "general": {
            name: general[name]
            for name in _ANALYSIS_GENERAL_FIELDS
            if name in general
        },
        "prompt": prompt,
        "validation": settings.validation.model_dump(),
    }
    pipeline = getattr(settings, "analysis_pipeline", None)
    if pipeline is not None:
        payload["analysis_pipeline"] = _plain_value(pipeline)
    return payload


def analysis_cohort_id(
    settings: Any,
    analysis_fingerprint: str,
    *,
    analysis_backend_mode: str = "deepseek_only",
    analysis_backend_identity: Mapping[str, Any] | None = None,
    analysis_profile_identity: Mapping[str, Any] | None = None,
) -> str:
    """Identify the model, settings, code, and prompt version for one run."""
    runtime = _validated_digest(analysis_fingerprint, "analysis_fingerprint")
    if analysis_backend_mode not in {"deepseek_only", "auto", "codex_only"}:
        raise ValueError("unsupported analysis backend mode")
    payload = {
        "contract": "production_analysis_v2",
        "settings": _analysis_settings_payload(settings),
        "analysis_fingerprint": runtime,
    }
    if analysis_backend_mode != "deepseek_only":
        payload["analysis_backend_mode"] = analysis_backend_mode
        identity = dict(analysis_backend_identity or {})
        if not identity:
            raise ValueError("non-Direct backend requires frozen runtime identity")
        payload["analysis_backend_identity"] = identity
    if analysis_profile_identity:
        payload["analysis_profile_identity"] = dict(analysis_profile_identity)
    return _stable_hash(payload)


def create_frozen_analysis_task(
    frame: KlineFrame,
    htf_context: dict[str, Any] | None,
    settings: Any,
    *,
    source: str,
    analysis_fingerprint: str,
    venue: str | None = None,
    previous_record: Any | None = None,
    incremental_new_bar_count: int | None = None,
    instrument_spec: Any | None = None,
    created_at_ms: int | None = None,
    analysis_backend_mode: str = "deepseek_only",
    analysis_backend_identity: Mapping[str, Any] | None = None,
    analysis_profile: AnalysisProfile | Mapping[str, object] | None = None,
    analysis_profile_fingerprint: str | None = None,
    execution_policy: str | None = None,
    take_profit_tick_size: str | None = None,
) -> AnalysisTask:
    """Deep-freeze one validated snapshot before it enters the scheduler."""
    if not isinstance(frame, KlineFrame):
        raise TypeError("frame must be a KlineFrame")
    if analysis_backend_mode not in {"deepseek_only", "auto", "codex_only"}:
        raise ValueError("unsupported analysis backend mode")
    if execution_policy not in {None, "limit_3bar_v1"}:
        raise ValueError("unknown execution policy")
    if execution_policy and (frame.timeframe != "1h" or getattr(settings, "analysis_pipeline", None) is None):
        raise ValueError("limit_3bar_v1 requires an explicit 1h pipeline")
    profile_identity: dict[str, Any] = {}
    profile_fp = str(analysis_profile_fingerprint or "").strip().lower()
    if analysis_profile is not None:
        validated_profile = validate_analysis_profile(analysis_profile)
        computed_fp = profile_fingerprint(validated_profile)
        if profile_fp and profile_fp != computed_fp:
            raise ValueError("analysis profile fingerprint mismatch")
        profile_fp = computed_fp
        profile_identity = {
            **profile_identity_payload(validated_profile),
            "profile_fingerprint": computed_fp,
        }
    elif profile_fp:
        if len(profile_fp) != 64 or any(
            character not in "0123456789abcdef" for character in profile_fp
        ):
            raise ValueError("analysis_profile_fingerprint must be a SHA-256 hex digest")
    if profile_fp:
        provided_identity = dict(analysis_backend_identity or {})
        identity_fp = str(
            provided_identity.get("profile_fingerprint")
            or provided_identity.get("analysis_profile_fingerprint")
            or ""
        ).strip().lower()
        if identity_fp and identity_fp != profile_fp:
            raise ValueError("analysis backend profile fingerprint mismatch")
        if provided_identity:
            provided_identity.setdefault("profile_fingerprint", profile_fp)
            analysis_backend_identity = provided_identity
    frozen_frame = copy.deepcopy(frame)
    if take_profit_tick_size is not None:
        from pa_agent.ai.take_profit_policy import FixedRiskRewardPolicy
        if profile_identity.get("take_profit_mode") != "fixed_rr":
            raise ValueError("target_tick_requires_fixed_rr_profile")
        take_profit_tick_size = FixedRiskRewardPolicy(
            tick_size=take_profit_tick_size
        ).model_dump(mode="json")["tick_size"]
    frozen_htf = copy.deepcopy(htf_context)
    incremental_count = (
        int(incremental_new_bar_count)
        if incremental_new_bar_count is not None
        else None
    )
    if previous_record is None or incremental_count is None or incremental_count <= 0:
        frozen_previous = None
        incremental_count = None
        history_context_version = None
    else:
        frozen_previous = copy.deepcopy(previous_record)
        history_context_version = HISTORY_CONTEXT_VERSION
    incremental_identity = _incremental_identity_payload(
        frozen_previous,
        incremental_count,
        history_context_version=history_context_version,
    )
    payload = FrozenAnalysisPayload(
        frame=frozen_frame,
        htf_context=frozen_htf or {},
        previous_record=frozen_previous,
        incremental_new_bar_count=incremental_count,
        history_context_version=history_context_version,
        instrument_spec=copy.deepcopy(instrument_spec),
        analysis_backend_mode=analysis_backend_mode,
        analysis_backend_identity=(
            copy.deepcopy(dict(analysis_backend_identity or {}))
            if analysis_backend_mode != "deepseek_only"
            else {}
        ),
        analysis_profile_fingerprint=profile_fp,
        analysis_profile_identity=copy.deepcopy(profile_identity),
        execution_policy=execution_policy,
        take_profit_tick_size=take_profit_tick_size,
    )
    cohort = analysis_cohort_id(
        settings, analysis_fingerprint, analysis_backend_mode=analysis_backend_mode,
        analysis_backend_identity=analysis_backend_identity,
        analysis_profile_identity=profile_identity,
    )
    if execution_policy:
        cohort = _stable_hash({"base": cohort, "execution_policy": execution_policy})
    if profile_identity.get("take_profit_mode") == "fixed_rr":
        cohort = _stable_hash({"base": cohort, "take_profit_tick_size": take_profit_tick_size})
    return AnalysisTask.create(
        source=source,
        venue=venue,
        symbol=frozen_frame.symbol,
        timeframe=frozen_frame.timeframe,
        snapshot_hash=_snapshot_hash(
            frozen_frame,
            frozen_htf,
            incremental_identity,
        ),
        cohort_id=cohort,
        payload=payload,
        created_at_ms=created_at_ms,
    )
