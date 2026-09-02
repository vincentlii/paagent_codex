"""Two-stage AI analysis orchestrator.

Coordinates the full Stage 1 (diagnosis) → Stage 2 (decision) pipeline:
  1. Build Stage 1 prompt via PromptAssembler
  2. Call DeepSeekClient
  3. Validate Stage 1 JSON
  4. Route strategy files
  5. Load experience entries
  6. Build Stage 2 prompt
  7. Call DeepSeekClient
  8. Validate Stage 2 JSON
  9. Persist full record

Cancel checks are performed before each stage and after each API call.
Network/timeout errors are caught and recorded on the partial record.

On validation failure, ``validation_retry`` may append a feedback user turn and
re-call the API (see ``ValidationSettings.retry_*``). Semantic / safety errors
are not retried; immutable-field cheat detection rejects suspicious retries.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, Optional

if TYPE_CHECKING:
    from pa_agent.ai.deepseek_client import DeepSeekClient
    from pa_agent.ai.json_validator import JsonValidator
    from pa_agent.ai.prompt_assembler import PromptAssembler
    from pa_agent.config.settings import Settings
    from pa_agent.records.experience_reader import ExperienceReader
    from pa_agent.records.pending_writer import PendingWriter

from pa_agent.ai.analysis_role_backend import (
    AnalysisRoleBackend,
    AnalysisRoleRequest,
    DirectAnalysisRoleBackend,
)
from pa_agent.ai.codex_analysis_backend import FullChainFallbackRequired
from pa_agent.ai.json_validator import Ok, ValidationError
from pa_agent.ai.pa_handoff import compile_pa_handoff
from pa_agent.ai.provider_failures import ModelCallFailure
from pa_agent.ai.reasoning_profiles import resolve_reasoning_profile
from pa_agent.ai.role_contracts import (
    JOINT_DECISION_SCHEMA,
    VPA_EVIDENCE_SCHEMA,
    RunOutcome,
    VpaEvidenceReport,
)
from pa_agent.ai.role_validator import (
    validate_joint_decision,
    validate_vpa_evidence,
)
from pa_agent.data.base import KlineFrame
from pa_agent.orchestrator.role_call import StructuredRoleResult, run_structured_role
from pa_agent.orchestrator.validation_retry import validate_with_retry
from pa_agent.records.schema import AnalysisRecord, RecordMeta
from pa_agent.records.suppressed_writer import SuppressedRecordWriter
from pa_agent.util.threading import CancelToken, OrchestratorEvent
from pa_agent.util.timefmt import now_local_ms

logger = logging.getLogger(__name__)

_EXECUTABLE_ORDER_TYPES = frozenset({"限价单", "突破单", "市价单", "limit", "breakout", "market"})
_PA_STAGE1_PROTECTED_KEYS = (
    "cycle_position",
    "alternative_cycle_position",
    "direction",
    "diagnosis_confidence",
    "gate_result",
    "spike_stage",
    "climax_risk",
    "market_phase",
    "transition_risk",
    "support_levels",
    "resistance_levels",
    "detected_patterns",
)


def _has_executable_order(decision: dict[str, Any]) -> bool:
    return str(decision.get("order_type") or "").strip().lower() in {
        value.lower() for value in _EXECUTABLE_ORDER_TYPES
    }


def _pa_joint_diff(
    baseline: AnalysisRecord | None,
    joint_stage2: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Compare the materialized Joint decision with its frozen PA baseline."""
    if baseline is None or not isinstance(joint_stage2, dict):
        return None
    base_s2 = (baseline.stage2_decision or {}).get("decision", {})
    joint_s2 = joint_stage2.get("decision", {})
    decision_keys = (
        "order_type",
        "order_direction",
        "entry_price",
        "stop_loss_price",
        "take_profit_price",
        "take_profit_price_2",
        "trade_confidence",
        "estimated_win_rate",
    )
    stage2_diff = {
        key: {"baseline": base_s2.get(key), "joint": joint_s2.get(key)}
        for key in decision_keys
        if base_s2.get(key) != joint_s2.get(key)
    }
    baseline_trade = _has_executable_order(base_s2)
    joint_trade = _has_executable_order(joint_s2)
    if baseline_trade and not joint_trade:
        effect = "joint_reduced_entry_attempt"
    elif not baseline_trade and joint_trade:
        effect = "joint_added_entry_attempt"
    elif baseline_trade and joint_trade and stage2_diff:
        effect = "joint_modified_entry_attempt"
    elif stage2_diff:
        effect = "joint_timing_changed_without_trade_rate_effect"
    else:
        effect = "keep_baseline"
    return {
        "stage2": stage2_diff,
        "observed_effect": {
            "classification": effect,
            "comparison_valid": True,
            "baseline_has_executable_order": baseline_trade,
            "joint_has_executable_order": joint_trade,
            "stage2_changed": bool(stage2_diff),
        },
    }


def _usage_from_call_attempts(calls: list[dict[str, Any]]) -> dict[str, int]:
    """Derive cost totals from the response-bearing call ledger."""
    response_calls = [
        call
        for call in calls
        if isinstance(call, dict) and call.get("response_hash")
    ]
    return {
        "prompt_tokens": sum(max(0, int(call.get("prompt_tokens") or 0)) for call in response_calls),
        "cached_prompt_tokens": sum(
            max(0, int(call.get("cached_prompt_tokens") or 0))
            for call in response_calls
        ),
        "completion_tokens": sum(
            max(0, int(call.get("completion_tokens") or 0))
            for call in response_calls
        ),
        "total_tokens": sum(max(0, int(call.get("total_tokens") or 0)) for call in response_calls),
        "api_calls": len(response_calls),
        "model_latency_ms": sum(
            max(0, round(float(call.get("latency_ms") or 0)))
            for call in response_calls
        ),
    }


def _role_execution_status(
    result: StructuredRoleResult | None,
    *,
    applicable: bool,
    failed: bool = False,
) -> str:
    """Represent role execution without ambiguous first-pass booleans."""
    if failed:
        return "failed"
    if not applicable:
        return "not_applicable"
    if result is None:
        return "not_attempted"
    if not result.ok:
        return "failed"
    if len(result.replies) > 1:
        return "repaired"
    return "validated"


