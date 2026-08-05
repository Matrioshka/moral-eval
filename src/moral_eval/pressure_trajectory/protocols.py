"""Minimal dependency protocols used by the trajectory runner."""

from __future__ import annotations

from typing import Protocol, Sequence

from .domain import (
    GenerationSettings,
    GenerationResult,
    Message,
    ModelMetadata,
    NominatedLabelLogits,
    TrajectoryEvent,
)


class ModelBackend(Protocol):
    def generate(
        self, transcript: Sequence[Message], settings: GenerationSettings
    ) -> GenerationResult: ...

    def next_token_label_logits(
        self, transcript: Sequence[Message], labels: Sequence[str]
    ) -> NominatedLabelLogits:
        """Validate labels contextually and return their token IDs and raw logits."""

    def reproducibility_metadata(self) -> ModelMetadata: ...


class EventRecorder(Protocol):
    def append(self, event: TrajectoryEvent) -> None: ...

    def load_events(self, run_id: str) -> list[TrajectoryEvent]: ...

    def run_exists(self, run_id: str) -> bool: ...
