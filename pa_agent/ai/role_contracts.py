"""Typed contracts for the PA, VPA, and joint-decision roles.

The legacy Stage 1/Stage 2 payloads remain the GUI-facing compatibility
views.  These contracts are the canonical hand-off between reasoning roles:
PA owns hypotheses and setup candidates, VPA owns volume evidence, and the
joint arbiter may only act on an existing PA candidate.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pa_agent.ai.prompts.schemas import STAGE2_SCHEMA

Relation = Literal["supports", "neutral", "challenges", "insufficient_data"]
DecisionEffect = Literal[
    "keep_brooks_baseline",
    "require_confirmation",
    "prefer_retest",
    "lower_confidence",
    "reject_current_attempt",
]


class PaHypothesis(BaseModel):
    """One PA interpretation grounded in Stage 1's typed fields."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    role: Literal["primary", "alternative"]
    cycle_position: str
    direction: Literal["bullish", "bearish", "neutral"]
    setup: str
    thesis: str
    fact_refs: list[str] = Field(default_factory=list)
    theory_rule_ids: list[str] = Field(default_factory=list)


class PaSetupCandidate(BaseModel):
    """A setup candidate that a later arbiter may keep, delay, or reject."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    hypothesis_id: str
    direction: Literal["bullish", "bearish", "neutral"]
    setup: str
    timing_state: Literal["eligible", "waiting"]
    trigger: str
    invalidation: str
    fact_refs: list[str] = Field(default_factory=list)
    theory_rule_ids: list[str] = Field(default_factory=list)


class PaHypothesisSet(BaseModel):
    """Canonical PA hand-off consumed by VPA and the joint arbiter."""

    model_config = ConfigDict(extra="forbid")

    primary: PaHypothesis
    alternative: PaHypothesis | None
    setup_candidates: list[PaSetupCandidate]
    gate_result: Literal["proceed", "wait", "unknown"]
    source_digest: str

    @classmethod
    def from_stage1(cls, stage1: dict[str, Any]) -> "PaHypothesisSet":
        """Delegate validated Stage 1 semantics without inventing conclusions."""
        evidence = stage1.get("pa_evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        fact_refs = [str(item) for item in evidence.get("fact_refs", [])]
        theory_ids = [str(item) for item in evidence.get("theory_rule_ids", [])]
        cycle = str(stage1.get("cycle_position") or "unknown")
        direction = str(stage1.get("direction") or "neutral")
        if direction not in {"bullish", "bearish", "neutral"}:
            direction = "neutral"
        setup = str(stage1.get("entry_setup") or "No current PA setup")
        primary = PaHypothesis(
            hypothesis_id="pa-primary",
            role="primary",
            cycle_position=cycle,
            direction=direction,
            setup=setup,
            thesis=(
                f"{cycle} / {direction}: {setup}. "
                f"Gate={stage1.get('gate_result', 'unknown')}."
            ),
            fact_refs=fact_refs,
            theory_rule_ids=theory_ids,
        )
        alternative_cycle = stage1.get("alternative_cycle_position")
        alternative = None
        if alternative_cycle and str(alternative_cycle) != cycle:
            alternative = PaHypothesis(
                hypothesis_id="pa-alternative",
                role="alternative",
                cycle_position=str(alternative_cycle),
                direction=direction,
                setup=setup,
                thesis=(
                    f"Competing PA framework: {alternative_cycle} / "
                    f"{direction}; reassess the same setup without changing "
                    "the current-timeframe directional authority."
                ),
                fact_refs=fact_refs,
                theory_rule_ids=theory_ids,
            )
        gate = str(stage1.get("gate_result") or "unknown")
        if gate not in {"proceed", "wait", "unknown"}:
            gate = "unknown"
        candidate = PaSetupCandidate(
            candidate_id="pa-setup-1",
            hypothesis_id=primary.hypothesis_id,
            direction=direction,
            setup=setup,
            timing_state="eligible" if gate == "proceed" else "waiting",
            trigger=setup,
            invalidation=str(stage1.get("risk_warning") or "Re-run PA on a new closed bar."),
            fact_refs=fact_refs,
            theory_rule_ids=theory_ids,
        )
        source_payload = {
            "cycle_position": cycle,
            "alternative_cycle_position": alternative_cycle,
            "direction": direction,
            "entry_setup": setup,
            "gate_result": gate,
            "pa_evidence": evidence,
        }
        digest = hashlib.sha256(
            json.dumps(
                source_payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return cls(
            primary=primary,
            alternative=alternative,
            setup_candidates=[candidate],
            gate_result=gate,
            source_digest=digest,
        )

    @property
    def hypothesis_ids(self) -> set[str]:
        result = {self.primary.hypothesis_id}
        if self.alternative is not None:
            result.add(self.alternative.hypothesis_id)
        return result

    @property
    def candidate_ids(self) -> set[str]:
        return {item.candidate_id for item in self.setup_candidates}


class VpaObservation(BaseModel):
    """One contextual interpretation of immutable program evidence."""

    model_config = ConfigDict(extra="forbid")

    bar_range: str
    evidence_ids: list[str]
    finding_kind: Literal[
        "background",
        "effort_result",
        "sequence_comparison",
        "acceptance_rejection",
        "entry_timing",
    ]
    interpretation: str


class VpaHypothesisAssessment(BaseModel):
    """VPA's evidence relation to one PA hypothesis."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    relation: Relation
    evidence_ids: list[str]
    reason: str


