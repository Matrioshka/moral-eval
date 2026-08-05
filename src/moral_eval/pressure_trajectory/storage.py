"""Append-only JSONL trajectory event storage."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .domain import TrajectoryEvent
from .events import event_from_record, event_to_record


class JsonlEventRecorder:
    """One append-only JSONL file per run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._active_run_id: str | None = None
        self._last_sequence = 0

    def append(self, event: TrajectoryEvent) -> None:
        if self._active_run_id is None:
            if event.sequence_number != 1 or event.event_type != "run_created":
                raise ValueError("The first event must be run_created with sequence number 1")
            if self.path.exists():
                raise FileExistsError(f"Refusing to overwrite existing event log: {self.path}")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            mode = "x"
            self._active_run_id = event.run_id
        else:
            mode = "a"
            if event.run_id != self._active_run_id:
                raise ValueError("A JSONL recorder cannot contain multiple run IDs")
            if not self.path.exists():
                raise FileNotFoundError("Active event log disappeared during the run")

        if event.sequence_number != self._last_sequence + 1:
            raise ValueError("Event sequence numbers must be contiguous and monotonic")
        line = json.dumps(event_to_record(event), sort_keys=True, ensure_ascii=False)
        with self.path.open(mode, encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._last_sequence = event.sequence_number

    def load_events(self, run_id: str) -> list[TrajectoryEvent]:
        if not self.path.exists():
            return []
        events: list[TrajectoryEvent] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid event JSON on line {line_number}: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"Event line {line_number} must contain an object")
            event = event_from_record(record)
            if event.run_id == run_id:
                events.append(event)
        for expected, event in enumerate(events, start=1):
            if event.sequence_number != expected:
                raise ValueError("Stored event sequence is not contiguous and monotonic")
        return events

    def run_exists(self, run_id: str) -> bool:
        return bool(self.load_events(run_id))
