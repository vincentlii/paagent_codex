"""Typed contracts for the PA, VPA, and joint-decision roles.

The legacy Stage 1/Stage 2 payloads remain the GUI-facing compatibility
views.  These contracts are the canonical hand-off between reasoning roles:
PA owns hypotheses and setup candidates, VPA owns volume evidence, and the
joint arbiter may only act on an existing PA candidate.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PREDECISION_VPA_SCOPE = "predecision_vpa_v2"

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
    setup: str | None
    thesis: str
    fact_refs: list[str] = Field(default_factory=list)
    challenging_fact_refs: list[str] = Field(default_factory=list)
    theory_rule_ids: list[str] = Field(default_factory=list)
    confirmation_condition: str | None = None
    disconfirmation_condition: str | None = None


class PaTradeMath(BaseModel):
    """Program-recomputed trade mathematics over the frozen PA plan."""

    model_config = ConfigDict(extra="forbid")

    risk: float
    reward_to_tp1: float
    reward_risk_ratio: float
    reward_to_tp2: float | None
    reward_risk_ratio_tp2: float | None
    risk_atr: float | None
    entry_distance_from_current_close_atr: float | None
    risk_pct_of_entry: float | None
    break_even_win_rate_pct: float
    pa_estimated_win_rate_pct: int
    estimated_expectancy: float
    equation_passes: bool
    win_rate_provenance: Literal["llm_subjective_prior"]
    calibration_id: str | None = None


class PaWindowContext(BaseModel):
    """Numeric price positions in PA-selected windows, without location labels."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str
    bar_range: str
    high: float
    high_bars: list[str]
    low: float
    low_bars: list[str]
    current_close_fraction_from_low: float
    entry_fraction_from_low: float
    stop_fraction_from_low: float
    tp1_fraction_from_low: float
    tp2_fraction_from_low: float | None


class PaSetupCandidate(BaseModel):
    """A setup candidate that a later arbiter may keep, delay, or reject."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    hypothesis_id: str
    direction: Literal["bullish", "bearish", "neutral"]
    setup: str | None
    timing_state: Literal["eligible", "waiting"]
    execution_mode: Literal[
        "passive_limit",
        "price_triggered_stop",
        "immediate_market",
    ]
    order_type: Literal["限价单", "突破单", "市价单"]
    order_direction: Literal["做多", "做空"]
    entry_price: float
    entry_basis_bar: str | None
    entry_basis_extreme: Literal["high", "low"] | None
    trigger: str | None
    stop_loss_price: float
    take_profit_price: float
    take_profit_price_2: float | None
    invalidation: str | None
    trade_math: PaTradeMath
    window_context: list[PaWindowContext] = Field(default_factory=list)
    fact_refs: list[str] = Field(default_factory=list)
    theory_rule_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def materialize_legacy_execution_mode(cls, value: Any) -> Any:
        """Keep frozen pre-field records replayable via an exact order mapping."""
        if not isinstance(value, dict) or value.get("execution_mode") is not None:
            return value
        execution_mode = {
            "限价单": "passive_limit",
            "突破单": "price_triggered_stop",
            "市价单": "immediate_market",
        }.get(value.get("order_type"))
        if execution_mode is None:
            return value
        return {**value, "execution_mode": execution_mode}


class PaHypothesisSet(BaseModel):
    """Canonical PA hand-off consumed by VPA and the joint arbiter."""

    model_config = ConfigDict(extra="forbid")

    primary: PaHypothesis
    alternative: PaHypothesis | None
    alternatives: list[PaHypothesis] = Field(default_factory=list)
    setup_candidates: list[PaSetupCandidate]
    gate_result: Literal["proceed", "wait", "unknown"]
    price_snapshot_hash: str
    source_digest: str

    @property
    def hypothesis_ids(self) -> set[str]:
        result = {self.primary.hypothesis_id}
        if self.alternative is not None:
            result.add(self.alternative.hypothesis_id)
        result.update(item.hypothesis_id for item in self.alternatives)
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
    sequence_claims: list[VpaSequenceClaimDraft] = Field(default_factory=list)
    interpretation: str
    epistemic_status: Literal[
        "candidate",
        "confirmed",
        "unresolved",
        "insufficient_data",
    ] = "unresolved"
    interpretation_label: str | None = Field(default=None, max_length=80)


class VpaObservation(VpaObservationDraft):
    """Canonical observation after program-owned fields are materialized."""

    model_config = ConfigDict(extra="forbid")

    sequence_claims: list[VpaSequenceClaim] = Field(default_factory=list)
    auto_bound_evidence_ids: list[str] = Field(default_factory=list)
    bar_range: str

    @property
    def effective_evidence_ids(self) -> set[str]:
        return set(self.evidence_ids) | set(self.auto_bound_evidence_ids)


class VpaHypothesisAssessment(BaseModel):
    """VPA's evidence relation to one PA hypothesis."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    relation: Relation
    evidence_ids: list[str]
    auto_bound_evidence_ids: list[str] = Field(default_factory=list)
    reason: str

    @property
    def effective_evidence_ids(self) -> set[str]:
        return set(self.evidence_ids) | set(self.auto_bound_evidence_ids)


class VpaHypothesisAssessmentDraft(BaseModel):
    """Provider-facing assessment with whitelist normalization."""

    model_config = ConfigDict(extra="ignore")

    hypothesis_id: str
    relation: Relation
    evidence_ids: list[str]
    reason: str


