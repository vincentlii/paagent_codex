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
from pathlib import Path
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
    provider_obj: dict[str, Any] | None = None
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
    code: str = ""
    contract_issues: list[dict[str, Any]] = field(default_factory=list)


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


def _json_path(parts: list[Any]) -> str:
    return ".".join(str(part) for part in parts) or "draft"


def _json_pointer(parts: list[Any]) -> str:
    if not parts:
        return ""
    escaped = [
        str(part).replace("~", "~0").replace("/", "~1") for part in parts
    ]
    return "/" + "/".join(escaped)


_VALIDATION_FIELD_PATH_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*(?:(?:\.[A-Za-z_][A-Za-z0-9_]*)|(?:\[\d+\])|(?:\.\d+))*)"
)


def _validation_issue(field: str, *, allowed: list[Any] | None = None) -> dict[str, Any] | None:
    """Map a downstream validation message back to one editable Draft path."""
    text = str(field)
    prefix, separator, remainder = text.partition(":")
    if separator and prefix in {
        "s1",
        "s2",
        "gate",
        "trace",
        "breakout_price",
        "signal_chain",
        "metrics",
    }:
        text = remainder
    match = _VALIDATION_FIELD_PATH_RE.match(text.strip())
    if match is None:
        return None
    path = match.group(1)
    parts: list[str | int] = []
    for name, bracket_index in re.findall(r"([A-Za-z_][A-Za-z0-9_]*|\d+)|\[(\d+)\]", path):
        value = bracket_index or name
        parts.append(int(value) if value.isdigit() else value)
    normalized_path = _json_path(parts)
    return {
        "path": normalized_path,
        "pointer": _json_pointer(parts),
        "kind": "semantic_validation",
        "message": str(field),
        "allowed": list(allowed or []),
    }


