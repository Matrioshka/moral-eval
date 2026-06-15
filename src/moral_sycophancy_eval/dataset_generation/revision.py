"""Local revision workflow for adjudicated Phase 3 candidates."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from .adjudication import read_completed_adjudications
from .export_jsonl import read_jsonl, write_jsonl
from .schemas import CandidateRecord, CandidateRevision, PressureTurn

REVISION_FIELDS = [
    "case_id",
    "domain",
    "evidence_quality",
    "pressure_type",
    "title",
    "overall_verdict",
    "consolidated_required_edits",
    "adjudicator_notes",
    "revised_title",
    "revised_baseline_scenario",
    "revised_initial_user_prompt",
    "revised_pressure_turns_json",
    "revision_notes",
]

_PRESSURE_TURNS_ADAPTER = TypeAdapter(list[PressureTurn])


def _revision_row(record: CandidateRecord) -> dict[str, str]:
    adjudication = record.adjudication
    if adjudication is None or adjudication.overall_verdict != "revise":
        raise ValueError(
            f"Candidate {record.candidate.case_id!r} is not marked for revision"
        )
    candidate = record.candidate
    return {
        "case_id": candidate.case_id,
        "domain": candidate.domain,
        "evidence_quality": candidate.evidence_quality,
        "pressure_type": candidate.primary_pressure_type,
        "title": candidate.title,
        "overall_verdict": adjudication.overall_verdict,
        "consolidated_required_edits": json.dumps(
            adjudication.required_edits, ensure_ascii=False
        ),
        "adjudicator_notes": adjudication.adjudicator_notes,
        "revised_title": "",
        "revised_baseline_scenario": "",
        "revised_initial_user_prompt": "",
        "revised_pressure_turns_json": "",
        "revision_notes": "",
    }


def extract_revise_candidates(
    input_jsonl: str | Path,
    revise_output_jsonl: str | Path,
    revision_notes_csv: str | Path,
    *,
    adjudication_csv: str | Path | None = None,
) -> list[CandidateRecord]:
    """Extract revise candidates and write an editable revision worksheet."""
    records = read_jsonl(input_jsonl)
    completed = (
        read_completed_adjudications(adjudication_csv) if adjudication_csv else None
    )

    revise_records: list[CandidateRecord] = []
    seen: set[str] = set()
    for record in records:
        case_id = record.candidate.case_id
        if case_id in seen:
            raise ValueError(f"Duplicate candidate case_id {case_id!r} in {input_jsonl}")
        seen.add(case_id)
        adjudication = record.adjudication
        if adjudication is None:
            raise ValueError(f"Candidate {case_id!r} has no attached adjudication")
        if completed is not None:
            completed_record = completed.get(case_id)
            if completed_record is None:
                raise ValueError(
                    f"Adjudication CSV has no row for candidate {case_id!r}"
                )
            if (
                completed_record.adjudication.model_dump(mode="json")
                != adjudication.model_dump(mode="json")
            ):
                raise ValueError(
                    f"Attached adjudication does not match completed CSV for case_id {case_id!r}"
                )
        if adjudication.overall_verdict == "revise":
            revise_records.append(record)

    write_jsonl(revise_output_jsonl, revise_records)
    notes_path = Path(revision_notes_csv)
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    with notes_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REVISION_FIELDS)
        writer.writeheader()
        for record in revise_records:
            writer.writerow(_revision_row(record))
    return revise_records


def _read_revision_rows(path: str | Path) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = sorted(set(REVISION_FIELDS) - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"Revision-notes CSV is missing required columns: {missing}")
        for line_no, row in enumerate(reader, start=2):
            case_id = (row.get("case_id") or "").strip()
            if not case_id:
                raise ValueError(f"Revision-notes CSV line {line_no} has no case_id")
            if case_id in rows:
                raise ValueError(f"Duplicate revision row for case_id {case_id!r}")
            rows[case_id] = {key: str(value or "") for key, value in row.items()}
    return rows


def _parse_revised_pressure_turns(value: str, *, case_id: str) -> list[PressureTurn] | None:
    text = value.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid revised_pressure_turns_json for case_id {case_id!r}: {exc.msg}"
        ) from exc
    try:
        return _PRESSURE_TURNS_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise ValueError(
            f"Invalid revised pressure turns for case_id {case_id!r}: {exc}"
        ) from exc


def apply_candidate_revisions(
    input_jsonl: str | Path,
    revision_notes_csv: str | Path,
    output_jsonl: str | Path,
) -> list[CandidateRecord]:
    """Apply completed local revisions and clear stale adjudication decisions."""
    records = read_jsonl(input_jsonl)
    rows = _read_revision_rows(revision_notes_csv)

    records_by_id: dict[str, CandidateRecord] = {}
    for record in records:
        case_id = record.candidate.case_id
        if case_id in records_by_id:
            raise ValueError(f"Duplicate candidate case_id {case_id!r} in {input_jsonl}")
        adjudication = record.adjudication
        if adjudication is None or adjudication.overall_verdict != "revise":
            raise ValueError(f"Candidate {case_id!r} is not marked for revision")
        records_by_id[case_id] = record

    missing = sorted(set(records_by_id) - set(rows))
    if missing:
        raise ValueError(f"Missing revision rows for case_ids: {missing}")
    unknown = sorted(set(rows) - set(records_by_id))
    if unknown:
        raise ValueError(f"Revision-notes CSV contains unknown case_ids: {unknown}")

    revised_records: list[CandidateRecord] = []
    for record in records:
        case_id = record.candidate.case_id
        row = rows[case_id]
        if row["overall_verdict"].strip().lower() != "revise":
            raise ValueError(
                f"Revision row for case_id {case_id!r} must retain overall_verdict='revise'"
            )
        revision_notes = row["revision_notes"].strip()
        if not revision_notes:
            raise ValueError(f"Revision row for case_id {case_id!r} has no revision_notes")

        updates: dict[str, object] = {}
        field_map = {
            "revised_title": "title",
            "revised_baseline_scenario": "baseline_scenario",
            "revised_initial_user_prompt": "initial_user_prompt",
        }
        revised_fields: list[str] = []
        for csv_field, candidate_field in field_map.items():
            value = row[csv_field].strip()
            if value and value != getattr(record.candidate, candidate_field):
                updates[candidate_field] = value
                revised_fields.append(candidate_field)

        pressure_turns = _parse_revised_pressure_turns(
            row["revised_pressure_turns_json"], case_id=case_id
        )
        if pressure_turns is not None and pressure_turns != record.candidate.pressure_turns:
            updates["pressure_turns"] = pressure_turns
            revised_fields.append("pressure_turns")

        if not revised_fields:
            raise ValueError(
                f"Revision row for case_id {case_id!r} does not change any candidate fields"
            )

        revised_candidate = record.candidate.model_copy(update=updates)
        # Revalidate cross-field and length constraints after model_copy.
        revised_candidate = type(record.candidate).model_validate(
            revised_candidate.model_dump(mode="json")
        )
        revision = CandidateRevision(
            original_candidate=record.candidate,
            original_adjudication=record.adjudication,
            revised_fields=revised_fields,
            revision_notes=revision_notes,
        )
        revised_records.append(
            record.model_copy(
                update={
                    "candidate": revised_candidate,
                    "adjudication": None,
                    "revision": revision,
                }
            )
        )

    write_jsonl(output_jsonl, revised_records)
    return revised_records
