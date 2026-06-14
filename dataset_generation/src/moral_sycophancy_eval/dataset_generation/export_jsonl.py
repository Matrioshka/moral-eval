"""JSONL import/export helpers for generated candidates and Inspect-style items."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from .schemas import CandidateRecord


def write_jsonl(path: str | Path, records: Iterable[CandidateRecord]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(record.model_dump_json(exclude_none=True) + "\n")


def read_jsonl(path: str | Path) -> list[CandidateRecord]:
    records: list[CandidateRecord] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(CandidateRecord.model_validate_json(line))
            except ValidationError as exc:
                raise ValueError(f"Invalid CandidateRecord JSONL at line {line_no}: {exc}") from exc
    return records


def candidate_to_inspect_free_response_item(record: CandidateRecord) -> dict[str, object]:
    """Export a record into a generic Inspect-compatible free-response shape.

    Adjust field names to match behaviour.py if your existing task expects a
    different dataset schema. This function intentionally keeps judgement metadata
    outside the conversation shown to the evaluated model.
    """
    candidate = record.candidate
    return {
        "id": candidate.case_id,
        "input": candidate.to_behaviour_messages()[0]["content"],
        "followups": [turn.followup_prompt for turn in candidate.pressure_turns],
        "metadata": {
            "title": candidate.title,
            "domain": candidate.domain,
            "evidence_quality": candidate.evidence_quality,
            "primary_pressure_type": candidate.primary_pressure_type,
            "target_update_direction": candidate.judgement_envelope.target_update_direction,
            "min_acceptable_scope": candidate.judgement_envelope.min_acceptable_scope.model_dump(),
            "max_acceptable_scope": candidate.judgement_envelope.max_acceptable_scope.model_dump(),
            "residual_blockers": candidate.judgement_envelope.residual_blockers,
            "expected_good_response_features": candidate.judgement_envelope.expected_good_response_features,
            "expected_failure_modes": candidate.judgement_envelope.expected_failure_modes,
            "generation_model": record.generation_model,
            "judge_model": record.judge_model,
            "prompt_version": record.prompt_version,
            "rubric_version": record.rubric_version,
            "qc": record.qc.model_dump() if record.qc else None,
        },
    }


def write_inspect_jsonl(path: str | Path, records: Iterable[CandidateRecord]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(candidate_to_inspect_free_response_item(record), ensure_ascii=False) + "\n")
