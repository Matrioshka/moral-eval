"""Small immutable domain types for pressure-trajectory runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

SemanticRole = Literal["bounded", "broader"]
BoundaryClassification = Literal["bounded_side", "tied", "broader_side"]
MessageRole = Literal["system", "user", "assistant"]
MeasurementTiming = Literal["pre_response", "post_response"]
MEASUREMENT_TIMINGS: tuple[MeasurementTiming, ...] = (
    "pre_response",
    "post_response",
)
TOKEN_SELECTION_POLICY = "canonical_exact_label_v1"
RUNNER_VERSION = "pressure_trajectory_runner_v2"


@dataclass(frozen=True, slots=True)
class Message:
    role: MessageRole
    content: str


@dataclass(frozen=True, slots=True)
class ActionChoice:
    action_id: str
    display_text: str
    semantic_role: SemanticRole


@dataclass(frozen=True, slots=True)
class PressureTurn:
    turn_index: int
    user_followup: str
    pressure_type: str
    pressure_intensity: str | None
    evidence_quality: str
    introduces_gate_relevant_safety_evidence: bool
    provenance: Mapping[str, Any] = field(default_factory=dict)
    resulting_evidence_state: str | None = None


@dataclass(frozen=True, slots=True)
class TrajectoryScenario:
    scenario_id: str
    category: str
    scenario_text: str
    action_choices: tuple[ActionChoice, ...]
    pressure_turns: tuple[PressureTurn, ...]
    source_metadata: Mapping[str, Any]
    initial_evidence: tuple[str, ...]
    initial_evidence_state: str = "unresolved"
    genuine_resolution_metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ConversationCheckpoint:
    checkpoint_id: str
    parent_checkpoint_id: str | None
    round_index: int
    transcript: tuple[Message, ...]
    current_evidence_state: str | None


@dataclass(frozen=True, slots=True)
class OptionMapping:
    mapping_id: str
    bounded_label: str
    broader_label: str
    ordered_action_ids: tuple[str, str]

    def label_for_role(self, role: SemanticRole) -> str:
        return self.bounded_label if role == "bounded" else self.broader_label


@dataclass(frozen=True, slots=True)
class LabelTokenLogit:
    label: str
    token_id: int
    raw_logit: float


@dataclass(frozen=True, slots=True)
class ContextualLabelToken:
    label: str
    token_id: int
    decoded_text: str


@dataclass(frozen=True, slots=True)
class NominatedLabelLogits:
    """Raw next-token logits only for caller-nominated labels."""

    values: tuple[LabelTokenLogit, ...]

    def for_label(self, label: str) -> LabelTokenLogit:
        matches = [value for value in self.values if value.label == label]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one result for label {label!r}")
        return matches[0]


@dataclass(frozen=True, slots=True)
class MeasurementResult:
    measurement_version: str
    measurement_timing: MeasurementTiming
    token_selection_policy: str
    mapping_id: str
    bounded_token_id: int
    broader_token_id: int
    raw_bounded_logit: float
    raw_broader_logit: float
    mapped_margin: float
    restricted_bounded_probability: float
    restricted_broader_probability: float
    boundary_classification: BoundaryClassification
    measurement_prompt_hash: str
    transcript_sha256: str
    checkpoint_id: str


@dataclass(frozen=True, slots=True)
class GenerationSettings:
    max_new_tokens: int = 256
    seed: int = 0
    do_sample: bool = False
    num_beams: int = 1


@dataclass(frozen=True, slots=True)
class GenerationResult:
    response_text: str
    generated_token_count: int
    eos_reached: bool
    max_new_tokens_reached: bool
    finish_reason: str | None
    generation_settings: GenerationSettings


@dataclass(frozen=True, slots=True)
class ModelMetadata:
    model_id: str
    requested_revision: str | None
    resolved_model_revision: str | None
    tokenizer_id: str | None
    resolved_tokenizer_revision: str | None
    device: str
    dtype: str
    software_versions: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class RunMetadata:
    run_id: str
    trajectory_id: str
    experiment_configuration: Mapping[str, Any]
    experiment_configuration_sha256: str
    execution_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TrajectoryEvent:
    schema_version: str
    event_id: str
    run_id: str
    timestamp_utc: str
    event_type: str
    sequence_number: int
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CheckpointSummary:
    checkpoint: ConversationCheckpoint
    generation: GenerationResult
    measurements: tuple[MeasurementResult, ...]

    @property
    def response(self) -> str:
        """Compatibility convenience for callers that only need response text."""
        return self.generation.response_text


@dataclass(frozen=True, slots=True)
class TrajectoryRunSummary:
    run_id: str
    scenario_id: str
    status: Literal["completed"]
    checkpoints: tuple[CheckpointSummary, ...]
    event_count: int
