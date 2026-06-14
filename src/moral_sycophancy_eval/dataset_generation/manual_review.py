"""Manual-review export helpers for generated dataset candidates."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from .schemas import CandidateRecord
from .validation import deterministic_validation_errors

MANUAL_REVIEW_FIELDS = [
    "case_id",
    "title",
    "domain",
    "evidence_quality",
    "pressure_type",
    "target_update_direction",
    "qc_mean",
    "qc_decision",
    "duplicate_risk",
    "deterministic_validation_errors",
    "baseline_scenario",
    "initial_user_prompt",
    "pressure_turns",
    "residual_blockers",
    "expected_good_response_features",
    "expected_failure_modes",
    "manual_decision",
    "manual_reason",
    "required_edits",
    "phase3_pilot_candidate",
]


def _join(values: Iterable[str]) -> str:
    return " | ".join(str(v).strip() for v in values if str(v).strip())


def record_to_manual_review_row(record: CandidateRecord) -> dict[str, object]:
    candidate = record.candidate
    qc = record.qc
    pressure_turns = "\n\n".join(
        f"T{turn.turn} [{turn.pressure_type}]: {turn.followup_prompt}"
        for turn in candidate.pressure_turns
    )
    return {
        "case_id": candidate.case_id,
        "title": candidate.title,
        "domain": candidate.domain,
        "evidence_quality": candidate.evidence_quality,
        "pressure_type": candidate.primary_pressure_type,
        "target_update_direction": candidate.judgement_envelope.target_update_direction,
        "qc_mean": round(qc.mean_quality_score, 3) if qc else "",
        "qc_decision": qc.decision if qc else "",
        "duplicate_risk": qc.duplicate_risk if qc else "",
        "deterministic_validation_errors": _join(deterministic_validation_errors(candidate)),
        "baseline_scenario": candidate.baseline_scenario,
        "initial_user_prompt": candidate.initial_user_prompt,
        "pressure_turns": pressure_turns,
        "residual_blockers": _join(candidate.judgement_envelope.residual_blockers),
        "expected_good_response_features": _join(candidate.judgement_envelope.expected_good_response_features),
        "expected_failure_modes": _join(candidate.judgement_envelope.expected_failure_modes),
        "manual_decision": "",
        "manual_reason": "",
        "required_edits": "",
        "phase3_pilot_candidate": "",
    }


def write_manual_review_csv(path: str | Path, records: list[CandidateRecord]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MANUAL_REVIEW_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(record_to_manual_review_row(record))


def write_manual_review_jsonl(path: str | Path, records: list[CandidateRecord]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record_to_manual_review_row(record), ensure_ascii=False) + "\n")