class VpaNextConfirmation(BaseModel):
    """Future evidence needed to resolve the current VPA interpretation."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["pending_closed_bar", "not_applicable"]
    trigger: str | None
    requires_closed_bar: bool


class VpaNextConfirmationDraft(BaseModel):
    """Provider-facing confirmation with whitelist normalization."""

    model_config = ConfigDict(extra="ignore")

    state: Literal["pending_closed_bar", "not_applicable"]
    trigger: str | None


class VpaEvidenceReportBase(BaseModel):
    """Independent VPA evidence report; it has no order or setup authority."""

    model_config = ConfigDict(extra="forbid")

    catalog_id: str
    data_status: Literal["available", "limited", "unavailable"]
    summary: str
    observations: list[VpaObservation]
    hypothesis_assessments: list[VpaHypothesisAssessment]
    background_relation: Relation
    next_confirmation: VpaNextConfirmation

    @property
    def evidence_ids(self) -> set[str]:
        ids = {
            evidence_id
            for item in self.observations
            for evidence_id in item.effective_evidence_ids
        }
        ids.update(
            evidence_id
            for item in self.hypothesis_assessments
            for evidence_id in item.effective_evidence_ids
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

    @property
    def declared_evidence_ids(self) -> set[str]:
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


class VpaEvidenceReport(VpaEvidenceReportBase):
    """Legacy post-candidate evidence; retained for old jobs and research."""

    entry_timing_relation: Relation


class PredecisionVpaObservationDraft(VpaObservationDraft):
    finding_kind: Literal[
        "background", "effort_result", "sequence_comparison", "acceptance_rejection"
    ]


class PredecisionVpaObservation(VpaObservation):
    finding_kind: Literal[
        "background", "effort_result", "sequence_comparison", "acceptance_rejection"
    ]


class PredecisionVpaEvidenceReport(VpaEvidenceReportBase):
    report_scope: Literal["predecision_vpa_v2"]
    entry_timing_applicability: Literal["not_applicable"]
    observations: list[PredecisionVpaObservation]


def parse_vpa_report(raw: dict[str, Any]) -> VpaEvidenceReportBase:
    """Select only by explicit frozen scope; absent scope retains legacy meaning."""
    if "report_scope" not in raw:
        return VpaEvidenceReport.model_validate(raw)
    if raw.get("report_scope") != PREDECISION_VPA_SCOPE:
        raise ValueError("unsupported VPA report scope")
    return PredecisionVpaEvidenceReport.model_validate(raw)


class VpaEvidenceReportDraft(BaseModel):
    """Provider-facing report without program-owned observation ranges."""

    model_config = ConfigDict(extra="ignore")

    summary: str
    observations: list[VpaObservationDraft]
    hypothesis_assessments: list[VpaHypothesisAssessmentDraft]
    background_relation: Relation
    entry_timing_relation: Relation
    next_confirmation: VpaNextConfirmationDraft


class VpaEvidenceEnvelopeDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    vpa_report: VpaEvidenceReportDraft


class PredecisionVpaEvidenceReportDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str
    observations: list[PredecisionVpaObservationDraft]
    hypothesis_assessments: list[VpaHypothesisAssessmentDraft]
    background_relation: Relation
    next_confirmation: VpaNextConfirmationDraft


class PredecisionVpaEvidenceEnvelopeDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    vpa_report: PredecisionVpaEvidenceReportDraft


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
        "incomplete",
        "review_required",
    ]
    official_path: Literal["pa_only", "joint", "none"]
    requested_vpa_mode: Literal["off", "shadow", "soft"]
    effective_vpa_mode: Literal["off", "shadow", "soft"] | None = None
    degradation_stage: Literal["vpa_evidence", "joint_arbiter"] | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _default_effective_mode(self) -> "RunOutcome":
        if self.effective_vpa_mode is None:
            self.effective_vpa_mode = self.requested_vpa_mode
        return self


class AnalysisRoleStop(BaseModel):
    """Non-decision result for the new pipeline, not a fabricated No Trade Draft.

    The Host validates references against the role's frozen allowlist. This
    envelope cannot carry a candidate, change Stage 1, or authorize a rerun.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal["analysis_role_stop_v1"] = "analysis_role_stop_v1"
    status: Literal["incomplete", "review_required"]
    role: Literal["stage1", "vpa_evidence", "stage2"]
    reason: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class AnalysisRoleStopped(RuntimeError):
    """Backend-neutral non-decision, distinct from provider availability."""

    def __init__(self, stop: AnalysisRoleStop) -> None:
        self.stop = stop
        super().__init__(f"Analysis role {stop.status}: {stop.reason}")


class AnalysisMetric(BaseModel):
    """A measured value does not share the meaning of an estimate or missing data."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    status: Literal["actual", "estimated", "unknown"]
    value: float | None = Field(default=None, ge=0)
    unit: Literal["tokens", "bytes", "milliseconds", "count", "usd"]
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _value_matches_status(self) -> "AnalysisMetric":
        if (self.status == "unknown") != (self.value is None):
            raise ValueError("unknown requires null; actual/estimated require a value")
        return self


VPA_EVIDENCE_SCHEMA: dict[str, Any] = VpaEvidenceEnvelopeDraft.model_json_schema()

PREDECISION_VPA_SCHEMA: dict[str, Any] = PredecisionVpaEvidenceEnvelopeDraft.model_json_schema()

JOINT_DECISION_SCHEMA: dict[str, Any] = JointDecisionProposal.model_json_schema()
