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

from pa_agent.ai.kline_references import K_REFERENCE_RE, parse_k_bar_range_bounds
from pa_agent.ai.vpa_measurements import evidence_has_relative_volume_comparison

logger = logging.getLogger(__name__)

_VPA_UNCLASSIFIED_SESSION_RE = re.compile(
    r"\b(?:asia|asian|london|new\s*york|tokyo|sydney)\b|亚洲|伦敦|纽约|东京|悉尼",
    re.IGNORECASE,
)
_VPA_ACTOR_ATTRIBUTION_RE = re.compile(
    r"\binstitution(?:al)?\b|\bsmart\s+money\b|\bactive\s+(?:buyer|seller)s?\b|"
    r"\baggress(?:ive|or)\s+(?:buying|selling|side)\b|\bbuyer[- ]initiated\b|"
    r"\bseller[- ]initiated\b|\u673a\u6784|\u4e3b\u529b|\u806a\u660e\u94b1|"
    r"\u4e3b\u52a8\u4e70\u76d8|\u4e3b\u52a8\u5356\u76d8|\u4e70\u65b9\u4e3b\u52a8|\u5356\u65b9\u4e3b\u52a8",
    re.IGNORECASE,
)
_VPA_PROXY_CERTAINTY_RE = re.compile(
    r"\bactive\s+demand\b|\bactive\s+supply\b|\bdemand\s+participation\b|"
    r"\bsupply\s+participation\b|"
    r"\b(?:bulls?|bears?)\s+(?:fully|aggressively|actively)\s+"
    r"(?:push(?:ed|ing)?|buy(?:ing)?|sell(?:ing)?|press(?:ed|ing)?)\b|"
    r"\u9700\u6c42\u6d3b\u8dc3|\u4f9b\u7ed9\u6d3b\u8dc3|"
    r"\u4e70\u65b9\u79ef\u6781\u53c2\u4e0e|\u5356\u65b9\u79ef\u6781\u53c2\u4e0e|"
    r"(?:\u591a\u5934|\u7a7a\u5934).{0,4}?"
    r"(?:\u5168\u529b|\u4e3b\u52a8|\u79ef\u6781).{0,4}?"
    r"(?:\u63a8\u52a8|\u8fdb\u653b|\u6253\u538b|\u629b\u552e|\u4e70\u5165|\u5356\u51fa)",
    re.IGNORECASE,
)
_VPA_FORMING_BAR_RE = re.compile(
    r"\bforming\s+bar\b|\bunclosed\s+bar\b|"
    r"(?<![A-Za-z0-9_])K\s*0(?![A-Za-z0-9_.])|\u672a\u6536\u76d8|\u672a\u95ed\u5408",
    re.IGNORECASE,
)


