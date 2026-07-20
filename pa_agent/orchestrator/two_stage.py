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
import dataclasses
import hashlib
import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from pa_agent.ai.deepseek_client import DeepSeekClient
    from pa_agent.ai.json_validator import JsonValidator
    from pa_agent.ai.prompt_assembler import PromptAssembler
    from pa_agent.config.settings import Settings
    from pa_agent.records.experience_reader import ExperienceReader
    from pa_agent.records.pending_writer import PendingWriter

from pa_agent.ai.json_validator import Ok, ValidationError
from pa_agent.config.vpa import VPA_PAIR_IDENTITY_VERSION
from pa_agent.data.base import KlineFrame
from pa_agent.orchestrator.validation_retry import validate_with_retry
from pa_agent.records.schema import AnalysisRecord, RecordMeta
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


class _NoopPendingWriter:
    """Suppress child-arm persistence; the parent saves one paired record."""

    def save_full(self, record: AnalysisRecord) -> None:  # noqa: ARG002
        return None

    def save_partial(self, record: AnalysisRecord, reason: str) -> None:  # noqa: ARG002
        return None


def _vpa_pair_diff(baseline: AnalysisRecord, vpa: AnalysisRecord) -> dict[str, Any]:
    """Compare only contract fields; never trust a model's self-declared overlap."""
    if vpa.exception is not None:
        failure = vpa.exception if isinstance(vpa.exception, dict) else {}
        return {
            "stage1": {},
            "stage2": {},
            "strategy_files": {
                "baseline": list(baseline.strategy_files_used or []),
                "vpa": list(vpa.strategy_files_used or []),
            },
            "observed_effect": {
                "classification": "vpa_arm_failed",
                "comparison_valid": False,
                "failure_stage": failure.get("stage"),
                "failure_category": failure.get("category"),
                "baseline_has_executable_order": _has_executable_order(
                    (baseline.stage2_decision or {}).get("decision", {})
                ),
                "vpa_has_executable_order": None,
                "stage1_changed": None,
                "stage2_changed": None,
            },
        }
    base_s1 = baseline.stage1_diagnosis or {}
    vpa_s1 = vpa.stage1_diagnosis or {}
    base_s2 = (baseline.stage2_decision or {}).get("decision", {})
    vpa_s2 = (vpa.stage2_decision or {}).get("decision", {})
    keys = (*_PA_STAGE1_PROTECTED_KEYS, "entry_setup", "bar_analysis")
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
    stage1_diff = {
        key: {"baseline": base_s1.get(key), "vpa": vpa_s1.get(key)}
        for key in keys
        if base_s1.get(key) != vpa_s1.get(key)
    }
    stage2_diff = {
        key: {"baseline": base_s2.get(key), "vpa": vpa_s2.get(key)}
        for key in decision_keys
        if base_s2.get(key) != vpa_s2.get(key)
    }
    baseline_trade = _has_executable_order(base_s2)
    vpa_trade = _has_executable_order(vpa_s2)
    if baseline_trade and not vpa_trade:
        effect = "vpa_reduced_entry_attempt"
    elif not baseline_trade and vpa_trade:
        effect = "vpa_added_entry_attempt"
    elif baseline_trade and vpa_trade and stage2_diff:
        effect = "vpa_modified_entry_attempt"
    elif stage1_diff or stage2_diff:
        effect = "diagnosis_or_timing_changed_without_trade_rate_effect"
    else:
        effect = "keep_baseline"
    return {
        "stage1": stage1_diff,
        "stage2": stage2_diff,
        "strategy_files": {
            "baseline": list(baseline.strategy_files_used or []),
            "vpa": list(vpa.strategy_files_used or []),
        },
        "observed_effect": {
            "classification": effect,
            "comparison_valid": True,
            "baseline_has_executable_order": baseline_trade,
            "vpa_has_executable_order": vpa_trade,
            "stage1_changed": bool(stage1_diff),
            "stage2_changed": bool(stage2_diff),
        },
    }


