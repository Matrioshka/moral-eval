from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from moral_eval.pressure_trajectory.adapters.existing_datasets import load_trajectory
from moral_eval.pressure_trajectory.domain import GenerationSettings
from moral_eval.pressure_trajectory.measurements import (
    MEASUREMENT_PROMPT_VERSION,
    MEASUREMENT_TIMING,
)
from moral_eval.pressure_trajectory.runner import TrajectoryRunner
from moral_eval.pressure_trajectory.storage import JsonlEventRecorder

from .fakes import FakeBackend, make_run_metadata

ROOT = Path(__file__).resolve().parents[2]
DATASET = (
    ROOT
    / "data"
    / "datasets"
    / "action_logprob"
    / "action_logprob_positive_control_gate_v0.jsonl"
)
FIXTURE = ROOT / "experiments" / "pressure_trajectory_mvp_v0.json"
RESOLUTION_FIXTURE = (
    ROOT / "experiments" / "pressure_trajectory_resolution_control_v0.json"
)
CASE_ID = "deployment_gate__autonomous_cyber_defence_pilot_001"


def loaded_main():
    return load_trajectory(
        dataset_path=DATASET,
        case_id=CASE_ID,
        fixture_path=FIXTURE,
    )


def make_runner(tmp_path, backend: FakeBackend):
    loaded = loaded_main()
    event_numbers = iter(range(1, 100))
    recorder = JsonlEventRecorder(tmp_path / "events.jsonl")
    settings = GenerationSettings(max_new_tokens=32, seed=7)
    runner = TrajectoryRunner(
        scenario=loaded.scenario,
        backend=backend,
        recorder=recorder,
        option_mappings=loaded.option_mappings,
        run_metadata=make_run_metadata(loaded, settings, run_id="run-test"),
        generation_settings=settings,
        clock=lambda: datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        id_factory=lambda: f"event-{next(event_numbers)}",
    )
    return runner, recorder, loaded


def expected_main_event_types() -> list[str]:
    result = [
        "run_created",
        "scenario_loaded",
        "baseline_response_generated",
        *(["measurement_recorded"] * 4),
    ]
    for _ in range(3):
        result.extend(
            [
                "pressure_turn_added",
                "model_response_generated",
                *(["measurement_recorded"] * 4),
            ]
        )
    result.append("run_completed")
    return result


def test_runner_accumulates_pressure_and_measures_post_response(tmp_path) -> None:
    responses = ["baseline", "round one", "round two", "round three"]
    backend = FakeBackend(responses=responses)
    runner, recorder, loaded = make_runner(tmp_path, backend)

    summary = runner.run()
    events = recorder.load_events("run-test")

    assert summary.status == "completed"
    assert len(summary.checkpoints) == 4
    assert sum(len(item.measurements) for item in summary.checkpoints) == 16
    assert backend.generate_count == 4
    assert backend.measurement_count == 16
    assert [len(transcript) for transcript in backend.generation_transcripts] == [
        1,
        3,
        5,
        7,
    ]
    assert [event.event_type for event in events] == expected_main_event_types()
    assert [event.sequence_number for event in events] == list(range(1, 27))
    assert summary.event_count == 26

    for checkpoint_index in range(4):
        measurements = backend.measurement_transcripts[
            checkpoint_index * 4 : (checkpoint_index + 1) * 4
        ]
        assert len(measurements) == 4
        for transcript in measurements:
            assert transcript[-2].role == "assistant"
            assert transcript[-2].content == responses[checkpoint_index]
            assert transcript[-1].role == "user"
            assert MEASUREMENT_PROMPT_VERSION not in transcript[-1].content
            for prior_turn in loaded.scenario.pressure_turns[:checkpoint_index]:
                assert prior_turn.user_followup in {
                    message.content for message in transcript if message.role == "user"
                }

    assert all(
        result.measurement_timing == MEASUREMENT_TIMING
        for item in summary.checkpoints
        for result in item.measurements
    )
    assert events[0].payload["measurement_timing"] == "post_response"
    assert (
        events[0].payload["run_metadata"]["experiment_configuration"]
        ["measurement"]["timing"]
        == "post_response"
    )
    assert [item.checkpoint.current_evidence_state for item in summary.checkpoints] == [
        "unresolved",
        "unresolved",
        "unresolved",
        "unresolved",
    ]


def test_runner_records_run_failed_before_reraising(tmp_path) -> None:
    backend = FakeBackend(responses=["baseline"], fail_generate_call=2)
    runner, recorder, _ = make_runner(tmp_path, backend)

    with pytest.raises(RuntimeError, match="controlled generation failure"):
        runner.run()

    events = recorder.load_events("run-test")
    assert events[-1].event_type == "run_failed"
    assert events[-1].payload["error_type"] == "RuntimeError"
    assert events[-1].payload["error_message"] == "controlled generation failure"
    assert "run_completed" not in {event.event_type for event in events}
    assert [event.sequence_number for event in events] == list(
        range(1, len(events) + 1)
    )


def test_final_invariant_failure_is_never_marked_completed(tmp_path) -> None:
    backend = FakeBackend(responses=["baseline", "one", "two", "three"])
    runner, recorder, _ = make_runner(tmp_path, backend)
    runner._measure = lambda checkpoint: ()  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="measurement count"):
        runner.run()

    event_types = [
        event.event_type for event in recorder.load_events("run-test")
    ]
    assert event_types[-1] == "run_failed"
    assert "run_completed" not in event_types


def test_separate_resolution_control_has_exact_sequence_and_counts(tmp_path) -> None:
    loaded = load_trajectory(
        dataset_path=DATASET,
        case_id=CASE_ID,
        fixture_path=RESOLUTION_FIXTURE,
    )
    backend = FakeBackend(responses=["baseline", "resolved response"])
    recorder = JsonlEventRecorder(tmp_path / "resolution.jsonl")
    event_numbers = iter(range(1, 100))
    settings = GenerationSettings()
    summary = TrajectoryRunner(
        scenario=loaded.scenario,
        backend=backend,
        recorder=recorder,
        option_mappings=loaded.option_mappings,
        run_metadata=make_run_metadata(
            loaded, settings, run_id="resolution-run"
        ),
        generation_settings=settings,
        clock=lambda: datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        id_factory=lambda: f"resolution-event-{next(event_numbers)}",
    ).run()

    assert len(summary.checkpoints) == 2
    assert sum(len(item.measurements) for item in summary.checkpoints) == 8
    assert [item.checkpoint.current_evidence_state for item in summary.checkpoints] == [
        "unresolved",
        "resolved",
    ]
    assert [
        event.event_type for event in recorder.load_events("resolution-run")
    ] == [
        "run_created",
        "scenario_loaded",
        "baseline_response_generated",
        *(["measurement_recorded"] * 4),
        "pressure_turn_added",
        "model_response_generated",
        *(["measurement_recorded"] * 4),
        "run_completed",
    ]
    assert summary.event_count == 14
