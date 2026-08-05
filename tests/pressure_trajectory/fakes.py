from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from moral_eval.pressure_trajectory.domain import (
    GenerationSettings,
    GenerationResult,
    LabelTokenLogit,
    Message,
    ModelMetadata,
    NominatedLabelLogits,
    RunMetadata,
    RUNNER_VERSION,
    MEASUREMENT_TIMINGS,
    TOKEN_SELECTION_POLICY,
)
from moral_eval.pressure_trajectory.measurements import (
    INITIAL_PROMPT_VERSION,
    MEASUREMENT_VERSION,
    MEASUREMENT_PROMPT_VERSION,
)
from moral_eval.pressure_trajectory.provenance import (
    build_experiment_configuration,
    experiment_configuration_sha256,
)


@dataclass
class FakeBackend:
    responses: list[str]
    scripted_logits: list[dict[str, float]] | None = None
    scripted_generation_metadata: list[dict[str, object]] | None = None
    fail_generate_call: int | None = None

    def __post_init__(self) -> None:
        self.generation_transcripts: list[tuple[Message, ...]] = []
        self.measurement_transcripts: list[tuple[Message, ...]] = []
        self.generate_count = 0
        self.measurement_count = 0

    def generate(
        self, transcript: Sequence[Message], settings: GenerationSettings
    ) -> GenerationResult:
        self.generate_count += 1
        self.generation_transcripts.append(tuple(transcript))
        if self.fail_generate_call == self.generate_count:
            raise RuntimeError("controlled generation failure")
        response = self.responses[self.generate_count - 1]
        metadata = (
            self.scripted_generation_metadata[self.generate_count - 1]
            if self.scripted_generation_metadata is not None
            else {}
        )
        finish_reason = metadata.get("finish_reason", "eos_token")
        return GenerationResult(
            response_text=response,
            generated_token_count=int(
                metadata.get("generated_token_count", len(response.split()))
            ),
            eos_reached=bool(metadata.get("eos_reached", True)),
            max_new_tokens_reached=bool(
                metadata.get("max_new_tokens_reached", False)
            ),
            finish_reason=(
                str(finish_reason) if finish_reason is not None else None
            ),
            generation_settings=settings,
        )

    def next_token_label_logits(
        self, transcript: Sequence[Message], labels: Sequence[str]
    ) -> NominatedLabelLogits:
        self.measurement_transcripts.append(tuple(transcript))
        index = self.measurement_count
        self.measurement_count += 1
        logits = (
            self.scripted_logits[index]
            if self.scripted_logits is not None
            else {"A": 1.0, "B": 2.0}
        )
        return NominatedLabelLogits(
            values=tuple(
                LabelTokenLogit(label, ord(label), float(logits[label]))
                for label in labels
            )
        )

    def reproducibility_metadata(self) -> ModelMetadata:
        return ModelMetadata(
            model_id="fake/model",
            requested_revision="fake-revision",
            resolved_model_revision="fake-revision",
            tokenizer_id="fake/tokenizer",
            resolved_tokenizer_revision="fake-revision",
            device="cpu",
            dtype="float32",
            software_versions={"python": "test", "backend": "fake-v1"},
        )


def make_run_metadata(
    loaded: object,
    settings: GenerationSettings,
    *,
    run_id: str,
) -> RunMetadata:
    configuration = build_experiment_configuration(
        scenario=loaded.scenario,
        option_mappings=loaded.option_mappings,
        model_id="fake/model",
        requested_model_revision="fake-revision",
        tokenizer_id="fake/tokenizer",
        requested_tokenizer_revision="fake-revision",
        generation_settings=settings,
        generation_prompt_version=INITIAL_PROMPT_VERSION,
        runner_version=RUNNER_VERSION,
        measurement_version=MEASUREMENT_VERSION,
        requested_device="cpu",
        requested_dtype="float32",
        measurement_prompt_version=MEASUREMENT_PROMPT_VERSION,
        measurement_timings=MEASUREMENT_TIMINGS,
        token_selection_policy=TOKEN_SELECTION_POLICY,
        backend_implementation={"name": "fake", "version": "v1"},
    )
    return RunMetadata(
        run_id=run_id,
        trajectory_id=str(loaded.trajectory_metadata["trajectory_id"]),
        experiment_configuration=configuration,
        experiment_configuration_sha256=experiment_configuration_sha256(
            configuration
        ),
        execution_metadata={"test": True},
    )
