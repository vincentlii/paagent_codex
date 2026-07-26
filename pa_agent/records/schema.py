"""Pydantic v2 data models for PA Agent records persistence.

Defines the canonical schema for analysis records, followup turns,
alarm payloads, validation errors, and experience entries.
"""

from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RecordMeta(BaseModel):
    """Metadata captured at the moment of analysis submission."""

    model_config = ConfigDict(extra="forbid")

    timestamp_local_iso: str  # Local time ISO string, used for filename
    timestamp_local_ms: int   # Local time in milliseconds
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    symbol: str
    timeframe: str
    bar_count: int
    ai_provider: dict         # Sanitized provider config snapshot (no plaintext API key)
    decision_stance: str = "conservative"  # conservative | balanced | aggressive | extreme_aggressive


class AnalysisRecord(BaseModel):
    """Full record of a two-stage AI analysis run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[5] = 5
    source_schema_version: Optional[int] = None
    meta: RecordMeta
    kline_data: list[dict]              # Same data as sent to AI
    htf_text: str
    htf_context: Optional[dict] = None  # Compact HTF provenance; full block stays in htf_text
    htf_kline_data: list[dict] = Field(default_factory=list)
    stage1_messages: list[dict]
    stage1_response: Optional[dict] = None  # Raw response (includes reasoning_content)
    stage1_diagnosis: Optional[dict] = None
    stage2_messages: list[dict]
    stage2_response: Optional[dict] = None
    stage2_decision: Optional[dict] = None
    pa_baseline_stage2: Optional[dict] = None
    joint_stage2: Optional[dict] = None
    strategy_files_used: list[str]
    experience_loaded: list[dict]
    exception: Optional[dict] = None    # If error occurred: category + debug info
    # Runtime projection for compatibility; JSON persistence derives it from
    # call_attempts and omits this duplicate field.
    usage_total: dict = Field(default_factory=dict, exclude=True)
    validation_audit: list[dict] = Field(default_factory=list)
    call_attempts: list[dict] = Field(default_factory=list)
    pa_hypotheses: Optional[dict] = None
    vpa_catalog: Optional[dict] = None
    vpa_evidence: Optional[dict] = None
    joint_decision: Optional[dict] = None
    role_statuses: dict = Field(default_factory=dict)
    role_runtime: dict = Field(default_factory=dict)
    role_responses: dict = Field(default_factory=dict)
    run_outcome: dict = Field(default_factory=dict)
    data_quality: dict = Field(default_factory=dict)
    snapshot_context: dict = Field(default_factory=dict)
    experiment_context: Optional[dict] = None

    @model_validator(mode="after")
    def derive_runtime_usage(self) -> "AnalysisRecord":
        from pa_agent.records.projection import usage_from_call_attempts

        self.usage_total = usage_from_call_attempts(self.call_attempts)
        return self

    @property
    def vpa_sidecar(self) -> Optional[dict]:
        """Deprecated in-memory UI projection; never persisted in schema v5."""
        from pa_agent.records.projection import gui_vpa_view

        return gui_vpa_view(self)


class FollowupTurn(BaseModel):
    """A single turn in the post-analysis free-chat session."""

    model_config = ConfigDict(extra="forbid")

    turn: int
    ts_ms: int
    user: str
    ai_content: str
    ai_reasoning: Optional[str]
    usage: dict
    cancelled: bool = False


class AlarmPayload(BaseModel):
    """Payload emitted when a JSON validation alarm is triggered (R8.6)."""

    model_config = ConfigDict(extra="forbid")

    category: str                       # 'a'..'e'
    stage: str                          # '阶段一-诊断' or '阶段二-决策'
    timestamp_local_iso: str
    raw_text: str
    parse_position: Optional[str]
    missing_fields: list[str]
    invalid_fields: list[str]
    consecutive_count: int
    history_excerpt: list[dict]


class ValidationError(BaseModel):
    """Structured validation error produced by JsonValidator.

    Note: this is a Pydantic model, not the built-in exception class.
    """

    model_config = ConfigDict(extra="forbid")

    category: str                       # 'a', 'b', 'c', or 'd'
    missing_fields: list[str] = []
    invalid_fields: list[str] = []
    raw_text: str
    parse_position: Optional[str] = None
    allowed_values: dict = {}


class ExperienceEntry(BaseModel):
    """A single entry loaded from the experience library."""

    model_config = ConfigDict(extra="forbid")

    filename: str
    case_type: str                      # 'success' or 'failure'
    cycle_position: str
    timestamp_ms: int
    content: dict                       # Parsed JSON content of the experience file
