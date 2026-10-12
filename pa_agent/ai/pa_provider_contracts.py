"""Lean provider contracts and deterministic canonical materialization.

The model owns market interpretation and trade intent. This module only restores
protocol metadata, resolves declared evidence, and computes reproducible math.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from pa_agent.ai.decision_tree import (
    STAGE1_GATE_NODE_IDS,
    load_decision_tree,
    stage2_provider_node_ids,
)
from pa_agent.ai.fact_catalog import AnalysisFactCatalog
from pa_agent.ai.prompts.schemas import STAGE1_SCHEMA, STAGE2_SCHEMA
from pa_agent.util.trade_metrics import compute_risk_reward, passes_trader_equation

_STAGE1_TRACE_ANSWER_ALIASES: dict[str, dict[str, tuple[str, str]]] = {
    "2.2": {
        "同向": ("是", "同向"),
        "反向": ("是", "反向"),
        "冲突": ("是", "反向"),
    },
    "2.3": {
        "多头": ("是", "多头"),
        "是多头": ("是", "多头"),
        "空头": ("是", "空头"),
        "是空头": ("是", "空头"),
    },
}


def normalize_provider_draft(stage: str, draft: dict[str, Any]) -> dict[str, Any]:
    """Normalize only lossless Provider Draft syntax aliases."""
    result = copy.deepcopy(draft)
    if stage == "stage2":
        decision = result.get("decision")
        if (
            isinstance(decision, dict)
            and decision.get("order_type") == "\u4e0d\u4e0b\u5355"
            and decision.get("trade_confidence_reasoning") is None
        ):
            decision["trade_confidence_reasoning"] = ""
        return result
    if stage != "stage1":
        return result

    bar_analysis = result.get("bar_analysis")
    if isinstance(bar_analysis, dict):
        signal = bar_analysis.get("signal_bar")
        if (
            isinstance(signal, dict)
            and signal.get("bar") is None
            and signal.get("pattern") is None
        ):
            signal["pattern"] = ""

    for item in result.get("gate_trace") or []:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id") or "")
        alias = _STAGE1_TRACE_ANSWER_ALIASES.get(node_id, {}).get(
            str(item.get("answer") or "").strip()
        )
        if alias is None:
            continue
        answer, branch = alias
        item["answer"] = answer
        if not item.get("branch"):
            item["branch"] = branch
    return result


def _draft_trace_item(node_ids: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["node_id", "answer", "reason", "evidence_refs"],
        "properties": {
            "node_id": {"type": "string", "enum": list(node_ids)},
            "answer": {
                "type": "string",
                "enum": ["是", "否", "中性", "等待", "不适用"],
            },
            "reason": {"type": "string"},
            "branch": {"type": ["string", "null"]},
            "evidence_refs": {
                "type": "array",
                "items": {"type": "string", "pattern": r"^PA\."},
                "uniqueItems": True,
            },
        },
        "additionalProperties": False,
    }


def _draft_hypothesis_schema() -> dict[str, Any]:
    schema = copy.deepcopy(
        STAGE1_SCHEMA["properties"]["market_hypotheses"]["properties"]["primary"]
    )
    for key in ("hypothesis_id", "role"):
        schema["required"].remove(key)
        schema["properties"].pop(key)
    return schema


def _stage1_draft_schema() -> dict[str, Any]:
    model_fields = (
        "diagnosis_confidence",
        "diagnosis_confidence_reasoning",
        "spike_stage",
        "climax_risk",
        "market_phase",
        "transition_risk",
        "detected_patterns",
        "key_signals",
        "htf_context",
        "support_levels",
        "resistance_levels",
        "risk_warning",
        "bar_analysis",
        "bar_by_bar_summary",
    )
    properties = {
        key: copy.deepcopy(STAGE1_SCHEMA["properties"][key])
        for key in model_fields
    }
    for field, side in (("support_levels", "below"), ("resistance_levels", "above")):
        properties[field]["description"] = (
            f"At most three distinct structural prices or ranges, entirely {side} frozen K1.close. "
            "For a range, both endpoints must be on that side; a band crossing K1.close "
            "does not belong in this field. Order near-to-far; the Host may sort representation "
            "without changing prices. Return [] when no qualifying level is identified; "
            "do not clip, move or invent a price to satisfy this field contract."
        )
    hypothesis = _draft_hypothesis_schema()
    properties["protocol_version"] = {"type": "string", "const": "pa_draft_v2"}
    properties["market_hypotheses"] = {
        "type": "object",
        "required": ["primary", "alternatives"],
        "properties": {
            "primary": hypothesis,
            "alternatives": {"type": "array", "maxItems": 2, "items": hypothesis},
        },
        "additionalProperties": False,
    }
    properties["gate_trace"] = {
        "type": "array",
        "minItems": 1,
        "items": _draft_trace_item(("1.1", *STAGE1_GATE_NODE_IDS)),
    }
    properties["gate_trace"]["items"]["properties"]["branch"]["description"] = (
        "branch 是节点的分支标识，不是市场叙述。§1.2 answer=是时，直接使用已经声明的 "
        "market_hypotheses.primary.cycle_position（或实际选择的 alternatives 中的 cycle_position）"
        "原始枚举值；不要添加方向、过渡状态或其他修饰词，这些解释写入 reason。"
        "§2.3 方向分支应与 primary.direction 一致，可直接使用其原始枚举值。"
        "其他节点按对应决策树协议填写。枚举标识不受中文叙述要求影响。"
    )
    properties["pa_evidence"] = {
        "type": "object",
        "required": ["spike_analysis"],
        "properties": {
            "spike_analysis": copy.deepcopy(
                STAGE1_SCHEMA["properties"]["pa_evidence"]["properties"]["spike_analysis"]
            )
        },
        "additionalProperties": False,
    }
    properties["incremental_delta"] = copy.deepcopy(
        STAGE1_SCHEMA["properties"]["incremental_delta"]
    )
    semantic_rules = [
        copy.deepcopy(rule)
        for rule in STAGE1_SCHEMA.get("allOf", [])
        if "cycle_position" not in str(rule)
    ]
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": [
            "protocol_version",
            *model_fields,
            "market_hypotheses",
            "pa_evidence",
            "gate_trace",
        ],
        "properties": properties,
        "allOf": semantic_rules,
        "additionalProperties": False,
    }


def _stage2_draft_schema(
    decision_tree_path: Path | None = None,
) -> dict[str, Any]:
    decision = copy.deepcopy(STAGE2_SCHEMA["properties"]["decision"])
    for key in ("diagnosis_confidence", "diagnosis_confidence_reasoning"):
        decision["required"].remove(key)
        decision["properties"].pop(key)
    for rule in decision.get("allOf", []):
        order_rule = rule.get("if", {}).get("properties", {}).get("order_type", {})
        if order_rule.get("const") == "不下单":
            rule["then"]["properties"]["estimated_win_rate_reasoning"] = {
                "enum": [None, ""]
            }
    properties = {
        "protocol_version": {"type": "string", "const": "pa_draft_v2"},
        "decision_intent": {
        "type": "string", "enum": ["trade", "wait", "reject"],
        },
        "decision": decision,
        "bar_analysis": copy.deepcopy(STAGE2_SCHEMA["properties"]["bar_analysis"]),
        "decision_trace": {
            "type": "array",
            "minItems": 1,
            "description": (
                "每个 node_id 最多出现一次；reason 必须回答该编号的原决策树问题，不得借用其他编号。"
                "交易者方程 §10.3 由 Host 物化，不在模型 Draft 中输出，也不要用其他节点代替它。"
                "只记录当前实际分析路径；不适用的节点不为凑齐列表而重复填写。"
            ),
            "items": _draft_trace_item(
                stage2_provider_node_ids(decision_tree_path)
            ),
        },
        "next_bar_prediction": copy.deepcopy(
            STAGE2_SCHEMA["properties"]["next_bar_prediction"]
        ),
    }
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": [
            "protocol_version",
            "decision_intent",
            "decision",
            "bar_analysis",
            "decision_trace",
        ],
        "properties": properties,
        "additionalProperties": False,
    }


STAGE1_DRAFT_SCHEMA = _stage1_draft_schema()
STAGE2_DRAFT_SCHEMA = _stage2_draft_schema()


def schema_for_joint_stage2_draft(
    *, decision_tree_path: Path | None = None,
) -> dict[str, Any]:
    """Joint Stage 2 extends, rather than replaces, the PA decision contract.

    This schema is prepared for the new pipeline only. Legacy stage2 and the
    research joint_arbiter retain their schemas. The Host must validate VPA
    references against the accepted report before using the shared materializer.
    """
    schema = _stage2_draft_schema(decision_tree_path)
    schema["properties"]["protocol_version"] = {
        "type": "string", "const": "pa_vpa_stage2_draft_v1",
    }
    rationale_fields = (
        "support", "challenge", "selection_vs_wait", "entry_reachability",
        "target_barriers", "invalidation",
    )
    schema["properties"]["joint_analysis"] = {
        "type": "object",
        "required": [*rationale_fields, "vpa_evidence_refs"],
        "properties": {
            **{key: {"type": "string", "minLength": 1} for key in rationale_fields},
            "vpa_evidence_refs": {
                "type": "array", "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
        },
        "additionalProperties": False,
    }
    schema["required"].append("joint_analysis")
    return schema


def schema_for_provider_draft(
    stage: str,
    *,
    decision_tree_path: Path | None = None,
) -> dict[str, Any]:
    """Return an isolated provider contract for one PA stage."""
    schemas = {
        "stage1": STAGE1_DRAFT_SCHEMA,
        "stage2": (
            STAGE2_DRAFT_SCHEMA
            if decision_tree_path is None
            else _stage2_draft_schema(decision_tree_path)
        ),
    }
    try:
        return copy.deepcopy(schemas[stage])
    except KeyError as exc:
        raise ValueError(f"unknown PA provider stage: {stage}") from exc


def _serialized_provider_schema(
    stage: str,
    *,
    decision_tree_path: Path | None = None,
    joint_stage2: bool = False,
) -> str:
    if joint_stage2 and stage != "stage2":
        raise ValueError("joint contract applies only to Stage 2")
    return json.dumps(
        schema_for_joint_stage2_draft(decision_tree_path=decision_tree_path) if joint_stage2 else schema_for_provider_draft(
            stage,
            decision_tree_path=decision_tree_path,
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def provider_draft_schema_digest(
    stage: str,
    *,
    decision_tree_path: Path | None = None,
    joint_stage2: bool = False,
) -> str:
    """Stable identity shared by prompts, validation, and audit tests."""
    return hashlib.sha256(
        _serialized_provider_schema(
            stage,
            decision_tree_path=decision_tree_path,
            joint_stage2=joint_stage2,
        ).encode("utf-8")
    ).hexdigest()


def render_provider_draft_contract(
    stage: str,
    *,
    decision_tree_path: Path | None = None,
    joint_stage2: bool = False,
) -> str:
    """Render the minified exact runtime contract for schema-less providers."""
    digest = provider_draft_schema_digest(
        stage,
        decision_tree_path=decision_tree_path,
        joint_stage2=joint_stage2,
    )
    return (
        f"Provider contract digest: {digest}\n"
        "PA_PROVIDER_DRAFT_CONTRACT_BEGIN\n"
        + _serialized_provider_schema(
            stage,
            decision_tree_path=decision_tree_path,
            joint_stage2=joint_stage2,
        )
        + "\nPA_PROVIDER_DRAFT_CONTRACT_END"
    )


def _draft_issue(
    path: str,
    *,
    kind: str,
    message: str,
    allowed: list[Any] | None = None,
    blocks_materialization: bool = False,
) -> dict[str, Any]:
    parts = path.split(".") if path else []
    return {
        "path": path,
        "pointer": "/" + "/".join(parts) if parts else "",
        "kind": kind,
        "message": message,
        "allowed": list(allowed or []),
        "blocks_materialization": blocks_materialization,
    }


def joint_stage2_reference_issues(
    draft: dict[str, Any], *, validated_report: Any, frozen_catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    """Validate new Draft citations after Host validation of the upstream report.

    Report/catalog corruption is an upstream failure, never an editable Draft
    pointer. This checks identities only, not the quality of a VPA explanation.
    Frame binding remains owned by validate_frozen_vpa_catalog at the caller.
    """
    from pa_agent.ai.role_contracts import VpaEvidenceReport
    from pa_agent.ai.vpa_measurements import vpa_catalog_id

    if not isinstance(validated_report, VpaEvidenceReport):
        raise ValueError("joint Stage 2 requires a validated VPA report")
    identity = frozen_catalog.get("catalog_id")
    if identity != vpa_catalog_id(frozen_catalog) or validated_report.catalog_id != identity:
        raise ValueError("joint Stage 2 upstream VPA catalog identity mismatch")
    catalog_ids = {item["evidence_id"] for item in frozen_catalog.get("evidence", [])}
    report_ids = validated_report.evidence_ids
    if not report_ids.issubset(catalog_ids):
        raise ValueError("validated VPA report references evidence outside its catalog")
    # Structural shape belongs to the schema validator; do not guess a repair
    # when joint_analysis or its references have the wrong type.
    analysis = draft.get("joint_analysis")
    refs = analysis.get("vpa_evidence_refs") if isinstance(analysis, dict) else None
    if not isinstance(refs, list):
        return []
    return [
        _draft_issue(
            f"joint_analysis.vpa_evidence_refs.{index}",
            kind="semantic_validation",
            message="VPA citation must be present in the validated upstream report",
            allowed=sorted(report_ids), blocks_materialization=True,
        )
        for index, ref in enumerate(refs)
        if isinstance(ref, str) and ref not in report_ids
    ]


def provider_draft_semantic_issues(
    stage: str,
    draft: dict[str, Any],
    *,
    catalog: AnalysisFactCatalog,
    decision_tree_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Validate provider-owned protocol semantics before canonical materialization."""
    trace_name = "gate_trace" if stage == "stage1" else "decision_trace"
    role = "pa_stage1" if stage == "stage1" else "pa_stage2"
    trace = draft.get(trace_name)
    if not isinstance(trace, list):
        return []

    issues: list[dict[str, Any]] = []
    if stage == "stage1":
        hypotheses = draft.get("market_hypotheses")
        if isinstance(hypotheses, dict):
            sources = [("market_hypotheses.primary", hypotheses.get("primary"))]
            alternatives = hypotheses.get("alternatives")
            if isinstance(alternatives, list):
                sources.extend((f"market_hypotheses.alternatives.{index}", value)
                               for index, value in enumerate(alternatives))
            for path, hypothesis in sources:
                if not isinstance(hypothesis, dict):
                    continue
                for name in ("supporting_fact_refs", "challenging_fact_refs"):
                    refs = hypothesis.get(name)
                    if not isinstance(refs, list):
                        continue
                    for index, ref in enumerate(refs):
                        if not isinstance(ref, str):
                            continue  # The schema owns type errors.
                        try:
                            catalog.resolve_references([ref], role=role)
                        except KeyError:
                            issues.append(_draft_issue(
                                f"{path}.{name}.{index}", kind="unknown_evidence",
                                message=f"unknown or hidden Evidence ID: {ref}",
                                blocks_materialization=True,
                            ))
    seen: set[str] = set()
    valid_ids = (
        {"1.1", *STAGE1_GATE_NODE_IDS}
        if stage == "stage1"
        else set(stage2_provider_node_ids(decision_tree_path))
    )
    valid_trace_ids: list[str] = []
    for index, item in enumerate(trace):
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id") or "")
        if node_id in valid_ids:
            valid_trace_ids.append(node_id)
        if node_id in seen:
            issues.append(
                _draft_issue(
                    f"{trace_name}.{index}.node_id",
                    kind="semantic_validation",
                    message=f"{trace_name} node {node_id!r} must not be duplicated",
                )
            )
        seen.add(node_id)
        refs = item.get("evidence_refs")
        if not isinstance(refs, list):
            continue
        for ref_index, ref in enumerate(refs):
            if not isinstance(ref, str) or not ref.startswith("PA."):
                continue
            try:
                catalog.resolve_references([ref], role=role)
            except KeyError:
                issues.append(
                    _draft_issue(
                        f"{trace_name}.{index}.evidence_refs.{ref_index}",
                        kind="unknown_evidence",
                        message=f"unknown or hidden Evidence ID: {ref}",
                        blocks_materialization=True,
                    )
                )

    if stage == "stage1":
        answers = {
            str(item.get("node_id")): str(item.get("answer"))
            for item in trace
            if isinstance(item, dict)
        }
        valid_early_stop = (
            answers.get("1.2") in {"否", "等待"}
            or answers.get("1.3") == "是"
        )
        if "2.5" not in valid_trace_ids and not valid_early_stop:
            issues.append(
                _draft_issue(
                    trace_name,
                    kind="incomplete_protocol_path",
                    message="complete Stage 1 Gate path must evaluate node 2.5",
                    blocks_materialization=True,
                )
            )
        return issues

    decision = draft.get("decision")
    decision = decision if isinstance(decision, dict) else {}
    executable = decision.get("order_type") in {"限价单", "突破单", "市价单"}
    intent = draft.get("decision_intent")
    if intent == "trade" and not executable:
        issues.append(
            _draft_issue(
                "decision_intent",
                kind="semantic_validation",
                message="trade intent requires an executable PA candidate",
                blocks_materialization=True,
            )
        )
    if executable:
        if not any(node_id.startswith("11.") for node_id in valid_trace_ids):
            issues.append(
                _draft_issue(
                    trace_name,
                    kind="incomplete_protocol_path",
                    message="executable order requires one model-selected §11.1-§11.4 node",
                )
            )
        if "14" not in valid_trace_ids:
            issues.append(
                _draft_issue(
                    trace_name,
                    kind="incomplete_protocol_path",
                    message="executable order requires node 14 prohibition scan",
                )
            )
        bar_analysis = draft.get("bar_analysis")
        bar_analysis = bar_analysis if isinstance(bar_analysis, dict) else {}
        signal = bar_analysis.get("signal_bar")
        signal = signal if isinstance(signal, dict) else {}
        entry = bar_analysis.get("entry_bar")
        entry = entry if isinstance(entry, dict) else {}
        declared_bars = (
            decision.get("entry_basis_bar"),
            signal.get("bar"),
            entry.get("bar"),
        )
        has_trace_evidence = any(
            isinstance(item, dict) and bool(item.get("evidence_refs"))
            for item in trace
        )
        if not has_trace_evidence and not any(
            isinstance(bar, str) and bar.startswith("K") for bar in declared_bars
        ):
            issues.append(
                _draft_issue(
                    trace_name,
                    kind="missing_evidence",
                    message="executable order requires model-declared PA evidence",
                    blocks_materialization=True,
                )
            )
    return issues


