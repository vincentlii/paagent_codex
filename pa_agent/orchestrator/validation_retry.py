"""Validate model output with optional continuation retry."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pa_agent.ai.json_validator import Ok, ValidationError, coalesce_model_json_text
from pa_agent.ai.retry_feedback import (
    build_retry_feedback,
    build_sanitized_retry_draft,
    parse_json_object,
)
from pa_agent.ai.retry_policy import (
    detect_immutable_field_changes,
    extract_feedback_targets,
    max_retries_for_category,
    should_retry,
)

logger = logging.getLogger(__name__)

StageName = Literal["stage1", "stage2"]
_NORMALIZE_KWARGS = {
    "kline_frame",
    "incremental_new_bar_count",
    "incremental_previous_stage1",
    "skip_next_bar",
}


@dataclass
class ValidationRetryResult:
    result: Ok | ValidationError
    canonical_messages: list[dict[str, Any]]
    audit_messages: list[dict[str, Any]]
    reply: Any
    attempts: int
    cheat_detected: bool = False
    validation_events: list[dict[str, Any]] | None = None


def append_assistant_turn(
    messages: list[dict[str, Any]],
    reply: Any,
    *,
    provider_settings: Any | None = None,
) -> list[dict[str, Any]]:
    """Append the successful assistant reply to *messages* for audit completeness."""
    content = getattr(reply, "content", None) or ""
    if not content.strip():
        return messages
    if messages and messages[-1].get("role") == "assistant":
        if (messages[-1].get("content") or "").strip() == content.strip():
            return messages
    preserve_mimo = False
    if provider_settings is not None:
        from pa_agent.ai.mimo_compat import (
            build_assistant_api_message,
            is_mimo_provider,
        )

        preserve_mimo = is_mimo_provider(
            getattr(provider_settings, "base_url", ""),
            getattr(provider_settings, "model", ""),
        )
    if preserve_mimo:
        reasoning = getattr(reply, "reasoning_content", None) or ""
        assistant_msg = build_assistant_api_message(
            content,
            reasoning_content=reasoning,
        )
    else:
        assistant_msg = {"role": "assistant", "content": content}
    return messages + [assistant_msg]


def validate_with_retry(
    *,
    stage: StageName,
    messages: list[dict[str, Any]],
    reply: Any,
    validator: Any,
    validation_settings: Any,
    validate_kwargs: dict[str, Any],
    call_api: Callable[[list[dict[str, Any]]], Any],
    provider_settings: Any | None = None,
) -> ValidationRetryResult:
    """Validate *reply*; on retryable failure append feedback and re-call API."""
    max_attempts = int(getattr(validation_settings, "retry_max", 3) or 0)
    if not getattr(validation_settings, "retry_enabled", True):
        max_attempts = 0
    if stage == "stage2" and not getattr(validation_settings, "retry_stage2", True):
        max_attempts = 0

    canonical_messages = list(messages)
    current_messages = list(messages)
    audit_messages = list(messages)
    current_reply = reply
    attempt = 0
    previous_raw: str | None = None
    previous_obj: dict[str, Any] | None = None
    previous_feedback_targets: set[str] = set()
    validation_events: list[dict[str, Any]] = []

    while True:
        content = coalesce_model_json_text(
            getattr(current_reply, "content", None) or "",
            getattr(current_reply, "reasoning_content", None) or "",
        )
        result = validator.validate(stage, content, **validate_kwargs)

        paired_vpa_context = bool(
            stage == "stage1" and validate_kwargs.get("paired_baseline_stage1") is not None
        )
        paired_vpa_stage2 = bool(
            stage == "stage2" and validate_kwargs.get("paired_baseline_stage2") is not None
        )
        full_prompt_vpa = bool(
            stage == "stage1"
            and validate_kwargs.get("vpa_evidence_catalog") is not None
            and validate_kwargs.get("paired_baseline_stage1") is None
        )
        paired_vpa_delta = paired_vpa_context or paired_vpa_stage2
        retry_cap = max_attempts
        if paired_vpa_delta:
            retry_cap = min(
                retry_cap,
                int(getattr(validation_settings, "retry_max_semantic", 1) or 1),
            )
        if isinstance(result, Ok):
            for normalization in result.normalization_events:
                validation_events.append(
                    {
                        "attempt": attempt + 1,
                        "category": "normalization",
                        "message": "unambiguous contract alias normalized",
                        "missing_fields": [],
                        "invalid_fields": [
                            f"normalized_alias:{normalization['path']}"
                        ],
                        "allowed_values": {},
                        "raw_text": content,
                        "retry_feedback": None,
                        "normalization": dict(normalization),
                    }
                )
            if attempt > 0 and previous_obj is not None and not paired_vpa_delta:
                before_norm = validator.normalize_parsed(
                    stage,
                    previous_obj,
                    **{
                        key: value
                        for key, value in validate_kwargs.items()
                        if key in _NORMALIZE_KWARGS
                    },
                )
                immutable_changes = detect_immutable_field_changes(
                    stage,
                    before_norm,
                    result.obj,
                    feedback_mentioned=previous_feedback_targets,
                )
                if immutable_changes:
                    logger.warning(
                        "%s retry changed immutable fields after attempt %d: %s",
                        stage,
                        attempt,
                        "; ".join(immutable_changes),
                    )
                    return ValidationRetryResult(
                        result=ValidationError(
                            category="c",
                            stage=stage,
                            raw_text=content,
                            message="重试修改了与反馈无关的不可变字段: "
                            + "; ".join(immutable_changes),
                            invalid_fields=[
                                f"retry_immutable:{change}"
                                for change in immutable_changes
                            ],
                        ),
                        canonical_messages=canonical_messages,
                        audit_messages=append_assistant_turn(
                            audit_messages,
                            current_reply,
                            provider_settings=provider_settings,
                        ),
                        reply=current_reply,
                        attempts=attempt + 1,
                        cheat_detected=True,
                        validation_events=validation_events,
                    )
            return ValidationRetryResult(
                result=result,
                canonical_messages=canonical_messages,
                audit_messages=append_assistant_turn(
                    audit_messages,
                    current_reply,
                    provider_settings=provider_settings,
                ),
                reply=current_reply,
                attempts=attempt + 1,
                validation_events=validation_events,
            )

        err = result
        event = {
            "attempt": attempt + 1,
            "category": err.category,
            "message": err.message,
            "missing_fields": list(err.missing_fields),
            "invalid_fields": list(err.invalid_fields),
            "allowed_values": {
                str(path): list(values)
                for path, values in err.allowed_values.items()
            },
            "raw_text": err.raw_text,
            "retry_feedback": None,
        }
        validation_events.append(event)
        if attempt >= retry_cap or not should_retry(
            err.category,
            err.invalid_fields,
            err.missing_fields,
            attempt=attempt,
            settings=validation_settings,
        ):
            return ValidationRetryResult(
                result=err,
                canonical_messages=canonical_messages,
                audit_messages=append_assistant_turn(
                    audit_messages,
                    current_reply,
                    provider_settings=provider_settings,
                ),
                reply=current_reply,
                attempts=attempt + 1,
                validation_events=validation_events,
            )

        attempt += 1
        effective_max_attempts = min(
            retry_cap,
            max_retries_for_category(err.category, validation_settings),
        )
        logger.info(
            "%s validation failed (category=%s), retry %d/%d",
            stage,
            err.category,
            attempt,
            effective_max_attempts,
        )

        previous_raw = content
        previous_obj = parse_json_object(previous_raw)
        previous_feedback_targets = extract_feedback_targets(
            err.invalid_fields,
            err.missing_fields,
        )

        feedback = build_retry_feedback(
            err,
            stage=stage,
            attempt=attempt,
            max_attempts=effective_max_attempts,
            frame=validate_kwargs.get("kline_frame"),
            previous_raw=previous_raw,
            paired_vpa_context=paired_vpa_context,
            paired_vpa_stage2=paired_vpa_stage2,
            full_prompt_vpa=full_prompt_vpa,
        )
        event["retry_feedback"] = feedback
        audit_messages = append_assistant_turn(
            audit_messages,
            current_reply,
            provider_settings=provider_settings,
        ) + [{"role": "user", "content": feedback}]
        retry_draft = build_sanitized_retry_draft(
            previous_raw,
            err.invalid_fields,
            err.missing_fields,
        )
        preserve_mimo = False
        if provider_settings is not None:
            from pa_agent.ai.mimo_compat import (
                build_assistant_api_message,
                is_mimo_provider,
            )

            preserve_mimo = is_mimo_provider(
                getattr(provider_settings, "base_url", ""),
                getattr(provider_settings, "model", ""),
            )
        if preserve_mimo:
            assistant_msg = build_assistant_api_message(
                retry_draft,
            )
        else:
            assistant_msg = {"role": "assistant", "content": retry_draft}
        current_messages = current_messages + [
            assistant_msg,
            {"role": "user", "content": feedback},
        ]
        current_reply = call_api(current_messages)