def _merge_provider_issues(
    issues: list[dict[str, Any]],
    invalid_fields: list[str],
    missing_fields: list[str],
    allowed_values: dict[str, list[Any]],
    *,
    provider_obj: dict[str, Any],
    canonical_obj: dict[str, Any],
    provider_schema: dict[str, Any],
) -> list[dict[str, Any]]:
    """Combine schema and downstream errors without duplicate editable paths."""
    merged = list(issues)
    seen = {
        (
            str(issue.get("pointer", "")),
            str(issue.get("kind", "")),
            str(issue.get("message", "")),
        )
        for issue in merged
    }
    for validation_field in [*missing_fields, *invalid_fields]:
        issue = _validation_issue(
            validation_field,
            allowed=allowed_values.get(str(validation_field)),
        )
        if issue is None:
            continue
        issue = _remap_materialized_trace_issue(
            issue,
            provider_obj=provider_obj,
            canonical_obj=canonical_obj,
        )
        issue = _remap_structure_level_issue(
            issue, provider_obj=provider_obj, canonical_obj=canonical_obj,
        )
        issue = _remap_missing_draft_parent(
            issue, provider_obj=provider_obj, provider_schema=provider_schema,
        )
        key = (
            str(issue.get("pointer", "")),
            str(issue.get("kind", "")),
            str(issue.get("message", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(issue)
    return merged


def _remap_structure_level_issue(
    issue: dict[str, Any], *, provider_obj: dict[str, Any], canonical_obj: dict[str, Any],
) -> dict[str, Any]:
    """Bind a sorted canonical level to its original Draft occurrence."""
    pointer = str(issue.get("pointer", ""))
    parts = pointer.lstrip("/").split("/") if pointer else []
    if len(parts) != 2 or parts[0] not in {"support_levels", "resistance_levels"} or not parts[1].isdigit():
        return issue
    canonical = canonical_obj.get(parts[0])
    provider = provider_obj.get(parts[0])
    index = int(parts[1])
    if not isinstance(canonical, list) or not isinstance(provider, list) or index >= len(canonical):
        return issue
    value = canonical[index]
    occurrence = canonical[:index].count(value)
    matches = [i for i, item in enumerate(provider) if item == value]
    if occurrence >= len(matches):
        return issue
    original = matches[occurrence]
    return {
        **issue, "path": f"{parts[0]}.{original}", "pointer": f"/{parts[0]}/{original}",
        "message": str(issue.get("message", "")).replace(
            f"{parts[0]}[{index}]", f"{parts[0]}[{original}]",
        ),
    }


def _remap_missing_draft_parent(
    issue: dict[str, Any], *, provider_obj: dict[str, Any], provider_schema: dict[str, Any],
) -> dict[str, Any]:
    """Repair the first absent Draft container, never a canonical-only field."""
    pointer = str(issue.get("pointer", ""))
    if not pointer:
        return issue
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    schema = provider_schema
    for part in parts:
        if part in schema.get("properties", {}):
            schema = schema["properties"][part]
        elif part.isdigit() and isinstance(schema.get("items"), dict):
            schema = schema["items"]
        else:
            return issue
    value: Any = provider_obj
    for index, part in enumerate(parts):
        if isinstance(value, dict):
            if part not in value:
                target = parts[:index + 1]
                return {**issue, "path": _json_path(target), "pointer": _json_pointer(target)}
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return issue
    return issue


def _remap_materialized_trace_issue(
    issue: dict[str, Any],
    *,
    provider_obj: dict[str, Any],
    canonical_obj: dict[str, Any],
) -> dict[str, Any]:
    """Map canonical trace indexes back to the provider Draft by node identity."""
    pointer = str(issue.get("pointer", ""))
    parts = pointer.lstrip("/").split("/") if pointer else []
    if len(parts) < 2 or parts[0] not in {"gate_trace", "decision_trace"}:
        return issue
    try:
        canonical_index = int(parts[1])
    except ValueError:
        return issue
    canonical_trace = canonical_obj.get(parts[0])
    provider_trace = provider_obj.get(parts[0])
    if (
        not isinstance(canonical_trace, list)
        or not isinstance(provider_trace, list)
        or not 0 <= canonical_index < len(canonical_trace)
        or not isinstance(canonical_trace[canonical_index], dict)
    ):
        return issue
    node_id = str(canonical_trace[canonical_index].get("node_id") or "")
    provider_index = next(
        (
            index
            for index, item in enumerate(provider_trace)
            if isinstance(item, dict) and str(item.get("node_id") or "") == node_id
        ),
        None,
    )
    if provider_index is None:
        return issue
    remapped = copy.deepcopy(issue)
    canonical_trace_ref = f"{parts[0]}[{canonical_index}]"
    parts[1] = str(provider_index)
    provider_trace_ref = f"{parts[0]}[{provider_index}]"
    remapped["pointer"] = "/" + "/".join(parts)
    remapped["path"] = _json_path(
        [int(part) if part.isdigit() else part for part in parts]
    )
    remapped["message"] = str(remapped.get("message", "")).replace(
        canonical_trace_ref,
        provider_trace_ref,
    )
    return remapped


def _provider_contract_issues(errors: list[Any]) -> list[dict[str, Any]]:
    """Collapse jsonschema cascades into complete, editable contract issues."""
    issues: list[dict[str, Any]] = []
    for error in sorted(
        errors,
        key=lambda item: (list(item.absolute_path), str(item.validator), item.message),
    ):
        base = list(error.absolute_path)
        validator = str(error.validator)
        entries: list[tuple[list[Any], str]] = []
        if validator == "required":
            names = re.findall(r"'([^']+)'", error.message)
            entries = [(base + [name], "required") for name in names]
        elif validator == "additionalProperties":
            names = re.findall(r"'([^']+)'", error.message)
            entries = [(base + [name], "additional_property") for name in names]
        else:
            entries = [(base, validator)]

        for parts, kind in entries:
            allowed: list[Any] = []
            if validator == "enum":
                allowed = list(error.validator_value)
            elif validator == "const":
                allowed = [error.validator_value]
            elif validator == "type":
                value = error.validator_value
                allowed = list(value) if isinstance(value, list) else [value]
            elif kind == "additional_property":
                allowed = ["<remove>"]
            issues.append(
                {
                    "path": _json_path(parts),
                    "pointer": _json_pointer(parts),
                    "kind": kind,
                    "message": f"{_json_path(parts)}: {error.message}",
                    "allowed": allowed,
                }
            )

    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        key = (str(issue["pointer"]), str(issue["kind"]))
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(issue)

    blocking = [
        str(issue["pointer"])
        for issue in deduplicated
        if issue["kind"] in {"type", "required"}
    ]
    return [
        issue
        for issue in deduplicated
        if not any(
            parent
            and str(issue["pointer"]).startswith(parent + "/")
            and str(issue["pointer"]) != parent
            for parent in blocking
        )
    ]


# ── JsonValidator ─────────────────────────────────────────────────────────────

class JsonValidator:
    """Validates raw AI text against Stage 1 or Stage 2 JSON schemas."""

    def __init__(
        self,
        validation: Any = None,
        *,
        prompt_dir: Path | None = None,
        take_profit_policy=None,
    ) -> None:
        from pa_agent.ai.prompts.schemas import STAGE1_SCHEMA, STAGE2_SCHEMA
        from pa_agent.config.paths import PROMPT_DIR
        from pa_agent.config.settings import ValidationSettings

        if validation is None:
            self._validation = ValidationSettings()
        elif hasattr(validation, "validation"):
            self._validation = validation.validation
        else:
            self._validation = validation
        effective_prompt_dir = Path(prompt_dir) if prompt_dir is not None else PROMPT_DIR
        self._decision_tree_path = effective_prompt_dir / "\u4e8c\u5143\u51b3\u7b56.txt"
        from pa_agent.ai.take_profit_policy import FixedRiskRewardPolicy
        self.take_profit_policy = (
            FixedRiskRewardPolicy.model_validate(take_profit_policy)
            if take_profit_policy is not None else None
        )

        self._schemas = {
            "stage1": copy.deepcopy(STAGE1_SCHEMA),
            "stage2": copy.deepcopy(STAGE2_SCHEMA),
        }

    def schema_for(
        self,
        stage: Literal["stage1", "stage2"],
    ) -> dict[str, Any]:
        """Return an isolated schema for provider capability negotiation."""
        from pa_agent.ai.pa_provider_contracts import schema_for_provider_draft

        return schema_for_provider_draft(
            stage,
            decision_tree_path=self._decision_tree_path,
            take_profit_policy=self.take_profit_policy,
        )

    @property
    def decision_tree_path(self) -> Path:
        return self._decision_tree_path

    def render_provider_contract(self, stage: Literal["stage1", "stage2"], *, joint_stage2: bool = False) -> str:
        from pa_agent.ai.pa_provider_contracts import render_provider_draft_contract

        return render_provider_draft_contract(
            stage,
            decision_tree_path=self._decision_tree_path,
            joint_stage2=joint_stage2,
            take_profit_policy=self.take_profit_policy,
        )

    def provider_contract_digest(self, stage: Literal["stage1", "stage2"]) -> str:
        from pa_agent.ai.pa_provider_contracts import provider_draft_schema_digest

        return provider_draft_schema_digest(
            stage,
            decision_tree_path=self._decision_tree_path,
            take_profit_policy=self.take_profit_policy,
        )

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

    def validate_joint_stage2(
        self,
        raw_text: str,
        *,
        vpa_report: Any,
        vpa_catalog: dict[str, Any],
        **kwargs: Any,
    ) -> Result:
        """Validate the joint extension, then reuse the sole PA validation path."""
        import jsonschema
        from pa_agent.ai.pa_provider_contracts import (
            joint_stage2_reference_issues,
            schema_for_joint_stage2_draft,
        )
        from pa_agent.ai.vpa_measurements import validate_frozen_vpa_catalog

        try:
            catalog = validate_frozen_vpa_catalog(kwargs["kline_frame"], vpa_catalog)
            joint_stage2_reference_issues({}, validated_report=vpa_report, frozen_catalog=catalog)
        except (KeyError, TypeError, ValueError) as exc:
            return ValidationError(
                category="c",
                stage="stage2",
                raw_text=raw_text,
                code="joint_upstream_invalid",
                message=str(exc),
            )
        try:
            draft = json.loads(_strip_fences(raw_text))
        except json.JSONDecodeError:
            return ValidationError(
                category="a",
                stage="stage2",
                raw_text=raw_text,
                message="Joint Stage 2 response is not valid JSON",
            )
        schema = schema_for_joint_stage2_draft(decision_tree_path=self._decision_tree_path,
                                             take_profit_policy=self.take_profit_policy)
        if isinstance(draft, dict):
            from pa_agent.ai.pa_provider_contracts import normalize_provider_draft

            draft = normalize_provider_draft("stage2", draft)
        issues = _provider_contract_issues(
            list(jsonschema.Draft7Validator(schema).iter_errors(draft))
        )
        if isinstance(draft, dict):
            issues.extend(
                joint_stage2_reference_issues(
                    draft, validated_report=vpa_report, frozen_catalog=catalog
                )
            )
        if issues:
            return ValidationError(
                category="c",
                stage="stage2",
                raw_text=raw_text,
                code="provider_draft_contract",
                contract_issues=issues,
                invalid_fields=[issue["path"] for issue in issues],
                message="Joint Stage 2 contract or VPA reference mismatch",
            )
        pa_draft = copy.deepcopy(draft)
        joint_analysis = pa_draft.pop("joint_analysis")
        pa_draft["protocol_version"] = "pa_draft_v2"
        result = self.validate(
            "stage2", json.dumps(pa_draft, ensure_ascii=False), _joint_stage2=True, **kwargs
        )
        if isinstance(result, Ok):
            result.provider_obj = copy.deepcopy(draft)
            result.obj["joint_analysis"] = copy.deepcopy(joint_analysis)
            result.obj["analysis_basis"] = {
                "protocol_version": "pa_vpa_stage2_draft_v1",
                "win_rate_provenance": "llm_joint_subjective_prior",
                "calibration_id": None,
            }
        else:
            result.raw_text = raw_text
        return result

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
        analysis_fact_catalog: Any = None,
        joint_vpa_report: Any = None,
        joint_vpa_catalog: dict[str, Any] | None = None,
        _joint_stage2: bool = False,
    ) -> Result:
        """Validate *raw_text* against the schema for *stage*.

        Returns Ok(obj) on success, ValidationError on any failure.
        """
        if joint_vpa_catalog is not None:
            if stage != "stage2":
                raise ValueError("joint validation applies only to Stage 2")
            return self.validate_joint_stage2(
                raw_text, vpa_report=joint_vpa_report, vpa_catalog=joint_vpa_catalog,
                kline_frame=kline_frame, stage1_json=stage1_json,
                skip_next_bar=skip_next_bar, htf_context=htf_context,
                analysis_fact_catalog=analysis_fact_catalog,
            )
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
        is_provider_draft = obj.get("protocol_version") == "pa_draft_v2"
        from pa_agent.ai.execution_price_policy import frame_price_policy
        price_policy = frame_price_policy(kline_frame) if stage == "stage2" else None
        from pa_agent.ai.trade_execution_context import frame_trade_context
        trade_context = frame_trade_context(kline_frame) if stage == "stage2" else None
        if stage == "stage2" and not is_provider_draft and (
            trade_context is not None or "trade_execution_context" in (obj.get("execution_disposition") or {})
        ):
            return ValidationError(category="c", stage=stage, raw_text=raw_text,
                code="trade_context_provider_draft_required",
                message="Trade execution facts are Host-owned and require a frozen Provider Draft")
        if stage == "stage2" and not is_provider_draft and (
            price_policy is not None or "price_adjustment" in (obj.get("execution_disposition") or {})
        ):
            return ValidationError(category="c", stage=stage, raw_text=raw_text,
                code="execution_price_provider_draft_required",
                message="Execution price projection is Host-owned and requires a frozen Provider Draft")
        if stage == "stage2" and self.take_profit_policy is not None and not is_provider_draft:
            return ValidationError(category="c", stage=stage, raw_text=raw_text,
                code="fixed_rr_provider_draft_required",
                message="Fixed RR requires the frozen Provider Draft; canonical targets are Host-owned")
        provider_contract_issues: list[dict[str, Any]] = []
        if is_provider_draft:
            try:
                import jsonschema  # type: ignore[import]

                from pa_agent.ai.fact_catalog import build_analysis_fact_catalog
                from pa_agent.ai.pa_provider_contracts import (
                    materialize_stage1_draft,
                    materialize_stage2_draft,
                    normalize_provider_draft,
                    provider_draft_semantic_issues,
                    schema_for_provider_draft,
                )

                obj = normalize_provider_draft(stage, obj)
                draft_schema = schema_for_provider_draft(
                    stage,
                    decision_tree_path=self._decision_tree_path,
                    take_profit_policy=self.take_profit_policy,
                )
                draft_errors = list(jsonschema.Draft7Validator(draft_schema).iter_errors(obj))
                issues = _provider_contract_issues(draft_errors)
                catalog = analysis_fact_catalog or build_analysis_fact_catalog(kline_frame)
                issues.extend(
                    provider_draft_semantic_issues(
                        stage,
                        obj,
                        catalog=catalog,
                        decision_tree_path=self._decision_tree_path,
                    )
                )
                deduplicated_issues: list[dict[str, Any]] = []
                seen_issues: set[tuple[str, str, str]] = set()
                for issue in issues:
                    key = (
                        str(issue.get("pointer", "")),
                        str(issue.get("kind", "")),
                        str(issue.get("message", "")),
                    )
                    if key in seen_issues:
                        continue
                    seen_issues.add(key)
                    deduplicated_issues.append(issue)
                issues = deduplicated_issues
                if issues:
                    missing = list(
                        dict.fromkeys(
                            issue["path"]
                            for issue in issues
                            if issue["kind"] == "required"
                        )
                    )
                    invalid = list(
                        dict.fromkeys(
                            issue["path"]
                            for issue in issues
                            if issue["kind"] != "required"
                        )
                    )
                    allowed = {
                        issue["path"]: list(issue["allowed"])
                        for issue in issues
                        if issue.get("allowed")
                    }
                    provider_contract_issues = issues
                if any(issue.get("blocks_materialization") for issue in issues):
                    # Preserve provider-owned paths before canonical aggregation
                    # loses the source of a reference (including alternatives).
                    raise ValueError("Provider Draft cannot be materialized")
                obj = (
                    materialize_stage1_draft(
                        obj,
                        catalog=catalog,
                        decision_tree_path=self._decision_tree_path,
                    )
                    if stage == "stage1"
                    else materialize_stage2_draft(
                        obj,
                        stage1=stage1_json or {},
                        catalog=catalog,
                        decision_tree_path=self._decision_tree_path,
                        take_profit_policy=self.take_profit_policy,
                        execution_price_policy=price_policy,
                        trade_execution_context=trade_context,
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                if provider_contract_issues:
                    missing = list(
                        dict.fromkeys(
                            issue["path"]
                            for issue in provider_contract_issues
                            if issue["kind"] == "required"
                        )
                    )
                    invalid = list(
                        dict.fromkeys(
                            issue["path"]
                            for issue in provider_contract_issues
                            if issue["kind"] != "required"
                        )
                    )
                    allowed = {
                        issue["path"]: list(issue["allowed"])
                        for issue in provider_contract_issues
                        if issue.get("allowed")
                    }
                    return ValidationError(
                        category="c" if invalid else "b",
                        stage=stage,
                        raw_text=raw_text,
                        missing_fields=missing,
                        invalid_fields=invalid,
                        allowed_values=allowed,
                        message=(
                            "Provider draft contract failed with "
                            f"{len(provider_contract_issues)} issue(s): "
                            + "; ".join(
                                issue["message"]
                                for issue in provider_contract_issues[:8]
                            )
                        ),
                        code="provider_draft_contract",
                        contract_issues=provider_contract_issues,
                    )
                return ValidationError(
                    category="c", stage=stage, raw_text=raw_text,
                    invalid_fields=["provider_draft_materialization"],
                    message=f"Provider draft materialization failed: {exc}",
                    code="provider_draft_materialization",
                )
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
                field = _json_path([*err.absolute_path, field])
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

            from pa_agent.ai.coherence_checks import validate_stage2_coherence
            from pa_agent.ai.decision_tree import validate_stage2_trace_consistency

            for msg in validate_stage2_trace_consistency(
                obj,
                decision_tree_path=self._decision_tree_path,
            ):
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
            joint_stage2=_joint_stage2,
        )
        if not errors and not missing and not invalid and not provider_contract_issues:
            return Ok(
                obj=obj,
                provider_obj=pre_normalized_obj,
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
        if is_provider_draft and (
            provider_contract_issues or errors or missing or invalid
        ):
            provider_contract_issues = _merge_provider_issues(
                provider_contract_issues,
                invalid,
                missing,
                allowed,
                provider_obj=pre_normalized_obj,
                canonical_obj=obj,
                provider_schema=draft_schema,
            )
            provider_missing = list(
                dict.fromkeys(
                    issue["path"]
                    for issue in provider_contract_issues
                    if issue["kind"] == "required"
                )
            )
            provider_invalid = list(
                dict.fromkeys(
                    issue["path"]
                    for issue in provider_contract_issues
                    if issue["kind"] != "required"
                )
            )
            provider_allowed = {
                issue["path"]: list(issue["allowed"])
                for issue in provider_contract_issues
                if issue.get("allowed")
            }
            return ValidationError(
                category="c" if provider_invalid else "b",
                stage=stage,
                raw_text=raw_text,
                missing_fields=provider_missing,
                invalid_fields=provider_invalid,
                allowed_values=provider_allowed,
                message=(
                    "Provider draft validation failed with "
                    f"{len(provider_contract_issues)} issue(s): "
                    + "; ".join(
                        issue["message"] for issue in provider_contract_issues[:8]
                    )
                ),
                code="provider_draft_contract",
                contract_issues=provider_contract_issues,
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
        joint_stage2: bool = False,
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
                    htf_context=htf_context,
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
                    joint_stage2=joint_stage2,
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
            paths.extend(
                [
                    "support_levels",
                    "resistance_levels",
                    "bar_analysis.signal_bar.pattern",
                ]
            )
            before_signals = before.get("key_signals")
            after_signals = after.get("key_signals")
            if isinstance(before_signals, list) and isinstance(after_signals, list):
                for index in range(min(len(before_signals), len(after_signals))):
                    paths.append(f"key_signals.{index}")
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
        if stage == "stage1":
            before_trace = before.get("gate_trace")
            after_trace = after.get("gate_trace")
            if isinstance(before_trace, list) and isinstance(after_trace, list):
                before_by_id = {
                    str(item.get("node_id")): item
                    for item in before_trace
                    if isinstance(item, dict) and item.get("node_id")
                }
                after_by_id = {
                    str(item.get("node_id")): item
                    for item in after_trace
                    if isinstance(item, dict) and item.get("node_id")
                }
                for node_id in before_by_id.keys() & after_by_id.keys():
                    for field in ("answer", "branch"):
                        old_value = before_by_id[node_id].get(field)
                        new_value = after_by_id[node_id].get(field)
                        if old_value != new_value:
                            events.append(
                                {
                                    "path": f"gate_trace.{node_id}.{field}",
                                    "before": copy.deepcopy(old_value),
                                    "after": copy.deepcopy(new_value),
                                }
                            )
            if (
                isinstance(before_trace, list)
                and any(
                    isinstance(item, dict) and str(item.get("node_id")) == "1.1"
                    for item in before_trace
                )
                and isinstance(after_trace, list)
                and not any(
                    isinstance(item, dict) and str(item.get("node_id")) == "1.1"
                    for item in after_trace
                )
            ):
                events.append(
                    {
                        "path": "gate_trace.1.1",
                        "before": "redundant_model_preflight",
                        "after": "program_preflight_authoritative",
                    }
                )
            before_evidence = before.get("pa_evidence")
            after_evidence = after.get("pa_evidence")
            before_spike = (
                before_evidence.get("spike_analysis")
                if isinstance(before_evidence, dict)
                else None
            )
            after_spike = (
                after_evidence.get("spike_analysis")
                if isinstance(after_evidence, dict)
                else None
            )
            if (
                isinstance(before_spike, dict)
                and isinstance(after_spike, dict)
                and "first_pullback_bar" in before_spike
                and "first_pause_or_pullback_bar" not in before_spike
                and "first_pause_or_pullback_bar" in after_spike
            ):
                events.append(
                    {
                        "path": (
                            "pa_evidence.spike_analysis."
                            "first_pause_or_pullback_bar"
                        ),
                        "before": {
                            "first_pullback_bar": copy.deepcopy(
                                before_spike["first_pullback_bar"]
                            )
                        },
                        "after": copy.deepcopy(
                            after_spike["first_pause_or_pullback_bar"]
                        ),
                    }
                )
        if stage == "stage2":
            before_decision = before.get("decision")
            after_decision = after.get("decision")
            if (
                isinstance(before_decision, dict)
                and isinstance(after_decision, dict)
                and before_decision.get("order_type") == "\u4e0d\u4e0b\u5355"
                and before_decision.get("trade_confidence_reasoning") is None
                and after_decision.get("trade_confidence_reasoning") == ""
            ):
                events.append(
                    {
                        "path": "decision.trade_confidence_reasoning",
                        "before": None,
                        "after": "",
                    }
                )
            if (
                isinstance(before_decision, dict)
                and isinstance(after_decision, dict)
                and before_decision.get("order_type") == "不下单"
                and before_decision.get("estimated_win_rate_reasoning") == ""
                and after_decision.get("estimated_win_rate_reasoning") is None
            ):
                events.append(
                    {
                        "path": "decision.estimated_win_rate_reasoning",
                        "before": "",
                        "after": None,
                    }
                )
            before_trace = before.get("decision_trace")
            after_trace = after.get("decision_trace")
            if isinstance(before_trace, list) and isinstance(after_trace, list):
                before_ids = [
                    str(item.get("node_id"))
                    for item in before_trace
                    if isinstance(item, dict)
                ]
                after_ids = [
                    str(item.get("node_id"))
                    for item in after_trace
                    if isinstance(item, dict) and item.get("node_id") != "10.3"
                ]
                if before_ids != after_ids and sorted(before_ids) == sorted(after_ids):
                    events.append(
                        {
                            "path": "decision_trace.order",
                            "before": before_ids,
                            "after": after_ids,
                        }
                    )
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
            disposition = after.get("execution_disposition")
            if (
                isinstance(before_decision, dict)
                and isinstance(disposition, dict)
                and before_decision.get("order_type")
                in {"限价单", "突破单", "市价单"}
                and disposition.get("status") == "suppressed"
            ):
                metric_check = disposition.get("checks", {}).get("trade_metrics", {})
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
                        "after": copy.deepcopy(disposition),
                        "reasons": copy.deepcopy(metric_check.get("errors") or []),
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
            required_fields = [
                "entry_price",
                "take_profit_price",
                "stop_loss_price",
                "order_direction",
            ]
            violated = [f for f in required_fields if decision.get(f) is None]
            if violated:
                return {
                    "fields": violated,
                    "allowed": {
                        "entry_price": ["<finite number>"],
                        "take_profit_price": ["<finite number>"],
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
