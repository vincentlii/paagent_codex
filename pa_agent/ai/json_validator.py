"""JSON validator for Stage 1 and Stage 2 AI outputs.

Categories:
  a — syntax error (invalid JSON)
  b — missing required field
  c — illegal value (enum violation, type mismatch, 不下单 price non-null, etc.)
  d — plain text (no JSON structure at all)
  e — provider error (quota/billing; non-retryable)
"""
from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

_NO_ORDER_REPAIR_PATHS = (
    "decision.order_type",
    "decision.order_direction",
    "decision.entry_price",
    "decision.entry_basis_bar",
    "decision.entry_basis_extreme",
    "decision.entry_rule",
    "decision.take_profit_price",
    "decision.take_profit_price_2",
    "decision.stop_loss_price",
    "decision.reasoning",
    "decision.trade_confidence",
    "decision.trade_confidence_reasoning",
    "decision.estimated_win_rate",
    "decision.estimated_win_rate_reasoning",
    "decision.key_factors",
    "decision.watch_points",
    "decision.risk_assessment",
    "decision.invalidation_condition",
    "decision_trace",
    "terminal",
)

# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class Ok:
    """Successful validation result."""
    obj: dict[str, Any]
    normalization_events: list[dict[str, Any]] = field(default_factory=list)
    warnings: list["ValidationWarning"] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationWarning:
    """A non-safety quality finding that must not trigger a model retry."""

    stage: str
    code: str
    message: str


@dataclass
class ValidationError:
    """Failed validation result."""
    category: Literal["a", "b", "c", "d", "e"]
    stage: str                          # "stage1" or "stage2"
    raw_text: str
    parse_position: str | None = None   # "line:col" if available
    missing_fields: list[str] = field(default_factory=list)
    invalid_fields: list[str] = field(default_factory=list)
    allowed_values: dict[str, list] = field(default_factory=dict)
    message: str = ""


Result = Ok | ValidationError

# ── Markdown fence stripper ───────────────────────────────────────────────────

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_TRAILING_FENCE_RE = re.compile(r"\n?```\s*$")
_LEADING_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?", re.IGNORECASE)


def _extract_outer_json_object(text: str) -> str:
    """Return the first top-level `{...}` object, ignoring trailing prose/fences."""
    start = text.find("{")
    if start < 0:
        return text.strip()

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
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
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:].strip()


def _strip_fences(text: str) -> str:
    """Remove markdown fences and isolate the JSON object payload."""
    t = text.strip()
    if not t:
        return t

    # ── Priority: find an embedded ```json ... ``` fence anywhere in text ──
    # Handles the case where the model outputs prose first, then a fenced block.
    m_embedded = _FENCE_RE.search(t)
    if m_embedded:
        t = m_embedded.group(1).strip()
        return _extract_outer_json_object(t)

    # Fully fenced ```json ... ``` starting at top
    if t.startswith("```"):
        m = _FENCE_RE.search(t)
        if m:
            t = m.group(1).strip()
        else:
            t = _LEADING_FENCE_RE.sub("", t, count=1).strip()

    # Common model mistake: raw JSON + trailing ``` only
    t = _TRAILING_FENCE_RE.sub("", t).strip()

    return _extract_outer_json_object(t)


def coalesce_model_json_text(content: str, reasoning: str | None = None) -> str:
    """Prefer content JSON; fall back to reasoning when content is empty or prose."""
    content_text = content if isinstance(content, str) else ""
    reasoning_text = reasoning if isinstance(reasoning, str) else ""
    stripped = _strip_fences(content_text)
    if stripped.startswith("{") or stripped.startswith("["):
        return content_text
    if reasoning_text:
        from_reasoning = _strip_fences(reasoning_text)
        if from_reasoning.startswith("{") or from_reasoning.startswith("["):
            logger.info("Extracting JSON from reasoning_content (content was not JSON)")
            return from_reasoning
    return content_text


