"""Lean provider contracts and deterministic canonical materialization.

The model owns market interpretation and trade intent. This module only restores
protocol metadata, resolves declared evidence, and computes reproducible math.
"""
from __future__ import annotations

import copy
from typing import Any

from pa_agent.ai.decision_tree import load_decision_tree
from pa_agent.ai.fact_catalog import AnalysisFactCatalog
from pa_agent.ai.prompts.schemas import STAGE1_SCHEMA, STAGE2_SCHEMA
from pa_agent.util.trade_metrics import compute_risk_reward, passes_trader_equation

_DRAFT_TRACE_ITEM = {
    "type": "object",
    "required": ["node_id", "answer", "reason", "evidence_refs"],
    "properties": {
        "node_id": {"type": "string"},
        "answer": {"type": "string", "enum": ["是", "否", "中性", "等待", "不适用"]},
        "reason": {"type": "string"},
        "branch": {"type": ["string", "null"]},
        "next_node": {"type": ["string", "null"]},
        "skipped": {"type": "boolean"},
        "evidence_refs": {
            "type": "array", "items": {"type": "string", "pattern": r"^PA\."},
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
    schema = copy.deepcopy(STAGE1_SCHEMA)
    removed = {
        "cycle_position", "alternative_cycle_position", "direction", "entry_setup", "gate_result",
    }
    schema["required"] = [key for key in schema["required"] if key not in removed]
    schema["required"] += [
        "protocol_version", "diagnosis_confidence_reasoning", "market_hypotheses",
    ]
    for key in removed:
        schema["properties"].pop(key)
    hypothesis = _draft_hypothesis_schema()
    schema["properties"]["protocol_version"] = {"const": "pa_draft_v2"}
    schema["properties"]["market_hypotheses"] = {
        "type": "object",
        "required": ["primary", "alternatives"],
        "properties": {
            "primary": hypothesis,
            "alternatives": {"type": "array", "maxItems": 2, "items": hypothesis},
        },
        "additionalProperties": False,
    }
    schema["properties"]["gate_trace"]["items"] = _DRAFT_TRACE_ITEM
    schema["properties"]["pa_evidence"] = {
        "type": "object",
        "required": ["spike_analysis"],
        "properties": {
            "spike_analysis": copy.deepcopy(
                STAGE1_SCHEMA["properties"]["pa_evidence"]["properties"]["spike_analysis"]
            )
        },
        "additionalProperties": False,
    }
    schema["allOf"] = [
        rule for rule in schema.get("allOf", []) if "cycle_position" not in str(rule)
    ]
    return schema


def _stage2_draft_schema() -> dict[str, Any]:
    schema = copy.deepcopy(STAGE2_SCHEMA)
    schema["required"] = [
        key for key in schema["required"] if key not in {"terminal", "next_cycle_prediction"}
    ] + ["protocol_version", "decision_intent"]
    schema["properties"].pop("terminal")
    schema["properties"]["protocol_version"] = {"const": "pa_draft_v2"}
    schema["properties"]["decision_intent"] = {
        "type": "string", "enum": ["trade", "wait", "reject"],
    }
    schema["properties"]["decision_trace"]["items"] = _DRAFT_TRACE_ITEM
    decision = schema["properties"]["decision"]
    for key in ("diagnosis_confidence", "diagnosis_confidence_reasoning"):
        decision["required"].remove(key)
        decision["properties"].pop(key)
    return schema


STAGE1_DRAFT_SCHEMA = _stage1_draft_schema()
STAGE2_DRAFT_SCHEMA = _stage2_draft_schema()


def _materialize_trace(
    trace: list[dict[str, Any]], *, catalog: AnalysisFactCatalog, role: str
) -> list[dict[str, Any]]:
    nodes = load_decision_tree()["node_index"]
    result: list[dict[str, Any]] = []
    for source in trace:
        node_id = str(source["node_id"])
        if node_id not in nodes:
            raise ValueError(f"unknown decision-tree node: {node_id}")
        refs = [str(item) for item in source.get("evidence_refs", [])]
        if not refs and not bool(source.get("skipped")):
            raise ValueError(f"decision-tree node {node_id} requires evidence_refs")
        resolved = catalog.resolve_references(refs, role=role)
        node = nodes[node_id]
        item = copy.deepcopy(source)
        item.setdefault("skipped", False)
        item["question"] = node["question"]
        item["section"] = node["section_title"]
        item["bar_range"] = resolved.bar_range or "不适用"
        result.append(item)
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
    draft: dict[str, Any], *, catalog: AnalysisFactCatalog
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
    result["gate_trace"] = _materialize_trace(
        result["gate_trace"], catalog=catalog, role="pa_stage1"
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
    *,
    catalog: AnalysisFactCatalog,
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
    ) or ["PA.BAR.K1"]
    resolved = catalog.resolve_references(refs, role="pa_stage2")
    node = load_decision_tree()["node_index"]["10.3"]
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
    draft: dict[str, Any], *, stage1: dict[str, Any], catalog: AnalysisFactCatalog
) -> dict[str, Any]:
    result = copy.deepcopy(draft)
    result.pop("protocol_version", None)
    intent = str(result.pop("decision_intent"))
    decision = result["decision"]
    decision["diagnosis_confidence"] = stage1["diagnosis_confidence"]
    decision["diagnosis_confidence_reasoning"] = stage1.get(
        "diagnosis_confidence_reasoning"
    ) or stage1.get("risk_warning") or "Inherited from Stage 1 diagnosis."
    trace = _materialize_trace(result["decision_trace"], catalog=catalog, role="pa_stage2")
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
            catalog=catalog,
        )
        insert_at = next(
            (i for i, item in enumerate(trace) if str(item["node_id"]).startswith(("11.", "14"))),
            len(trace),
        )
        trace.insert(insert_at, equation)
    result["decision_trace"] = trace
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
