"""Trajectory event creation and JSON serialisation."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping
from uuid import uuid4

from .domain import TrajectoryEvent

EVENT_SCHEMA_VERSION = "pressure_trajectory_event_v1"
Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def unique_id() -> str:
    return str(uuid4())


def json_compatible(value: Any) -> Any:
    if is_dataclass(value):
        return {
            item.name: json_compatible(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_compatible(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Value of type {type(value).__name__} is not JSON-compatible")


def immutable_json_value(value: Any) -> Any:
    """Deep-freeze an already JSON-compatible value for immutable event payloads."""
    compatible = json_compatible(value)
    if isinstance(compatible, dict):
        return MappingProxyType(
            {key: immutable_json_value(item) for key, item in compatible.items()}
        )
    if isinstance(compatible, list):
        return tuple(immutable_json_value(item) for item in compatible)
    return compatible


def canonical_json_hash(value: Any) -> str:
    import hashlib

    encoded = json.dumps(
        json_compatible(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class EventFactory:
    def __init__(
        self,
        run_id: str,
        *,
        clock: Clock = utc_now,
        id_factory: IdFactory = unique_id,
    ) -> None:
        self.run_id = run_id
        self.clock = clock
        self.id_factory = id_factory
        self.sequence_number = 0

    def create(self, event_type: str, payload: Mapping[str, Any]) -> TrajectoryEvent:
        timestamp = self.clock()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Event clock must return a timezone-aware datetime")
        self.sequence_number += 1
        return TrajectoryEvent(
            schema_version=EVENT_SCHEMA_VERSION,
            event_id=self.id_factory(),
            run_id=self.run_id,
            timestamp_utc=timestamp.astimezone(timezone.utc).isoformat(),
            event_type=event_type,
            sequence_number=self.sequence_number,
            payload=immutable_json_value(payload),
        )


def event_to_record(event: TrajectoryEvent) -> dict[str, Any]:
    return json_compatible(event)


def event_from_record(record: Mapping[str, Any]) -> TrajectoryEvent:
    required = {
        "schema_version",
        "event_id",
        "run_id",
        "timestamp_utc",
        "event_type",
        "sequence_number",
        "payload",
    }
    if set(record) != required:
        raise ValueError("Trajectory event has unexpected or missing fields")
    if record["schema_version"] != EVENT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported event schema {record['schema_version']!r}")
    if not isinstance(record["payload"], dict):
        raise ValueError("Trajectory event payload must be an object")
    return TrajectoryEvent(
        schema_version=str(record["schema_version"]),
        event_id=str(record["event_id"]),
        run_id=str(record["run_id"]),
        timestamp_utc=str(record["timestamp_utc"]),
        event_type=str(record["event_type"]),
        sequence_number=int(record["sequence_number"]),
        payload=immutable_json_value(record["payload"]),
    )
