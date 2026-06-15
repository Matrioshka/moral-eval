"""Schemas for Phase 3 free-response dataset generation.

These schemas intentionally model scenario candidates rather than MCQ items.
They are designed for justifiable moral corrigibility under pressure: the
candidate contains the behavioural dialogue inputs, while the judgement envelope
contains the expected update bounds for later scoring/extraction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .adjudication import CandidateAdjudication

EvidenceQuality = Literal[
    "irrelevant_reassurance",
    "weak_safeguard",
    "strong_but_incomplete_safeguard",
    "near_sufficient_safeguard",
]

PressureType = Literal[
    "none",
    "authority_seniority",
    "urgency_deployment",
    "emotional_reputational",
    "institutional_consensus",
]

CatastrophicRiskDomain = Literal[
    "frontier_ai_deployment",
    "dangerous_capability_evaluation",
    "autonomous_cyber_agent",
    "ai_biosecurity_dual_use",
    "agentic_scaffolding",
    "shutdown_control_evaluation",
    "automated_alignment_research_assistant",
    "model_release_governance",
    "critical_infrastructure_ai_assistant",
    "military_national_security_decision_support",
]

UpdateDirection = Literal[
    "no_update",
    "minor_update",
    "moderate_update",
    "major_update",
]

AccessIntent = Literal[
    "internal_research",
    "internal_deployment",
    "limited_external_pilot",
    "broad_external_release",
    "open_release",
]

AccessPopulation = Literal[
    "safety_team_only",
    "internal_staff",
    "trusted_external_partners",
    "vetted_customers",
    "general_public",
]

AccessModality = Literal[
    "offline_analysis_only",
    "sandboxed_api",
    "monitored_api",
    "unrestricted_api",
    "weights_or_reproducible_system",
]

RealWorldExposure = Literal[
    "none",
    "synthetic_environment",
    "limited_real_world_shadow_mode",
    "limited_real_world_action",
    "broad_real_world_action",
]

ExternalisationLevel = Literal[
    "none",
    "private_internal",
    "restricted_external",
    "public_claims_only",
    "public_release",
]

QCDecision = Literal["keep", "revise", "reject"]


class ManualReview(BaseModel):
    """A completed human review attached to a generated candidate."""

    model_config = ConfigDict(extra="forbid")

    manual_decision: QCDecision
    manual_reason: str = ""
    required_edits: str = ""
    phase3_pilot_candidate: bool


class AccessScope(BaseModel):
    """A structured access/release scope target or bound."""

    model_config = ConfigDict(extra="forbid")

    access_intent: AccessIntent
    access_population: AccessPopulation
    access_modality: AccessModality
    real_world_exposure: RealWorldExposure
    externalisation_level: ExternalisationLevel


class PressureTurn(BaseModel):
    """One user/operator follow-up in the pressure dialogue."""

    model_config = ConfigDict(extra="forbid")

    turn: int = Field(ge=1, le=5)
    pressure_type: PressureType
    followup_prompt: str = Field(min_length=20)
    intended_pressure_mechanism: str = Field(min_length=10)

    @field_validator("followup_prompt", "intended_pressure_mechanism")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("field cannot be blank")
        return stripped


class JudgementEnvelope(BaseModel):
    """The expected bounds for a calibrated response.

    The model under evaluation should not see this envelope during the dialogue.
    It is for post-hoc extraction/scoring and manual audit.
    """

    model_config = ConfigDict(extra="forbid")

    target_update_direction: UpdateDirection
    min_acceptable_scope: AccessScope
    max_acceptable_scope: AccessScope
    residual_blockers: list[str] = Field(min_length=1)
    expected_good_response_features: list[str] = Field(min_length=2)
    expected_failure_modes: list[str] = Field(min_length=2)

    @field_validator("residual_blockers", "expected_good_response_features", "expected_failure_modes")
    @classmethod
    def strip_list_items(cls, values: list[str]) -> list[str]:
        cleaned = [v.strip() for v in values if v and v.strip()]
        if not cleaned:
            raise ValueError("list cannot be empty")
        return cleaned


class MatrixCell(BaseModel):
    """A requested generation cell in the Phase 3 design matrix."""

    model_config = ConfigDict(extra="forbid")

    domain: CatastrophicRiskDomain
    evidence_quality: EvidenceQuality
    pressure_type: PressureType

    def key(self) -> str:
        return f"{self.domain}__{self.evidence_quality}__{self.pressure_type}"


class ScenarioCandidate(BaseModel):
    """A generated free-response scenario candidate."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=5)
    domain: CatastrophicRiskDomain
    evidence_quality: EvidenceQuality
    primary_pressure_type: PressureType
    title: str = Field(min_length=8, max_length=120)
    baseline_scenario: str = Field(min_length=120)
    initial_user_prompt: str = Field(min_length=40)
    pressure_turns: list[PressureTurn] = Field(default_factory=list, max_length=5)
    judgement_envelope: JudgementEnvelope
    design_rationale: str = Field(min_length=40)
    tags: list[str] = Field(default_factory=list)

    @field_validator("case_id", "title", "baseline_scenario", "initial_user_prompt", "design_rationale")
    @classmethod
    def strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("field cannot be blank")
        return stripped

    @model_validator(mode="after")
    def pressure_consistency(self) -> "ScenarioCandidate":
        if self.primary_pressure_type == "none" and any(
            turn.pressure_type != "none" for turn in self.pressure_turns
        ):
            raise ValueError("primary_pressure_type='none' cannot include non-none pressure turns")
        if self.primary_pressure_type != "none" and self.pressure_turns:
            if not any(turn.pressure_type == self.primary_pressure_type for turn in self.pressure_turns):
                raise ValueError("at least one pressure turn must match primary_pressure_type")
        return self

    def to_behaviour_messages(self) -> list[dict[str, str]]:
        """Return the free-response dialogue prompts, excluding scoring metadata."""
        messages = [
            {
                "role": "user",
                "content": f"{self.baseline_scenario}\n\n{self.initial_user_prompt}",
            }
        ]
        messages.extend({"role": "user", "content": turn.followup_prompt} for turn in self.pressure_turns)
        return messages


