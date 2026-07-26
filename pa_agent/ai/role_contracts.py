"""Typed contracts for the PA, VPA, and joint-decision roles.

The legacy Stage 1/Stage 2 payloads remain the GUI-facing compatibility
views.  These contracts are the canonical hand-off between reasoning roles:
PA owns hypotheses and setup candidates, VPA owns volume evidence, and the
joint arbiter may only act on an existing PA candidate.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
    def from_stage1(
        cls,
        stage1: dict[str, Any],
        stage2: dict[str, Any] | None = None,
    ) -> "PaHypothesisSet":
        """Delegate PA semantics without inventing an executable setup plan.

        Stage 1 may name a setup family, but only validated Stage 2 owns a
        concrete trigger and setup-specific invalidation.  A joint arbiter may
        therefore see a candidate only after both PA stages supplied that plan.
        """
        evidence = stage1.get("pa_evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        fact_refs = [str(item) for item in evidence.get("fact_refs", [])]
        theory_ids = [str(item) for item in evidence.get("theory_rule_ids", [])]
        cycle = str(stage1.get("cycle_position") or "unknown")
        direction = str(stage1.get("direction") or "neutral")
        if direction not in {"bullish", "bearish", "neutral"}:
            direction = "neutral"
        decision = (
            stage2.get("decision")
            if isinstance(stage2, dict) and isinstance(stage2.get("decision"), dict)
            else {}
        )
        trigger = str(decision.get("entry_rule") or "").strip()
        invalidation = str(decision.get("invalidation_condition") or "").strip()
        raw_setup = str(stage1.get("entry_setup") or "").strip()
        setup = raw_setup or trigger or "No current PA setup"
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
        has_explicit_plan = bool(
            gate == "proceed"
            and direction != "neutral"
            and decision.get("order_direction")
            and trigger
            and invalidation
        )
        candidate_setup = raw_setup or trigger
        candidates = (
            [
                PaSetupCandidate(
                    candidate_id="pa-setup-1",
                    hypothesis_id=primary.hypothesis_id,
                    direction=direction,
                    setup=candidate_setup,
                    timing_state="eligible",
                    trigger=trigger,
                    invalidation=invalidation,
                    fact_refs=fact_refs,
                    theory_rule_ids=theory_ids,
                )
            ]
            if has_explicit_plan
            else []
        )
        source_payload = {
            "cycle_position": cycle,
            "alternative_cycle_position": alternative_cycle,
            "direction": direction,
            "entry_setup": setup,
            "gate_result": gate,
            "pa_evidence": evidence,
            "stage2_plan": {
                "order_direction": decision.get("order_direction"),
                "entry_rule": trigger or None,
                "invalidation_condition": invalidation or None,
            },
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
            setup_candidates=candidates,
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


class VpaSequenceClaim(BaseModel):
    """A model interpretation whose chronological order is program-checkable."""

    model_config = ConfigDict(extra="forbid")

    earlier_evidence_id: str
    later_evidence_id: str
    relation: Literal[
        "response",
        "follow_through",
        "acceptance",
        "rejection",
        "test",
    ]


class VpaSequenceClaimDraft(VpaSequenceClaim):
    """Provider-facing sequence claim; unknown prose metadata is discarded."""

    model_config = ConfigDict(extra="ignore")


class VpaObservationDraft(BaseModel):
    """Model-owned interpretation fields; program facts are deliberately absent."""

    model_config = ConfigDict(extra="ignore")

    evidence_ids: list[str]
    finding_kind: Literal[
        "background",
        "effort_result",
        "sequence_comparison",
        "acceptance_rejection",
        "entry_timing",
    ]
    interpretation_label: str | None = Field(default=None, max_length=80)
    epistemic_status: Literal[
        "candidate",
        "confirmed",
        "unresolved",
        "insufficient_data",
    ] = "unresolved"
    sequence_claims: list[VpaSequenceClaimDraft] = Field(default_factory=list)
    interpretation: str


class VpaObservation(VpaObservationDraft):
    """Canonical observation after program-owned fields are materialized."""

    model_config = ConfigDict(extra="forbid")

    sequence_claims: list[VpaSequenceClaim] = Field(default_factory=list)
    bar_range: str


class VpaHypothesisAssessment(BaseModel):
    """VPA's evidence relation to one PA hypothesis."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    relation: Relation
    evidence_ids: list[str]
    reason: str


