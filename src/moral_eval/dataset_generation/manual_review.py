"""Manual-review export helpers for generated dataset candidates."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from .adjudication import read_completed_adjudications
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


def _records_by_id(
    records: list[CandidateRecord], *, source: str | Path
) -> dict[str, CandidateRecord]:
    indexed: dict[str, CandidateRecord] = {}
    for record in records:
        case_id = record.candidate.case_id
        if case_id in indexed:
            raise ValueError(f"Duplicate candidate case_id {case_id!r} in {source}")
        indexed[case_id] = record
    return indexed


def prepare_manual_review_inputs(
    *,
    adjudicated_candidates_jsonl: str | Path,
    ready_output_jsonl: str | Path,
    manual_review_template_csv: str | Path,
    unresolved_output_jsonl: str | Path,
    summary_output_json: str | Path,
    revisions_enabled: bool,
    revise_candidates_jsonl: str | Path | None = None,
    revised_candidates_jsonl: str | Path | None = None,
    revised_adjudication_csv: str | Path | None = None,
    adjudicated_revised_candidates_jsonl: str | Path | None = None,
) -> dict[str, int]:
    """Prepare bounded post-adjudication inputs for final manual review."""
    original_records = read_jsonl(adjudicated_candidates_jsonl)
    original_by_id = _records_by_id(
        original_records, source=adjudicated_candidates_jsonl
    )
    ready: list[CandidateRecord] = []
    unresolved: list[CandidateRecord] = []
    original_revise: dict[str, CandidateRecord] = {}

    for record in original_records:
        adjudication = record.adjudication
        case_id = record.candidate.case_id
        if adjudication is None:
            raise ValueError(f"Original candidate {case_id!r} has no adjudication")
        if adjudication.overall_verdict == "keep":
            ready.append(record)
        elif adjudication.overall_verdict == "revise":
            original_revise[case_id] = record
        else:
            raise ValueError(
                f"Original adjudicated input unexpectedly contains rejected candidate {case_id!r}"
            )

    summary = {
        "original_keep_ready": len(ready),
        "original_revise_unresolved": 0,
        "revised_keep_ready": 0,
        "revised_revise_unresolved": 0,
        "revised_reject_excluded": 0,
        "ready_total": 0,
        "unresolved_total": 0,
    }

    if not revisions_enabled:
        unresolved.extend(original_revise.values())
        summary["original_revise_unresolved"] = len(original_revise)
    elif original_revise:
        required_paths = {
            "revise candidates": revise_candidates_jsonl,
            "revised candidates": revised_candidates_jsonl,
            "revised adjudication CSV": revised_adjudication_csv,
            "adjudicated revised candidates": adjudicated_revised_candidates_jsonl,
        }
        missing_paths = [
            label
            for label, path in required_paths.items()
            if path is None or not Path(path).is_file()
        ]
        if missing_paths:
            raise ValueError(
                "Revised work is expected but required artefacts are missing: "
                + ", ".join(missing_paths)
            )

        revise_records = read_jsonl(Path(revise_candidates_jsonl))
        revise_by_id = _records_by_id(
            revise_records, source=Path(revise_candidates_jsonl)
        )
        if set(revise_by_id) != set(original_revise):
            raise ValueError(
                "Revise-candidate IDs do not match original revise IDs; "
                f"missing={sorted(set(original_revise) - set(revise_by_id))}, "
                f"unknown={sorted(set(revise_by_id) - set(original_revise))}"
            )

        revised_records = read_jsonl(Path(revised_candidates_jsonl))
        revised_by_id = _records_by_id(
            revised_records, source=Path(revised_candidates_jsonl)
        )
        if set(revised_by_id) != set(original_revise):
            raise ValueError(
                "Revised-candidate IDs do not match original revise IDs; "
                f"missing={sorted(set(original_revise) - set(revised_by_id))}, "
                f"unknown={sorted(set(revised_by_id) - set(original_revise))}"
            )
        for case_id, record in revised_by_id.items():
            revision = record.revision
            if (
                revision is None
                or revision.original_candidate.case_id != case_id
                or revision.original_adjudication.overall_verdict != "revise"
            ):
                raise ValueError(
                    f"Revised candidate {case_id!r} lacks valid revise provenance"
                )

        completed = read_completed_adjudications(Path(revised_adjudication_csv))
        if set(completed) != set(revised_by_id):
            raise ValueError(
                "Revised adjudication IDs do not match revised candidates; "
                f"missing={sorted(set(revised_by_id) - set(completed))}, "
                f"unknown={sorted(set(completed) - set(revised_by_id))}"
            )

        adjudicated_revised = read_jsonl(Path(adjudicated_revised_candidates_jsonl))
        adjudicated_revised_by_id = _records_by_id(
            adjudicated_revised, source=Path(adjudicated_revised_candidates_jsonl)
        )
        expected_retained_ids = {
            case_id
            for case_id, completed_record in completed.items()
            if completed_record.adjudication.overall_verdict != "reject"
        }
        if set(adjudicated_revised_by_id) != expected_retained_ids:
            raise ValueError(
                "Adjudicated revised candidate IDs do not match non-reject second-pass "
                f"adjudications; missing={sorted(expected_retained_ids - set(adjudicated_revised_by_id))}, "
                f"unknown={sorted(set(adjudicated_revised_by_id) - expected_retained_ids)}"
            )

        for case_id in revised_by_id:
            completed_adjudication = completed[case_id].adjudication
            verdict = completed_adjudication.overall_verdict
            if verdict == "reject":
                summary["revised_reject_excluded"] += 1
                continue
            record = adjudicated_revised_by_id[case_id]
            if record.adjudication is None or (
                record.adjudication.model_dump(mode="json")
                != completed_adjudication.model_dump(mode="json")
            ):
                raise ValueError(
                    "Attached revised adjudication does not match completed CSV for "
                    f"case_id {case_id!r}"
                )
            if record.revision is None:
                raise ValueError(
                    f"Adjudicated revised candidate {case_id!r} lacks revision provenance"
                )
            if verdict == "keep":
                if case_id in original_by_id and case_id not in original_revise:
                    raise ValueError(
                        f"Revised candidate {case_id!r} conflicts with an original ready candidate"
                    )
                ready.append(record)
                summary["revised_keep_ready"] += 1
            else:
                unresolved.append(record)
                summary["revised_revise_unresolved"] += 1

    ready_ids = [record.candidate.case_id for record in ready]
    if len(ready_ids) != len(set(ready_ids)):
        raise ValueError("Combined manual-review input contains duplicate candidate IDs")

    summary["ready_total"] = len(ready)
    summary["unresolved_total"] = len(unresolved)
    write_jsonl(ready_output_jsonl, ready)
    write_manual_review_csv(manual_review_template_csv, ready)
    write_jsonl(unresolved_output_jsonl, unresolved)
    summary_path = Path(summary_output_json)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return summary


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
