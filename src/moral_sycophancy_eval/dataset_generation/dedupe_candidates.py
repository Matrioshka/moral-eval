"""Lightweight duplicate and near-duplicate detection.

This is deliberately conservative and dependency-light. For larger candidate
batches, add embedding-based dedupe later, but do not make embeddings a blocker
for the Phase 3 pilot.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .schemas import CandidateRecord, ScenarioCandidate


def normalise_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def candidate_signature(candidate: ScenarioCandidate) -> str:
    blockers = " ".join(candidate.judgement_envelope.residual_blockers[:3])
    return normalise_text(
        " | ".join(
            [
                candidate.domain,
                candidate.evidence_quality,
                candidate.primary_pressure_type,
                candidate.title,
                candidate.baseline_scenario[:600],
                blockers,
            ]
        )
    )


def pairwise_similarity(a: ScenarioCandidate, b: ScenarioCandidate) -> float:
    return SequenceMatcher(None, candidate_signature(a), candidate_signature(b)).ratio()


def flag_near_duplicates(records: list[CandidateRecord], *, threshold: float = 0.86) -> list[tuple[int, int, float]]:
    """Return index pairs whose signatures are suspiciously similar."""
    duplicates: list[tuple[int, int, float]] = []
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            score = pairwise_similarity(records[i].candidate, records[j].candidate)
            if score >= threshold:
                duplicates.append((i, j, score))
    return duplicates


def drop_near_duplicates(records: list[CandidateRecord], *, threshold: float = 0.86) -> list[CandidateRecord]:
    """Keep the first instance of each near-duplicate cluster."""
    drop: set[int] = set()
    for i, j, _score in flag_near_duplicates(records, threshold=threshold):
        if j not in drop:
            drop.add(j)
    return [record for idx, record in enumerate(records) if idx not in drop]
