"""Manual-review export helpers for generated dataset candidates."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from .export_jsonl import read_jsonl, write_jsonl
from .schemas import CandidateRecord, ManualReview
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


def _parse_review_bool(value: object, *, case_id: str) -> bool:
    normalised = str(value or "").strip().lower()
    if normalised in {"true", "1", "yes", "y"}:
        return True
    if normalised in {"", "false", "0", "no", "n"}:
        return False
    raise ValueError(
        f"Invalid phase3_pilot_candidate value {value!r} for case_id {case_id!r}; "
        "expected true/false, yes/no, or 1/0"
    )


def read_completed_manual_reviews(path: str | Path) -> dict[str, ManualReview]:
    """Read completed review rows keyed by case ID."""
    path = Path(path)
    reviews: dict[str, ManualReview] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {
            "case_id",
            "manual_decision",
            "manual_reason",
            "required_edits",
            "phase3_pilot_candidate",
        }
        missing_columns = sorted(required - set(reader.fieldnames or []))
        if missing_columns:
            raise ValueError(f"Manual-review CSV is missing required columns: {missing_columns}")

        for line_no, row in enumerate(reader, start=2):
            case_id = (row.get("case_id") or "").strip()
            if not case_id:
                raise ValueError(f"Manual-review CSV line {line_no} has no case_id")
            if case_id in reviews:
                raise ValueError(f"Duplicate manual-review row for case_id {case_id!r}")

            decision = (row.get("manual_decision") or "").strip().lower()
            if decision not in {"keep", "revise", "reject"}:
                raise ValueError(
                    f"Invalid manual_decision {decision!r} for case_id {case_id!r}; "
                    "expected keep, revise, or reject"
                )
            reviews[case_id] = ManualReview(
                manual_decision=decision,
                manual_reason=(row.get("manual_reason") or "").strip(),
                required_edits=(row.get("required_edits") or "").strip(),
                phase3_pilot_candidate=_parse_review_bool(
                    row.get("phase3_pilot_candidate"),
                    case_id=case_id,
                ),
            )
    return reviews


def apply_manual_review(
    *,
    review_input_jsonl: str | Path,
    manual_review_csv: str | Path,
    pilot_output_jsonl: str | Path,
    allow_reviewed_validation_errors: bool = False,
) -> list[CandidateRecord]:
    """Attach completed reviews and write explicitly approved pilot candidates.

    Every input candidate must have exactly one review row. Candidates are
    included only when ``phase3_pilot_candidate`` is explicitly true. Rejected
    candidates are never included, even if that flag was set accidentally.
    """
    records = read_jsonl(review_input_jsonl)
    reviews = read_completed_manual_reviews(manual_review_csv)

    records_by_id: dict[str, CandidateRecord] = {}
    for record in records:
        case_id = record.candidate.case_id
        if case_id in records_by_id:
            raise ValueError(f"Duplicate candidate case_id {case_id!r} in {review_input_jsonl}")
        records_by_id[case_id] = record

    missing_reviews = sorted(set(records_by_id) - set(reviews))
    if missing_reviews:
        raise ValueError(f"Missing manual-review rows for case_ids: {missing_reviews}")
    unknown_reviews = sorted(set(reviews) - set(records_by_id))
    if unknown_reviews:
        raise ValueError(f"Manual-review CSV contains unknown case_ids: {unknown_reviews}")

    selected: list[CandidateRecord] = []
    for record in records:
        review = reviews[record.candidate.case_id]
        reviewed_record = record.model_copy(update={"manual_review": review})
        if review.manual_decision == "reject" or not review.phase3_pilot_candidate:
            continue
        if (
            deterministic_validation_errors(record.candidate)
            and not allow_reviewed_validation_errors
        ):
            continue
        selected.append(reviewed_record)

    write_jsonl(pilot_output_jsonl, selected)
    return selected