def _stable_hash(value: Any) -> str:
    """Hash audit metadata without retaining a second prompt or response copy."""
    encoded = json.dumps(value, ensure_ascii=True, default=str, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _call_attempt_entry(
    *,
    arm: str,
    stage: str,
    attempt: int,
    messages: list[dict[str, Any]],
    reply: Any | None = None,
    status: str = "received",
    validation_category: str | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    """Build a content-free, reproducible ledger entry for one provider call."""
    usage = getattr(reply, "usage", None)
    response_hash = None
    if reply is not None:
        response_hash = _stable_hash(
            {
                "content": getattr(reply, "content", "") or "",
                "reasoning_content": getattr(reply, "reasoning_content", "") or "",
            }
        )
    latency_ms = getattr(reply, "latency_ms", None) if reply is not None else None
    model_call_meta = (
        getattr(reply, "_pa_model_call_meta", {})
        if reply is not None
        else {}
    )
    if not isinstance(model_call_meta, dict):
        model_call_meta = {}
    return {
        "arm": arm,
        "stage": stage,
        "attempt": max(1, int(attempt)),
        "request_hash": _stable_hash(messages),
        "response_hash": response_hash,
        "prompt_tokens": max(0, int(getattr(usage, "prompt_tokens", 0) or 0)),
        "completion_tokens": max(0, int(getattr(usage, "completion_tokens", 0) or 0)),
        "cached_prompt_tokens": max(
            0, int(getattr(usage, "cached_prompt_tokens", 0) or 0)
        ),
        "total_tokens": max(0, int(getattr(usage, "total_tokens", 0) or 0)),
        "latency_ms": float(latency_ms) if latency_ms is not None else None,
        "status": status,
        "validation_category": validation_category,
        "error_type": error_type,
        "analysis_backend": model_call_meta.get("analysis_backend", "direct"),
        "provider_id": model_call_meta.get("provider_id"),
        "model": model_call_meta.get("model"),
        "runtime_version": model_call_meta.get("runtime_version"),
        "structured_output_mode": model_call_meta.get("structured_output_mode"),
        "thinking": model_call_meta.get("thinking"),
        "reasoning_effort": model_call_meta.get("reasoning_effort"),
        "requested_thinking": model_call_meta.get("requested_thinking"),
        "requested_reasoning_effort": model_call_meta.get(
            "requested_reasoning_effort"
        ),
        "effective_thinking": model_call_meta.get("effective_thinking"),
        "effective_reasoning_effort": model_call_meta.get(
            "effective_reasoning_effort"
        ),
        "provider_fallback_used": bool(
            model_call_meta.get("provider_fallback_used", False)
        ),
        "transport_attempts": copy.deepcopy(
            model_call_meta.get("transport_attempts") or []
        ),
        "job_digest": model_call_meta.get("job_digest"),
        "codex_thread_id": model_call_meta.get("thread_id"),
        "initial_context_bytes": max(
            0, int(model_call_meta.get("initial_context_bytes") or 0)
        ),
        "evidence_call_count": max(
            0, int(model_call_meta.get("evidence_call_count") or 0)
        ),
        "evidence_response_bytes": max(
            0, int(model_call_meta.get("evidence_response_bytes") or 0)
        ),
        "created_at_ms": int(datetime.now().timestamp() * 1000),
    }


def _apply_validation_to_call_attempts(
    calls: list[dict[str, Any]],
    *,
    arm: str,
    stage: str,
    validation_events: list[dict[str, Any]],
    result: Ok | ValidationError,
) -> None:
    """Attach validator outcomes to response-bearing calls in validation order."""
    response_calls = [
        call
        for call in calls
        if call.get("arm") == arm
        and call.get("stage") == stage
        and call.get("response_hash")
    ]
    for event in validation_events:
        index = int(event.get("attempt") or 0) - 1
        if 0 <= index < len(response_calls):
            if event.get("category") == "normalization":
                response_calls[index]["normalization_applied"] = True
                continue
            if event.get("severity") == "warning" or event.get(
                "category"
            ) == "warning":
                if event.get("category") == "execution_policy":
                    response_calls[index]["execution_policy_applied"] = True
                response_calls[index]["validation_warning_count"] = (
                    int(response_calls[index].get("validation_warning_count") or 0)
                    + max(1, int(event.get("occurrence_count") or 1))
                )
                continue
            response_calls[index]["status"] = "validation_failed"
            response_calls[index]["validation_category"] = str(
                event.get("category") or ""
            ) or None
    if not response_calls:
        return
    final_call = response_calls[-1]
    if isinstance(result, Ok):
        final_call["status"] = "validated"
        final_call["validation_category"] = None
    elif final_call.get("status") != "validation_failed":
        final_call["status"] = "validation_failed"
        final_call["validation_category"] = result.category


def _effective_reasoning_profile(
    calls: list[dict[str, Any]],
    role: str,
) -> dict[str, Any]:
    matching = [
        item
        for item in calls
        if item.get("stage") == role and item.get("response_hash")
    ]
    if not matching:
        return {
            "provider_id": None,
            "thinking": None,
            "reasoning_effort": None,
            "source": "unavailable",
        }
    latest = matching[-1]
    return {
        "provider_id": latest.get("provider_id"),
        "thinking": latest.get("effective_thinking"),
        "reasoning_effort": latest.get("effective_reasoning_effort"),
        "source": "call_ledger",
    }


def _validation_failure_status(
    error: ValidationError,
) -> Literal["unavailable_provider", "invalid_model_output"]:
    """Separate provider-declared failures from exhausted model contracts."""
    return "unavailable_provider" if error.category == "e" else "invalid_model_output"


def _apply_confidence_threshold(stage2: dict, threshold: int) -> dict:
    """Project confidence eligibility without changing the model candidate."""
    from pa_agent.ai.execution_policy import apply_confidence_execution_policy

    return apply_confidence_execution_policy(stage2, threshold)


def _latency_ms_label(latency_ms: object) -> str:
    """Format API latency for console logs; tolerate mocks or missing values."""
    try:
        return f"{float(latency_ms):.0f}ms"
    except (TypeError, ValueError):
        return "?"

# When the gateway buffers the full reply, emit pseudo-stream chunks to the UI.
_FALLBACK_STREAM_CHUNK = 48


def _json_truncation_hint(content: str, err: ValidationError) -> str | None:
    """Detect incomplete JSON (stream stopped mid-object) vs a stray syntax typo."""
    if err.category != "a":
        return None
    stripped = (content or "").strip()
    if not stripped.startswith("{"):
        return None
    depth = 0
    in_string = False
    escape = False
    for ch in stripped:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    if depth > 0 or not stripped.rstrip().endswith("}"):
        return (
            f"阶段 JSON 正文约 {len(stripped)} 字符，在输出过程中被截断"
            f"（未闭合对象约 {max(depth, 1)} 层，解析位置 {err.parse_position}）。"
            " 常见原因：completion 额度主要在思考区用尽，正文 JSON 只写了一小段。"
        )
    return None


def _enrich_stage2_validation_message(err: ValidationError, reply: Any) -> str:
    """Add actionable context for empty content or truncated JSON."""
    from pa_agent.ai.provider_errors import (
        PROVIDER_QUOTA_USER_MESSAGE,
        is_provider_quota_exhausted,
    )

    if err.category == "e" or is_provider_quota_exhausted(err.raw_text):
        return err.message or PROVIDER_QUOTA_USER_MESSAGE
    from pa_agent.ai.validation_messages import format_validation_errors

    detail = format_validation_errors(
        err.invalid_fields, missing_fields=err.missing_fields
    )
    content = (getattr(reply, "content", None) or "").strip()
    trunc = _json_truncation_hint(content, err)
    if trunc:
        usage = getattr(reply, "usage", None)
        completion = getattr(usage, "completion_tokens", 0) if usage else 0
        reasoning_len = len(getattr(reply, "reasoning_content", None) or "")
        msg = f"{err.message}。{trunc} completion_tokens≈{completion}，思考区约 {reasoning_len} 字。"
        return f"{msg}。{detail}" if detail else msg
    if err.category != "d" or (err.raw_text or "").strip():
        return f"{err.message}。{detail}" if detail else err.message
    if content:
        return f"{err.message}。{detail}" if detail else err.message
    reasoning = getattr(reply, "reasoning_content", None) or ""
    usage = getattr(reply, "usage", None)
    completion = getattr(usage, "completion_tokens", 0) if usage else 0
    if reasoning and "{" not in reasoning:
        return (
            f"{err.message}：扩展思考已输出约 {len(reasoning)} 字，但正文 content 为空，"
            f"且思考中未见 JSON（completion_tokens≈{completion}）。"
            " 常见原因是思考在输出阶段二 JSON 前被截断或网关提前结束流。"
            " 请缩短 prompt、检查 Packy 分组限额，或调整模型/Reasoning Effort 后重新分析。"
        )
    if reasoning:
        return (
            f"{err.message}：正文 content 为空；请把阶段二 JSON 写在 content 正文，"
            "不要只写在思考区。"
        )
    return f"{err.message}。{detail}" if detail else err.message


def _enrich_stage1_validation_message(err: ValidationError, reply: Any) -> str:
    """Add actionable context for empty content or truncated JSON."""
    from pa_agent.ai.provider_errors import (
        PROVIDER_QUOTA_USER_MESSAGE,
        is_provider_quota_exhausted,
    )

    if err.category == "e" or is_provider_quota_exhausted(err.raw_text):
        return err.message or PROVIDER_QUOTA_USER_MESSAGE
    from pa_agent.ai.validation_messages import format_validation_errors

    detail = format_validation_errors(
        err.invalid_fields, missing_fields=err.missing_fields
    )
    content = (getattr(reply, "content", None) or "").strip()
    trunc = _json_truncation_hint(content, err)
    if trunc:
        usage = getattr(reply, "usage", None)
        completion = getattr(usage, "completion_tokens", 0) if usage else 0
        reasoning_len = len(getattr(reply, "reasoning_content", None) or "")
        msg = f"{err.message}。{trunc} completion_tokens≈{completion}，思考区约 {reasoning_len} 字。"
        return f"{msg}。{detail}" if detail else msg
    if err.category != "d" or (err.raw_text or "").strip():
        return f"{err.message}。{detail}" if detail else err.message
    if content:
        return f"{err.message}。{detail}" if detail else err.message
    reasoning = getattr(reply, "reasoning_content", None) or ""
    usage = getattr(reply, "usage", None)
    completion = getattr(usage, "completion_tokens", 0) if usage else 0
    if reasoning and "{" not in reasoning:
        return (
            f"{err.message}：扩展思考已输出约 {len(reasoning)} 字，但正文 content 为空，"
            f"且思考中未见 JSON（completion_tokens≈{completion}）。"
            " 常见原因是思考占满输出额度后被截断。"
            " 请缩短 prompt、检查网关输出上限，或调整 Reasoning Effort 后重新分析。"
        )
    if reasoning:
        return (
            f"{err.message}：正文 content 为空；请把阶段一 JSON 写在 content 正文，"
            "不要只写在思考区。"
        )
    return f"{err.message}。{detail}" if detail else err.message


def _emit_buffered_stream(
    text: str,
    on_token: Callable[[str], None] | None,
    *,
    chunk_size: int = _FALLBACK_STREAM_CHUNK,
) -> bool:
    """Push *text* through *on_token* in slices if the API did not stream deltas."""
    if on_token is None or not text:
        return False
    for i in range(0, len(text), chunk_size):
        on_token(text[i : i + chunk_size])
    return True


def _build_empty_record(
    frame: KlineFrame,
    settings: Optional["Settings"],
    *,
    htf_context: dict[str, Any] | None = None,
    prompt_dir: Path | None = None,
) -> AnalysisRecord:
    """Build a partial AnalysisRecord with meta populated from the frame."""
    ts_ms = now_local_ms()
    ts_iso = datetime.fromtimestamp(ts_ms / 1000).isoformat(timespec="milliseconds")

    # Build masked provider snapshot
    ai_provider: dict[str, Any] = {}
    if settings is not None:
        from pa_agent.util.mask_secret import mask_secret
        p = settings.provider
        ai_provider = {
            "model": p.model,
            "base_url": p.base_url,
            "api_key": mask_secret(p.api_key) if p.api_key else "****",
            "thinking": p.thinking,
            "reasoning_effort": p.reasoning_effort,
            "reasoning_profiles": p.reasoning_profiles.model_dump(
                exclude_none=True
            ),
            "context_window": p.context_window,
        }

    from pa_agent.data.snapshot_bundle import serialize_frame_bars

    kline_data = serialize_frame_bars(frame)
    htf_frame = (
        htf_context.get("frame")
        if isinstance(htf_context, dict) and htf_context.get("status") == "available"
        else None
    )
    htf_kline_data = serialize_frame_bars(
        htf_frame if isinstance(htf_frame, KlineFrame) else None
    )

    from pa_agent.ai.decision_stance import normalize_stance

    decision_stance = "conservative"
    if settings is not None:
        decision_stance = normalize_stance(
            getattr(settings.general, "decision_stance", "conservative")
        )

    meta = RecordMeta(
        timestamp_local_iso=ts_iso,
        timestamp_local_ms=ts_ms,
        symbol=frame.symbol,
        timeframe=frame.timeframe,
        bar_count=len(frame.bars),
        ai_provider=ai_provider,
        decision_stance=decision_stance,
    )

    from pa_agent.ai.higher_timeframe import (
        render_higher_timeframe_context,
        serialize_higher_timeframe_context,
    )
    from pa_agent.config.vpa import resolve_vpa_mode
    from pa_agent.data.snapshot_bundle import compact_snapshot_context

    requested_mode = resolve_vpa_mode(
        getattr(settings, "prompt", None) if settings is not None else None
    )
    from pa_agent.ai.pa_provider_contracts import provider_draft_schema_digest
    from pa_agent.quality.runtime_fingerprint import (
        VALIDATION_POLICY_VERSION,
        analysis_runtime_fingerprint,
        loaded_validation_fingerprint,
    )

    decision_tree_path = (
        Path(prompt_dir) / "\u4e8c\u5143\u51b3\u7b56.txt"
        if prompt_dir is not None
        else None
    )
    runtime_identity = {
        "analysis_runtime_fingerprint": analysis_runtime_fingerprint(
            prompt_dir=prompt_dir,
        ),
        "loaded_validation_fingerprint": loaded_validation_fingerprint(),
        "validation_policy_version": VALIDATION_POLICY_VERSION,
        "provider_contract_digests": {
            "stage1": provider_draft_schema_digest(
                "stage1",
                decision_tree_path=decision_tree_path,
            ),
            "stage2": provider_draft_schema_digest(
                "stage2",
                decision_tree_path=decision_tree_path,
            ),
        },
    }

    return AnalysisRecord(
        meta=meta,
        kline_data=kline_data,
        htf_text=render_higher_timeframe_context(htf_context),
        htf_context=serialize_higher_timeframe_context(htf_context),
        htf_kline_data=htf_kline_data,
        snapshot_context=compact_snapshot_context(frame),
        stage1_messages=[],
        stage1_response=None,
        stage1_diagnosis=None,
        stage2_messages=[],
        stage2_response=None,
        stage2_decision=None,
        strategy_files_used=[],
        experience_loaded=[],
        exception=None,
        usage_total={},
        role_runtime=runtime_identity,
        run_outcome=RunOutcome(
            status="internal_error",
            official_path="none",
            requested_vpa_mode=requested_mode,
            reason="run_not_completed",
        ).model_dump(),
    )


def _with_run_outcome(
    record: AnalysisRecord,
    *,
    status: str,
    requested_vpa_mode: str,
    reason: str | None = None,
    official_path: str = "none",
) -> AnalysisRecord:
    """Attach one explicit terminal run state before persistence."""
    return record.model_copy(
        update={
            "run_outcome": RunOutcome(
                status=status,
                official_path=official_path,
                requested_vpa_mode=requested_vpa_mode,
                reason=reason,
            ).model_dump()
        }
    )


def _completed_run_status(
    record: AnalysisRecord,
    *extra_audit: dict[str, Any],
) -> str:
    """Distinguish clean completion from accepted quality warnings."""
    has_validation_warning = any(
        str(item.get("severity") or item.get("category") or "") == "warning"
        for item in [*record.validation_audit, *extra_audit]
    )
    return "completed_with_warnings" if has_validation_warning else "completed"


def _accumulate_usage(current: dict, reply_or_usage: Any) -> dict:
    """Merge token usage and optional request latency into the audit totals."""
    result = dict(current)
    reply_usage = getattr(reply_or_usage, "usage", reply_or_usage)
    result["prompt_tokens"] = (
        result.get("prompt_tokens", 0) + getattr(reply_usage, "prompt_tokens", 0)
    )
    result["cached_prompt_tokens"] = (
        result.get("cached_prompt_tokens", 0)
        + getattr(reply_usage, "cached_prompt_tokens", 0)
    )
    result["completion_tokens"] = (
        result.get("completion_tokens", 0) + getattr(reply_usage, "completion_tokens", 0)
    )
    result["total_tokens"] = (
        result.get("total_tokens", 0) + getattr(reply_usage, "total_tokens", 0)
    )
    latency_ms = getattr(reply_or_usage, "latency_ms", None)
    if latency_ms is not None:
        result["api_calls"] = int(result.get("api_calls", 0) or 0) + 1
        result["model_latency_ms"] = int(result.get("model_latency_ms", 0) or 0) + max(
            0,
            round(float(latency_ms)),
        )
    return result


def _accumulate_usage_calls(current: dict, usage_calls: list[Any]) -> dict:
    total = dict(current)
    for usage in usage_calls:
        if usage is not None:
            total = _accumulate_usage(total, usage)
    return total


class TwoStageOrchestrator:
    """Orchestrates the two-stage AI analysis pipeline.

    Parameters
    ----------
    client:
        DeepSeekClient instance for API calls.
    assembler:
        PromptAssembler for building Stage 1 and Stage 2 message lists.
    router:
        Either the ``route_strategy_files`` function or an object with a
        ``.route()`` method.
    validator:
        JsonValidator for validating Stage 1 and Stage 2 responses.
    pending_writer:
        PendingWriter for persisting full and partial records.
    exp_reader:
        ExperienceReader for loading top-5 experience entries.
    settings:
        Optional Settings object; used for ``ai_provider`` meta and
        ``reasoning_effort`` forwarding.
    """

    def __init__(
        self,
        client: "DeepSeekClient",
        assembler: "PromptAssembler",
        router: Any,
        validator: "JsonValidator",
        pending_writer: "PendingWriter",
        exp_reader: "ExperienceReader",
        settings: Optional["Settings"] = None,
        *,
        analysis_backend: AnalysisRoleBackend | None = None,
        call_arm: str = "baseline",
        allow_provider_fallback: bool = True,
        defer_execution_policy: bool = False,
        allow_research_joint: bool = False,
        profile_identity: dict[str, Any] | None = None,
    ) -> None:
        assembler_tree = getattr(assembler, "decision_tree_path", None)
        validator_tree = getattr(validator, "decision_tree_path", None)
        if isinstance(assembler_tree, (str, Path)) and isinstance(
            validator_tree, (str, Path)
        ):
            assembler_tree = Path(assembler_tree).resolve()
            validator_tree = Path(validator_tree).resolve()
            if assembler_tree != validator_tree:
                raise ValueError(
                    "prompt resource mismatch: assembler and validator must use "
                    "the same decision-tree snapshot"
                )
        self._client = client
        self._assembler = assembler
        self._router = router
        self._validator = validator
        self._pending_writer = pending_writer
        self._exp_reader = exp_reader
        self._settings = settings
        self._analysis_backend = analysis_backend or DirectAnalysisRoleBackend(
            client=client,
            settings=settings,
            profile_identity=profile_identity,
            allow_provider_fallback=allow_provider_fallback,
            logger=logger,
        )
        self._profile_identity = dict(profile_identity or {})
        self._call_arm = call_arm
        self._allow_provider_fallback = allow_provider_fallback
        self._defer_execution_policy = defer_execution_policy
        self._allow_research_joint = allow_research_joint
        self._initial_stage2_messages: list[dict[str, Any]] = []

    def _validation_settings(self) -> Any:
        if self._settings is not None and hasattr(self._settings, "validation"):
            settings = self._settings.validation
            limit = getattr(self._analysis_backend, "validation_retry_limit", None)
            if isinstance(limit, int) and limit >= 0:
                return settings.model_copy(
                    update={
                        "retry_max": min(settings.retry_max, limit),
                        "retry_max_semantic": min(
                            settings.retry_max_semantic,
                            limit,
                        ),
                    }
                )
            return settings
        from pa_agent.config.settings import ValidationSettings

        return ValidationSettings()

    def initial_stage2_messages(self) -> list[dict[str, Any]]:
        """Return the clean Stage-2 request before any validation retry turns."""
        return copy.deepcopy(self._initial_stage2_messages)

    def _runtime_prompt_dir(self) -> Path | None:
        value = getattr(self._assembler, "prompt_dir", None)
        return Path(value) if isinstance(value, (str, Path)) else None

    def _clone_role_client(self, provider: Any) -> tuple[Any, bool]:
        """Clone provider state when the client supports it; mocks stay compatible."""
        clone = getattr(self._client, "clone_with_provider", None)
        if callable(clone):
            return clone(provider), True
        return self._client, False

    def _persist_full(
        self,
        record: AnalysisRecord,
        on_event: Callable[[OrchestratorEvent], None],
    ) -> bool:
        """Persist a completed record and emit an event matching the write result."""
        record.usage_total = _usage_from_call_attempts(record.call_attempts)
        result = self._pending_writer.save_full(record)
        ok = bool(getattr(result, "ok", True))
        if not ok:
            on_event(OrchestratorEvent.RecordSaveFailed)
        elif not bool(getattr(result, "audit_ok", True)):
            on_event(OrchestratorEvent.RecordAuditFailed)
        return ok

    def _persist_partial(
        self,
        record: AnalysisRecord,
        reason: str,
        on_event: Callable[[OrchestratorEvent], None],
    ) -> bool:
        """Persist a partial record without confusing it with a completed result."""
        record.usage_total = _usage_from_call_attempts(record.call_attempts)
        result = self._pending_writer.save_partial(record, reason)
        ok = result is not None and bool(getattr(result, "ok", True))
        if not ok:
            on_event(OrchestratorEvent.RecordSaveFailed)
        elif not bool(getattr(result, "audit_ok", True)):
            on_event(OrchestratorEvent.RecordAuditFailed)
        return ok

    # ── Public API ────────────────────────────────────────────────────────────

    def submit(
        self,
        frame: KlineFrame,
        cancel_token: CancelToken,
        on_event: Callable[[OrchestratorEvent], None],
        *,
        on_stage1_reasoning: Callable[[str], None] | None = None,
        on_stage1_content: Callable[[str], None] | None = None,
        on_stage2_reasoning: Callable[[str], None] | None = None,
        on_stage2_content: Callable[[str], None] | None = None,
        on_stage_prompt: Callable[[str, str, str], None] | None = None,
        on_stage2_files: Callable[[list[str]], None] | None = None,
        previous_record: AnalysisRecord | None = None,
        incremental_new_bar_count: int | None = None,
        htf_context: dict[str, Any] | None = None,
        frozen_vpa_catalog: dict[str, Any] | None = None,
        backend_fallback_context: dict[str, Any] | None = None,
    ) -> AnalysisRecord:
        """Run the two-stage analysis pipeline and return an AnalysisRecord.

        The ``on_event`` callback is called synchronously at each pipeline
        milestone.  The returned record is always fully populated with
        whatever data was collected before the pipeline terminated (whether
        by success, validation failure, cancellation, or network error).

        Parameters
        ----------
        frame:
            Immutable KlineFrame snapshot to analyse.
        cancel_token:
            Token checked before each stage and after each API call.
        on_event:
            Callback invoked with OrchestratorEvent values.

        Returns
        -------
        AnalysisRecord
            Fully or partially populated record.
        """
        from pa_agent.config.vpa import resolve_production_vpa_mode, resolve_vpa_mode

        configured_mode = resolve_vpa_mode(getattr(self._settings, "prompt", None))
        mode = (
            "soft"
            if configured_mode == "soft" and self._allow_research_joint
            else resolve_production_vpa_mode(getattr(self._settings, "prompt", None))
        )
        if mode in {"shadow", "soft"}:
            return self._submit_vpa_roles(
                frame,
                cancel_token,
                on_event,
                mode=mode,
                configured_mode=configured_mode,
                on_stage1_reasoning=on_stage1_reasoning,
                on_stage1_content=on_stage1_content,
                on_stage2_reasoning=on_stage2_reasoning,
                on_stage2_content=on_stage2_content,
                on_stage_prompt=on_stage_prompt,
                on_stage2_files=on_stage2_files,
                previous_record=previous_record,
                incremental_new_bar_count=incremental_new_bar_count,
                htf_context=htf_context,
                frozen_vpa_catalog=frozen_vpa_catalog,
                backend_fallback_context=backend_fallback_context,
            )

        # ── Step 1: Build partial record ──────────────────────────────────────
        record = _build_empty_record(
            frame,
            self._settings,
            htf_context=htf_context,
            prompt_dir=self._runtime_prompt_dir(),
        )
        backend_runtime_identity = getattr(
            self._analysis_backend, "runtime_identity", {}
        )
        record = record.model_copy(
            update={
                "role_runtime": {
                    **copy.deepcopy(record.role_runtime),
                    "analysis_backend": self._analysis_backend.backend_id,
                    "analysis_backend_identity": copy.deepcopy(
                        dict(backend_runtime_identity or {})
                    ),
                    "analysis_profile_fingerprint": self._profile_identity.get(
                        "profile_fingerprint"
                    ),
                    "requested_model": self._profile_identity.get(
                        "deepseek_model"
                    ),
                    "effective_model": self._profile_identity.get(
                        "deepseek_model"
                    ),
                }
            }
        )
        if backend_fallback_context:
            record = record.model_copy(
                update={
                    "role_runtime": {
                        **copy.deepcopy(record.role_runtime),
                        "backend_fallback": copy.deepcopy(
                            backend_fallback_context
                        ),
                    }
                }
            )
        call_arm = self._call_arm
        call_attempts = list(record.call_attempts)

        def _append_call(
            stage: str,
            messages: list[dict[str, Any]],
            *,
            reply: Any | None = None,
            status: str = "received",
            validation_category: str | None = None,
            error_type: str | None = None,
        ) -> None:
            attempt = 1 + sum(
                1
                for call in call_attempts
                if call.get("arm") == call_arm and call.get("stage") == stage
            )
            call_attempts.append(
                _call_attempt_entry(
                    arm=call_arm,
                    stage=stage,
                    attempt=attempt,
                    messages=messages,
                    reply=reply,
                    status=status,
                    validation_category=validation_category,
                    error_type=error_type,
                )
            )

        # ── Step 2: Pre-Stage-1 cancel check ─────────────────────────────────
        if cancel_token.is_set():
            record = _with_run_outcome(
                record,
                status="cancelled",
                requested_vpa_mode=mode,
                reason="cancelled_before_preflight",
            )
            self._persist_partial(record, "user_cancelled", on_event)
            on_event(OrchestratorEvent.Cancelled)
            return record

        # ── Step 2.5: Preflight data gate (before Stage1Started) ─────────────
        from pa_agent.data.snapshot_bundle import build_snapshot_bundle

        snapshot_bundle = build_snapshot_bundle(
            frame,
            htf_context,
            expected_identity=getattr(frame, "data_metadata", {}),
            captured_at_ms=getattr(frame, "snapshot_ts_local_ms", None),
        )
        pf = snapshot_bundle.data_quality
        record = record.model_copy(update={"data_quality": pf.to_dict()})
        if not pf.ok:
            record = record.model_copy(update={
                "exception": {
                    "type": "insufficient_data",
                    "stage": "preflight",
                    "failed_check": pf.failed_check,
                    "message": pf.reason,
                }
            })
            record = _with_run_outcome(
                record,
                status="unavailable_input",
                requested_vpa_mode=mode,
                reason=pf.reason,
            )
            self._persist_partial(record, "insufficient_data", on_event)
            on_event(OrchestratorEvent.InsufficientData)
            return record

        from pa_agent.ai.fact_catalog import build_analysis_fact_catalog

        analysis_catalog = build_analysis_fact_catalog(frame)
        frozen_snapshot_hash = analysis_catalog.snapshot_hash
        frozen_cutoff_ts_open = int(frame.bars[0].ts_open)
        run_assembler = self._assembler.clone_with_analysis_fact_catalog(analysis_catalog)

        # ── Step 3: Stage 1 started ───────────────────────────────────────────
        on_event(OrchestratorEvent.Stage1Started)

        # Resolve analysis mode from settings (default: original)
        analysis_mode = "original"
        if self._settings is not None:
            analysis_mode = str(
                getattr(self._settings.general, "analysis_mode", "original") or "original"
            )

        # ── Step 4: Build Stage 1 messages ───────────────────────────────────
        if previous_record is not None and incremental_new_bar_count is not None:
            messages_s1 = run_assembler.build_incremental_stage1(
                frame,
                previous_record,
                incremental_new_bar_count,
                analysis_mode=analysis_mode,
                provider_settings=getattr(self._settings, "provider", None),
                htf_context=htf_context,
            )
        else:
            messages_s1 = run_assembler.build_stage1(
                frame,
                analysis_mode=analysis_mode,
                htf_context=htf_context,
            )
        from pa_agent.ai.higher_timeframe import serialize_higher_timeframe_context
        from pa_agent.ai.prompt_assembler import stage1_prompt_txt_files

        previous_context = None
        if previous_record is not None:
            previous_context = {
                "stage1_diagnosis": copy.deepcopy(previous_record.stage1_diagnosis),
                "stage2_decision": copy.deepcopy(previous_record.stage2_decision),
            }
        stage1_agent_context = {
            "task_id": record.meta.run_id,
            "symbol": frame.symbol,
            "timeframe": frame.timeframe,
            "analysis_mode": analysis_mode,
            "higher_timeframe": serialize_higher_timeframe_context(htf_context),
            "previous_analysis": previous_context,
            "incremental_new_bar_count": incremental_new_bar_count,
            "allowed_knowledge_ids": stage1_prompt_txt_files(),
        }

        # ── Step 5: Call AI for Stage 1 ───────────────────────────────────────
        logger.debug("\n" + "="*80)
        logger.debug("【Stage 1 发送的完整 Prompt】")
        logger.debug("="*80)
        for msg in messages_s1:
            role = msg.get("role", "?").upper()
            content = msg.get("content", "")
            logger.debug("\n--- [%s] ---\n%s", role, content)
        logger.debug("="*80 + "\n")

        # Notify conversation tab of the prompt being sent
        if on_stage_prompt is not None:
            s1_system = next((m.get("content", "") for m in messages_s1 if m.get("role") == "system"), "")
            s1_user = next((m.get("content", "") for m in messages_s1 if m.get("role") == "user"), "")
            on_stage_prompt("stage1", s1_system, s1_user)

        _s1_thinking, _s1_effort = self._thinking_params("stage1")
        s1_streamed_reasoning = False
        s1_streamed_content = False

        def _on_s1_reasoning(chunk: str) -> None:
            nonlocal s1_streamed_reasoning
            s1_streamed_reasoning = True
            if on_stage1_reasoning is not None:
                on_stage1_reasoning(chunk)

        def _on_s1_content(chunk: str) -> None:
            nonlocal s1_streamed_content
            s1_streamed_content = True
            if on_stage1_content is not None:
                on_stage1_content(chunk)

        try:
            reply_s1 = self._stream_chat_resilient(
                messages_s1,
                stage="stage1",
                on_reasoning_token=_on_s1_reasoning,
                on_content_token=_on_s1_content,
                cancel_token=cancel_token,
                thinking=_s1_thinking,
                reasoning_effort=_s1_effort,
                stage_label="Stage 1",
                on_attempt_error=lambda exc: _append_call(
                    "stage1",
                    messages_s1,
                    status="request_failed",
                    error_type=type(exc).__name__,
                ),
                snapshot_hash=frozen_snapshot_hash,
                cutoff_ts_open=frozen_cutoff_ts_open,
                agent_context=stage1_agent_context,
            )
        except FullChainFallbackRequired:
            raise
        except ModelCallFailure as exc:
            if exc.failure.kind == "cancelled":
                record = _with_run_outcome(
                    record,
                    status="cancelled",
                    requested_vpa_mode=mode,
                    reason="cancelled_during_stage1",
                )
                self._persist_partial(record, "user_cancelled", on_event)
                on_event(OrchestratorEvent.Cancelled)
                return record
            logger.warning("Stage 1 provider failure (%s): %s", exc.failure.kind, exc)
            record = record.model_copy(
                update={
                    "stage1_messages": messages_s1,
                    "call_attempts": copy.deepcopy(call_attempts),
                    "exception": self._provider_failure_payload(exc, stage="stage1"),
                }
            )
            record = _with_run_outcome(
                record,
                status="unavailable_provider",
                requested_vpa_mode=mode,
                reason=str(exc),
            )
            self._persist_partial(record, "provider_error", on_event)
            on_event(OrchestratorEvent.Stage1Failed)
            return record

        _append_call("stage1", messages_s1, reply=reply_s1)
        record = record.model_copy(
            update={"call_attempts": copy.deepcopy(call_attempts)}
        )

        if not s1_streamed_reasoning and reply_s1.reasoning_content:
            _emit_buffered_stream(reply_s1.reasoning_content, on_stage1_reasoning)
        if not s1_streamed_content and reply_s1.content:
            _emit_buffered_stream(reply_s1.content, on_stage1_content)

        # ── Step 6: Post-Stage-1-call cancel check ────────────────────────────
        if cancel_token.is_set():
            call_attempts[-1]["status"] = "cancelled"
            record = record.model_copy(
                update={
                    "stage1_messages": messages_s1,
                    "stage1_response": reply_s1.raw,
                    "usage_total": _accumulate_usage(record.usage_total, reply_s1),
                    "call_attempts": copy.deepcopy(call_attempts),
                }
            )
            record = _with_run_outcome(
                record,
                status="cancelled",
                requested_vpa_mode=mode,
                reason="cancelled_after_stage1",
            )
            self._persist_partial(record, "user_cancelled", on_event)
            on_event(OrchestratorEvent.Cancelled)
            return record

        # ── Step 7: Validate Stage 1 ──────────────────────────────────────────
        logger.debug("\n" + "="*80)
        logger.debug("【Stage 1 AI 完整响应】")
        logger.debug("="*80)
        logger.debug(reply_s1.content)
        if reply_s1.reasoning_content:
            logger.debug("\n--- [思考过程] ---\n%s", reply_s1.reasoning_content)
        logger.debug(
            "\n--- [Token 用量] prompt=%s completion=%s latency=%s ---",
            reply_s1.usage.prompt_tokens,
            reply_s1.usage.completion_tokens,
            _latency_ms_label(reply_s1.latency_ms),
        )
        logger.debug("="*80 + "\n")

        prev_s1: dict[str, Any] | None = None
        if previous_record is not None and int(incremental_new_bar_count or 0) > 0:
            prev_s1 = previous_record.stage1_diagnosis

        s1_usage_calls: list[Any] = [reply_s1]

        def _call_s1_retry(msgs: list[dict]) -> Any:
            nonlocal s1_streamed_reasoning, s1_streamed_content
            on_event(OrchestratorEvent.Stage1Retry)
            s1_streamed_reasoning = False
            s1_streamed_content = False
            r = self._stream_chat_resilient(
                msgs,
                stage="stage1",
                on_reasoning_token=_on_s1_reasoning,
                on_content_token=_on_s1_content,
                cancel_token=cancel_token,
                thinking=_s1_thinking,
                reasoning_effort=_s1_effort,
                stage_label="Stage 1 repair",
                on_attempt_error=lambda exc: _append_call(
                    "stage1",
                    msgs,
                    status="request_failed",
                    error_type=type(exc).__name__,
                ),
                snapshot_hash=frozen_snapshot_hash,
                cutoff_ts_open=frozen_cutoff_ts_open,
                agent_context=stage1_agent_context,
            )
            if not s1_streamed_reasoning and r.reasoning_content:
                _emit_buffered_stream(r.reasoning_content, on_stage1_reasoning)
            if not s1_streamed_content and r.content:
                _emit_buffered_stream(r.content, on_stage1_content)
            s1_usage_calls.append(r)
            _append_call("stage1", msgs, reply=r)
            return r

        try:
            vr_s1 = validate_with_retry(
                stage="stage1",
                messages=messages_s1,
                reply=reply_s1,
                validator=self._validator,
                validation_settings=self._validation_settings(),
                validate_kwargs={
                    "kline_frame": frame,
                    "incremental_new_bar_count": int(
                        incremental_new_bar_count or 0
                    ),
                    "incremental_previous_stage1": prev_s1,
                    "htf_context": htf_context,
                    "analysis_fact_catalog": analysis_catalog,
                },
                call_api=_call_s1_retry,
                provider_settings=getattr(self._settings, "provider", None),
            )
        except FullChainFallbackRequired:
            raise
        except ModelCallFailure as exc:
            failure = self._provider_failure_payload(exc, stage="stage1")
            failure["phase"] = "validation_retry"
            record = record.model_copy(
                update={
                    "stage1_messages": messages_s1,
                    "stage1_response": reply_s1.raw,
                    "call_attempts": copy.deepcopy(call_attempts),
                    "usage_total": _accumulate_usage(
                        record.usage_total,
                        reply_s1,
                    ),
                    "exception": failure,
                }
            )
            record = _with_run_outcome(
                record,
                status=(
                    "cancelled"
                    if exc.failure.kind == "cancelled"
                    else "unavailable_provider"
                ),
                requested_vpa_mode=mode,
                reason=str(exc),
            )
            if exc.failure.kind == "cancelled":
                self._persist_partial(record, "user_cancelled", on_event)
                on_event(OrchestratorEvent.Cancelled)
            else:
                self._persist_partial(record, "provider_error", on_event)
                on_event(OrchestratorEvent.Stage1Failed)
            return record
        messages_s1 = vr_s1.canonical_messages
        _apply_validation_to_call_attempts(
            call_attempts,
            arm=call_arm,
            stage="stage1",
            validation_events=vr_s1.validation_events or [],
            result=vr_s1.result,
        )
        record = record.model_copy(
            update={
                "call_attempts": copy.deepcopy(call_attempts),
                "validation_audit": [
                    *record.validation_audit,
                    *[
                        {"stage": "stage1", **event}
                        for event in (vr_s1.validation_events or [])
                    ],
                ]
            }
        )
        reply_s1 = vr_s1.reply
        result_s1 = vr_s1.result
        if vr_s1.attempts > 1 and not isinstance(result_s1, ValidationError):
            logger.info("Stage 1 validation succeeded after %d attempt(s)", vr_s1.attempts)

        if isinstance(result_s1, ValidationError):
            err = result_s1
            err_message = _enrich_stage1_validation_message(err, reply_s1)
            logger.warning(
                "Stage 1 validation failed: category=%s message=%s",
                err.category,
                err_message,
            )
            record = record.model_copy(
                update={
                    "stage1_messages": messages_s1,
                    "stage1_response": reply_s1.raw,
                    "usage_total": _accumulate_usage_calls(record.usage_total, s1_usage_calls),
                    "exception": {
                        "type": "provider_error" if err.category == "e" else "validation_error",
                        "stage": "stage1",
                        "category": err.category,
                        "message": err_message,
                        "missing_fields": err.missing_fields,
                        "invalid_fields": err.invalid_fields,
                        "raw_text": err.raw_text,
                        "parse_position": err.parse_position,
                        "validation_attempts": vr_s1.attempts,
                    },
                }
            )
            record = _with_run_outcome(
                record,
                status=_validation_failure_status(err),
                requested_vpa_mode=mode,
                reason=err_message,
            )
            self._persist_partial(record, f"stage1_{err.category}", on_event)
            on_event(OrchestratorEvent.Stage1Failed)
            return record

        # Validation passed — extract the parsed JSON
        assert isinstance(result_s1, Ok)
        stage1_json: dict = result_s1.obj
        stage1_for_stage2 = stage1_json

        # ── Step 9: Stage 1 done ──────────────────────────────────────────────
        on_event(OrchestratorEvent.Stage1Done)

        # ── Step 10: Route strategy files ─────────────────────────────────────
        if callable(self._router) and not hasattr(self._router, "route"):
            strategy_files = self._router(stage1_for_stage2)
        else:
            strategy_files = self._router.route(stage1_for_stage2)

        # ── Step 11: Load experience entries ──────────────────────────────────
        cycle_position: str = stage1_for_stage2.get("cycle_position", "unknown")
        direction = str(stage1_for_stage2.get("direction", "") or "")
        patterns = stage1_for_stage2.get("detected_patterns") or []
        prompt_cfg = getattr(self._settings, "prompt", None) if self._settings else None
        max_exp = getattr(prompt_cfg, "experience_max_entries", 0) if prompt_cfg else 0
        max_chars = (
            getattr(prompt_cfg, "experience_max_chars_per_entry", 400) if prompt_cfg else 400
        )
        if max_exp <= 0:
            experience_entries = []
        elif hasattr(self._exp_reader, "read_for_stage2"):
            experience_entries = self._exp_reader.read_for_stage2(
                cycle_position,
                direction=direction,
                patterns=patterns,
                max_entries=max_exp,
                max_chars_per_entry=max_chars,
            )
        else:
            experience_entries = self._exp_reader.read_top5(cycle_position)[:max_exp]

        # ── Step 12: Pre-Stage-2 cancel check ────────────────────────────────
        if cancel_token.is_set():
            record = record.model_copy(
                update={
                    "stage1_messages": messages_s1,
                    "stage1_response": reply_s1.raw,
                    "stage1_diagnosis": stage1_json,
                    "strategy_files_used": strategy_files,
                    "experience_loaded": [
                        e.model_dump() if hasattr(e, "model_dump") else dict(e)
                        for e in experience_entries
                    ],
                    "usage_total": _accumulate_usage(record.usage_total, reply_s1),
                }
            )
            record = _with_run_outcome(
                record,
                status="cancelled",
                requested_vpa_mode=mode,
                reason="cancelled_before_stage2",
            )
            self._persist_partial(record, "user_cancelled", on_event)
            on_event(OrchestratorEvent.Cancelled)
            return record

        # ── Step 13: Stage 2 started ──────────────────────────────────────────
        on_event(OrchestratorEvent.Stage2Started)
        if on_stage2_files is not None:
            on_stage2_files(list(strategy_files))

        gate_result = str(stage1_for_stage2.get("gate_result", "proceed")).lower()
        if gate_result in ("wait", "unknown"):
            from pa_agent.ai.decision_tree import build_stage2_gate_wait_response

            if on_stage_prompt is not None:
                on_stage_prompt("stage2", "", "（阶段一闸门未通过，跳过阶段二模型调用）")
            short_msg = (
                f"阶段一 gate_result={gate_result}，程序已短路生成阶段二结果，"
                "未向模型发起请求。\n"
            )
            _emit_buffered_stream(short_msg, on_stage2_content)

            stage2_json = build_stage2_gate_wait_response(stage1_for_stage2)
            on_event(OrchestratorEvent.Stage2Done)
            logger.info("next_bar_prediction direction=null probs=null/null/null unpredictable=true (gate short-circuit)")
            usage_total = _accumulate_usage(record.usage_total, reply_s1)
            record = record.model_copy(
                update={
                    "stage1_messages": messages_s1,
                    "stage1_response": reply_s1.raw,
                    "stage1_diagnosis": stage1_json,
                    "stage2_messages": [],
                    "stage2_response": None,
                    "stage2_decision": stage2_json,
                    "strategy_files_used": strategy_files,
                    "experience_loaded": [
                        e.model_dump() if hasattr(e, "model_dump") else dict(e)
                        for e in experience_entries
                    ],
                    "usage_total": usage_total,
                    "exception": None,
                    "run_outcome": RunOutcome(
                        status=_completed_run_status(record),
                        official_path="pa_only",
                        requested_vpa_mode=mode,
                    ).model_dump(),
                }
            )
            if not self._persist_full(record, on_event):
                return record
            on_event(OrchestratorEvent.RecordSaved)
            return record

        # ── Step 14: Build Stage 2 messages ───────────────────────────────────
        _enable_next_bar = bool(
            getattr(getattr(self._settings, "general", None), "enable_next_bar_prediction", False)
        )
        messages_s2 = run_assembler.build_stage2_continuation(
            frame=frame,
            stage1_messages=messages_s1,
            stage1_reply_content=reply_s1.content,
            stage1_json=stage1_for_stage2,
            strategy_files=strategy_files,
            experience_entries=experience_entries,
            decision_stance=record.meta.decision_stance,
            previous_record=previous_record,
            enable_next_bar_prediction=_enable_next_bar,
            provider_settings=getattr(self._settings, "provider", None),
            htf_context=htf_context,
        )
        self._initial_stage2_messages = copy.deepcopy(messages_s2)
        from pa_agent.ai.prompt_assembler import stage2_prompt_txt_files

        stage2_agent_context = {
            "task_id": record.meta.run_id,
            "symbol": frame.symbol,
            "timeframe": frame.timeframe,
            "stage1_diagnosis": copy.deepcopy(stage1_for_stage2),
            "router_manifest": list(strategy_files),
            "decision_stance": record.meta.decision_stance,
            "higher_timeframe": serialize_higher_timeframe_context(htf_context),
            "experience_entries": [
                entry.model_dump() if hasattr(entry, "model_dump") else dict(entry)
                for entry in experience_entries
            ],
            "allowed_knowledge_ids": stage2_prompt_txt_files(
                strategy_files,
                direction=direction,
                load_full_strategy_library=bool(
                    getattr(prompt_cfg, "stage2_load_full_strategy_library", False)
                ),
            ),
        }

        # ── Step 15: Call AI for Stage 2 ──────────────────────────────────────
        logger.debug("\n" + "="*80)
        logger.debug("【Stage 2 发送的完整 Prompt】")
        logger.debug("="*80)
        for msg in messages_s2:
            role = msg.get("role", "?").upper()
            content = msg.get("content", "")
            logger.debug("\n--- [%s] ---\n%s", role, content)
        logger.debug("="*80 + "\n")

        # Notify conversation tab of the prompt being sent
        if on_stage_prompt is not None:
            s2_system = next((m.get("content", "") for m in messages_s2 if m.get("role") == "system"), "")
            s2_user = next((m.get("content", "") for m in reversed(messages_s2) if m.get("role") == "user"), "")
            on_stage_prompt("stage2", s2_system, s2_user)

        s2_streamed_reasoning = False
        s2_streamed_content = False

        def _on_s2_reasoning(chunk: str) -> None:
            nonlocal s2_streamed_reasoning
            s2_streamed_reasoning = True
            if on_stage2_reasoning is not None:
                on_stage2_reasoning(chunk)

        def _on_s2_content(chunk: str) -> None:
            nonlocal s2_streamed_content
            s2_streamed_content = True
            if on_stage2_content is not None:
                on_stage2_content(chunk)

        _s2_thinking, _s2_effort = self._thinking_params("stage2")
        try:
            reply_s2 = self._stream_chat_resilient(
                messages_s2,
                stage="stage2",
                on_reasoning_token=_on_s2_reasoning,
                on_content_token=_on_s2_content,
                cancel_token=cancel_token,
                thinking=_s2_thinking,
                reasoning_effort=_s2_effort,
                stage_label="Stage 2",
                on_attempt_error=lambda exc: _append_call(
                    "stage2",
                    messages_s2,
                    status="request_failed",
                    error_type=type(exc).__name__,
                ),
                snapshot_hash=frozen_snapshot_hash,
                cutoff_ts_open=frozen_cutoff_ts_open,
                agent_context=stage2_agent_context,
            )
        except FullChainFallbackRequired:
            raise
        except ModelCallFailure as exc:
            if exc.failure.kind == "cancelled":
                record = record.model_copy(
                    update={
                        "stage1_messages": messages_s1,
                        "stage1_response": reply_s1.raw,
                        "stage1_diagnosis": stage1_json,
                        "stage2_messages": messages_s2,
                        "call_attempts": copy.deepcopy(call_attempts),
                        "strategy_files_used": strategy_files,
                        "experience_loaded": [
                            e.model_dump() if hasattr(e, "model_dump") else dict(e)
                            for e in experience_entries
                        ],
                        "usage_total": _accumulate_usage(
                            record.usage_total,
                            reply_s1,
                        ),
                    }
                )
                record = _with_run_outcome(
                    record,
                    status="cancelled",
                    requested_vpa_mode=mode,
                    reason="cancelled_during_stage2",
                )
                self._persist_partial(record, "user_cancelled", on_event)
                on_event(OrchestratorEvent.Cancelled)
                return record
            logger.warning("Stage 2 provider failure (%s): %s", exc.failure.kind, exc)
            record = record.model_copy(
                update={
                    "stage1_messages": messages_s1,
                    "stage1_response": reply_s1.raw,
                    "stage1_diagnosis": stage1_json,
                    "stage2_messages": messages_s2,
                    "call_attempts": copy.deepcopy(call_attempts),
                    "strategy_files_used": strategy_files,
                    "experience_loaded": [
                        e.model_dump() if hasattr(e, "model_dump") else dict(e)
                        for e in experience_entries
                    ],
                    "usage_total": _accumulate_usage(record.usage_total, reply_s1),
                    "exception": self._provider_failure_payload(exc, stage="stage2"),
                }
            )
            record = _with_run_outcome(
                record,
                status="unavailable_provider",
                requested_vpa_mode=mode,
                reason=str(exc),
            )
            self._persist_partial(record, "provider_error", on_event)
            on_event(OrchestratorEvent.Stage2Failed)
            return record

        _append_call("stage2", messages_s2, reply=reply_s2)
        record = record.model_copy(
            update={"call_attempts": copy.deepcopy(call_attempts)}
        )

        if not s2_streamed_reasoning and reply_s2.reasoning_content:
            _emit_buffered_stream(reply_s2.reasoning_content, on_stage2_reasoning)
        if not s2_streamed_content and reply_s2.content:
            _emit_buffered_stream(reply_s2.content, on_stage2_content)

        # ── Step 16: Post-Stage-2-call cancel check ───────────────────────────
        if cancel_token.is_set():
            call_attempts[-1]["status"] = "cancelled"
            record = record.model_copy(
                update={
                    "stage1_messages": messages_s1,
                    "stage1_response": reply_s1.raw,
                    "stage1_diagnosis": stage1_json,
                    "stage2_messages": messages_s2,
                    "stage2_response": reply_s2.raw,
                    "call_attempts": copy.deepcopy(call_attempts),
                    "strategy_files_used": strategy_files,
                    "experience_loaded": [
                        e.model_dump() if hasattr(e, "model_dump") else dict(e)
                        for e in experience_entries
                    ],
                    "usage_total": _accumulate_usage(
                        _accumulate_usage(record.usage_total, reply_s1),
                        reply_s2,
                    ),
                }
            )
            record = _with_run_outcome(
                record,
                status="cancelled",
                requested_vpa_mode=mode,
                reason="cancelled_after_stage2",
            )
            self._persist_partial(record, "user_cancelled", on_event)
            on_event(OrchestratorEvent.Cancelled)
            return record

        # ── Step 17: Validate Stage 2 ─────────────────────────────────────────
        logger.debug("\n" + "="*80)
        logger.debug("【Stage 2 AI 完整响应】")
        logger.debug("="*80)
        logger.debug(reply_s2.content)
        if reply_s2.reasoning_content:
            logger.debug("\n--- [思考过程] ---\n%s", reply_s2.reasoning_content)
        logger.debug(
            "\n--- [Token 用量] prompt=%s completion=%s latency=%s ---",
            reply_s2.usage.prompt_tokens,
            reply_s2.usage.completion_tokens,
            _latency_ms_label(reply_s2.latency_ms),
        )
        logger.debug("="*80 + "\n")

        s2_usage_calls: list[Any] = [reply_s2]

        def _call_s2_retry(msgs: list[dict]) -> Any:
            nonlocal s2_streamed_reasoning, s2_streamed_content
            on_event(OrchestratorEvent.Stage2Retry)
            s2_streamed_reasoning = False
            s2_streamed_content = False
            r = self._stream_chat_resilient(
                msgs,
                stage="stage2",
                on_reasoning_token=_on_s2_reasoning,
                on_content_token=_on_s2_content,
                cancel_token=cancel_token,
                thinking=_s2_thinking,
                reasoning_effort=_s2_effort,
                stage_label="Stage 2 repair",
                on_attempt_error=lambda exc: _append_call(
                    "stage2",
                    msgs,
                    status="request_failed",
                    error_type=type(exc).__name__,
                ),
                snapshot_hash=frozen_snapshot_hash,
                cutoff_ts_open=frozen_cutoff_ts_open,
                agent_context=stage2_agent_context,
            )
            if not s2_streamed_reasoning and r.reasoning_content:
                _emit_buffered_stream(r.reasoning_content, on_stage2_reasoning)
            if not s2_streamed_content and r.content:
                _emit_buffered_stream(r.content, on_stage2_content)
            s2_usage_calls.append(r)
            _append_call("stage2", msgs, reply=r)
            return r

        try:
            vr_s2 = validate_with_retry(
                stage="stage2",
                messages=messages_s2,
                reply=reply_s2,
                validator=self._validator,
                validation_settings=self._validation_settings(),
                validate_kwargs={
                    "kline_frame": frame,
                    "stage1_json": stage1_for_stage2,
                    "skip_next_bar": not _enable_next_bar,
                    "analysis_fact_catalog": analysis_catalog,
                },
                call_api=_call_s2_retry,
                provider_settings=getattr(self._settings, "provider", None),
            )
        except FullChainFallbackRequired:
            raise
        except ModelCallFailure as exc:
            failure = self._provider_failure_payload(exc, stage="stage2")
            failure["phase"] = "validation_retry"
            record = record.model_copy(
                update={
                    "stage1_messages": messages_s1,
                    "stage1_response": reply_s1.raw,
                    "stage1_diagnosis": stage1_json,
                    "stage2_messages": messages_s2,
                    "stage2_response": reply_s2.raw,
                    "call_attempts": copy.deepcopy(call_attempts),
                    "strategy_files_used": strategy_files,
                    "experience_loaded": [
                        e.model_dump() if hasattr(e, "model_dump") else dict(e)
                        for e in experience_entries
                    ],
                    "usage_total": _accumulate_usage(
                        _accumulate_usage(record.usage_total, reply_s1),
                        reply_s2,
                    ),
                    "exception": failure,
                }
            )
            record = _with_run_outcome(
                record,
                status=(
                    "cancelled"
                    if exc.failure.kind == "cancelled"
                    else "unavailable_provider"
                ),
                requested_vpa_mode=mode,
                reason=str(exc),
            )
            if exc.failure.kind == "cancelled":
                self._persist_partial(record, "user_cancelled", on_event)
                on_event(OrchestratorEvent.Cancelled)
            else:
                self._persist_partial(record, "provider_error", on_event)
                on_event(OrchestratorEvent.Stage2Failed)
            return record
        messages_s2 = vr_s2.canonical_messages
        _apply_validation_to_call_attempts(
            call_attempts,
            arm=call_arm,
            stage="stage2",
            validation_events=vr_s2.validation_events or [],
            result=vr_s2.result,
        )
        record = record.model_copy(
            update={
                "call_attempts": copy.deepcopy(call_attempts),
                "validation_audit": [
                    *record.validation_audit,
                    *[
                        {"stage": "stage2", **event}
                        for event in (vr_s2.validation_events or [])
                    ],
                ]
            }
        )
        reply_s2 = vr_s2.reply
        result_s2 = vr_s2.result
        if vr_s2.attempts > 1 and not isinstance(result_s2, ValidationError):
            logger.info("Stage 2 validation succeeded after %d attempt(s)", vr_s2.attempts)

        if isinstance(result_s2, ValidationError):
            err = result_s2
            err_message = _enrich_stage2_validation_message(err, reply_s2)
            logger.warning(
                "Stage 2 validation failed: category=%s message=%s",
                err.category,
                err_message,
            )
            record = record.model_copy(
                update={
                    "stage1_messages": messages_s1,
                    "stage1_response": reply_s1.raw,
                    "stage1_diagnosis": stage1_json,
                    "stage2_messages": messages_s2,
                    "stage2_response": reply_s2.raw,
                    "strategy_files_used": strategy_files,
                    "experience_loaded": [
                        e.model_dump() if hasattr(e, "model_dump") else dict(e)
                        for e in experience_entries
                    ],
                    "usage_total": _accumulate_usage_calls(
                        _accumulate_usage_calls(record.usage_total, s1_usage_calls),
                        s2_usage_calls,
                    ),
                    "exception": {
                        "type": "provider_error" if err.category == "e" else "validation_error",
                        "stage": "stage2",
                        "category": err.category,
                        "message": err_message,
                        "missing_fields": err.missing_fields,
                        "invalid_fields": err.invalid_fields,
                        "raw_text": err.raw_text,
                        "parse_position": err.parse_position,
                        "validation_attempts": vr_s2.attempts,
                    },
                }
            )
            record = _with_run_outcome(
                record,
                status=_validation_failure_status(err),
                requested_vpa_mode=mode,
                reason=err_message,
            )
            self._persist_partial(record, f"stage2_{err.category}", on_event)
            on_event(OrchestratorEvent.Stage2Failed)
            return record

        # Validation passed
        assert isinstance(result_s2, Ok)
        stage2_json: dict = result_s2.obj
        if not _enable_next_bar and isinstance(stage2_json, dict):
            stage2_json = copy.deepcopy(stage2_json)
            stage2_json.pop("next_bar_prediction", None)
        if not self._defer_execution_policy:
            stage2_json = _apply_confidence_threshold(
                stage2_json,
                int(
                    getattr(
                        getattr(self._settings, "general", None),
                        "decision_confidence_threshold",
                        0,
                    )
                ),
            )

        # ── Step 19: Stage 2 done ─────────────────────────────────────────────
        on_event(OrchestratorEvent.Stage2Done)

        # ── Step 19.5: Log next_bar_prediction (R9.3, NFR2.1) ───────────────────
        _pred = stage2_json if isinstance(stage2_json, dict) else {}
        _nb_pred = _pred.get("next_bar_prediction")
        if not _enable_next_bar:
            logger.info("next_bar_prediction omitted (feature disabled)")
        elif isinstance(_nb_pred, dict):
            if _nb_pred.get("unpredictable"):
                logger.info("next_bar_prediction direction=null probs=null/null/null unpredictable=true")
            else:
                _probs = _nb_pred.get("probabilities") or {}
                logger.info(
                    "next_bar_prediction direction=%s probs=%s/%s/%s unpredictable=false",
                    _nb_pred.get("direction"),
                    _probs.get("bullish"),
                    _probs.get("bearish"),
                    _probs.get("neutral"),
                )
        elif _enable_next_bar:
            logger.info("next_bar_prediction absent from stage2 response")

        _nc_pred = _pred.get("next_cycle_prediction")
        if isinstance(_nc_pred, dict):
            if _nc_pred.get("unpredictable"):
                logger.info("next_cycle_prediction cycle=null unpredictable=true")
            else:
                logger.info(
                    "next_cycle_prediction cycle=%s direction=%s unpredictable=false",
                    _nc_pred.get("cycle"),
                    _nc_pred.get("direction"),
                )
        else:
            logger.info("next_cycle_prediction absent from stage2 response")

        # ── Step 20: Build final record ───────────────────────────────────────
        usage_total = _accumulate_usage_calls(
            _accumulate_usage_calls(record.usage_total, s1_usage_calls),
            s2_usage_calls,
        )
        record = record.model_copy(
            update={
                "stage1_messages": messages_s1,
                "stage1_response": reply_s1.raw,
                "stage1_diagnosis": stage1_json,
                "stage2_messages": messages_s2,
                "stage2_response": reply_s2.raw,
                "stage2_decision": stage2_json,
                "strategy_files_used": strategy_files,
                "experience_loaded": [
                    e.model_dump() if hasattr(e, "model_dump") else dict(e)
                    for e in experience_entries
                ],
                "usage_total": usage_total,
                "exception": None,
                "run_outcome": RunOutcome(
                    status=_completed_run_status(record),
                    official_path="pa_only",
                    requested_vpa_mode=mode,
                ).model_dump(),
            }
        )

        # ── Step 22: Persist full record ──────────────────────────────────────
        if not self._persist_full(record, on_event):
            return record

        # ── Step 23: Record saved event ───────────────────────────────────────
        on_event(OrchestratorEvent.RecordSaved)

        # ── Step 24: Return ───────────────────────────────────────────────────
        return record

    def _submit_vpa_roles(
        self,
        frame: KlineFrame,
        cancel_token: CancelToken,
        on_event: Callable[[OrchestratorEvent], None],
        *,
        mode: str,
        configured_mode: str,
        on_stage1_reasoning: Callable[[str], None] | None,
        on_stage1_content: Callable[[str], None] | None,
        on_stage2_reasoning: Callable[[str], None] | None,
        on_stage2_content: Callable[[str], None] | None,
        on_stage_prompt: Callable[[str, str, str], None] | None,
        on_stage2_files: Callable[[list[str]], None] | None,
        previous_record: AnalysisRecord | None,
        incremental_new_bar_count: int | None,
        htf_context: dict[str, Any] | None,
        frozen_vpa_catalog: dict[str, Any] | None,
        backend_fallback_context: dict[str, Any] | None,
    ) -> AnalysisRecord:
        """Run PA, independent VPA evidence, and a bounded joint arbiter."""
        from pa_agent.ai.json_validator import JsonValidator
        from pa_agent.ai.vpa_measurements import (
            build_vpa_evidence_catalog,
            validate_frozen_vpa_catalog,
        )
        from pa_agent.data.snapshot_bundle import build_snapshot_bundle

        assert self._settings is not None
        preflight_record = _build_empty_record(
            frame,
            self._settings,
            htf_context=htf_context,
            prompt_dir=self._runtime_prompt_dir(),
        )
        if backend_fallback_context:
            preflight_record = preflight_record.model_copy(
                update={
                    "role_runtime": {
                        **copy.deepcopy(preflight_record.role_runtime),
                        "backend_fallback": copy.deepcopy(
                            backend_fallback_context
                        ),
                    }
                }
            )
        if cancel_token.is_set():
            preflight_record = preflight_record.model_copy(
                update={
                    "run_outcome": RunOutcome(
                        status="cancelled",
                        official_path="none",
                        requested_vpa_mode=configured_mode,
                        effective_vpa_mode=mode,
                        reason="cancelled_before_preflight",
                    ).model_dump()
                }
            )
            self._persist_partial(preflight_record, "user_cancelled", on_event)
            on_event(OrchestratorEvent.Cancelled)
            return preflight_record

        snapshot_bundle = build_snapshot_bundle(
            frame,
            htf_context,
            expected_identity=getattr(frame, "data_metadata", {}),
            captured_at_ms=getattr(frame, "snapshot_ts_local_ms", None),
        )
        preflight = snapshot_bundle.data_quality
        preflight_record = preflight_record.model_copy(
            update={"data_quality": preflight.to_dict()}
        )
        if not preflight.ok:
            preflight_record = preflight_record.model_copy(
                update={
                    "exception": {
                        "type": "insufficient_data",
                        "stage": "preflight",
                        "failed_check": preflight.failed_check,
                        "message": preflight.reason,
                    },
                    "run_outcome": RunOutcome(
                        status="unavailable_input",
                        official_path="none",
                        requested_vpa_mode=configured_mode,
                        effective_vpa_mode=mode,
                        reason=preflight.reason,
                    ).model_dump(),
                }
            )
            self._persist_partial(preflight_record, "insufficient_data", on_event)
            on_event(OrchestratorEvent.InsufficientData)
            return preflight_record

        if frozen_vpa_catalog is not None:
            try:
                frozen_vpa_catalog = validate_frozen_vpa_catalog(
                    frame,
                    frozen_vpa_catalog,
                )
            except ValueError as exc:
                preflight_record = preflight_record.model_copy(
                    update={
                        "exception": {
                            "type": "insufficient_data",
                            "stage": "preflight",
                            "failed_check": "frozen_vpa_catalog_mismatch",
                            "message": str(exc),
                        },
                        "run_outcome": RunOutcome(
                            status="unavailable_input",
                            official_path="none",
                            requested_vpa_mode=configured_mode,
                            effective_vpa_mode=mode,
                            reason=str(exc),
                        ).model_dump(),
                    }
                )
                self._persist_partial(
                    preflight_record,
                    "frozen_vpa_catalog_mismatch",
                    on_event,
                )
                on_event(OrchestratorEvent.InsufficientData)
                return preflight_record

        baseline_settings = self._settings.model_copy(deep=True)
        baseline_settings.prompt.vpa_mode = "off"
        baseline_settings.prompt.vpa_enabled = False
        role_settings = self._settings.model_copy(deep=True)
        role_settings.prompt.vpa_mode = mode
        role_settings.prompt.vpa_enabled = True

        codex_pa_baseline = self._analysis_backend.backend_id == "codex_agent"
        if codex_pa_baseline:
            baseline_client = None
            baseline_isolated = True
            baseline_backend = self._analysis_backend
        else:
            baseline_client, baseline_isolated = self._clone_role_client(
                baseline_settings.provider
            )
            baseline_backend = DirectAnalysisRoleBackend(
                client=baseline_client,
                settings=baseline_settings,
                profile_identity=self._profile_identity,
                allow_provider_fallback=False,
                logger=logger,
            )
        vpa_client, vpa_isolated = self._clone_role_client(
            role_settings.provider
        )
        joint_client: Any | None = None
        joint_isolated = True
        if mode == "soft" and self._allow_research_joint:
            joint_client, joint_isolated = self._clone_role_client(
                role_settings.provider
            )

        def relay_baseline_event(event: OrchestratorEvent) -> None:
            if event != OrchestratorEvent.RecordSaved:
                on_event(event)

        baseline_runner = TwoStageOrchestrator(
            client=baseline_client,
            analysis_backend=baseline_backend,
            assembler=self._assembler.clone_with_prompt_settings(
                baseline_settings.prompt
            ),
            router=self._router,
            validator=JsonValidator(
                baseline_settings,
                prompt_dir=self._runtime_prompt_dir(),
            ),
            pending_writer=SuppressedRecordWriter(),
            exp_reader=self._exp_reader,
            settings=baseline_settings,
            call_arm="pa",
            allow_provider_fallback=False,
            defer_execution_policy=True,
            allow_research_joint=False,
        )
        baseline = baseline_runner.submit(
            frame,
            cancel_token,
            relay_baseline_event,
            on_stage1_reasoning=on_stage1_reasoning,
            on_stage1_content=on_stage1_content,
            on_stage2_reasoning=on_stage2_reasoning,
            on_stage2_content=on_stage2_content,
            on_stage_prompt=on_stage_prompt,
            on_stage2_files=on_stage2_files,
            previous_record=previous_record,
            incremental_new_bar_count=incremental_new_bar_count,
            htf_context=htf_context,
            backend_fallback_context=backend_fallback_context,
        )
        if baseline.exception is not None or cancel_token.is_set():
            if cancel_token.is_set():
                baseline = baseline.model_copy(
                    update={
                        "run_outcome": RunOutcome(
                            status="cancelled",
                            official_path="none",
                            requested_vpa_mode=configured_mode,
                            effective_vpa_mode=mode,
                            reason="cancelled_during_pa",
                        ).model_dump()
                    }
                )
                reason = "user_cancelled"
            else:
                baseline_outcome = (
                    baseline.run_outcome
                    if isinstance(baseline.run_outcome, dict)
                    else {}
                )
                baseline_status = str(
                    baseline_outcome.get("status") or "internal_error"
                )
                if baseline_status not in {
                    "unavailable_input",
                    "unavailable_provider",
                    "invalid_model_output",
                    "internal_error",
                }:
                    baseline_status = "internal_error"
                baseline = baseline.model_copy(
                    update={
                        "run_outcome": RunOutcome(
                            status=baseline_status,
                            official_path="none",
                            requested_vpa_mode=configured_mode,
                            effective_vpa_mode=mode,
                            reason=(
                                str(baseline_outcome.get("reason") or "")
                                or "PA framework or PA-only decision failed"
                            ),
                        ).model_dump()
                    }
                )
                reason = "pa_analysis_failed"
            self._persist_partial(baseline, reason, on_event)
            return baseline

        stage1 = copy.deepcopy(baseline.stage1_diagnosis or {})
        baseline_stage2 = copy.deepcopy(baseline.stage2_decision or {})
        hypotheses = compile_pa_handoff(stage1, baseline_stage2, frame)
        hypotheses_dict = hypotheses.model_dump()
        catalog = (
            copy.deepcopy(frozen_vpa_catalog)
            if frozen_vpa_catalog is not None
            else build_vpa_evidence_catalog(frame)
        )
        snapshot_hash = str(catalog.get("snapshot_hash") or "")
        role_assembler = self._assembler.clone_with_prompt_settings(
            role_settings.prompt,
            vpa_evidence_catalog=catalog,
        )
        role_calls: list[dict[str, Any]] = []
        role_audit: list[dict[str, Any]] = []
        role_replies: list[Any] = []
        if configured_mode == "soft" and mode == "shadow":
            role_audit.append(
                {
                    "stage": "runtime_policy",
                    "arm": "production",
                    "code": "soft_not_promoted",
                    "severity": "warning",
                    "owner": "runtime_policy",
                    "retry_policy": "never",
                    "message": "Soft is research-only; production executed Shadow.",
                }
            )

        def execute_role(
            *,
            role: str,
            client: Any,
            schema: dict[str, Any],
            messages: list[dict[str, Any]],
            validate: Callable[[str], Any],
        ) -> tuple[StructuredRoleResult | None, dict[str, Any] | None]:
            role_backend = DirectAnalysisRoleBackend(
                client=client,
                settings=role_settings,
                profile_identity=self._profile_identity,
                allow_provider_fallback=False,
                logger=logger,
            )

            def call_api(current_messages: list[dict[str, Any]]) -> Any:
                profile = self._thinking_profile(role)
                attempt = 1 + sum(
                    1
                    for item in role_calls
                    if item.get("arm") == role and item.get("stage") == role
                )
                try:
                    result = role_backend.run(
                        AnalysisRoleRequest(
                            role=role,
                            messages=current_messages,
                            schema=schema,
                            thinking=profile.thinking,
                            reasoning_effort=profile.reasoning_effort,
                        ),
                        on_reasoning_token=None,
                        on_content_token=None,
                        cancel_token=cancel_token,
                    )
                except ModelCallFailure as exc:
                    failed = _call_attempt_entry(
                        arm=role,
                        stage=role,
                        attempt=attempt,
                        messages=current_messages,
                        status="request_failed",
                        error_type=exc.failure.kind,
                    )
                    failed["transport_attempts"] = [
                        dict(item) for item in exc.attempts
                    ]
                    role_calls.append(failed)
                    raise
                reply = result.reply
                role_replies.append(reply)
                role_calls.append(
                    _call_attempt_entry(
                        arm=role,
                        stage=role,
                        attempt=attempt,
                        messages=current_messages,
                        reply=reply,
                    )
                )
                return reply

            try:
                result = run_structured_role(
                    role=role,
                    messages=messages,
                    call_api=call_api,
                    validate=validate,
                    max_repairs=1,
                )
            except ModelCallFailure as exc:
                return None, self._provider_failure_payload(exc, stage=role)

            role_audit.extend(
                {
                    "stage": role,
                    "arm": role,
                    **copy.deepcopy(event),
                }
                for event in result.validation_events
            )
            validation_result: Ok | ValidationError
            if result.ok:
                validation_result = Ok(result.obj or {})
            else:
                validation_result = ValidationError(
                    category="c",
                    stage=role,
                    raw_text="",
                    invalid_fields=list(result.hard_errors),
                    message="; ".join(result.hard_errors),
                )
            _apply_validation_to_call_attempts(
                role_calls,
                arm=role,
                stage=role,
                validation_events=list(result.validation_events),
                result=validation_result,
            )
            if result.ok:
                return result, None
            return result, {
                "type": "validation_error",
                "stage": role,
                "category": "c",
                "message": "; ".join(result.hard_errors),
                "invalid_fields": list(result.hard_errors),
                "validation_attempts": len(result.replies),
            }

        def role_response_payload(
            result: StructuredRoleResult | None,
        ) -> list[dict[str, Any]]:
            if result is None:
                return []
            return [
                copy.deepcopy(getattr(reply, "raw", {}))
                for reply in result.replies
            ]

        def finish_cancelled(
            reason: str,
            *,
            vpa_report_obj: dict[str, Any] | None = None,
        ) -> AnalysisRecord:
            record = baseline.model_copy(
                update={
                    "pa_hypotheses": copy.deepcopy(hypotheses_dict),
                    "vpa_evidence": copy.deepcopy(vpa_report_obj),
                    "run_outcome": RunOutcome(
                        status="cancelled",
                        official_path="none",
                        requested_vpa_mode=configured_mode,
                        effective_vpa_mode=mode,
                        reason=reason,
                    ).model_dump(),
                    "usage_total": _accumulate_usage_calls(
                        baseline.usage_total,
                        role_replies,
                    ),
                    "call_attempts": [
                        *copy.deepcopy(baseline.call_attempts),
                        *copy.deepcopy(role_calls),
                    ],
                    "validation_audit": [
                        *copy.deepcopy(baseline.validation_audit),
                        *copy.deepcopy(role_audit),
                    ],
                }
            )
            self._persist_partial(record, "user_cancelled", on_event)
            on_event(OrchestratorEvent.Cancelled)
            return record

        def finish(
            *,
            status: str,
            official_path: str,
            official_stage2: dict[str, Any],
            vpa_result: StructuredRoleResult | None,
            vpa_report_obj: dict[str, Any] | None,
            joint_result: StructuredRoleResult | None,
            joint_envelope: dict[str, Any] | None,
            degradation_stage: str | None = None,
            degradation_reason: str | None = None,
        ) -> AnalysisRecord:
            threshold = int(
                getattr(
                    getattr(self._settings, "general", None),
                    "decision_confidence_threshold",
                    0,
                )
            )
            executable_stage2 = _apply_confidence_threshold(
                official_stage2,
                threshold,
            )
            research_joint = bool(self._allow_research_joint and mode == "soft")
            joint_stage2 = (
                copy.deepcopy(joint_envelope.get("stage2_decision") or {})
                if research_joint and isinstance(joint_envelope, dict)
                else None
            )
            joint_semantic = copy.deepcopy(joint_envelope) if research_joint else None
            if isinstance(joint_semantic, dict):
                joint_semantic.pop("stage2_decision", None)
            completed_status = _completed_run_status(
                baseline,
                *role_audit,
            )
            effective_status = (
                completed_status
                if status == "completed"
                else status
            )
            joint_applicable = bool(
                hypotheses.candidate_ids
                and str(stage1.get("gate_result") or "unknown") == "proceed"
                and baseline_runner.initial_stage2_messages()
            )
            role_statuses = {
                "vpa": _role_execution_status(
                    vpa_result,
                    applicable=True,
                    failed=degradation_stage == "vpa_evidence",
                )
            }
            if research_joint:
                role_statuses["joint"] = _role_execution_status(
                    joint_result,
                    applicable=joint_applicable,
                    failed=degradation_stage == "joint_arbiter",
                )
            authority_violations = sorted(
                {
                    str(violation)
                    for event in role_audit
                    if isinstance(event, dict)
                    for violation in event.get("authority_violations", [])
                    if str(violation).strip()
                }
            )
            reasoning_roles = ["stage1", "stage2", "vpa_evidence"]
            if research_joint:
                reasoning_roles.append("joint_arbiter")
            effective_calls = [
                *copy.deepcopy(baseline.call_attempts),
                *copy.deepcopy(role_calls),
            ]
            role_runtime = {
                **copy.deepcopy(baseline.role_runtime),
                "design": (
                    "pa_vpa_joint_research_v1"
                    if research_joint
                    else "pa_vpa_shadow_v2"
                ),
                "same_frozen_input": True,
                "snapshot_hash": snapshot_hash,
                "pa_source_digest": hypotheses.source_digest,
                "entry_attempt_applicable": bool(hypotheses.candidate_ids),
                "official_path": official_path,
                "client_instances_isolated": bool(
                    baseline_isolated and vpa_isolated and joint_isolated
                ),
                "provider_fallback_allowed": False,
                "authority_violations": authority_violations,
                "reasoning_profiles": {
                    role: self._thinking_profile(role).to_dict()
                    for role in reasoning_roles
                },
                "provider_effective_reasoning_profiles": {
                    role: _effective_reasoning_profile(effective_calls, role)
                    for role in reasoning_roles
                },
            }
            usage = _accumulate_usage_calls(
                baseline.usage_total,
                role_replies,
            )
            outcome = RunOutcome(
                status=effective_status,
                official_path=official_path,
                requested_vpa_mode=configured_mode,
                effective_vpa_mode=mode,
                degradation_stage=degradation_stage,
                reason=degradation_reason,
            ).model_dump()
            record = baseline.model_copy(
                update={
                    "stage2_decision": executable_stage2,
                    "pa_baseline_stage2": (
                        copy.deepcopy(baseline_stage2) if research_joint else None
                    ),
                    "joint_stage2": copy.deepcopy(joint_stage2),
                    "pa_hypotheses": copy.deepcopy(hypotheses_dict),
                    "vpa_catalog": copy.deepcopy(catalog),
                    "vpa_evidence": copy.deepcopy(vpa_report_obj),
                    "joint_decision": joint_semantic,
                    "role_statuses": role_statuses,
                    "role_runtime": role_runtime,
                    "role_responses": {
                        "vpa_evidence": role_response_payload(vpa_result),
                        **(
                            {"joint_arbiter": role_response_payload(joint_result)}
                            if research_joint
                            else {}
                        ),
                    },
                    "run_outcome": outcome,
                    "usage_total": usage,
                    "call_attempts": effective_calls,
                    "validation_audit": [
                        *copy.deepcopy(baseline.validation_audit),
                        *copy.deepcopy(role_audit),
                    ],
                    "exception": None,
                }
            )
            if not self._persist_full(record, on_event):
                return record
            on_event(OrchestratorEvent.RecordSaved)
            return record

        from pa_agent.ai.prompt_assembler import VpaPromptBuildError

        try:
            vpa_messages = role_assembler.build_vpa_evidence_report(
                frame,
                stage1,
                hypotheses_dict,
                htf_context=htf_context,
            )
        except Exception as exc:  # VPA is optional after the official PA result.
            code = (
                exc.code
                if isinstance(exc, VpaPromptBuildError)
                else "vpa_prompt_build_failed"
            )
            if not isinstance(exc, VpaPromptBuildError):
                logger.exception("Unexpected VPA prompt build failure")
            role_audit.append(
                {
                    "stage": "vpa_evidence",
                    "arm": "vpa_evidence",
                    "code": code,
                    "category": "local_prompt_build",
                    "severity": "error",
                    "owner": "vpa_evidence",
                    "retry_policy": "never",
                    "message": str(exc),
                }
            )
            return finish(
                status="completed_with_warnings",
                official_path="pa_only",
                official_stage2=baseline_stage2,
                vpa_result=None,
                vpa_report_obj=None,
                joint_result=None,
                joint_envelope=None,
                degradation_stage="vpa_evidence",
                degradation_reason=f"{code}: {exc}",
            )
        vpa_result, vpa_failure = execute_role(
            role="vpa_evidence",
            client=vpa_client,
            schema=VPA_EVIDENCE_SCHEMA,
            messages=vpa_messages,
            validate=lambda raw: validate_vpa_evidence(
                raw,
                catalog=catalog,
                pa_hypotheses=hypotheses,
            ),
        )
        if cancel_token.is_set() or (vpa_failure or {}).get(
            "failure_kind"
        ) == "cancelled":
            return finish_cancelled("cancelled_during_vpa_evidence")
        if vpa_failure is not None or vpa_result is None or not vpa_result.ok:
            return finish(
                status="completed_with_warnings",
                official_path="pa_only",
                official_stage2=baseline_stage2,
                vpa_result=vpa_result,
                vpa_report_obj=None,
                joint_result=None,
                joint_envelope=None,
                degradation_stage="vpa_evidence",
                degradation_reason=(
                    str((vpa_failure or {}).get("message") or "")
                    or "VPA evidence role failed"
                ),
            )

        assert vpa_result.obj is not None
        vpa_report_obj = copy.deepcopy(vpa_result.obj["vpa_report"])
        vpa_report = VpaEvidenceReport.model_validate(vpa_report_obj)
        if mode != "soft" or not self._allow_research_joint:
            return finish(
                status="completed",
                official_path="pa_only",
                official_stage2=baseline_stage2,
                vpa_result=vpa_result,
                vpa_report_obj=vpa_report_obj,
                joint_result=None,
                joint_envelope=None,
            )

        gate_result = str(stage1.get("gate_result") or "unknown")
        if (
            gate_result != "proceed"
            or not baseline_runner.initial_stage2_messages()
            or not hypotheses.candidate_ids
        ):
            return finish(
                status="completed",
                official_path="pa_only",
                official_stage2=baseline_stage2,
                vpa_result=vpa_result,
                vpa_report_obj=vpa_report_obj,
                joint_result=None,
                joint_envelope=None,
            )

        try:
            joint_messages = role_assembler.build_joint_decision(
                baseline_stage2,
                hypotheses_dict,
                vpa_report_obj,
            )
        except Exception as exc:  # Research Joint must not invalidate the PA arm.
            logger.exception("Unexpected Joint prompt build failure")
            role_audit.append(
                {
                    "stage": "joint_arbiter",
                    "arm": "joint_arbiter",
                    "code": "joint_prompt_build_failed",
                    "category": "local_prompt_build",
                    "severity": "error",
                    "owner": "joint_arbiter",
                    "retry_policy": "never",
                    "message": str(exc),
                }
            )
            return finish(
                status="degraded_pa_only",
                official_path="pa_only",
                official_stage2=baseline_stage2,
                vpa_result=vpa_result,
                vpa_report_obj=vpa_report_obj,
                joint_result=None,
                joint_envelope=None,
                degradation_stage="joint_arbiter",
                degradation_reason=f"joint_prompt_build_failed: {exc}",
            )
        enable_next_bar = bool(
            getattr(
                getattr(self._settings, "general", None),
                "enable_next_bar_prediction",
                False,
            )
        )
        assert joint_client is not None
        joint_result, joint_failure = execute_role(
            role="joint_arbiter",
            client=joint_client,
            schema=JOINT_DECISION_SCHEMA,
            messages=joint_messages,
            validate=lambda raw: validate_joint_decision(
                raw,
                baseline_stage2=baseline_stage2,
                stage1=stage1,
                pa_hypotheses=hypotheses,
                vpa_report=vpa_report,
                catalog=catalog,
                kline_frame=frame,
                skip_next_bar=not enable_next_bar,
                validator=(
                    self._validator
                    if isinstance(self._validator, JsonValidator)
                    else None
                ),
            ),
        )
        if cancel_token.is_set() or (joint_failure or {}).get(
            "failure_kind"
        ) == "cancelled":
            return finish_cancelled(
                "cancelled_during_joint_arbiter",
                vpa_report_obj=vpa_report_obj,
            )
        if (
            joint_failure is not None
            or joint_result is None
            or not joint_result.ok
        ):
            return finish(
                status="degraded_pa_only",
                official_path="pa_only",
                official_stage2=baseline_stage2,
                vpa_result=vpa_result,
                vpa_report_obj=vpa_report_obj,
                joint_result=joint_result,
                joint_envelope=None,
                degradation_stage="joint_arbiter",
                degradation_reason=(
                    str((joint_failure or {}).get("message") or "")
                    or "Joint arbiter failed"
                ),
            )

        assert joint_result.obj is not None
        joint_envelope = copy.deepcopy(joint_result.obj)
        official_path = "joint" if mode == "soft" else "pa_only"
        official_stage2 = (
            joint_envelope["stage2_decision"]
            if official_path == "joint"
            else baseline_stage2
        )
        return finish(
            status="completed",
            official_path=official_path,
            official_stage2=official_stage2,
            vpa_result=vpa_result,
            vpa_report_obj=vpa_report_obj,
            joint_result=joint_result,
            joint_envelope=joint_envelope,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _thinking_profile(self, role: str):
        """Resolve one role without changing the prompt or decision contract."""
        provider = (
            self._settings.provider
            if self._settings is not None
            else getattr(self._client, "_settings", None)
        )
        return resolve_reasoning_profile(provider, role)

    def _thinking_params(self, role: str) -> tuple[bool, str]:
        """Return the effective thinking controls for one logical role."""
        profile = self._thinking_profile(role)
        return profile.thinking, profile.reasoning_effort

    def _stream_chat_resilient(
        self,
        messages: list[dict[str, Any]],
        *,
        stage: Literal["stage1", "stage2"],
        on_reasoning_token: Callable[[str], None] | None,
        on_content_token: Callable[[str], None] | None,
        cancel_token: CancelToken,
        thinking: bool,
        reasoning_effort: str,
        stage_label: str,
        on_attempt_error: Callable[[Exception], None] | None = None,
        snapshot_hash: str | None = None,
        cutoff_ts_open: int | None = None,
        agent_context: dict[str, Any] | None = None,
    ) -> Any:
        """Run one typed role through the selected analysis backend."""
        schema_for = getattr(self._validator, "schema_for", None)
        schema = schema_for(stage) if callable(schema_for) else None
        del stage_label  # retained in the stable orchestrator helper interface
        return self._analysis_backend.run(
            AnalysisRoleRequest(
                role=stage,
                messages=messages,
                schema=schema,
                thinking=thinking,
                reasoning_effort=reasoning_effort,
                snapshot_hash=snapshot_hash,
                cutoff_ts_open=cutoff_ts_open,
                agent_context=agent_context,
            ),
            on_reasoning_token=on_reasoning_token,
            on_content_token=on_content_token,
            cancel_token=cancel_token,
            on_attempt_error=on_attempt_error,
        ).reply

    @staticmethod
    def _provider_failure_payload(
        exc: ModelCallFailure,
        *,
        stage: str,
    ) -> dict[str, Any]:
        return {
            "type": "provider_error",
            "stage": stage,
            "failure_kind": exc.failure.kind,
            "status_code": exc.failure.status_code,
            "retryable": exc.failure.retryable,
            "message": str(exc),
            "attempts": [dict(attempt) for attempt in exc.attempts],
        }