class VpaNextConfirmation(BaseModel):
    """Future evidence needed to resolve the current VPA interpretation."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["pending_closed_bar", "not_applicable"]
    trigger: str
    requires_closed_bar: Literal[True]


class VpaEvidenceReport(BaseModel):
    """Independent VPA evidence report; it has no order or setup authority."""

    model_config = ConfigDict(extra="forbid")

    catalog_id: str
    data_status: Literal["available", "limited", "unavailable"]
    summary: str
    observations: list[VpaObservation]
    hypothesis_assessments: list[VpaHypothesisAssessment]
    background_relation: Relation
    entry_timing_relation: Relation
    next_confirmation: VpaNextConfirmation

    @property
    def evidence_ids(self) -> set[str]:
        ids = {
            evidence_id
            for item in self.observations
            for evidence_id in item.evidence_ids
        }
        ids.update(
            evidence_id
            for item in self.hypothesis_assessments
            for evidence_id in item.evidence_ids
        )
        return ids


class JointArbitration(BaseModel):
    """Why the joint arbiter kept, delayed, or rejected a PA candidate."""

    model_config = ConfigDict(extra="forbid")

    selected_candidate_id: str | None
    decision_effect: DecisionEffect
    evidence_used: list[str]
    reason: str


class JointDecisionProposal(BaseModel):
    """Joint arbitration metadata plus a normal validated Stage 2 decision."""

    model_config = ConfigDict(extra="forbid")

    joint_arbitration: JointArbitration
    stage2_decision: dict[str, Any]


class RunOutcome(BaseModel):
    """Explicit final state of one analysis run."""

    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "completed",
        "completed_with_warnings",
        "degraded_pa_only",
        "unavailable_input",
        "unavailable_provider",
        "cancelled",
        "internal_error",
    ]
    official_path: Literal["pa_only", "joint", "none"]
    requested_vpa_mode: Literal["off", "shadow", "soft"]
    degradation_stage: Literal["vpa_evidence", "joint_arbiter"] | None = None
    reason: str | None = None


_VPA_MODEL_SCHEMA = VpaEvidenceReport.model_json_schema()
_VPA_MODEL_DEFS = _VPA_MODEL_SCHEMA.pop("$defs", {})
VPA_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["vpa_report"],
    "properties": {
        "vpa_report": _VPA_MODEL_SCHEMA,
    },
    "additionalProperties": False,
}
if _VPA_MODEL_DEFS:
    VPA_EVIDENCE_SCHEMA["$defs"] = _VPA_MODEL_DEFS

_JOINT_STAGE2_SCHEMA = copy.deepcopy(STAGE2_SCHEMA)
_JOINT_STAGE2_SCHEMA["properties"].pop("vpa_impact", None)
_JOINT_ARBITRATION_SCHEMA = JointArbitration.model_json_schema()
_JOINT_ARBITRATION_DEFS = _JOINT_ARBITRATION_SCHEMA.pop("$defs", {})

JOINT_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["joint_arbitration", "stage2_decision"],
    "properties": {
        "joint_arbitration": _JOINT_ARBITRATION_SCHEMA,
        "stage2_decision": _JOINT_STAGE2_SCHEMA,
    },
    "additionalProperties": False,
}
if _JOINT_ARBITRATION_DEFS:
    JOINT_DECISION_SCHEMA["$defs"] = _JOINT_ARBITRATION_DEFS