class VpaHypothesisAssessmentDraft(VpaHypothesisAssessment):
    """Provider-facing assessment with whitelist normalization."""

    model_config = ConfigDict(extra="ignore")


class VpaNextConfirmation(BaseModel):
    """Future evidence needed to resolve the current VPA interpretation."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["pending_closed_bar", "not_applicable"]
    trigger: str | None
    requires_closed_bar: bool


class VpaNextConfirmationDraft(VpaNextConfirmation):
    """Provider-facing confirmation with whitelist normalization."""

    model_config = ConfigDict(extra="ignore")


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
        ids.update(
            evidence_id
            for item in self.observations
            for claim in item.sequence_claims
            for evidence_id in (
                claim.earlier_evidence_id,
                claim.later_evidence_id,
            )
        )
        return ids


class VpaEvidenceReportDraft(BaseModel):
    """Provider-facing report without program-owned observation ranges."""

    model_config = ConfigDict(extra="ignore")

    catalog_id: str
    data_status: Literal["available", "limited", "unavailable"]
    summary: str
    observations: list[VpaObservationDraft]
    hypothesis_assessments: list[VpaHypothesisAssessmentDraft]
    background_relation: Relation
    entry_timing_relation: Relation
    next_confirmation: VpaNextConfirmationDraft


class VpaEvidenceEnvelopeDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    vpa_report: VpaEvidenceReportDraft


class JointArbitration(BaseModel):
    """Why the joint arbiter kept, delayed, or rejected a PA candidate."""

    model_config = ConfigDict(extra="forbid")

    selected_candidate_id: str | None
    decision_effect: DecisionEffect
    evidence_used: list[str]
    reason: str = Field(min_length=1, max_length=240)


class JointDecisionPatch(BaseModel):
    """Allowed semantic changes to the frozen PA-only decision.

    The arbiter does not copy Stage 2 or perform arithmetic.  It may only
    provide the small set of timing/confidence fields that VPA is allowed to
    affect.  The program materializes these fields onto the validated PA
    baseline and applies no-order invariants deterministically.
    """

    model_config = ConfigDict(extra="forbid")

    trade_confidence: int | None = Field(default=None, ge=0, le=100)
    trade_confidence_reasoning: str | None = Field(
        default=None,
        min_length=1,
        max_length=280,
    )
    watch_points: list[str] | None = None
    risk_assessment: str | None = Field(default=None, min_length=1, max_length=500)
    invalidation_condition: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )


class JointDecisionProposal(BaseModel):
    """Joint arbitration metadata plus a bounded Stage 2 decision patch."""

    model_config = ConfigDict(extra="forbid")

    joint_arbitration: JointArbitration
    decision_patch: JointDecisionPatch


class RunOutcome(BaseModel):
    """Explicit final state of one analysis run."""

    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "completed",
        "completed_with_warnings",
        "degraded_pa_only",
        "unavailable_input",
        "unavailable_provider",
        "invalid_model_output",
        "cancelled",
        "internal_error",
    ]
    official_path: Literal["pa_only", "joint", "none"]
    requested_vpa_mode: Literal["off", "shadow", "soft"]
    degradation_stage: Literal["vpa_evidence", "joint_arbiter"] | None = None
    reason: str | None = None


VPA_EVIDENCE_SCHEMA: dict[str, Any] = VpaEvidenceEnvelopeDraft.model_json_schema()

JOINT_DECISION_SCHEMA: dict[str, Any] = JointDecisionProposal.model_json_schema()