def _vpa_bar_range_bounds(value: object) -> tuple[int, int] | None:
    """Return inclusive K-line bounds for a simple VPA observation range."""
    return parse_k_bar_range_bounds(value)

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

        self._prompt_settings: Any | None = None
        if validation is None:
            self._validation = ValidationSettings()
        elif hasattr(validation, "validation"):
            self._validation = validation.validation
            prompt = getattr(validation, "prompt", None)
            self._prompt_settings = prompt
        else:
            self._validation = validation

        self._schemas = {
            "stage1": copy.deepcopy(STAGE1_SCHEMA),
            "stage2": copy.deepcopy(STAGE2_SCHEMA),
        }
        if self._vpa_enabled():
            for stage, field_name in (
                ("stage1", "vpa_context"),
                ("stage2", "vpa_impact"),
            ):
                required = self._schemas[stage]["required"]
                if field_name not in required:
                    required.append(field_name)
                self._schemas[stage]["properties"][field_name]["type"] = "object"

    def schema_for(
        self,
        stage: Literal["stage1", "stage2"],
    ) -> dict[str, Any]:
        """Return an isolated schema for provider capability negotiation."""
        return copy.deepcopy(self._schemas[stage])

    def _vpa_enabled(self) -> bool:
        from pa_agent.config.vpa import vpa_is_enabled

        return vpa_is_enabled(self._prompt_settings)

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
                vpa_enabled=self._vpa_enabled(),
            )
        from pa_agent.ai.stage2_normalizer import normalize_stage2

        return normalize_stage2(
            obj,
            kline_frame=kline_frame,
            skip_next_bar=skip_next_bar,
            vpa_enabled=self._vpa_enabled(),
        )

    def validate(
        self,
        stage: Literal["stage1", "stage2"],
        raw_text: str,
        *,
        kline_frame: Any = None,
        stage1_json: dict[str, Any] | None = None,
        vpa_evidence_catalog: dict[str, Any] | None = None,
        incremental_new_bar_count: int = 0,
        incremental_previous_stage1: dict[str, Any] | None = None,
        skip_next_bar: bool = False,
        paired_baseline_stage1: dict[str, Any] | None = None,
        paired_baseline_stage2: dict[str, Any] | None = None,
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

        if stage == "stage1" and paired_baseline_stage1 is not None:
            unexpected = sorted(str(key) for key in obj if key != "vpa_context")
            if unexpected:
                return ValidationError(
                    category="c",
                    stage=stage,
                    raw_text=raw_text,
                    invalid_fields=[f"paired_vpa_output:{key}" for key in unexpected],
                    message="Paired VPA Stage 1 may output only vpa_context",
                )
            from pa_agent.ai.vpa_sanitizer import strip_vpa_fields

            baseline = strip_vpa_fields(copy.deepcopy(paired_baseline_stage1))
            if "vpa_context" in obj:
                baseline["vpa_context"] = copy.deepcopy(obj["vpa_context"])
            obj = baseline

        if stage == "stage2" and paired_baseline_stage2 is not None:
            allowed = {"vpa_impact", "decision", "decision_trace", "terminal"}
            unexpected = sorted(str(key) for key in obj if key not in allowed)
            if unexpected:
                return ValidationError(
                    category="c",
                    stage=stage,
                    raw_text=raw_text,
                    invalid_fields=[
                        f"paired_vpa_stage2_output:{key}" for key in unexpected
                    ],
                    message=(
                        "Paired VPA Stage 2 may output only vpa_impact and optional "
                        "decision/decision_trace/terminal overrides"
                    ),
                )
            from pa_agent.ai.vpa_sanitizer import strip_vpa_fields

            baseline = strip_vpa_fields(copy.deepcopy(paired_baseline_stage2))
            # Execution policy is applied after model validation. Historical
            # records may still carry this runtime annotation; it is never part
            # of the frozen model-output schema used by the paired VPA arm.
            baseline.pop("confidence_threshold", None)
            for key in allowed:
                if key in obj:
                    baseline[key] = copy.deepcopy(obj[key])
            obj = baseline

        pre_normalized_obj = copy.deepcopy(obj)
        obj = self.normalize_parsed(
            stage,
            obj,
            kline_frame=kline_frame,
            incremental_new_bar_count=incremental_new_bar_count,
            incremental_previous_stage1=incremental_previous_stage1,
            skip_next_bar=skip_next_bar,
        )
        normalization_events = self._contract_normalization_events(
            stage,
            pre_normalized_obj,
            obj,
        )
        if self._vpa_enabled() and stage == "stage1":
            from pa_agent.ai.vpa_sanitizer import materialize_vpa_observation_ranges

            obj = materialize_vpa_observation_ranges(obj, vpa_evidence_catalog)
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

        for err in errors:
            path = ".".join(str(p) for p in err.absolute_path) or err.schema_path[-1]
            if err.validator == "required":
                # Extract the missing property name from the message
                missing.append(err.message.split("'")[1] if "'" in err.message else str(path))
            else:
                invalid.append(str(path) or err.message[:80])
                if "enum" in err.schema:
                    allowed[str(path)] = err.schema["enum"]

        if (
            stage == "stage2"
            and not skip_next_bar
            and not isinstance(obj.get("next_bar_prediction"), dict)
        ):
            missing.append("next_bar_prediction")

        if self._vpa_enabled():
            for msg in self._check_vpa_hard_contract(
                stage,
                obj,
                stage1_json=stage1_json,
                vpa_evidence_catalog=vpa_evidence_catalog,
            ):
                invalid.append(f"vpa:{msg}")
            if stage == "stage2" and paired_baseline_stage2 is not None:
                for msg in self._check_paired_vpa_stage2_contract(
                    obj,
                    paired_baseline_stage2,
                ):
                    invalid.append(f"vpa_pair:{msg}")

        # ── Explicit cross-field checks ───────────────────────────────────────
        if stage == "stage1":
            from pa_agent.ai.decision_tree import validate_gate_result_consistency
            from pa_agent.ai.coherence_checks import (
                validate_incremental_stage1_coherence,
                validate_stage1_coherence,
            )

            for msg in validate_gate_result_consistency(obj):
                invalid.append(f"gate:{msg}")
            for msg in validate_stage1_coherence(obj, kline_frame=kline_frame):
                invalid.append(f"s1:{msg}")
            from pa_agent.ai.higher_timeframe import validate_higher_timeframe_output

            for msg in validate_higher_timeframe_output(obj, htf_context):
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

            for msg in self._check_trade_metrics(obj):
                invalid.append(f"metrics:{msg}")

            from pa_agent.ai.decision_tree import validate_stage2_trace_consistency
            from pa_agent.ai.coherence_checks import validate_stage2_coherence

            for msg in validate_stage2_trace_consistency(obj):
                invalid.append(f"trace:{msg}")
            for msg in validate_stage2_coherence(
                obj,
                stage1_json if isinstance(stage1_json, dict) else {},
                kline_frame=kline_frame,
            ):
                invalid.append(f"s2:{msg}")

        warnings = self._collect_quality_warnings(
            stage,
            obj,
            kline_frame=kline_frame,
            stage1_json=stage1_json,
            vpa_evidence_catalog=vpa_evidence_catalog,
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
        return ValidationError(
            category=category,
            stage=stage,
            raw_text=raw_text,
            missing_fields=missing,
            invalid_fields=invalid,
            allowed_values=allowed,
            message=f"{len(errors)} schema error(s): {first_message}",
        )

    def _collect_quality_warnings(
        self,
        stage: Literal["stage1", "stage2"],
        obj: dict[str, Any],
        *,
        kline_frame: Any,
        stage1_json: dict[str, Any] | None,
        vpa_evidence_catalog: dict[str, Any] | None,
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
        if self._vpa_enabled():
            messages.extend(
                ("vpa_quality", message)
                for message in self._check_vpa_quality_warnings(
                    stage,
                    obj,
                    stage1_json=stage1_json,
                    vpa_evidence_catalog=vpa_evidence_catalog,
                )
            )
        return [
            ValidationWarning(stage=stage, code=code, message=message)
            for code, message in messages
        ]

    @staticmethod
    def _contract_normalization_events(
        stage: str,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Audit exact syntax aliases accepted at the model boundary."""
        paths: list[str] = []
        if stage == "stage1":
            before_bars = before.get("bar_by_bar_summary")
            after_bars = after.get("bar_by_bar_summary")
            if isinstance(before_bars, list) and isinstance(after_bars, list):
                for index in range(min(len(before_bars), len(after_bars))):
                    paths.append(f"bar_by_bar_summary.{index}.context_effect")
            paths.append("vpa_context.next_confirmation.state")

        events: list[dict[str, Any]] = []
        for path in paths:
            old_value = JsonValidator._path_value(before, path)
            new_value = JsonValidator._path_value(after, path)
            if old_value is not None and new_value is not None and old_value != new_value:
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
    def _check_vpa_hard_contract(
        stage: str,
        obj: dict[str, Any],
        *,
        stage1_json: dict[str, Any] | None,
        vpa_evidence_catalog: dict[str, Any] | None,
    ) -> list[str]:
        """Validate program-owned VPA evidence and prevent a second decision model."""
        catalog = vpa_evidence_catalog if isinstance(vpa_evidence_catalog, dict) else {}
        evidence = {
            str(item.get("evidence_id")): item
            for item in catalog.get("evidence", [])
            if isinstance(item, dict) and item.get("evidence_id")
        }
        catalog_id = str(catalog.get("catalog_id") or "")
        data_status = str(catalog.get("data_status") or "unavailable")
        if stage == "stage1":
            context = obj.get("vpa_context")
            if not isinstance(context, dict):
                return []
            observations = [
                observation
                for observation in context.get("observations", [])
                if isinstance(observation, dict)
            ]
            referenced_ids = [
                str(evidence_id)
                for observation in observations
                for evidence_id in observation.get("evidence_ids", [])
            ]
            referenced = [evidence[value] for value in referenced_ids if value in evidence]
            sources = {str(item.get("source", "")) for item in referenced}
            relations = {
                str(context.get("background_relation") or ""),
                str(context.get("entry_timing_relation") or ""),
            }
            errors: list[str] = []
            if not catalog_id:
                errors.append("vpa_context missing program evidence catalog")
            elif context.get("catalog_id") != catalog_id:
                errors.append(
                    "vpa_context.catalog_id does not match program evidence catalog"
                )
            errors.extend(
                f"vpa_context.observations unknown evidence_id {value}"
                for value in referenced_ids
                if value not in evidence
            )
            if context.get("data_status") != data_status:
                errors.append(
                    "vpa_context.data_status does not match program evidence catalog"
                )
            if relations & {"supports", "challenges"} and not referenced_ids:
                errors.append(
                    "vpa_context supports/challenges relations require referenced evidence"
                )
            if relations & {"supports", "challenges"} and "volume" not in sources:
                errors.append(
                    "vpa_context supports/challenges relations require program volume evidence"
                )
            elif relations & {"supports", "challenges"} and not any(
                evidence_has_relative_volume_comparison(item) for item in referenced
            ):
                errors.append(
                    "vpa_context supports/challenges relations require relative-volume evidence"
                )
            if data_status == "unavailable" and relations != {"insufficient_data"}:
                errors.append(
                    "vpa_context unavailable program data requires insufficient_data "
                    "for background and timing"
                )
            if data_status != "unavailable":
                for index, observation in enumerate(observations):
                    observation_ids = [
                        str(evidence_id)
                        for evidence_id in observation.get("evidence_ids", [])
                    ]
                    if not observation_ids:
                        errors.append(
                            f"vpa_context.observations[{index}].evidence_ids requires "
                            "evidence_ids when data is available"
                        )
                        continue
                    raw_range = observation.get("bar_range")
                    bounds = _vpa_bar_range_bounds(raw_range)
                    if raw_range and bounds is None:
                        errors.append(
                            f"vpa_context.observations[{index}].bar_range is invalid: "
                            f"{raw_range}"
                        )
                        continue
                    if bounds is None:
                        continue
                    for evidence_id in observation_ids:
                        evidence_bounds = _vpa_bar_range_bounds(
                            (evidence.get(evidence_id) or {}).get("bar_range")
                        )
                        if evidence_bounds is not None and (
                            evidence_bounds[0] < bounds[0] or evidence_bounds[1] > bounds[1]
                        ):
                            errors.append(
                                f"vpa_context.observations[{index}].evidence_ids "
                                f"evidence_id {evidence_id} is outside observation "
                                f"bar_range {raw_range}"
                            )
                    interpretation = str(observation.get("interpretation") or "")
                    for match in K_REFERENCE_RE.finditer(interpretation):
                        referenced_bar = int(match.group(1))
                        if referenced_bar < bounds[0] or referenced_bar > bounds[1]:
                            errors.append(
                                f"vpa_context.observations[{index}].interpretation "
                                f"references K{referenced_bar} outside observation "
                                f"bar_range {raw_range}"
                            )
            return errors

        impact = obj.get("vpa_impact")
        if not isinstance(impact, dict):
            return []
        context = stage1_json.get("vpa_context") if isinstance(stage1_json, dict) else None
        stage1_ids = {
            str(evidence_id)
            for observation in (context or {}).get("observations", [])
            if isinstance(observation, dict)
            for evidence_id in observation.get("evidence_ids", [])
        }
        used = [str(value) for value in impact.get("evidence_used", [])]
        errors = [
            f"vpa_impact.evidence_used unknown evidence_id {value}"
            for value in used
            if value not in evidence
        ]
        errors.extend(
            f"vpa_impact.evidence_used evidence_id {value} was not referenced by stage1"
            for value in used
            if value not in stage1_ids
        )
        background_relation = impact.get("background_relation")
        timing_relation = impact.get("entry_timing_relation")
        effect = impact.get("decision_effect")
        if timing_relation in {"neutral", "insufficient_data"} and effect != "keep_brooks_baseline":
            errors.append(
                "vpa_impact.decision_effect neutral/insufficient_data timing must "
                "keep_brooks_baseline"
            )
        if effect != "keep_brooks_baseline" and not used:
            errors.append(
                "vpa_impact.evidence_used non-baseline decision_effect requires evidence_used"
            )
        if effect != "keep_brooks_baseline" and used and not any(
            evidence_has_relative_volume_comparison(evidence.get(value))
            for value in used
        ):
            errors.append(
                "vpa_impact.evidence_used non-baseline decision_effect requires "
                "relative-volume evidence"
            )
        if data_status == "unavailable" and (
            background_relation != "insufficient_data"
            or timing_relation != "insufficient_data"
            or effect != "keep_brooks_baseline"
        ):
            errors.append(
                "vpa_impact unavailable program data cannot alter Brooks baseline"
            )
        if isinstance(context, dict):
            if background_relation != context.get("background_relation"):
                errors.append(
                    "vpa_impact.background_relation changed between VPA stages"
                )
            if timing_relation != context.get("entry_timing_relation"):
                errors.append(
                    "vpa_impact.entry_timing_relation changed between VPA stages"
                )
        return errors

    @staticmethod
    def _check_vpa_contract(
        stage: str,
        obj: dict[str, Any],
        *,
        stage1_json: dict[str, Any] | None,
        vpa_evidence_catalog: dict[str, Any] | None,
    ) -> list[str]:
        """Return all VPA findings for focused rule tests and diagnostics."""
        return [
            *JsonValidator._check_vpa_hard_contract(
                stage,
                obj,
                stage1_json=stage1_json,
                vpa_evidence_catalog=vpa_evidence_catalog,
            ),
            *JsonValidator._check_vpa_quality_warnings(
                stage,
                obj,
                stage1_json=stage1_json,
                vpa_evidence_catalog=vpa_evidence_catalog,
            ),
        ]

    @staticmethod
    def _check_vpa_quality_warnings(
        stage: str,
        obj: dict[str, Any],
        *,
        stage1_json: dict[str, Any] | None,
        vpa_evidence_catalog: dict[str, Any] | None,
    ) -> list[str]:
        """Collect prose-level VPA findings that are not safe hard-reject signals."""
        catalog = (
            vpa_evidence_catalog
            if isinstance(vpa_evidence_catalog, dict)
            else {}
        )
        evidence = {
            str(item.get("evidence_id")): item
            for item in catalog.get("evidence", [])
            if isinstance(item, dict) and item.get("evidence_id")
        }
        data_status = str(catalog.get("data_status") or "unavailable")
        capabilities = catalog.get("capabilities") or {}
        warnings: list[str] = []

        from pa_agent.ai.factual_grounding import (
            validate_future_confirmation_narrative,
            validate_inference_only_narrative,
            validate_vpa_comparator_narrative,
        )

        if stage == "stage1":
            context = obj.get("vpa_context")
            if not isinstance(context, dict):
                return []
            observations = [
                item
                for item in context.get("observations", [])
                if isinstance(item, dict)
            ]
            substantive = {
                "effort_result",
                "sequence_comparison",
                "acceptance_rejection",
                "entry_timing",
            }
            if data_status != "unavailable" and not any(
                observation.get("finding_kind") in substantive
                and any(
                    evidence_has_relative_volume_comparison(
                        evidence.get(str(evidence_id))
                    )
                    for evidence_id in observation.get("evidence_ids", [])
                )
                for observation in observations
            ):
                warnings.append(
                    "available VPA context requires an independent price-volume finding"
                )

            raw_confirmation = context.get("next_confirmation")
            confirmation_trigger = (
                raw_confirmation.get("trigger")
                if isinstance(raw_confirmation, dict)
                else str(raw_confirmation or "")
            )
            narratives = [
                ("vpa_context.summary", context.get("summary")),
                *[
                    (
                        f"vpa_context.observations[{index}].interpretation",
                        item.get("interpretation"),
                    )
                    for index, item in enumerate(observations)
                ],
            ]
            for path, text in narratives:
                warnings.extend(validate_inference_only_narrative(text, path=path))
            confirmation_state = (
                raw_confirmation.get("state")
                if isinstance(raw_confirmation, dict)
                else None
            )
            if confirmation_state == "pending_closed_bar":
                warnings.extend(
                    validate_future_confirmation_narrative(
                        confirmation_trigger,
                        path="vpa_context.next_confirmation.trigger",
                    )
                )
            else:
                warnings.extend(
                    validate_inference_only_narrative(
                        confirmation_trigger,
                        path="vpa_context.next_confirmation.trigger",
                    )
                )

            seasonality = catalog.get("comparators", {}).get("seasonality", {})
            market_session = (
                str(seasonality.get("market_session") or "")
                if isinstance(seasonality, dict)
                else ""
            )
            for path, text in narratives:
                narrative = str(text or "")
                if (
                    market_session.lower() == "unclassified"
                    and _VPA_UNCLASSIFIED_SESSION_RE.search(narrative)
                ):
                    warnings.append(
                        f"{path} contains an unsupported named-session claim"
                    )
                if (
                    not capabilities.get("actor_attribution", False)
                    and _VPA_ACTOR_ATTRIBUTION_RE.search(narrative)
                ):
                    warnings.append(
                        f"{path} volume provenance cannot support actor attribution"
                    )
                if (
                    capabilities.get("interpretation_ceiling")
                    == "relative_activity_only"
                    and _VPA_PROXY_CERTAINTY_RE.search(narrative)
                ):
                    warnings.append(
                        f"{path} proxy/unknown volume supports relative activity only, "
                        "not active demand/supply"
                    )
            if _VPA_FORMING_BAR_RE.search(str(confirmation_trigger or "")):
                warnings.append(
                    "vpa_context.next_confirmation.trigger must wait for a future closed bar"
                )
            for index, observation in enumerate(observations):
                observation_ids = {
                    str(evidence_id)
                    for evidence_id in observation.get("evidence_ids", [])
                }
                warnings.extend(
                    validate_vpa_comparator_narrative(
                        observation.get("interpretation"),
                        evidence_items=(
                            item
                            for evidence_id, item in evidence.items()
                            if evidence_id in observation_ids
                        ),
                        path=f"vpa_context.observations[{index}].interpretation",
                    )
                )
            return warnings

        impact = obj.get("vpa_impact")
        if not isinstance(impact, dict):
            return []
        context = (
            stage1_json.get("vpa_context")
            if isinstance(stage1_json, dict)
            else None
        )
        stage1_ids = {
            str(evidence_id)
            for observation in (context or {}).get("observations", [])
            if isinstance(observation, dict)
            for evidence_id in observation.get("evidence_ids", [])
        }
        used = [str(value) for value in impact.get("evidence_used", [])]
        if (
            data_status != "unavailable"
            and (stage1_json or {}).get("gate_result") == "proceed"
            and stage1_ids
            and not used
        ):
            warnings.append(
                "VPA Stage 2 must cite evidence_used when the PA gate proceeds"
            )
        warnings.extend(
            validate_inference_only_narrative(
                impact.get("reason"),
                path="vpa_impact.reason",
            )
        )
        warnings.extend(
            validate_vpa_comparator_narrative(
                impact.get("reason"),
                evidence_items=(
                    evidence[value] for value in used if value in evidence
                ),
                path="vpa_impact.reason",
            )
        )
        reason = str(impact.get("reason") or "")
        if (
            not capabilities.get("actor_attribution", False)
            and _VPA_ACTOR_ATTRIBUTION_RE.search(reason)
        ):
            warnings.append(
                "vpa_impact.reason volume provenance cannot support actor attribution"
            )
        if (
            capabilities.get("interpretation_ceiling") == "relative_activity_only"
            and _VPA_PROXY_CERTAINTY_RE.search(reason)
        ):
            warnings.append(
                "vpa_impact.reason proxy/unknown volume supports relative activity only, "
                "not active demand/supply"
            )
        return warnings

    @staticmethod
    def _check_paired_vpa_stage2_contract(
        obj: dict[str, Any],
        baseline_stage2: dict[str, Any],
    ) -> list[str]:
        """Enforce frozen Brooks fields and prevent VPA-created trades."""
        decision = obj.get("decision")
        baseline_decision = baseline_stage2.get("decision")
        if not isinstance(decision, dict) or not isinstance(baseline_decision, dict):
            return ["paired Stage 2 requires both decision objects"]

        errors: list[str] = []
        for key in (
            "diagnosis_confidence",
            "diagnosis_confidence_reasoning",
            "estimated_win_rate",
            "estimated_win_rate_reasoning",
        ):
            if decision.get(key) != baseline_decision.get(key):
                errors.append(f"decision.{key} must equal frozen Brooks baseline")

        for key in (
            "diagnosis_summary",
            "bar_analysis",
            "next_bar_prediction",
            "next_cycle_prediction",
        ):
            if obj.get(key) != baseline_stage2.get(key):
                errors.append(f"{key} must equal frozen Brooks baseline")

        baseline_order = baseline_decision.get("order_type")
        current_order = decision.get("order_type")
        if baseline_order == "不下单" and current_order != "不下单":
            errors.append("decision.order_type cannot create a trade from a no-order baseline")

        impact = obj.get("vpa_impact")
        effect = impact.get("decision_effect") if isinstance(impact, dict) else None
        if effect == "keep_brooks_baseline":
            if decision != baseline_decision:
                errors.append(
                    "decision must equal baseline when "
                    "decision_effect=keep_brooks_baseline"
                )
            if obj.get("decision_trace") != baseline_stage2.get("decision_trace"):
                errors.append(
                    "decision_trace must equal baseline when "
                    "decision_effect=keep_brooks_baseline"
                )
            if obj.get("terminal") != baseline_stage2.get("terminal"):
                errors.append(
                    "terminal must equal baseline when decision_effect=keep_brooks_baseline"
                )
        else:
            effect_fields = (
                "order_type",
                "order_direction",
                "entry_price",
                "stop_loss_price",
                "take_profit_price",
                "take_profit_price_2",
                "entry_rule",
                "entry_basis_bar",
                "entry_basis_extreme",
                "trade_confidence",
                "watch_points",
                "risk_assessment",
                "invalidation_condition",
            )
            if not any(
                decision.get(key) != baseline_decision.get(key) for key in effect_fields
            ):
                errors.append(
                    "non-baseline decision_effect must be applied to existing Stage 2 fields"
                )
        if effect == "lower_confidence":
            try:
                lowered = float(decision.get("trade_confidence")) < float(
                    baseline_decision.get("trade_confidence")
                )
            except (TypeError, ValueError):
                lowered = False
            if not lowered:
                errors.append(
                    "decision_effect=lower_confidence requires a lower trade_confidence"
                )
        if effect in {"require_confirmation", "prefer_retest"}:
            timing_fields = (
                "order_type",
                "entry_price",
                "entry_rule",
                "watch_points",
                "invalidation_condition",
            )
            if not any(
                decision.get(key) != baseline_decision.get(key)
                for key in timing_fields
            ):
                errors.append(
                    f"decision_effect={effect} must change entry timing fields"
                )
        if effect == "reject_current_attempt" and not (
            baseline_order != "不下单" and current_order == "不下单"
        ):
            errors.append(
                "decision_effect=reject_current_attempt requires an executable baseline and no-order result"
            )
        return errors

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
