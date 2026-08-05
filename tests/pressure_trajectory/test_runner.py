from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from moral_eval.pressure_trajectory.adapters.existing_datasets import load_trajectory
from moral_eval.pressure_trajectory.analysis import analyse_event_log
from moral_eval.pressure_trajectory.domain import (
    GenerationSettings,
    MEASUREMENT_TIMINGS,
    RUNNER_VERSION,
    TOKEN_SELECTION_POLICY,
)
from moral_eval.pressure_trajectory.measurements import (
    MEASUREMENT_VERSION,
    MEASUREMENT_PROMPT_VERSION,
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
FIXTURE = ROOT / "experiments" / "pressure_trajectory_mvp_v1.json"
RESOLUTION_FIXTURE = (
    ROOT / "experiments" / "pressure_trajectory_resolution_control_v1.json"
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
        *(["measurement_recorded"] * 4),
        "baseline_response_generated",
        *(["measurement_recorded"] * 4),
    ]
    for _ in range(3):
        result.extend(
            [
                "pressure_turn_added",
                *(["measurement_recorded"] * 4),
                "model_response_generated",
                *(["measurement_recorded"] * 4),
            ]
        )
    result.append("run_completed")
    return result


def test_runner_measures_before_and_after_generation_without_shadow_leakage(
    tmp_path,
) -> None:
    responses = ["baseline", "round one", "round two", "round three"]
    backend = FakeBackend(responses=responses)
    runner, recorder, loaded = make_runner(tmp_path, backend)

    summary = runner.run()
    events = recorder.load_events("run-test")

    assert summary.status == "completed"
    assert len(summary.checkpoints) == 4
    assert sum(len(item.measurements) for item in summary.checkpoints) == 32
    assert backend.generate_count == 4
    assert backend.measurement_count == 32
    assert [len(transcript) for transcript in backend.generation_transcripts] == [
        1,
        3,
        5,
        7,
    ]
    assert [event.event_type for event in events] == expected_main_event_types()
    assert [event.sequence_number for event in events] == list(range(1, 43))
    assert summary.event_count == 42
    assert all(
        "Reply with exactly A or B" not in message.content
        for transcript in backend.generation_transcripts
        for message in transcript
    )

    for checkpoint_index in range(4):
        measurements = backend.measurement_transcripts[
            checkpoint_index * 8 : (checkpoint_index + 1) * 8
        ]
        assert len(measurements) == 8
        pre_transcripts = measurements[:4]
        post_transcripts = measurements[4:]
        for transcript in pre_transcripts:
            assert responses[checkpoint_index] not in {
                message.content for message in transcript
            }
            assert transcript[-1].role == "user"
        for transcript in post_transcripts:
            assert transcript[-2].role == "assistant"
            assert transcript[-2].content == responses[checkpoint_index]
            assert transcript[-1].role == "user"
            assert MEASUREMENT_PROMPT_VERSION not in transcript[-1].content
        for transcript in measurements:
            for prior_turn in loaded.scenario.pressure_turns[:checkpoint_index]:
                assert prior_turn.user_followup in {
                    message.content for message in transcript if message.role == "user"
                }
        item = summary.checkpoints[checkpoint_index]
        assert tuple(
            result.measurement_timing for result in item.measurements
        ) == (*(["pre_response"] * 4), *(["post_response"] * 4))
        assert {
            result.transcript_sha256 for result in item.measurements[:4]
        }.isdisjoint(
            result.transcript_sha256 for result in item.measurements[4:]
        )

    assert events[0].payload["runner_version"] == RUNNER_VERSION
    assert events[0].payload["measurement_version"] == MEASUREMENT_VERSION
    assert events[0].payload["measurement_timings"] == MEASUREMENT_TIMINGS
    assert events[0].payload["token_selection_policy"] == TOKEN_SELECTION_POLICY
    assert (
        events[0].payload["run_metadata"]["experiment_configuration"]
        ["measurement"]["timings"]
        == MEASUREMENT_TIMINGS
    )
    assert (
        events[0].payload["run_metadata"]["experiment_configuration"]
        ["measurement"]["token_selection_policy"]
        == TOKEN_SELECTION_POLICY
    )
    assert [item.checkpoint.current_evidence_state for item in summary.checkpoints] == [
        "unresolved",
        "unresolved",
        "unresolved",
        "unresolved",
    ]
    analysis = analyse_event_log(recorder.path)
    assert analysis["structural_status"] == "completed"
    assert not analysis["diagnostic_only"]
    assert not analysis["legacy_v1_post_response_only"]
    assert len(analysis["rounds"]) == 4
    assert all(
        set(item["measurements"]) == {"pre_response", "post_response"}
        for item in analysis["rounds"]
    )
    assert all(
        len(item["post_minus_pre_self_anchoring_shift"]) == 4
        for item in analysis["rounds"]
    )
    assert all(
        item["mean_post_minus_pre_self_anchoring_shift"] == 0.0
        for item in analysis["rounds"]
    )
    assert analysis["file_sha256"]


def test_runner_records_generation_completion_metadata(tmp_path) -> None:
    backend = FakeBackend(
        responses=["baseline", "one", "two", "three"],
        scripted_generation_metadata=[
            {
                "generated_token_count": 32,
                "eos_reached": False,
                "max_new_tokens_reached": True,
                "finish_reason": "max_new_tokens",
            },
            *(
                {
                    "generated_token_count": 5,
                    "eos_reached": True,
                    "max_new_tokens_reached": False,
                    "finish_reason": "eos_token",
                }
                for _ in range(3)
            ),
        ],
    )
    runner, recorder, _ = make_runner(tmp_path, backend)

    summary = runner.run()
    baseline = summary.checkpoints[0].generation
    event = next(
        item
        for item in recorder.load_events("run-test")
        if item.event_type == "baseline_response_generated"
    )

    assert baseline.generated_token_count == 32
    assert not baseline.eos_reached
    assert baseline.max_new_tokens_reached
    assert baseline.finish_reason == "max_new_tokens"
    assert baseline.generation_settings.max_new_tokens == 32
    assert event.payload["generation"]["generated_token_count"] == 32


def test_runner_records_run_failed_before_reraising(tmp_path) -> None:
    backend = FakeBackend(responses=["baseline"], fail_generate_call=1)
    runner, recorder, _ = make_runner(tmp_path, backend)

    with pytest.raises(RuntimeError, match="controlled generation failure"):
        runner.run()

    events = recorder.load_events("run-test")
    assert events[-1].event_type == "run_failed"
    assert events[-1].payload["error_type"] == "RuntimeError"
    assert events[-1].payload["error_message"] == "controlled generation failure"
    assert "run_completed" not in {event.event_type for event in events}
    assert [event.event_type for event in events] == [
        "run_created",
        "scenario_loaded",
        *("measurement_recorded" for _ in range(4)),
        "run_failed",
    ]
    assert [event.sequence_number for event in events] == list(
        range(1, len(events) + 1)
    )
    analysis = analyse_event_log(recorder.path)
    assert analysis["structural_status"] == "failed"
    assert analysis["diagnostic_only"]
    assert analysis["terminal_failure"]["error_type"] == "RuntimeError"
    assert analysis["positive_control_qualification"] is None
    assert analysis["rounds"][0]["measurements"]["pre_response"]["complete"]
    assert analysis["rounds"][0]["measurements"]["pre_response"][
        "diagnostic_only"
    ]
    assert "post_response" not in analysis["rounds"][0]["measurements"]
    assert analysis["rounds"][0]["round_complete"] is False
    assert analysis["incomplete_rounds"][0]["round_index"] == 0


def test_final_invariant_failure_is_never_marked_completed(tmp_path) -> None:
    backend = FakeBackend(responses=["baseline", "one", "two", "three"])
    runner, recorder, _ = make_runner(tmp_path, backend)
    runner._measure = lambda checkpoint, timing: ()  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="measurement count"):
        runner.run()

    event_types = [
        event.event_type for event in recorder.load_events("run-test")
    ]
    assert event_types[-1] == "run_failed"
    assert "run_completed" not in event_types