def _vpa_authority_violations(baseline: Any, vpa: Any) -> list[str]:
    """Report fields VPA is not authorized to own, without hiding A/B drift."""
    base_s1 = getattr(baseline, "stage1_diagnosis", None) or {}
    vpa_s1 = getattr(vpa, "stage1_diagnosis", None) or {}
    violations: list[str] = []
    for key in _PA_STAGE1_PROTECTED_KEYS:
        if base_s1.get(key) != vpa_s1.get(key):
            violations.append(f"stage1.{key}_changed")
    base_bar = base_s1.get("bar_analysis") or {}
    vpa_bar = vpa_s1.get("bar_analysis") or {}
    base_signal = base_bar.get("signal_bar") or {}
    vpa_signal = vpa_bar.get("signal_bar") or {}
    bar_semantics = {
        "always_in": (base_bar.get("always_in"), vpa_bar.get("always_in")),
        "bar_type": (base_bar.get("bar_type"), vpa_bar.get("bar_type")),
        "signal_bar.bar": (base_signal.get("bar"), vpa_signal.get("bar")),
        "signal_bar.quality": (
            base_signal.get("quality"),
            vpa_signal.get("quality"),
        ),
        "signal_bar.pattern": (
            base_signal.get("pattern"),
            vpa_signal.get("pattern"),
        ),
        "entry_setup_type": (
            base_bar.get("entry_setup_type"),
            vpa_bar.get("entry_setup_type"),
        ),
        "follow_through": (
            base_bar.get("follow_through"),
            vpa_bar.get("follow_through"),
        ),
    }
    violations.extend(
        f"stage1.bar_analysis.{field}_changed"
        for field, (baseline_value, vpa_value) in bar_semantics.items()
        if baseline_value != vpa_value
    )
    if list(getattr(baseline, "strategy_files_used", None) or []) != list(
        getattr(vpa, "strategy_files_used", None) or []
    ):
        violations.append("routing.strategy_files_changed")

    base_decision = (getattr(baseline, "stage2_decision", None) or {}).get(
        "decision", {}
    )
    vpa_decision = (getattr(vpa, "stage2_decision", None) or {}).get("decision", {})
    base_trade = _has_executable_order(base_decision)
    vpa_trade = _has_executable_order(vpa_decision)
    if not base_trade and vpa_trade:
        violations.append("stage2.created_trade_from_no_order")
    if base_trade and vpa_trade:
        base_direction = str(base_decision.get("order_direction") or "").lower()
        vpa_direction = str(vpa_decision.get("order_direction") or "").lower()
        if base_direction != vpa_direction:
            violations.append("stage2.order_direction_changed")
    if base_decision.get("estimated_win_rate") != vpa_decision.get(
        "estimated_win_rate"
    ):
        violations.append("stage2.estimated_win_rate_changed")
    if base_decision.get("diagnosis_confidence") != vpa_decision.get(
        "diagnosis_confidence"
    ):
        violations.append("stage2.diagnosis_confidence_changed")
    return violations


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


def _provider_fingerprint(provider: Any) -> dict[str, Any]:
    """Record reproducibility-relevant settings without storing credentials."""
    return {
        "model": str(getattr(provider, "model", "") or ""),
        "base_url": str(getattr(provider, "base_url", "") or ""),
        "thinking": bool(getattr(provider, "thinking", False)),
        "reasoning_effort": str(getattr(provider, "reasoning_effort", "") or ""),
        "context_window": int(getattr(provider, "context_window", 0) or 0),
    }