def format_model_json_for_context(raw_text: str) -> str | None:
    """Extract JSON from model output and return pretty-printed text for prompts."""
    stripped = _strip_fences(raw_text or "")
    if not stripped.startswith("{"):
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    if isinstance(obj, dict):
        return json.dumps(obj, ensure_ascii=False, indent=2)
    return stripped


# ── JsonValidator ─────────────────────────────────────────────────────────────

class JsonValidator:
    """Validates raw AI text against Stage 1 or Stage 2 JSON schemas."""

    def __init__(self, validation: Any = None) -> None:
        from pa_agent.ai.prompts.schemas import STAGE1_SCHEMA, STAGE2_SCHEMA
        from pa_agent.config.settings import ValidationSettings

        if validation is None:
            self._validation = ValidationSettings()
        elif hasattr(validation, "validation"):
            self._validation = validation.validation
        else:
            self._validation = validation

        self._schemas = {
            "stage1": copy.deepcopy(STAGE1_SCHEMA),
            "stage2": copy.deepcopy(STAGE2_SCHEMA),
        }

    def schema_for(
        self,
        stage: Literal["stage1", "stage2"],
    ) -> dict[str, Any]:
        """Return an isolated schema for provider capability negotiation."""
        return copy.deepcopy(self._schemas[stage])

    def normalize_parsed(
        self,
        stage: Literal["stage1", "stage2"],
        obj: dict[str, Any],
        *,
        kline_frame: Any = None,
        incremental_new_bar_count: int = 0,
        incremental_previous_stage1: dict[str, Any] | None = None,
        skip_next_bar: bool = False,
    ) -> dict[str, Any]:
        """Apply the same post-parse normalization as :meth:`validate`."""
        if stage == "stage1":
            from pa_agent.ai.stage1_normalizer import normalize_stage1

            return normalize_stage1(
                obj,
                kline_frame=kline_frame,
                incremental_new_bar_count=int(incremental_new_bar_count or 0),
                incremental_previous_stage1=incremental_previous_stage1
                if incremental_new_bar_count > 0
                else None,
            )
        from pa_agent.ai.stage2_normalizer import normalize_stage2

        return normalize_stage2(
            obj,
            kline_frame=kline_frame,
            skip_next_bar=skip_next_bar,
        )

    def validate(
        self,
        stage: Literal["stage1", "stage2"],
        raw_text: str,
        *,
        kline_frame: Any = None,
        stage1_json: dict[str, Any] | None = None,
        incremental_new_bar_count: int = 0,
        incremental_previous_stage1: dict[str, Any] | None = None,
        skip_next_bar: bool = False,
        htf_context: dict[str, Any] | None = None,
    ) -> Result:
        """Validate *raw_text* against the schema for *stage*.

        Returns Ok(obj) on success, ValidationError on any failure.
        """
        schema = self._schemas[stage]

        # ── Category d / e: plain text (no JSON at all) ───────────────────────
        stripped = _strip_fences(raw_text)
        if not stripped.startswith("{") and not stripped.startswith("["):
            from pa_agent.ai.provider_errors import (
                PROVIDER_QUOTA_USER_MESSAGE,
                is_provider_quota_exhausted,
            )

            if is_provider_quota_exhausted(stripped):
                return ValidationError(
                    category="e",
                    stage=stage,
                    raw_text=raw_text,
                    message=PROVIDER_QUOTA_USER_MESSAGE,
                    invalid_fields=["provider:quota_exhausted"],
                )
            return ValidationError(
                category="d",
                stage=stage,
                raw_text=raw_text,
                message="Response is plain text, not JSON",
            )

        # ── Category a: syntax error ──────────────────────────────────────────
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            pos = f"{exc.lineno}:{exc.colno}"
            return ValidationError(
                category="a",
                stage=stage,
                raw_text=raw_text,
                parse_position=pos,
                message=f"JSON syntax error at {pos}: {exc.msg}",
            )

        if not isinstance(obj, dict):
            return ValidationError(
                category="a",
                stage=stage,
                raw_text=raw_text,
                message="Top-level JSON value is not an object",
            )

        pre_normalized_obj = copy.deepcopy(obj)
        obj = self.normalize_parsed(
            stage,
            obj,
            kline_frame=kline_frame,
            incremental_new_bar_count=incremental_new_bar_count,
            incremental_previous_stage1=incremental_previous_stage1,
            skip_next_bar=skip_next_bar,
        )
        if stage == "stage2":
            from pa_agent.ai.execution_policy import (
                apply_stage2_execution_policy,
            )

            obj = apply_stage2_execution_policy(obj)
        normalization_events = self._contract_normalization_events(
            stage,
            pre_normalized_obj,
            obj,
        )
        # ── Schema validation (b and c) ───────────────────────────────────────
        try:
            import jsonschema  # type: ignore[import]
        except ImportError:
            return ValidationError(
                category="c",
                stage=stage,
                raw_text=raw_text,
                invalid_fields=["validator_dependency:jsonschema"],
                message="Required validator dependency jsonschema is not installed",
            )

        errors = list(jsonschema.Draft7Validator(schema).iter_errors(obj))

        # Classify errors
        missing: list[str] = []
        invalid: list[str] = []
        allowed: dict[str, list] = {}
        schema_issue_keys: list[str] = []

        for err in errors:
            path = ".".join(str(p) for p in err.absolute_path) or err.schema_path[-1]
            if err.validator == "required":
                # Extract the missing property name from the message
                field = err.message.split("'")[1] if "'" in err.message else str(path)
                missing.append(field)
                schema_issue_keys.append(f"missing:{field}")
            else:
                invalid.append(str(path) or err.message[:80])
                schema_issue_keys.append(f"invalid:{path}")
                if "enum" in err.schema:
                    allowed[str(path)] = err.schema["enum"]
        missing = list(dict.fromkeys(missing))
        invalid = list(dict.fromkeys(invalid))

        if (
            stage == "stage2"
            and not skip_next_bar
            and not isinstance(obj.get("next_bar_prediction"), dict)
        ):
            missing.append("next_bar_prediction")

        # ── Explicit cross-field checks ───────────────────────────────────────
        if stage == "stage1":
            from pa_agent.ai.coherence_checks import (
                validate_incremental_stage1_coherence,
                validate_stage1_coherence,
            )
            from pa_agent.ai.decision_tree import validate_gate_result_consistency

            for msg in validate_gate_result_consistency(obj):
                invalid.append(f"gate:{msg}")
            for msg in validate_stage1_coherence(obj, kline_frame=kline_frame):
                invalid.append(f"s1:{msg}")
            from pa_agent.ai.higher_timeframe import validate_higher_timeframe_output

            for msg in validate_higher_timeframe_output(
                obj,
                htf_context,
                current_frame=kline_frame,
            ):
                invalid.append(f"s1:{msg}")
            if incremental_new_bar_count > 0:
                for msg in validate_incremental_stage1_coherence(
                    obj,
                    new_bar_count=incremental_new_bar_count,
                    previous_stage1=incremental_previous_stage1,
                ):
                    invalid.append(f"s1:{msg}")

        if stage == "stage2":
            no_order_err = self._check_no_order_invariant(obj)
            if no_order_err:
                invalid.extend(no_order_err["fields"])
                allowed.update(no_order_err["allowed"])

            breakout_err = self._check_breakout_order_basis(obj)
            if breakout_err:
                invalid.extend(breakout_err["fields"])
                allowed.update(breakout_err["allowed"])

            for msg in self._check_breakout_price_extreme(obj, kline_frame):
                invalid.append(f"breakout_price:{msg}")

            for msg in self._check_signal_chain(
                obj,
                kline_frame,
            ):
                invalid.append(f"signal_chain:{msg}")

            for msg in self._check_next_bar_prediction(obj):
                invalid.append(msg)

            for msg in self._check_next_cycle_prediction(obj):
                invalid.append(msg)

            trade_metric_errors = self._check_trade_metrics(obj)
            for msg in trade_metric_errors:
                invalid.append(f"metrics:trade_metrics {msg}")
            if trade_metric_errors:
                self._append_no_order_repair_targets(invalid, prefix="metrics")

            from pa_agent.ai.coherence_checks import validate_stage2_coherence
            from pa_agent.ai.decision_tree import validate_stage2_trace_consistency

            for msg in validate_stage2_trace_consistency(obj):
                invalid.append(f"trace:{msg}")
            stage2_coherence_errors = validate_stage2_coherence(
                obj,
                stage1_json if isinstance(stage1_json, dict) else {},
                kline_frame=kline_frame,
            )
            for msg in stage2_coherence_errors:
                invalid.append(f"s2:{msg}")
            if any(
                msg.startswith("decision.order_type:")
                for msg in stage2_coherence_errors
            ):
                self._append_no_order_repair_targets(invalid, prefix="s2")

        warnings = self._collect_quality_warnings(
            stage,
            obj,
            kline_frame=kline_frame,
            stage1_json=stage1_json,
            htf_context=htf_context,
        )
        if not errors and not missing and not invalid:
            return Ok(
                obj=obj,
                normalization_events=normalization_events,
                warnings=warnings,
            )

        # Determine category: b if only missing fields, c otherwise
        if invalid or (missing and errors and errors[0].validator not in ("required",)):
            category: Literal["b", "c"] = "c"
        elif missing:
            category = "b"
        else:
            category = "c"

        first_message = errors[0].message[:120] if errors else (
            invalid[0] if invalid else (
                f"missing field {missing[0]}" if missing else "custom validation failed"
            )
        )
        schema_error_count = len(dict.fromkeys(schema_issue_keys))
        message = (
            f"{schema_error_count} schema error(s): {first_message}"
            if schema_error_count
            else f"validation error: {first_message}"
        )
        return ValidationError(
            category=category,
            stage=stage,
            raw_text=raw_text,
            missing_fields=missing,
            invalid_fields=invalid,
            allowed_values=allowed,
            message=message,
        )

    def _collect_quality_warnings(
        self,
        stage: Literal["stage1", "stage2"],
        obj: dict[str, Any],
        *,
        kline_frame: Any,
        stage1_json: dict[str, Any] | None,
        htf_context: dict[str, Any] | None,
    ) -> list[ValidationWarning]:
        messages: list[tuple[str, str]] = []
        if stage == "stage1":
            from pa_agent.ai.coherence_checks import collect_stage1_quality_warnings
            from pa_agent.ai.higher_timeframe import (
                collect_higher_timeframe_warnings,
            )

            messages.extend(
                ("stage1_quality", message)
                for message in collect_stage1_quality_warnings(
                    obj,
                    kline_frame=kline_frame,
                )
            )
            messages.extend(
                ("htf_citation", message)
                for message in collect_higher_timeframe_warnings(obj, htf_context)
            )
        else:
            from pa_agent.ai.coherence_checks import collect_stage2_quality_warnings

            messages.extend(
                ("stage2_quality", message)
                for message in collect_stage2_quality_warnings(
                    obj,
                    kline_frame=kline_frame,
                )
            )
        return [
            ValidationWarning(stage=stage, code=code, message=message)
            for code, message in messages
        ]

    @staticmethod
    def _append_no_order_repair_targets(
        invalid: list[str],
        *,
        prefix: str,
    ) -> None:
        for path in _NO_ORDER_REPAIR_PATHS:
            invalid.append(
                f"{prefix}:{path} must be rewritten as part of the "
                "atomic no-order repair"
            )

    @staticmethod
    def _contract_normalization_events(
        stage: str,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Audit exact syntax aliases accepted at the model boundary."""
        paths: list[str] = []
        if stage == "stage1":
            paths.extend(["support_levels", "resistance_levels"])
            before_bars = before.get("bar_by_bar_summary")
            after_bars = after.get("bar_by_bar_summary")
            if isinstance(before_bars, list) and isinstance(after_bars, list):
                for index in range(min(len(before_bars), len(after_bars))):
                    paths.append(f"bar_by_bar_summary.{index}.context_effect")
        else:
            paths.extend(
                [
                    "next_bar_prediction.direction",
                    "next_cycle_prediction.cycle",
                ]
            )

        events: list[dict[str, Any]] = []
        if stage == "stage2":
            for field in ("next_bar_prediction", "next_cycle_prediction"):
                old_prediction = before.get(field)
                new_prediction = after.get(field)
                degraded = (
                    isinstance(new_prediction, dict)
                    and new_prediction.get("unpredictable") is True
                    and (
                        not isinstance(old_prediction, dict)
                        or old_prediction.get("unpredictable") is not True
                    )
                )
                if degraded:
                    events.append(
                        {
                            "path": field,
                            "before": copy.deepcopy(old_prediction),
                            "after": copy.deepcopy(new_prediction),
                        }
                    )
            before_decision = before.get("decision")
            after_decision = after.get("decision")
            if (
                isinstance(before_decision, dict)
                and isinstance(after_decision, dict)
                and before_decision.get("order_type")
                in {"限价单", "突破单", "市价单"}
                and after_decision.get("order_type") == "不下单"
            ):
                from pa_agent.util.trade_metrics import validate_order_trade_metrics

                events.append(
                    {
                        "path": "execution_policy",
                        "before": {
                            key: copy.deepcopy(before_decision.get(key))
                            for key in (
                                "order_type",
                                "order_direction",
                                "entry_price",
                                "stop_loss_price",
                                "take_profit_price",
                                "take_profit_price_2",
                                "estimated_win_rate",
                            )
                        },
                        "after": {
                            "order_type": "不下单",
                            "terminal": copy.deepcopy(after.get("terminal")),
                        },
                        "reasons": validate_order_trade_metrics(before_decision),
                    }
                )
        for path in paths:
            old_value = JsonValidator._path_value(before, path)
            new_value = JsonValidator._path_value(after, path)
            if new_value is not None and old_value != new_value:
                events.append(
                    {
                        "path": path,
                        "before": old_value,
                        "after": new_value,
                    }
                )
        return events

    @staticmethod
    def _path_value(obj: dict[str, Any], path: str) -> Any:
        value: Any = obj
        for part in path.split("."):
            if isinstance(value, list) and part.isdigit():
                index = int(part)
                if index >= len(value):
                    return None
                value = value[index]
            elif isinstance(value, dict):
                value = value.get(part)
            else:
                return None
        return value

    @staticmethod
    def _check_no_order_invariant(obj: dict) -> dict | None:
        """Explicitly enforce the 不下单 ↔ null iron law.

        Returns a dict with 'fields' and 'allowed' if violated, else None.
        """
        decision = obj.get("decision", {})
        if not isinstance(decision, dict):
            return None

        order_type = decision.get("order_type")
        price_fields = [
            "entry_price",
            "take_profit_price",
            "take_profit_price_2",
            "stop_loss_price",
            "order_direction",
        ]

        if order_type == "不下单":
            violated = [f for f in price_fields if decision.get(f) is not None]
            if violated:
                return {
                    "fields": violated,
                    "allowed": {f: [None] for f in violated},
                }
        elif order_type in ("限价单", "突破单", "市价单"):
            violated = [f for f in price_fields if decision.get(f) is None]
            if violated:
                return {
                    "fields": violated,
                    "allowed": {
                        "entry_price": ["<finite number>"],
                        "take_profit_price": ["<finite number>"],
                        "take_profit_price_2": ["<finite number>"],
                        "stop_loss_price": ["<finite number>"],
                        "order_direction": ["做多", "做空"],
                    },
                }
        return None

    @staticmethod
    def _check_breakout_order_basis(obj: dict) -> dict | None:
        """Require breakout orders to be tied to a bar extreme, not a mid-bar price."""
        decision = obj.get("decision", {})
        if not isinstance(decision, dict) or decision.get("order_type") != "突破单":
            return None

        fields: list[str] = []
        allowed: dict[str, list] = {}
        direction = decision.get("order_direction")
        extreme = decision.get("entry_basis_extreme")

        if not decision.get("entry_basis_bar"):
            fields.append("decision.entry_basis_bar")
            allowed["decision.entry_basis_bar"] = ["K{n}"]
        if extreme not in ("high", "low"):
            fields.append("decision.entry_basis_extreme")
            allowed["decision.entry_basis_extreme"] = ["high", "low"]
        if not decision.get("entry_rule"):
            fields.append("decision.entry_rule")
            allowed["decision.entry_rule"] = [
                "做多突破单=依据K线高点上方1跳动",
                "做空突破单=依据K线低点下方1跳动",
            ]

        if direction == "做多" and extreme == "low":
            fields.append("decision.entry_basis_extreme")
            allowed["decision.entry_basis_extreme"] = ["做多突破单必须使用 high"]
        if direction == "做空" and extreme == "high":
            fields.append("decision.entry_basis_extreme")
            allowed["decision.entry_basis_extreme"] = ["做空突破单必须使用 low"]

        if fields:
            return {"fields": fields, "allowed": allowed}
        return None

    @staticmethod
    def _check_trade_metrics(
        obj: dict,
    ) -> list[str]:
        """Enforce RR and trader equation from entry/stop/target (not narrative distances)."""
        from pa_agent.util.trade_metrics import validate_order_trade_metrics

        decision = obj.get("decision", {})
        if not isinstance(decision, dict):
            return []
        return validate_order_trade_metrics(decision)

    @staticmethod
    def _check_breakout_price_extreme(obj: dict, kline_frame: Any = None) -> list[str]:
        """Numerically verify breakout entry is outside the cited bar extreme."""
        if kline_frame is None:
            return []
        decision = obj.get("decision", {})
        if not isinstance(decision, dict) or decision.get("order_type") != "突破单":
            return []

        basis = _parse_k_seq(decision.get("entry_basis_bar"))
        if basis is None:
            return []
        bar = _bar_by_seq(kline_frame, basis)
        if bar is None:
            return [f"entry_basis_bar K{basis} not found in current K-line frame"]

        try:
            entry = float(decision.get("entry_price"))
        except (TypeError, ValueError):
            return []

        direction = decision.get("order_direction")
        extreme = decision.get("entry_basis_extreme")
        if direction == "做多" and extreme == "high" and entry <= float(bar.high):
            return [
                f"做多突破单 entry_price={entry:.6g} must be above "
                f"K{basis}.high={float(bar.high):.6g}"
            ]
        if direction == "做空" and extreme == "low" and entry >= float(bar.low):
            return [
                f"做空突破单 entry_price={entry:.6g} must be below "
                f"K{basis}.low={float(bar.low):.6g}"
            ]
        return []

    @staticmethod
    def _check_next_cycle_prediction(obj: dict) -> list[str]:
        """Cross-field validation for next_cycle_prediction.

        Returns error message list; caller adds each to invalid_fields.
        """
        from pa_agent.ai.cycle_enums import CYCLE_ENUM, CYCLE_ORDER

        pred = obj.get("next_cycle_prediction")
        if pred is None:
            return []  # Missing field is backward-compatible (R5.1)
        if not isinstance(pred, dict):
            return ["next_cycle_prediction: must be an object when present"]

        errors: list[str] = []
        unpredictable = bool(pred.get("unpredictable", False))

        if unpredictable:
            if pred.get("cycle") is not None:
                errors.append("next_cycle_prediction.cycle: must be null when unpredictable=true")
            if pred.get("direction") is not None:
                errors.append("next_cycle_prediction.direction: must be null when unpredictable=true")
            if pred.get("probabilities") is not None:
                errors.append("next_cycle_prediction.probabilities: must be null when unpredictable=true")
            return errors

        # unpredictable=false path
        cycle = pred.get("cycle")
        if cycle not in CYCLE_ENUM:
            errors.append(
                f"next_cycle_prediction.cycle: {cycle!r} is not a valid cycle enum value; "
                f"expected one of {list(CYCLE_ENUM)}"
            )

        probs = pred.get("probabilities")
        if not isinstance(probs, dict):
            return errors + ["next_cycle_prediction.probabilities: must be an object when unpredictable=false"]

        for key in CYCLE_ORDER:
            value = probs.get(key)
            if not isinstance(value, int) or not (0 <= value <= 100):
                errors.append(
                    f"next_cycle_prediction.probabilities.{key}: must be int in [0, 100]"
                )
        if errors:
            return errors

        # Sum constraint [99, 101]
        total = sum(probs[k] for k in CYCLE_ORDER)
        if not (99 <= total <= 101):
            errors.append(
                f"next_cycle_prediction.probabilities: sum={total}, must satisfy 99 <= sum <= 101"
            )

        # cycle = argmax (accept any tied winner)
        max_value = max(probs[k] for k in CYCLE_ORDER)
        tied_winners = [k for k in CYCLE_ORDER if probs[k] == max_value]
        if cycle not in tied_winners:
            errors.append(
                f"next_cycle_prediction.cycle: expected one of {tied_winners} "
                f"(argmax of probabilities), got {cycle!r}"
            )

        return errors

    @staticmethod
    def _check_next_bar_prediction(obj: dict) -> list[str]:
        """Cross-field validation: sum constraint, direction=argmax, null consistency.

        Returns error message list; caller adds each to invalid_fields.
        """
        pred = obj.get("next_bar_prediction")
        if pred is None:
            return []  # Missing field is backward-compatible (R2.3, R7.3)
        if not isinstance(pred, dict):
            return ["next_bar_prediction: must be an object when present"]

        errors: list[str] = []
        unpredictable = bool(pred.get("unpredictable", False))

        if unpredictable:
            if pred.get("direction") is not None:
                errors.append("next_bar_prediction.direction: must be null when unpredictable=true")
            if pred.get("probabilities") is not None:
                errors.append("next_bar_prediction.probabilities: must be null when unpredictable=true")
            return errors

        # unpredictable=false path
        probs = pred.get("probabilities")
        if not isinstance(probs, dict):
            return ["next_bar_prediction.probabilities: must be an object when unpredictable=false"]

        for key in ("bullish", "bearish", "neutral"):
            value = probs.get(key)
            if not isinstance(value, int) or not (0 <= value <= 100):
                errors.append(f"next_bar_prediction.probabilities.{key}: must be int in [0, 100]")
        if errors:
            return errors

        # R3.2: sum in [99, 101]
        total = probs["bullish"] + probs["bearish"] + probs["neutral"]
        if not (99 <= total <= 101):
            errors.append(
                f"next_bar_prediction.probabilities: sum={total}, must satisfy 99 <= sum <= 101"
            )

        # R3.3: direction = argmax, accept any tied winner
        order = ("bullish", "bearish", "neutral")
        max_value = max(probs[k] for k in order)
        tied_winners = [k for k in order if probs[k] == max_value]
        direction = pred.get("direction")
        if direction not in tied_winners:
            errors.append(
                f"next_bar_prediction.direction: expected one of {tied_winners} "
                f"(argmax of probabilities), got {direction!r}"
            )

        return errors

    @staticmethod
    def _check_signal_chain(
        obj: dict,
        kline_frame: Any = None,
    ) -> list[str]:
        """Verify explicit bar references without judging setup quality."""
        decision = obj.get("decision", {})
        if not isinstance(decision, dict):
            return []
        if decision.get("order_type") not in ("限价单", "突破单", "市价单"):
            return []

        errors: list[str] = []
        bar_analysis = obj.get("bar_analysis")
        if not isinstance(bar_analysis, dict):
            return ["bar_analysis is required when placing an order"]

        signal_bar = bar_analysis.get("signal_bar")
        entry_bar = bar_analysis.get("entry_bar")
        if not isinstance(signal_bar, dict):
            errors.append("bar_analysis.signal_bar is required when placing an order")
        if not isinstance(entry_bar, dict):
            errors.append("bar_analysis.entry_bar is required when placing an order")
        if errors:
            return errors

        sig_seq = _parse_k_seq(signal_bar.get("bar"))
        entry_seq = _parse_k_seq(entry_bar.get("bar"))
        strength = str(entry_bar.get("strength", "") or "").strip().lower()
        freshness = str(entry_bar.get("freshness", "fresh")).strip().lower()
        pending_entry = (
            strength == "not_triggered"
            or freshness == "pending"
            or entry_bar.get("bar") is None
        )
        order_type = decision.get("order_type")
        planned_entry = pending_entry and order_type in ("限价单", "突破单")
        planned_limit = order_type == "限价单" and any(
            isinstance(item, dict)
            and str(item.get("node_id", "") or "").strip().lstrip("§") == "9.0P"
            and str(item.get("answer", "") or "").strip() == "是"
            for item in obj.get("decision_trace", [])
        )
        if planned_entry:
            if entry_bar.get("bar") is not None:
                errors.append("pending entry requires bar=null")
            if strength != "not_triggered":
                errors.append("pending entry requires strength=not_triggered")
            if freshness != "pending":
                errors.append("pending entry requires freshness=pending")
            if entry_bar.get("follow_through") is not None:
                errors.append("pending entry requires follow_through=null")
            if entry_bar.get("still_valid") is not None:
                errors.append("pending entry requires still_valid=null")
        else:
            if entry_seq is None:
                errors.append("executable order requires a concrete entry_bar.bar")
            if strength not in {"strong", "weak"}:
                errors.append("triggered entry requires strength=strong|weak")
            if freshness != "fresh":
                errors.append("new executable order requires freshness=fresh")
            if not isinstance(entry_bar.get("follow_through"), bool):
                errors.append("triggered entry requires boolean follow_through")
            if entry_bar.get("still_valid") is not True:
                errors.append("executable order requires still_valid=true")
            if order_type == "市价单" and entry_bar.get("follow_through") is not True:
                errors.append("market order requires follow_through=true")
        if sig_seq is None and not planned_entry:
            errors.append("bar_analysis.signal_bar.bar must be a K{n} reference")
        if entry_seq is None and not pending_entry:
            errors.append("bar_analysis.entry_bar.bar must be a K{n} reference")
        if pending_entry and decision.get("order_type") == "市价单":
            errors.append("market order requires a concrete entry_bar.bar")
        if sig_seq is not None and entry_seq is not None and sig_seq <= entry_seq:
            errors.append(
                "signal_bar must be older than entry_bar "
                f"(expected signal K seq > entry K seq, got K{sig_seq} and K{entry_seq})"
            )
        if kline_frame is not None:
            k1 = _bar_by_seq(kline_frame, 1)
            if planned_limit and k1 is not None:
                try:
                    entry_price = float(decision.get("entry_price"))
                    k1_close = float(k1.close)
                except (TypeError, ValueError):
                    pass
                else:
                    direction = decision.get("order_direction")
                    if direction == "做多" and entry_price >= k1_close:
                        errors.append(
                            "§9.0P planned long limit requires entry_price < "
                            f"K1.close ({entry_price:.6g} >= {k1_close:.6g})"
                        )
                    if direction == "做空" and entry_price <= k1_close:
                        errors.append(
                            "§9.0P planned short limit requires entry_price > "
                            f"K1.close ({entry_price:.6g} <= {k1_close:.6g})"
                        )

        if freshness in ("stale", "invalid"):
            errors.append("entry_bar.freshness stale/invalid cannot support a new order")
        return errors


def _parse_k_seq(value: object) -> int | None:
    if value is None:
        return None
    m = re.search(r"K\s*(\d+)", str(value), flags=re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1))


def _bar_by_seq(kline_frame: Any, seq: int) -> Any | None:
    for bar in getattr(kline_frame, "bars", ()) or ():
        if getattr(bar, "seq", None) == seq:
            return bar
    return None
