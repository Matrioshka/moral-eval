from __future__ import annotations

from datetime import datetime, timezone

import pytest

from moral_eval.pressure_trajectory.events import EventFactory
from moral_eval.pressure_trajectory.storage import JsonlEventRecorder
from scripts.pressure_trajectory import CliError, validate_output_path


def test_jsonl_replay_preserves_event_sequence(tmp_path) -> None:
    ids = iter(("event-1", "event-2"))
    factory = EventFactory(
        "run-1",
        clock=lambda: datetime(2026, 8, 5, tzinfo=timezone.utc),
        id_factory=lambda: next(ids),
    )
    recorder = JsonlEventRecorder(tmp_path / "events.jsonl")
    original = [
        factory.create("run_created", {"value": 1}),
        factory.create("run_completed", {"value": 2}),
    ]
    for event in original:
        recorder.append(event)

    replayed = JsonlEventRecorder(recorder.path).load_events("run-1")

    assert replayed == original
    assert [event.sequence_number for event in replayed] == [1, 2]


def test_existing_log_is_not_silently_overwritten(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("old content\n", encoding="utf-8")
    factory = EventFactory("run-1")

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        JsonlEventRecorder(path).append(factory.create("run_created", {}))

    assert path.read_text(encoding="utf-8") == "old content\n"


def test_event_payload_is_deeply_immutable() -> None:
    event = EventFactory("run-1").create(
        "run_created", {"nested": {"values": [1, 2]}}
    )

    with pytest.raises(TypeError):
        event.payload["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        event.payload["nested"]["new"] = "value"  # type: ignore[index]
    assert event.payload["nested"]["values"] == (1, 2)


def test_cli_output_validation_requires_explicit_overwrite(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("existing run\n", encoding="utf-8")

    with pytest.raises(CliError, match="Refusing to overwrite"):
        validate_output_path(path, overwrite=False)

    validate_output_path(path, overwrite=True)
    assert path.read_text(encoding="utf-8") == "existing run\n"