def _apply_confidence_threshold(stage2: dict, threshold: int) -> dict:
    """Apply the configured confidence policy once, before persistence and UI."""
    out = copy.deepcopy(stage2)
    decision = out.get("decision")
    if not isinstance(decision, dict) or threshold <= 0:
        return out
    if decision.get("order_type") == "不下单":
        return out
    try:
        confidence = int(decision.get("trade_confidence"))
    except (TypeError, ValueError):
        confidence = -1
    if confidence >= threshold:
        out["confidence_threshold"] = {
            "configured": threshold,
            "trade_confidence": confidence,
            "result": "passed",
        }
        return out

    original_order_type = decision.get("order_type")
    decision["order_type"] = "不下单"
    for key in (
        "order_direction",
        "entry_price",
        "stop_loss_price",
        "take_profit_price",
        "take_profit_price_2",
        "entry_rule",
        "entry_basis_bar",
        "entry_basis_extreme",
        "estimated_win_rate",
        "estimated_win_rate_reasoning",
    ):
        decision[key] = None
    reason = f"程序置信度阈值未通过：{confidence} < {threshold}，原方案 {original_order_type} 不执行。"
    decision["reasoning"] = f"{decision.get('reasoning', '')}\n{reason}".strip()
    trace = list(out.get("decision_trace") or [])
    trace.append(
        {
            "node_id": "confidence_threshold",
            "question": "交易置信度是否达到配置门槛",
            "answer": "否",
            "reason": reason,
            "bar_range": "K1",
        }
    )
    out["decision"] = decision
    out["decision_trace"] = trace
    out["terminal"] = {
        "node_id": "confidence_threshold",
        "outcome": "wait",
        "label": "交易置信度门槛未通过",
    }
    out["confidence_threshold"] = {
        "configured": threshold,
        "trade_confidence": confidence,
        "result": "rejected",
    }
    return out


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
            "context_window": p.context_window,
        }

    # Serialize kline bars
    kline_data: list[dict] = []
    for bar in frame.bars:
        if dataclasses.is_dataclass(bar) and not isinstance(bar, type):
            kline_data.append(dataclasses.asdict(bar))
        else:
            kline_data.append(bar.__dict__)

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

    return AnalysisRecord(
        meta=meta,
        kline_data=kline_data,
        htf_text=render_higher_timeframe_context(htf_context),
        htf_context=serialize_higher_timeframe_context(htf_context),
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
    )


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
        paired_arm: str | None = None,
        paired_baseline_stage1: dict[str, Any] | None = None,
        paired_baseline_stage2_messages: list[dict[str, Any]] | None = None,
        paired_baseline_stage2: dict[str, Any] | None = None,
        paired_strategy_files: list[str] | None = None,
        vpa_evidence_catalog: dict[str, Any] | None = None,
        allow_provider_fallback: bool = True,
        defer_execution_policy: bool = False,
    ) -> None:
        self._client = client
        self._assembler = assembler
        self._router = router
        self._validator = validator
        self._pending_writer = pending_writer
        self._exp_reader = exp_reader
        self._settings = settings
        self._paired_arm = paired_arm
        self._paired_baseline_stage1 = copy.deepcopy(paired_baseline_stage1)
        self._paired_baseline_stage2_messages = copy.deepcopy(
            paired_baseline_stage2_messages
        )
        self._paired_baseline_stage2 = copy.deepcopy(paired_baseline_stage2)
        self._paired_strategy_files = (
            list(paired_strategy_files) if paired_strategy_files is not None else None
        )
        self._vpa_evidence_catalog = copy.deepcopy(vpa_evidence_catalog)
        self._allow_provider_fallback = allow_provider_fallback
        self._defer_execution_policy = defer_execution_policy
        self._initial_stage2_messages: list[dict[str, Any]] = []

    def _validation_settings(self) -> Any:
        if self._settings is not None and hasattr(self._settings, "validation"):
            return self._settings.validation
        from pa_agent.config.settings import ValidationSettings

        return ValidationSettings()

    def initial_stage2_messages(self) -> list[dict[str, Any]]:
        """Return the clean Stage-2 request before any validation retry turns."""
        return copy.deepcopy(self._initial_stage2_messages)

    def _clone_paired_client(self, provider: Any) -> tuple[Any, bool]:
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
        result = self._pending_writer.save_full(record)
        if isinstance(self._pending_writer, _NoopPendingWriter):
            return True
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
        result = self._pending_writer.save_partial(record, reason)
        if isinstance(self._pending_writer, _NoopPendingWriter):
            return True
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
        from pa_agent.config.vpa import resolve_vpa_mode

        configured_mode = resolve_vpa_mode(getattr(self._settings, "prompt", None))
        mode = configured_mode
        if configured_mode == "soft":
            logger.warning("VPA Soft is locked pending Shadow acceptance; using Shadow")
            mode = "shadow"
        if self._paired_arm is None and mode == "shadow":
            shadow_design = str(
                getattr(
                    getattr(self._settings, "prompt", None),
                    "vpa_shadow_design",
                    "full_prompt_ab",
                )
                or "full_prompt_ab"
            )
            return self._submit_vpa_paired(
                frame,
                cancel_token,
                on_event,
                mode=mode,
                configured_mode=configured_mode,
                shadow_design=shadow_design,
                on_stage1_reasoning=on_stage1_reasoning,
                on_stage1_content=on_stage1_content,
                on_stage2_reasoning=on_stage2_reasoning,
                on_stage2_content=on_stage2_content,
                on_stage_prompt=on_stage_prompt,
                on_stage2_files=on_stage2_files,
                previous_record=previous_record,
                incremental_new_bar_count=incremental_new_bar_count,
                htf_context=htf_context,
            )

        # ── Step 1: Build partial record ──────────────────────────────────────
        record = _build_empty_record(frame, self._settings, htf_context=htf_context)
        call_arm = self._paired_arm or "baseline"
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
            self._persist_partial(record, "user_cancelled", on_event)
            on_event(OrchestratorEvent.Cancelled)
            return record

        # ── Step 2.5: Preflight data gate (before Stage1Started) ─────────────
        from pa_agent.data.validation import validate_analysis_frame
        pf = validate_analysis_frame(frame)
        if not pf.ok:
            record = record.model_copy(update={
                "exception": {
                    "type": "insufficient_data",
                    "stage": "preflight",
                    "failed_check": pf.failed_check,
                    "message": pf.reason,
                }
            })
            self._persist_partial(record, "insufficient_data", on_event)
            on_event(OrchestratorEvent.InsufficientData)
            return record

        # ── Step 3: Stage 1 started ───────────────────────────────────────────
        on_event(OrchestratorEvent.Stage1Started)

        # Resolve analysis mode from settings (default: original)
        analysis_mode = "original"
        if self._settings is not None:
            analysis_mode = str(
                getattr(self._settings.general, "analysis_mode", "original") or "original"
            )

        # ── Step 4: Build Stage 1 messages ───────────────────────────────────
        if self._paired_baseline_stage1 is not None:
            messages_s1 = self._assembler.build_paired_vpa_context(
                frame,
                self._paired_baseline_stage1,
                htf_context=htf_context,
            )
        elif previous_record is not None and incremental_new_bar_count is not None:
            messages_s1 = self._assembler.build_incremental_stage1(
                frame,
                previous_record,
                incremental_new_bar_count,
                analysis_mode=analysis_mode,
                provider_settings=getattr(self._settings, "provider", None),
                htf_context=htf_context,
            )
        else:
            messages_s1 = self._assembler.build_stage1(
                frame,
                analysis_mode=analysis_mode,
                htf_context=htf_context,
            )

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

        _thinking, _effort = self._thinking_params()
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
                on_reasoning_token=_on_s1_reasoning,
                on_content_token=_on_s1_content,
                cancel_token=cancel_token,
                thinking=_thinking,
                reasoning_effort=_effort,
                stage_label="Stage 1",
                on_attempt_error=lambda exc: _append_call(
                    "stage1",
                    messages_s1,
                    status="request_failed",
                    error_type=type(exc).__name__,
                ),
            )
        except Exception as exc:
            if self._is_network_error(exc):
                logger.warning("Stage 1 network error: %s", exc)
                record = record.model_copy(
                    update={
                        "stage1_messages": messages_s1,
                        "call_attempts": copy.deepcopy(call_attempts),
                        "exception": {
                            "type": "network_error",
                            "stage": "stage1",
                            "message": str(exc),
                        },
                    }
                )
                self._persist_partial(record, "network_error", on_event)
                on_event(OrchestratorEvent.Stage1Failed)
                return record
            raise

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
            try:
                r = self._client.stream_chat(
                    msgs,
                    on_reasoning_token=_on_s1_reasoning,
                    on_content_token=_on_s1_content,
                    cancel_token=cancel_token,
                    thinking=_thinking,
                    reasoning_effort=_effort,
                )
            except Exception as exc:
                _append_call(
                    "stage1",
                    msgs,
                    status="request_failed",
                    error_type=type(exc).__name__,
                )
                raise
            if not s1_streamed_reasoning and r.reasoning_content:
                _emit_buffered_stream(r.reasoning_content, on_stage1_reasoning)
            if not s1_streamed_content and r.content:
                _emit_buffered_stream(r.content, on_stage1_content)
            s1_usage_calls.append(r)
            _append_call("stage1", msgs, reply=r)
            return r

        vr_s1 = validate_with_retry(
            stage="stage1",
            messages=messages_s1,
            reply=reply_s1,
            validator=self._validator,
            validation_settings=self._validation_settings(),
            validate_kwargs={
                "kline_frame": frame,
                "vpa_evidence_catalog": self._vpa_evidence_catalog,
                "incremental_new_bar_count": int(incremental_new_bar_count or 0),
                "incremental_previous_stage1": prev_s1,
                "paired_baseline_stage1": self._paired_baseline_stage1,
                "htf_context": htf_context,
            },
            call_api=_call_s1_retry,
            provider_settings=getattr(self._settings, "provider", None),
        )
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
            self._persist_partial(record, f"stage1_{err.category}", on_event)
            on_event(OrchestratorEvent.Stage1Failed)
            return record

        # Validation passed — extract the parsed JSON
        assert isinstance(result_s1, Ok)
        stage1_json: dict = result_s1.obj
        stage1_for_stage2 = stage1_json
        if self._paired_baseline_stage1 is not None:
            stage1_for_stage2 = copy.deepcopy(self._paired_baseline_stage1)
            vpa_context = stage1_json.get("vpa_context")
            if isinstance(vpa_context, dict):
                stage1_for_stage2["vpa_context"] = vpa_context

        # ── Step 9: Stage 1 done ──────────────────────────────────────────────
        on_event(OrchestratorEvent.Stage1Done)

        # ── Step 10: Route strategy files ─────────────────────────────────────
        if self._paired_strategy_files is not None:
            strategy_files = list(self._paired_strategy_files)
        elif callable(self._router) and not hasattr(self._router, "route"):
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
            if self._paired_arm == "vpa":
                vpa_context = stage1_for_stage2.get("vpa_context")
                vpa_context = vpa_context if isinstance(vpa_context, dict) else {}
                stage2_json["vpa_impact"] = {
                    "background_relation": vpa_context.get(
                        "background_relation", "insufficient_data"
                    ),
                    "entry_timing_relation": vpa_context.get(
                        "entry_timing_relation", "insufficient_data"
                    ),
                    "evidence_used": [],
                    "decision_effect": "keep_brooks_baseline",
                    "reason": "阶段一闸门未通过，阶段二未执行 VPA 入场影响评估。",
                }
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
        if self._paired_baseline_stage2_messages:
            messages_s2 = self._assembler.build_paired_vpa_stage2(
                self._paired_baseline_stage2_messages,
                self._paired_baseline_stage2 or {},
                frame,
                stage1_for_stage2,
            )
        else:
            messages_s2 = self._assembler.build_stage2_continuation(
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

        try:
            reply_s2 = self._stream_chat_resilient(
                messages_s2,
                on_reasoning_token=_on_s2_reasoning,
                on_content_token=_on_s2_content,
                cancel_token=cancel_token,
                thinking=_thinking,
                reasoning_effort=_effort,
                stage_label="Stage 2",
                on_attempt_error=lambda exc: _append_call(
                    "stage2",
                    messages_s2,
                    status="request_failed",
                    error_type=type(exc).__name__,
                ),
            )
        except Exception as exc:
            if self._is_network_error(exc):
                logger.warning("Stage 2 network error: %s", exc)
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
                        "exception": {
                            "type": "network_error",
                            "stage": "stage2",
                            "message": str(exc),
                        },
                    }
                )
                self._persist_partial(record, "network_error", on_event)
                on_event(OrchestratorEvent.Stage2Failed)
                return record
            raise

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
            try:
                r = self._client.stream_chat(
                    msgs,
                    on_reasoning_token=_on_s2_reasoning,
                    on_content_token=_on_s2_content,
                    cancel_token=cancel_token,
                    thinking=_thinking,
                    reasoning_effort=_effort,
                )
            except Exception as exc:
                _append_call(
                    "stage2",
                    msgs,
                    status="request_failed",
                    error_type=type(exc).__name__,
                )
                raise
            if not s2_streamed_reasoning and r.reasoning_content:
                _emit_buffered_stream(r.reasoning_content, on_stage2_reasoning)
            if not s2_streamed_content and r.content:
                _emit_buffered_stream(r.content, on_stage2_content)
            s2_usage_calls.append(r)
            _append_call("stage2", msgs, reply=r)
            return r

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
                "vpa_evidence_catalog": self._vpa_evidence_catalog,
                "paired_baseline_stage2": self._paired_baseline_stage2,
            },
            call_api=_call_s2_retry,
            provider_settings=getattr(self._settings, "provider", None),
        )
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
            }
        )

        # ── Step 22: Persist full record ──────────────────────────────────────
        if not self._persist_full(record, on_event):
            return record

        # ── Step 23: Record saved event ───────────────────────────────────────
        on_event(OrchestratorEvent.RecordSaved)

        # ── Step 24: Return ───────────────────────────────────────────────────
        return record

    def _submit_vpa_paired(
        self,
        frame: KlineFrame,
        cancel_token: CancelToken,
        on_event: Callable[[OrchestratorEvent], None],
        *,
        mode: str,
        configured_mode: str,
        shadow_design: str,
        on_stage1_reasoning: Callable[[str], None] | None,
        on_stage1_content: Callable[[str], None] | None,
        on_stage2_reasoning: Callable[[str], None] | None,
        on_stage2_content: Callable[[str], None] | None,
        on_stage_prompt: Callable[[str, str, str], None] | None,
        on_stage2_files: Callable[[list[str]], None] | None,
        previous_record: AnalysisRecord | None,
        incremental_new_bar_count: int | None,
        htf_context: dict[str, Any] | None,
    ) -> AnalysisRecord:
        """Run a frozen-snapshot baseline/VPA experiment and persist the A arm."""
        from pa_agent.ai.json_validator import JsonValidator
        from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog
        from pa_agent.data.validation import validate_analysis_frame

        assert self._settings is not None
        preflight_record = _build_empty_record(
            frame,
            self._settings,
            htf_context=htf_context,
        )
        if cancel_token.is_set():
            self._persist_partial(preflight_record, "user_cancelled", on_event)
            on_event(OrchestratorEvent.Cancelled)
            return preflight_record
        preflight = validate_analysis_frame(frame)
        if not preflight.ok:
            preflight_record = preflight_record.model_copy(
                update={
                    "exception": {
                        "type": "insufficient_data",
                        "stage": "preflight",
                        "failed_check": preflight.failed_check,
                        "message": preflight.reason,
                    }
                }
            )
            self._persist_partial(preflight_record, "insufficient_data", on_event)
            on_event(OrchestratorEvent.InsufficientData)
            return preflight_record

        baseline_settings = self._settings.model_copy(deep=True)
        baseline_settings.prompt.vpa_mode = "off"
        baseline_settings.prompt.vpa_enabled = False
        vpa_settings = self._settings.model_copy(deep=True)
        vpa_settings.prompt.vpa_mode = "shadow"
        vpa_settings.prompt.vpa_enabled = True
        catalog = build_vpa_evidence_catalog(frame)
        snapshot_hash = str(catalog["snapshot_hash"])
        full_prompt_ab = shadow_design == "full_prompt_ab"
        baseline_client, baseline_client_isolated = self._clone_paired_client(
            baseline_settings.provider
        )
        vpa_client, vpa_client_isolated = self._clone_paired_client(vpa_settings.provider)

        def relay_baseline_event(event: OrchestratorEvent) -> None:
            if event != OrchestratorEvent.RecordSaved:
                on_event(event)

        baseline_runner = TwoStageOrchestrator(
            client=baseline_client,
            assembler=self._assembler.clone_with_prompt_settings(baseline_settings.prompt),
            router=self._router,
            validator=JsonValidator(baseline_settings),
            pending_writer=_NoopPendingWriter(),
            exp_reader=self._exp_reader,
            settings=baseline_settings,
            paired_arm="baseline",
            allow_provider_fallback=False,
            defer_execution_policy=True,
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
            previous_record=None if full_prompt_ab else previous_record,
            incremental_new_bar_count=(
                None if full_prompt_ab else incremental_new_bar_count
            ),
            htf_context=htf_context,
        )
        if baseline.exception is not None or cancel_token.is_set():
            self._persist_partial(baseline, "vpa_baseline_failed", on_event)
            return baseline

        vpa_runner_kwargs: dict[str, Any] = {}
        if not full_prompt_ab:
            vpa_runner_kwargs.update(
                paired_baseline_stage1=baseline.stage1_diagnosis or {},
                paired_baseline_stage2_messages=baseline_runner.initial_stage2_messages(),
                paired_baseline_stage2=baseline.stage2_decision or {},
                paired_strategy_files=baseline.strategy_files_used,
            )
        vpa_runner = TwoStageOrchestrator(
            client=vpa_client,
            assembler=self._assembler.clone_with_prompt_settings(
                vpa_settings.prompt,
                vpa_evidence_catalog=catalog,
            ),
            router=self._router,
            validator=JsonValidator(vpa_settings),
            pending_writer=_NoopPendingWriter(),
            exp_reader=self._exp_reader,
            settings=vpa_settings,
            paired_arm="vpa",
            vpa_evidence_catalog=catalog,
            allow_provider_fallback=False,
            defer_execution_policy=True,
            **vpa_runner_kwargs,
        )
        vpa = vpa_runner.submit(
            frame,
            cancel_token,
            lambda _event: None,
            previous_record=None if full_prompt_ab else previous_record,
            incremental_new_bar_count=(
                None if full_prompt_ab else incremental_new_bar_count
            ),
            htf_context=htf_context,
        )

        vpa_stage1 = vpa.stage1_diagnosis or {}
        baseline_model_stage2 = copy.deepcopy(baseline.stage2_decision or {})
        vpa_model_stage2 = copy.deepcopy(vpa.stage2_decision or {})
        confidence_threshold = int(
            getattr(
                getattr(self._settings, "general", None),
                "decision_confidence_threshold",
                0,
            )
        )
        baseline_execution_stage2 = _apply_confidence_threshold(
            baseline_model_stage2,
            confidence_threshold,
        )
        vpa_execution_stage2 = _apply_confidence_threshold(
            vpa_model_stage2,
            confidence_threshold,
        )
        baseline_execution = baseline.model_copy(
            update={"stage2_decision": baseline_execution_stage2}
        )
        vpa_execution = vpa.model_copy(
            update={"stage2_decision": vpa_execution_stage2}
        )
        execution_comparison = _vpa_pair_diff(baseline_execution, vpa_execution)
        model_comparison = _vpa_pair_diff(baseline, vpa)
        clients_isolated = baseline_client_isolated and vpa_client_isolated
        authority_violations = _vpa_authority_violations(baseline, vpa)
        baseline_audit = [
            {**copy.deepcopy(event), "arm": "baseline"}
            for event in baseline.validation_audit
        ]
        vpa_audit = [
            {**copy.deepcopy(event), "arm": "vpa"}
            for event in vpa.validation_audit
        ]
        paired_audit = [*baseline_audit, *vpa_audit]
        paired_calls = [
            *copy.deepcopy(baseline.call_attempts),
            *copy.deepcopy(vpa.call_attempts),
        ]
        first_pass_clean = bool(baseline.call_attempts and vpa.call_attempts) and not paired_audit
        first_pass_clean = first_pass_clean and all(
            str(call.get("status") or "") == "validated" for call in paired_calls
        )
        same_frozen_input = bool(
            baseline.kline_data == vpa.kline_data
            and baseline.htf_context == vpa.htf_context
        )
        catalog_id = str(catalog["catalog_id"])
        baseline_has_vpa_catalog = any(
            catalog_id in str(message.get("content", ""))
            for message in baseline.stage1_messages
        )
        vpa_has_vpa_catalog = any(
            catalog_id in str(message.get("content", ""))
            for message in vpa.stage1_messages
        )
        intended_prompt_difference_present = bool(
            not baseline_has_vpa_catalog and vpa_has_vpa_catalog
        )
        protocol_valid = bool(
            clients_isolated
            and baseline.exception is None
            and vpa.exception is None
            and baseline.meta.run_id != vpa.meta.run_id
            and same_frozen_input
            and intended_prompt_difference_present
        )
        full_prompt_ab_eligible = bool(
            full_prompt_ab
            and protocol_valid
            and first_pass_clean
            and not authority_violations
        )
        if full_prompt_ab:
            design_name = "independent_full_prompt_counterfactual_v1"
            experiment_type = "full_prompt_ab"
            counterfactual_scope = "independent_brooks_vs_brooks_plus_vpa"
        else:
            design_name = "frozen_brooks_vpa_marginal_v4"
            experiment_type = "online_vpa_marginal_sidecar"
            counterfactual_scope = "vpa_evidence_delta_on_frozen_brooks"
        provider_fingerprint = _provider_fingerprint(self._settings.provider)
        baseline_stage1_messages_hash = _stable_hash(baseline.stage1_messages)
        vpa_stage1_messages_hash = _stable_hash(vpa.stage1_messages)
        pair_input_hash = _stable_hash(
            {
                "design": design_name,
                "counterfactual_scope": counterfactual_scope,
                "snapshot_hash": snapshot_hash,
                "catalog_id": catalog["catalog_id"],
                "provider": provider_fingerprint,
                "baseline_stage1_messages_hash": baseline_stage1_messages_hash,
                "vpa_stage1_messages_hash": vpa_stage1_messages_hash,
            }
        )
        pair_id = f"vpa-{pair_input_hash[:12]}"
        sidecar = {
            "mode": mode,
            "configured_mode": configured_mode,
            "pair_id": pair_id,
            "snapshot_hash": snapshot_hash,
            "status": "completed" if vpa.exception is None else "failed",
            "evidence_catalog": catalog,
            "stage1_context": vpa_stage1.get("vpa_context"),
            "stage2_impact": vpa_model_stage2.get("vpa_impact"),
            "comparison": execution_comparison,
            "model_comparison": model_comparison,
            "paired_execution": {
                "design": design_name,
                "experiment_type": experiment_type,
                "counterfactual_scope": counterfactual_scope,
                "protocol_valid": protocol_valid,
                "full_prompt_ab_eligible": full_prompt_ab_eligible,
                "first_pass_clean": first_pass_clean,
                "authority_violations": authority_violations,
                "official_arm": "baseline",
                "arm_sequence": ["baseline", "vpa"],
                "provider": provider_fingerprint,
                "pair_input_hash": pair_input_hash,
                "pair_identity_version": VPA_PAIR_IDENTITY_VERSION,
                "provider_fallback_allowed": False,
                "client_instances_isolated": clients_isolated,
                "same_frozen_input": same_frozen_input,
                "baseline_has_vpa_catalog": baseline_has_vpa_catalog,
                "vpa_has_vpa_catalog": vpa_has_vpa_catalog,
                "intended_prompt_difference_present": intended_prompt_difference_present,
                "attempt_run_id": baseline.meta.run_id,
                "baseline_stage1_messages_hash": baseline_stage1_messages_hash,
                "vpa_stage1_messages_hash": vpa_stage1_messages_hash,
                "baseline_stage2_messages_hash": _stable_hash(baseline.stage2_messages),
                "vpa_stage2_messages_hash": _stable_hash(vpa.stage2_messages),
                "stage2_request_messages_hash": _stable_hash(
                    baseline_runner.initial_stage2_messages()
                ),
                "stage2_baseline_prefix_reused": bool(
                    not full_prompt_ab and baseline_runner.initial_stage2_messages()
                ),
                "baseline_prompt_price_only": True,
                "incremental_context_disabled_for_protocol": full_prompt_ab,
                "execution_policy": {
                    "decision_confidence_threshold": confidence_threshold,
                    "applied_after_both_model_arms": True,
                },
            },
            "vpa_arm": {
                "stage1_messages": vpa.stage1_messages,
                "stage1_response": vpa.stage1_response,
                "stage1_diagnosis": vpa_stage1,
                "stage2_messages": vpa.stage2_messages,
                "stage2_response": vpa.stage2_response,
                "stage2_model_decision": vpa_model_stage2,
                "stage2_decision": vpa_execution_stage2,
                "strategy_files_used": vpa.strategy_files_used,
                "exception": vpa.exception,
                "usage_total": vpa.usage_total,
                "validation_audit": vpa_audit,
            },
        }
        final_stage2 = copy.deepcopy(baseline_execution_stage2)
        final_usage = {
            key: int((baseline.usage_total or {}).get(key, 0) or 0)
            + int((vpa.usage_total or {}).get(key, 0) or 0)
            for key in set((baseline.usage_total or {})) | set((vpa.usage_total or {}))
        }
        final_record = baseline.model_copy(
            update={
                "stage2_decision": final_stage2,
                "vpa_sidecar": sidecar,
                "usage_total": final_usage,
                "call_attempts": paired_calls,
                "validation_audit": paired_audit,
            }
        )
        if not self._persist_full(final_record, on_event):
            return final_record
        on_event(OrchestratorEvent.RecordSaved)
        return final_record

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _thinking_params(self) -> tuple[bool, str]:
        """Return (thinking, reasoning_effort) from settings defaults."""
        if self._settings is None:
            return True, "high"
        p = self._settings.provider
        return p.thinking, p.reasoning_effort

    def _stream_chat_resilient(
        self,
        messages: list[dict[str, Any]],
        *,
        on_reasoning_token: Callable[[str], None] | None,
        on_content_token: Callable[[str], None] | None,
        cancel_token: CancelToken,
        thinking: bool,
        reasoning_effort: str,
        stage_label: str,
        on_attempt_error: Callable[[Exception], None] | None = None,
    ) -> Any:
        """Call stream_chat; on connection error, switch to QClaw and retry once."""
        original_model = (
            self._settings.provider.model if self._settings is not None else ""
        )
        tried_qclaw = False
        tried_cursor = False
        tried_workbuddy = False
        while True:
            try:
                return self._client.stream_chat(
                    messages,
                    on_reasoning_token=on_reasoning_token,
                    on_content_token=on_content_token,
                    cancel_token=cancel_token,
                    thinking=thinking,
                    reasoning_effort=reasoning_effort,
                )
            except Exception as exc:
                if on_attempt_error is not None:
                    on_attempt_error(exc)
                if not self._is_network_error(exc):
                    raise
                if not self._allow_provider_fallback:
                    raise
                # Try WorkBuddy fallback first (if model is openclaw_wb),
                # then QClaw fallback (if model is openclaw)
                if not tried_workbuddy and self._try_workbuddy_fallback(
                    original_model=original_model
                ):
                    tried_workbuddy = True
                    logger.info(
                        "%s network error (%s); applied WorkBuddy provider — retrying",
                        stage_label,
                        exc,
                    )
                elif not tried_cursor and self._try_cursor_fallback(
                    original_model=original_model
                ):
                    tried_cursor = True
                    logger.info(
                        "%s network error (%s); applied Cursor provider — retrying",
                        stage_label,
                        exc,
                    )
                elif not tried_qclaw and self._try_qclaw_fallback(
                    original_model=original_model
                ):
                    tried_qclaw = True
                    logger.info(
                        "%s network error (%s); applied QClaw provider — retrying",
                        stage_label,
                        exc,
                    )
                else:
                    raise

    def _try_qclaw_fallback(self, *, original_model: str = "") -> bool:
        """Apply local QClaw provider (like settings Save with model=openclaw)."""
        from pa_agent.ai.qclaw_connector import (
            apply_qclaw_provider_to_settings,
            is_openclaw_model,
        )
        from pa_agent.config.paths import SETTINGS_JSON_PATH

        if not is_openclaw_model(original_model):
            return False
        if self._settings is None:
            return False

        from pa_agent.config.settings import save_settings
        from pa_agent.util.logging import update_api_key

        err = apply_qclaw_provider_to_settings(self._settings)
        if err:
            logger.warning("QClaw auto-fallback unavailable: %s", err)
            return False

        self._client.update_provider(self._settings.provider)
        try:
            save_settings(self._settings, SETTINGS_JSON_PATH)
            update_api_key(self._settings.provider.api_key)
        except Exception as save_exc:  # noqa: BLE001
            logger.warning("QClaw fallback applied but settings save failed: %s", save_exc)

        logger.info(
            "QClaw auto-fallback: model=%s base_url=%s",
            self._settings.provider.model,
            self._settings.provider.base_url,
        )
        return True

    def _try_cursor_fallback(self, *, original_model: str = "") -> bool:
        """Apply Cursor route via QClaw (like settings Save with model=openclaw_cs)."""
        from pa_agent.ai.cursor_connector import (
            apply_cursor_provider_to_settings,
            is_openclaw_cs_model,
        )
        from pa_agent.config.paths import SETTINGS_JSON_PATH

        if not is_openclaw_cs_model(original_model):
            return False
        if self._settings is None:
            return False

        from pa_agent.config.settings import save_settings
        from pa_agent.util.logging import update_api_key

        err = apply_cursor_provider_to_settings(
            self._settings,
            preferred_model=original_model,
        )
        if err:
            logger.warning("Cursor auto-fallback unavailable: %s", err)
            return False

        self._client.update_provider(self._settings.provider)
        try:
            save_settings(self._settings, SETTINGS_JSON_PATH)
            update_api_key(self._settings.provider.api_key)
        except Exception as save_exc:  # noqa: BLE001
            logger.warning("Cursor fallback applied but settings save failed: %s", save_exc)

        logger.info(
            "Cursor auto-fallback: model=%s base_url=%s",
            self._settings.provider.model,
            self._settings.provider.base_url,
        )
        return True

    def _try_workbuddy_fallback(self, *, original_model: str = "") -> bool:
        """Apply WorkBuddy provider (like settings Save with model=openclaw_wb)."""
        from pa_agent.ai.workbuddy_connector import (
            apply_workbuddy_provider_to_settings,
            is_openclaw_wb_model,
        )
        from pa_agent.config.paths import SETTINGS_JSON_PATH

        if not is_openclaw_wb_model(original_model):
            return False
        if self._settings is None:
            return False

        from pa_agent.config.settings import save_settings
        from pa_agent.util.logging import update_api_key

        err = apply_workbuddy_provider_to_settings(self._settings)
        if err:
            logger.warning("WorkBuddy auto-fallback unavailable: %s", err)
            return False

        self._client.update_provider(self._settings.provider)
        try:
            save_settings(self._settings, SETTINGS_JSON_PATH)
            update_api_key(self._settings.provider.api_key)
        except Exception as save_exc:  # noqa: BLE001
            logger.warning("WorkBuddy fallback applied but settings save failed: %s", save_exc)

        logger.info(
            "WorkBuddy auto-fallback: model=%s base_url=%s",
            self._settings.provider.model,
            self._settings.provider.base_url,
        )
        return True

    @staticmethod
    def _is_network_error(exc: Exception) -> bool:
        """Return True if *exc* is a network/timeout error (SDK, httpx, or OS reset)."""
        from pa_agent.ai.deepseek_client import CancelledError

        if isinstance(exc, CancelledError):
            return False

        try:
            import openai  # type: ignore[import]

            if isinstance(
                exc,
                (
                    openai.APITimeoutError,
                    openai.APIConnectionError,
                    openai.APIStatusError,
                ),
            ):
                return True
        except ImportError:
            pass

        try:
            import httpx  # type: ignore[import]

            if isinstance(
                exc,
                (
                    httpx.ReadError,
                    httpx.ConnectError,
                    httpx.TimeoutException,
                    httpx.RemoteProtocolError,
                ),
            ):
                return True
        except ImportError:
            pass

        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, TimeoutError)):
            return True
        if isinstance(exc, OSError) and getattr(exc, "winerror", None) in (
            10054,  # WSAECONNRESET — remote host closed connection
            10053,  # WSAECONNABORTED
            10060,  # WSAETIMEDOUT
        ):
            return True

        cause = exc.__cause__
        if cause is not None and cause is not exc:
            return TwoStageOrchestrator._is_network_error(cause)
        return False