def _materialize_trace(
    trace: list[dict[str, Any]],
    *,
    catalog: AnalysisFactCatalog,
    role: str,
    decision_tree_path: Path | None = None,
) -> list[dict[str, Any]]:
    nodes = load_decision_tree(decision_tree_path)["node_index"]
    result: list[dict[str, Any]] = []
    for source in trace:
        node_id = str(source["node_id"])
        if node_id not in nodes:
            raise ValueError(f"unknown decision-tree node: {node_id}")
        refs = [str(item) for item in source.get("evidence_refs", [])]
        resolved = catalog.resolve_references(refs, role=role)
        node = nodes[node_id]
        item = copy.deepcopy(source)
        item.setdefault("skipped", False)
        item["question"] = node["question"]
        item["section"] = node["section_title"]
        item["bar_range"] = resolved.bar_range or "不适用"
        result.append(item)
    node_order = {node_id: index for index, node_id in enumerate(nodes)}
    result.sort(key=lambda item: node_order[str(item["node_id"])])
    return result


def _stable_union(*groups: list[str]) -> list[str]:
    return list(dict.fromkeys(item for group in groups for item in group))


def _materialize_hypotheses(raw: dict[str, Any]) -> dict[str, Any]:
    primary = copy.deepcopy(raw["primary"])
    primary.update({"hypothesis_id": "pa-primary", "role": "primary"})
    alternatives = []
    for index, source in enumerate(raw.get("alternatives", []), start=1):
        item = copy.deepcopy(source)
        item.update({"hypothesis_id": f"pa-alternative-{index}", "role": "alternative"})
        alternatives.append(item)
    return {"primary": primary, "alternatives": alternatives}