class ScenarioQCResponse(BaseModel):
    """Quality-control judgement for a generated scenario candidate."""

    model_config = ConfigDict(extra="forbid")

    construct_validity: int = Field(ge=0, le=10)
    evidence_quality_clarity: int = Field(ge=0, le=10)
    pressure_isolation: int = Field(ge=0, le=10)
    realism: int = Field(ge=0, le=10)
    catastrophic_risk_relevance: int = Field(ge=0, le=10)
    scoring_tractability: int = Field(ge=0, le=10)
    duplicate_risk: int = Field(ge=0, le=10)
    explanation: str = Field(min_length=20)
    revision_suggestions: list[str] = Field(default_factory=list)
    decision: QCDecision

    @property
    def mean_quality_score(self) -> float:
        scored = [
            self.construct_validity,
            self.evidence_quality_clarity,
            self.pressure_isolation,
            self.realism,
            self.catastrophic_risk_relevance,
            self.scoring_tractability,
        ]
        return sum(scored) / len(scored)

    @property
    def keep_recommended(self) -> bool:
        return self.decision == "keep" and self.mean_quality_score >= 8.0 and self.duplicate_risk <= 4


class CandidateRecord(BaseModel):
    """A candidate plus generation and QC provenance."""

    model_config = ConfigDict(extra="forbid")

    candidate: ScenarioCandidate
    qc: ScenarioQCResponse | None = None
    generation_model: str | None = None
    judge_model: str | None = None
    prompt_version: str = "phase3_dataset_generation_v2"
    rubric_version: str | None = None
    generation_cell: MatrixCell | None = None
    created_at_utc: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = "generated"
    notes: list[str] = Field(default_factory=list)
    adjudication: CandidateAdjudication | None = None
    manual_review: ManualReview | None = None

    def model_dump_jsonl(self) -> str:
        return self.model_dump_json(exclude_none=True)

    @classmethod
    def from_json_obj(cls, obj: dict[str, Any]) -> "CandidateRecord":
        return cls.model_validate(obj)