def test_prospective_event_count_is_validated_before_run_completed(tmp_path) -> None:
    backend = FakeBackend(responses=["baseline", "one", "two", "three"])
    runner, recorder, _ = make_runner(tmp_path, backend)
    original_record = runner._record

    def record_with_extra_event(event_type, payload):
        original_record(event_type, payload)
        checkpoint = payload.get("checkpoint")
        if (
            event_type == "model_response_generated"
            and getattr(checkpoint, "round_index", None) == 3
        ):
            original_record("unexpected_test_event", {})

    runner._record = record_with_extra_event  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="Prospective final event count"):
        runner.run()

    events = recorder.load_events("run-test")
    assert events[-1].event_type == "run_failed"
    assert "run_completed" not in {event.event_type for event in events}


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
    assert sum(len(item.measurements) for item in summary.checkpoints) == 16
    assert [item.checkpoint.current_evidence_state for item in summary.checkpoints] == [
        "unresolved",
        "resolved",
    ]
    assert [
        event.event_type for event in recorder.load_events("resolution-run")
    ] == [
        "run_created",
        "scenario_loaded",
        *(["measurement_recorded"] * 4),
        "baseline_response_generated",
        *(["measurement_recorded"] * 4),
        "pressure_turn_added",
        *(["measurement_recorded"] * 4),
        "model_response_generated",
        *(["measurement_recorded"] * 4),
        "run_completed",
    ]
    assert summary.event_count == 22
