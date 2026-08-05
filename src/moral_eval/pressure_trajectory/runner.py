"""Deterministic orchestration of cumulative pressure trajectories."""

from __future__ import annotations

from typing import Sequence

from .domain import (
    CheckpointSummary,
    ConversationCheckpoint,
    GenerationSettings,
    MEASUREMENT_TIMINGS,
    MeasurementResult,
    MeasurementTiming,
    Message,
    OptionMapping,
    RunMetadata,
    TrajectoryRunSummary,
    TrajectoryScenario,
    TOKEN_SELECTION_POLICY,
    RUNNER_VERSION,
)
from .events import Clock, EventFactory, IdFactory
from .measurements import (
    INITIAL_PROMPT_VERSION,
    MEASUREMENT_VERSION,
    MEASUREMENT_PROMPT_VERSION,
    build_initial_user_prompt,
    measure_checkpoint,
    sha256_text,
)
from .provenance import (
    experiment_configuration_sha256,
    validate_experiment_configuration,
)
from .protocols import EventRecorder, ModelBackend


class TrajectoryRunner:
    def __init__(
        self,
        *,
        scenario: TrajectoryScenario,
        backend: ModelBackend,
        recorder: EventRecorder,
        option_mappings: Sequence[OptionMapping],
        run_metadata: RunMetadata,
        generation_settings: GenerationSettings,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        if not option_mappings:
            raise ValueError("At least one option mapping is required")
        if len({mapping.mapping_id for mapping in option_mappings}) != len(option_mappings):
            raise ValueError("Option mapping IDs must be unique")
        self.scenario = scenario
        self.backend = backend
        self.recorder = recorder
        self.option_mappings = tuple(option_mappings)
        self.run_metadata = run_metadata
        self.generation_settings = generation_settings
        factory_kwargs = {}
        if clock is not None:
            factory_kwargs["clock"] = clock
        if id_factory is not None:
            factory_kwargs["id_factory"] = id_factory
        self.events = EventFactory(run_metadata.run_id, **factory_kwargs)

    def _record(self, event_type: str, payload: dict[str, object]) -> None:
        self.recorder.append(self.events.create(event_type, payload))

    def _measure(
        self,
        checkpoint: ConversationCheckpoint,
        measurement_timing: MeasurementTiming,
    ) -> tuple[MeasurementResult, ...]:
        results: list[MeasurementResult] = []
        for mapping in self.option_mappings:
            result = measure_checkpoint(
                backend=self.backend,
                scenario=self.scenario,
                checkpoint=checkpoint,
                mapping=mapping,
                measurement_timing=measurement_timing,
            )
            results.append(result)
            self._record("measurement_recorded", {"measurement": result})
        return tuple(results)

    def run(self) -> TrajectoryRunSummary:
        if self.recorder.run_exists(self.run_metadata.run_id):
            raise FileExistsError(f"Run already exists: {self.run_metadata.run_id}")
        calculated_hash = experiment_configuration_sha256(
            self.run_metadata.experiment_configuration
        )
        if calculated_hash != self.run_metadata.experiment_configuration_sha256:
            raise ValueError("Run metadata experiment configuration hash does not match")
        if self.run_metadata.trajectory_id != self.scenario.source_metadata.get(
            "trajectory_id"
        ):
            raise ValueError("Run metadata trajectory ID does not match the scenario")
        validate_experiment_configuration(
            self.run_metadata.experiment_configuration,
            scenario=self.scenario,
            option_mappings=self.option_mappings,
            generation_settings=self.generation_settings,
            generation_prompt_version=INITIAL_PROMPT_VERSION,
            runner_version=RUNNER_VERSION,
            measurement_version=MEASUREMENT_VERSION,
            measurement_prompt_version=MEASUREMENT_PROMPT_VERSION,
            measurement_timings=MEASUREMENT_TIMINGS,
            token_selection_policy=TOKEN_SELECTION_POLICY,
        )

        run_created = False
        checkpoint_summaries: list[CheckpointSummary] = []
        try:
            model_metadata = self.backend.reproducibility_metadata()
            configured_model = self.run_metadata.experiment_configuration["model"]
            if model_metadata.model_id != configured_model["model_id"]:
                raise ValueError(
                    "Backend model ID does not match the experiment configuration"
                )
            if (
                model_metadata.requested_revision
                != configured_model["requested_model_revision"]
            ):
                raise ValueError(
                    "Backend requested revision does not match the experiment "
                    "configuration"
                )
            initial_prompt = build_initial_user_prompt(self.scenario)
            self._record(
                "run_created",
                {
                    "run_metadata": self.run_metadata,
                    "model_runtime": model_metadata,
                    "initial_prompt_version": INITIAL_PROMPT_VERSION,
                    "initial_prompt_sha256": sha256_text(initial_prompt),
                    "runner_version": RUNNER_VERSION,
                    "measurement_version": MEASUREMENT_VERSION,
                    "measurement_prompt_version": MEASUREMENT_PROMPT_VERSION,
                    "measurement_timings": MEASUREMENT_TIMINGS,
                    "token_selection_policy": TOKEN_SELECTION_POLICY,
                },
            )
            run_created = True
            self._record(
                "scenario_loaded",
                {
                    "scenario": self.scenario,
                    "option_mappings": self.option_mappings,
                },
            )

            transcript: tuple[Message, ...] = (
                Message(role="user", content=initial_prompt),
            )
            checkpoint_id = f"{self.run_metadata.run_id}:checkpoint:0"
            pre_checkpoint = ConversationCheckpoint(
                checkpoint_id=checkpoint_id,
                parent_checkpoint_id=None,
                round_index=0,
                transcript=transcript,
                current_evidence_state=self.scenario.initial_evidence_state,
            )
            pre_measurements = self._measure(pre_checkpoint, "pre_response")
            generation = self.backend.generate(transcript, self.generation_settings)
            transcript = (
                *transcript,
                Message(role="assistant", content=generation.response_text),
            )
            checkpoint = ConversationCheckpoint(
                checkpoint_id=checkpoint_id,
                parent_checkpoint_id=None,
                round_index=0,
                transcript=transcript,
                current_evidence_state=self.scenario.initial_evidence_state,
            )
            self._record(
                "baseline_response_generated",
                {
                    "response": generation.response_text,
                    "generation": generation,
                    "checkpoint": checkpoint,
                },
            )
            post_measurements = self._measure(checkpoint, "post_response")
            checkpoint_summaries.append(
                CheckpointSummary(
                    checkpoint,
                    generation,
                    (*pre_measurements, *post_measurements),
                )
            )

            for turn in self.scenario.pressure_turns:
                self._record(
                    "pressure_turn_added",
                    {
                        "turn": turn,
                        "parent_checkpoint_id": checkpoint.checkpoint_id,
                    },
                )
                transcript = (
                    *checkpoint.transcript,
                    Message(role="user", content=turn.user_followup),
                )
                checkpoint_id = (
                    f"{self.run_metadata.run_id}:checkpoint:{turn.turn_index}"
                )
                evidence_state = (
                    turn.resulting_evidence_state
                    or checkpoint.current_evidence_state
                )
                pre_checkpoint = ConversationCheckpoint(
                    checkpoint_id=checkpoint_id,
                    parent_checkpoint_id=checkpoint.checkpoint_id,
                    round_index=turn.turn_index,
                    transcript=transcript,
                    current_evidence_state=evidence_state,
                )
                pre_measurements = self._measure(
                    pre_checkpoint, "pre_response"
                )
                generation = self.backend.generate(
                    transcript, self.generation_settings
                )
                transcript = (
                    *transcript,
                    Message(role="assistant", content=generation.response_text),
                )
                checkpoint = ConversationCheckpoint(
                    checkpoint_id=checkpoint_id,
                    parent_checkpoint_id=pre_checkpoint.parent_checkpoint_id,
                    round_index=turn.turn_index,
                    transcript=transcript,
                    current_evidence_state=evidence_state,
                )
                self._record(
                    "model_response_generated",
                    {
                        "response": generation.response_text,
                        "generation": generation,
                        "checkpoint": checkpoint,
                    },
                )
                post_measurements = self._measure(
                    checkpoint, "post_response"
                )
                checkpoint_summaries.append(
                    CheckpointSummary(
                        checkpoint,
                        generation,
                        (*pre_measurements, *post_measurements),
                    )
                )

            expected_checkpoint_count = 1 + len(self.scenario.pressure_turns)
            expected_measurement_count = (
                expected_checkpoint_count
                * len(self.option_mappings)
                * len(MEASUREMENT_TIMINGS)
            )
            actual_measurement_count = sum(
                len(item.measurements) for item in checkpoint_summaries
            )
            expected_measurement_sequence = tuple(
                (timing, mapping.mapping_id)
                for timing in MEASUREMENT_TIMINGS
                for mapping in self.option_mappings
            )
            if len(checkpoint_summaries) != expected_checkpoint_count:
                raise RuntimeError("Final checkpoint count violates runner invariant")
            if actual_measurement_count != expected_measurement_count:
                raise RuntimeError("Final measurement count violates runner invariant")
            if any(
                tuple(
                    (result.measurement_timing, result.mapping_id)
                    for result in item.measurements
                )
                != expected_measurement_sequence
                for item in checkpoint_summaries
            ):
                raise RuntimeError(
                    "Final timing/mapping sequence violates runner invariant"
                )

            fixture_checkpoint_count = self.scenario.source_metadata.get(
                "expected_checkpoint_count"
            )
            fixture_measurement_count = self.scenario.source_metadata.get(
                "expected_measurement_count"
            )
            fixture_event_count = self.scenario.source_metadata.get(
                "expected_event_count"
            )
            if fixture_checkpoint_count != expected_checkpoint_count:
                raise RuntimeError(
                    "Final checkpoint count differs from the fixture expectation"
                )
            if fixture_measurement_count != expected_measurement_count:
                raise RuntimeError(
                    "Final measurement count differs from the fixture expectation"
                )
            prospective_final_event_count = self.events.sequence_number + 1
            if fixture_event_count != prospective_final_event_count:
                raise RuntimeError(
                    "Prospective final event count differs from the fixture "
                    "expectation"
                )
            self._record(
                "run_completed",
                {
                    "checkpoint_count": len(checkpoint_summaries),
                    "measurement_count": actual_measurement_count,
                    "event_count": prospective_final_event_count,
                },
            )
            return TrajectoryRunSummary(
                run_id=self.run_metadata.run_id,
                scenario_id=self.scenario.scenario_id,
                status="completed",
                checkpoints=tuple(checkpoint_summaries),
                event_count=self.events.sequence_number,
            )
        except Exception as exc:
            if run_created:
                try:
                    self._record(
                        "run_failed",
                        {
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                            "completed_checkpoint_count": len(checkpoint_summaries),
                        },
                    )
                except Exception:
                    pass
            raise
