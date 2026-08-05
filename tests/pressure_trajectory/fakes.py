from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from moral_eval.pressure_trajectory.domain import (
    GenerationSettings,
    LabelTokenLogit,
    Message,
    ModelMetadata,
    NominatedLabelLogits,
    RunMetadata,
    TOKEN_SELECTION_POLICY,
)
from moral_eval.pressure_trajectory.measurements import (
    MEASUREMENT_PROMPT_VERSION,
    MEASUREMENT_TIMING,
)
from moral_eval.pressure_trajectory.provenance import (
    build_experiment_configuration,
    experiment_configuration_sha256,
)


@dataclass
class FakeBackend:
    responses: list[str]
    scripted_logits: list[dict[str, float]] | None = None
    fail_generate_call: int | None = None

    def __post_init__(self) -> None:
        self.generation_transcripts: list[tuple[Message, ...]] = []
        self.measurement_transcripts: list[tuple[Message, ...]] = []
        self.generate_count = 0
        self.measurement_count = 0

    def generate(
        self, transcript: Sequence[Message], settings: GenerationSettings
    ) -> str:
        del settings
        self.generate_count += 1
        self.generation_transcripts.append(tuple(transcript))
        if self.fail_generate_call == self.generate_count:
            raise RuntimeError("controlled generation failure")
        return self.responses[self.generate_count - 1]

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
        requested_device="cpu",
        requested_dtype="float32",
        measurement_prompt_version=MEASUREMENT_PROMPT_VERSION,
        measurement_timing=MEASUREMENT_TIMING,
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