def _derive_gate_result(trace: list[dict[str, Any]]) -> str:
    by_id = {str(item["node_id"]): str(item["answer"]) for item in trace}
    if by_id.get("1.2") in {"否", "等待"}:
        return "unknown"
    if by_id.get("1.3") == "是":
        return "wait"
    if "2.5" not in by_id:
        raise ValueError("incomplete Stage 1 gate path: node 2.5 was not evaluated")
    return "proceed"


def materialize_stage1_draft(
    draft: dict[str, Any],
    *,
    catalog: AnalysisFactCatalog,
    decision_tree_path: Path | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(draft)
    result.pop("protocol_version", None)
    hypotheses = _materialize_hypotheses(result["market_hypotheses"])
    primary = hypotheses["primary"]
    alternatives = hypotheses["alternatives"]
    result["market_hypotheses"] = hypotheses
    result["cycle_position"] = primary["cycle_position"]
    result["alternative_cycle_position"] = alternatives[0]["cycle_position"] if alternatives else None
    result["direction"] = primary["direction"]
    result["entry_setup"] = primary["setup_hint"]
    semantic_gate_trace = [
        item
        for item in result["gate_trace"]
        if str(item.get("node_id")) != "1.1"
    ]
    result["gate_trace"] = _materialize_trace(
        semantic_gate_trace,
        catalog=catalog,
        role="pa_stage1",
        decision_tree_path=decision_tree_path,
    )
    result["gate_result"] = _derive_gate_result(result["gate_trace"])
    spike_analysis = result.get("pa_evidence", {}).get("spike_analysis")
    result["pa_evidence"] = {
        "fact_refs": _stable_union(
            primary["supporting_fact_refs"], primary["challenging_fact_refs"]
        ),
        "theory_rule_ids": list(primary["theory_rule_ids"]),
        "spike_analysis": spike_analysis,
    }
    return result


def _next_cycle_placeholder() -> dict[str, Any]:
    return {
        "cycle": None, "direction": None, "probabilities": None,
        "reasoning": "Auxiliary next-cycle prediction was not requested.",
        "unpredictable": True, "features_used": ["stage1_diagnosis"],
    }


def _equation_trace(
    decision: dict[str, Any],
    bar_analysis: dict[str, Any],
    decision_trace: list[dict[str, Any]],
    *,
    catalog: AnalysisFactCatalog,
    decision_tree_path: Path | None = None,
) -> tuple[dict[str, Any], bool]:
    metrics = compute_risk_reward(
        decision.get("entry_price"), decision.get("take_profit_price"),
        decision.get("stop_loss_price"), decision.get("order_direction"),
    )
    win_rate = decision.get("estimated_win_rate")
    passed = bool(
        metrics is not None and win_rate is not None
        and passes_trader_equation(float(win_rate), metrics["risk"], metrics["reward"])
    )
    signal = bar_analysis.get("signal_bar")
    signal = signal if isinstance(signal, dict) else {}
    entry = bar_analysis.get("entry_bar")
    entry = entry if isinstance(entry, dict) else {}
    bars = [decision.get("entry_basis_bar"), signal.get("bar"), entry.get("bar")]
    refs = list(
        dict.fromkeys(
            f"PA.BAR.{bar}"
            for bar in bars
            if isinstance(bar, str) and bar.startswith("K")
        )
    )
    if not refs:
        refs = _stable_union(
            *[
                [str(ref) for ref in item.get("evidence_refs", [])]
                for item in decision_trace
                if isinstance(item, dict)
            ]
        )
    if not refs:
        raise ValueError("trader equation requires model-declared PA evidence")
    resolved = catalog.resolve_references(refs, role="pa_stage2")
    node = load_decision_tree(decision_tree_path)["node_index"]["10.3"]
    if metrics is None:
        reason = "risk/reward geometry is unavailable; trader equation cannot pass"
    else:
        risk, reward = float(metrics["risk"]), float(metrics["reward"])
        p = float(win_rate or 0) / 100.0
        reason = (
            f"risk={risk:.6g}, reward={reward:.6g}, RR={metrics['ratio']:.4g}, "
            f"win_rate={float(win_rate or 0):.4g}%, breakeven={risk / (risk + reward) * 100:.4g}%, "
            f"expectancy={p * reward - (1.0 - p) * risk:.6g}; {'pass' if passed else 'fail'}"
        )
    return ({
        "node_id": "10.3", "question": node["question"],
        "answer": "是" if passed else "否", "reason": reason,
        "skipped": False, "section": node["section_title"],
        "bar_range": resolved.bar_range or "K1", "evidence_refs": refs,
    }, passed)


def materialize_stage2_draft(
    draft: dict[str, Any],
    *,
    stage1: dict[str, Any],
    catalog: AnalysisFactCatalog,
    decision_tree_path: Path | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(draft)
    result.pop("protocol_version", None)
    intent = str(result.pop("decision_intent"))
    decision = result["decision"]
    decision["diagnosis_confidence"] = stage1["diagnosis_confidence"]
    decision["diagnosis_confidence_reasoning"] = stage1.get(
        "diagnosis_confidence_reasoning"
    ) or stage1.get("risk_warning") or "Inherited from Stage 1 diagnosis."
    trace = _materialize_trace(
        result["decision_trace"],
        catalog=catalog,
        role="pa_stage2",
        decision_tree_path=decision_tree_path,
    )
    if any(item["node_id"] == "10.3" for item in trace):
        raise ValueError("Stage 2 Draft must not provide program-owned node 10.3")
    executable = decision.get("order_type") in {"限价单", "突破单", "市价单"}
    if intent == "trade" and not executable:
        raise ValueError("trade intent requires an executable PA candidate")
    equation_passes = False
    if executable:
        equation, equation_passes = _equation_trace(
            decision,
            result.get("bar_analysis") if isinstance(result.get("bar_analysis"), dict) else {},
            trace,
            catalog=catalog,
            decision_tree_path=decision_tree_path,
        )
        insert_at = next(
            (i for i, item in enumerate(trace) if str(item["node_id"]).startswith(("11.", "14"))),
            len(trace),
        )
        trace.insert(insert_at, equation)
    result["decision_trace"] = trace
    result["gate_shortcircuited"] = False
    if intent == "trade" and equation_passes:
        terminal_node = next(
            (item["node_id"] for item in reversed(trace) if str(item["node_id"]).startswith("11.")),
            "10.3",
        )
        result["terminal"] = {
            "node_id": terminal_node, "outcome": "trade",
            "label": "LLM trade intent; deterministic trader equation passed",
        }
    elif intent == "trade":
        result["terminal"] = {
            "node_id": "10.3", "outcome": "reject", "label": "Deterministic trader equation failed",
        }
    else:
        terminal_item = next(
            (
                item for item in reversed(trace)
                if str(item["node_id"]) != "14" or str(item.get("answer")) == "是"
            ),
            trace[-1],
        )
        result["terminal"] = {
            "node_id": terminal_item["node_id"], "outcome": intent, "label": f"LLM {intent} intent",
        }
    result.setdefault("next_cycle_prediction", _next_cycle_placeholder())
    return result
