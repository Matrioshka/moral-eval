"""File-based adjudication helpers for Phase 3 dataset candidates."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AdjudicationLabel = Literal["pass", "revise", "reject"]
AdjudicationVerdict = Literal["keep", "revise", "reject"]

ADJUDICATION_CRITERIA = (
    "construct_targeting",
    "evidence_quality_label",
    "pressure_isolation",
    "update_calibration",
    "scope_envelope",
    "ideal_answer_calibration",
    "catastrophic_risk_relevance",
    "scoring_tractability",
    "wording_quality",
)

ADJUDICATION_CONTEXT_FIELDS = [
    "case_id",
    "title",
    "domain",
    "evidence_quality",
    "pressure_type",
    "target_update_direction",
    "baseline_scenario",
    "initial_user_prompt",
    "pressure_turns",
    "residual_blockers",
    "expected_good_response_features",
    "expected_failure_modes",
]

ADJUDICATION_FIELDS = [*ADJUDICATION_CONTEXT_FIELDS]
for _criterion in ADJUDICATION_CRITERIA:
    ADJUDICATION_FIELDS.extend(
        [
            f"{_criterion}_score",
            f"{_criterion}_label",
            f"{_criterion}_rationale",
            f"{_criterion}_required_edits",
        ]
    )
ADJUDICATION_FIELDS.extend(
    [
        "overall_verdict",
        "required_edits",
        "adjudicator_notes",
        "debate_turns",
        "adjudication_version",
        "adjudicated_at_utc",
    ]
)


class AdjudicationCriterionScore(BaseModel):
    """One criterion-level adjudication judgement."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)
    label: AdjudicationLabel
    rationale: str = ""
    required_edits: list[str] = Field(default_factory=list)

    @field_validator("rationale")
    @classmethod
    def strip_rationale(cls, value: str) -> str:
        return value.strip()

    @field_validator("required_edits")
    @classmethod
    def clean_required_edits(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value and value.strip()]


class DebateTurn(BaseModel):
    """One ordered contribution to an optional adjudication debate."""

    model_config = ConfigDict(extra="forbid")

    turn_index: int = Field(ge=1)
    role: str = Field(min_length=1)
    content: str = Field(min_length=1)

    @field_validator("role", "content")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("field cannot be blank")
        return stripped


class CandidateAdjudication(BaseModel):
    """Complete pre-manual-review quality-gate decision for one candidate."""

    model_config = ConfigDict(extra="forbid")

    overall_verdict: AdjudicationVerdict
    pilot_ready_for_manual_review: bool | None = None
    criteria: dict[str, AdjudicationCriterionScore]
    required_edits: list[str] = Field(default_factory=list)
    debate_turns: list[DebateTurn] = Field(default_factory=list)
    adjudicator_notes: str = ""
    adjudication_version: str = "phase3_adjudication_v1"
    adjudicated_at_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @field_validator("criteria")
    @classmethod
    def require_all_criteria(
        cls, values: dict[str, AdjudicationCriterionScore]
    ) -> dict[str, AdjudicationCriterionScore]:
        expected = set(ADJUDICATION_CRITERIA)
        actual = set(values)
        if actual != expected:
            missing = sorted(expected - actual)
            unknown = sorted(actual - expected)
            raise ValueError(
                f"criteria must match the adjudication rubric; missing={missing}, unknown={unknown}"
            )
        return values

    @field_validator("required_edits")
    @classmethod
    def clean_required_edits(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value and value.strip()]

    @field_validator("adjudicator_notes", "adjudication_version", "adjudicated_at_utc")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_verdict_and_readiness(self) -> "CandidateAdjudication":
        rejected = [name for name, score in self.criteria.items() if score.label == "reject"]
        if self.overall_verdict == "keep" and rejected:
            raise ValueError(
                "overall_verdict='keep' is inconsistent with rejected criteria: "
                f"{sorted(rejected)}"
            )
        expected_readiness = self.overall_verdict == "keep"
        if (
            self.pilot_ready_for_manual_review is not None
            and self.pilot_ready_for_manual_review != expected_readiness
        ):
            raise ValueError(
                "pilot_ready_for_manual_review must be derived from overall_verdict"
            )
        self.pilot_ready_for_manual_review = expected_readiness
        return self


class DebateAdjudicationRecord(BaseModel):
    """Case-linked adjudication record suitable for summaries and future judges."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    adjudication: CandidateAdjudication


def _json_list(values: Iterable[Any]) -> str:
    return json.dumps(list(values), ensure_ascii=False)


def make_blank_adjudication_template(candidate_record: Any) -> dict[str, object]:
    """Return one flattened, editable adjudication CSV row."""
    candidate = candidate_record.candidate
    envelope = candidate.judgement_envelope
    row: dict[str, object] = {
        "case_id": candidate.case_id,
        "title": candidate.title,
        "domain": candidate.domain,
        "evidence_quality": candidate.evidence_quality,
        "pressure_type": candidate.primary_pressure_type,
        "target_update_direction": envelope.target_update_direction,
        "baseline_scenario": candidate.baseline_scenario,
        "initial_user_prompt": candidate.initial_user_prompt,
        "pressure_turns": _json_list(
            turn.model_dump(mode="json") for turn in candidate.pressure_turns
        ),
        "residual_blockers": _json_list(envelope.residual_blockers),
        "expected_good_response_features": _json_list(
            envelope.expected_good_response_features
        ),
        "expected_failure_modes": _json_list(envelope.expected_failure_modes),
    }
    for criterion in ADJUDICATION_CRITERIA:
        row[f"{criterion}_score"] = ""
        row[f"{criterion}_label"] = ""
        row[f"{criterion}_rationale"] = ""
        row[f"{criterion}_required_edits"] = "[]"
    row.update(
        {
            "overall_verdict": "",
            "required_edits": "[]",
            "adjudicator_notes": "",
            "debate_turns": "[]",
            "adjudication_version": "phase3_adjudication_v1",
            "adjudicated_at_utc": "",
        }
    )
    return row


def write_adjudication_template(input_jsonl: str | Path, output_csv: str | Path) -> None:
    """Write one blank adjudication row per candidate record."""
    from .export_jsonl import read_jsonl

    records = read_jsonl(input_jsonl)
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ADJUDICATION_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(make_blank_adjudication_template(record))


def _parse_json_list(value: object, *, field: str, case_id: str) -> list[Any]:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {field!r} for case_id {case_id!r}: {exc.msg}"
        ) from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{field!r} for case_id {case_id!r} must be a JSON list")
    return parsed


def read_completed_adjudications(
    path: str | Path,
) -> dict[str, DebateAdjudicationRecord]:
    records: dict[str, DebateAdjudicationRecord] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = set(ADJUDICATION_FIELDS) - set(ADJUDICATION_CONTEXT_FIELDS[1:])
        missing_columns = sorted(required - set(reader.fieldnames or []))
        if missing_columns:
            raise ValueError(f"Adjudication CSV is missing required columns: {missing_columns}")

        for line_no, row in enumerate(reader, start=2):
            case_id = (row.get("case_id") or "").strip()
            if not case_id:
                raise ValueError(f"Adjudication CSV line {line_no} has no case_id")
            if case_id in records:
                raise ValueError(f"Duplicate adjudication row for case_id {case_id!r}")

            criteria: dict[str, AdjudicationCriterionScore] = {}
            for criterion in ADJUDICATION_CRITERIA:
                score_text = (row.get(f"{criterion}_score") or "").strip()
                try:
                    score = int(score_text)
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid {criterion} score {score_text!r} for case_id {case_id!r}"
                    ) from exc
                criteria[criterion] = AdjudicationCriterionScore(
                    score=score,
                    label=(row.get(f"{criterion}_label") or "").strip().lower(),
                    rationale=(row.get(f"{criterion}_rationale") or "").strip(),
                    required_edits=_parse_json_list(
                        row.get(f"{criterion}_required_edits"),
                        field=f"{criterion}_required_edits",
                        case_id=case_id,
                    ),
                )

            adjudication = CandidateAdjudication(
                overall_verdict=(row.get("overall_verdict") or "").strip().lower(),
                criteria=criteria,
                required_edits=_parse_json_list(
                    row.get("required_edits"), field="required_edits", case_id=case_id
                ),
                debate_turns=_parse_json_list(
                    row.get("debate_turns"), field="debate_turns", case_id=case_id
                ),
                adjudicator_notes=(row.get("adjudicator_notes") or "").strip(),
                adjudication_version=(
                    (row.get("adjudication_version") or "").strip()
                    or "phase3_adjudication_v1"
                ),
                adjudicated_at_utc=(
                    (row.get("adjudicated_at_utc") or "").strip()
                    or datetime.now(timezone.utc).isoformat()
                ),
            )
            records[case_id] = DebateAdjudicationRecord(
                case_id=case_id, adjudication=adjudication
            )
    return records


def summarize_adjudications(adjudication_records: Iterable[Any]) -> dict[str, int]:
    """Summarise adjudication verdicts and downstream readiness."""
    adjudications: list[CandidateAdjudication] = []
    for record in adjudication_records:
        if isinstance(record, CandidateAdjudication):
            adjudications.append(record)
        else:
            adjudication = getattr(record, "adjudication", None)
            if adjudication is None:
                raise ValueError("Adjudication summary received a record without adjudication")
            adjudications.append(adjudication)

    counts = Counter(item.overall_verdict for item in adjudications)
    total = len(adjudications)
    keep = counts["keep"]
    revise = counts["revise"]
    reject = counts["reject"]
    return {
        "total": total,
        "keep": keep,
        "revise": revise,
        "reject": reject,
        "retained": keep + revise,
        "excluded": reject,
        "ready_for_manual_review": keep,
        "not_ready_for_manual_review": revise + reject,
    }


def apply_adjudication(
    input_jsonl: str | Path,
    adjudication_csv: str | Path,
    output_jsonl: str | Path,
    *,
    summary_output_path: str | Path | None = None,
) -> list[Any]:
    """Attach adjudications and retain keep/revise candidates for later work."""
    from .export_jsonl import read_jsonl, write_jsonl

    candidates = read_jsonl(input_jsonl)
    adjudications = read_completed_adjudications(adjudication_csv)

    candidates_by_id: dict[str, Any] = {}
    for candidate_record in candidates:
        case_id = candidate_record.candidate.case_id
        if case_id in candidates_by_id:
            raise ValueError(f"Duplicate candidate case_id {case_id!r} in {input_jsonl}")
        candidates_by_id[case_id] = candidate_record

    missing = sorted(set(candidates_by_id) - set(adjudications))
    if missing:
        raise ValueError(f"Missing adjudication rows for case_ids: {missing}")
    unknown = sorted(set(adjudications) - set(candidates_by_id))
    if unknown:
        raise ValueError(f"Adjudication CSV contains unknown case_ids: {unknown}")

    retained: list[Any] = []
    ordered_adjudications: list[DebateAdjudicationRecord] = []
    for candidate_record in candidates:
        case_id = candidate_record.candidate.case_id
        adjudication_record = adjudications[case_id]
        ordered_adjudications.append(adjudication_record)
        if adjudication_record.adjudication.overall_verdict == "reject":
            continue
        retained.append(
            candidate_record.model_copy(
                update={"adjudication": adjudication_record.adjudication}
            )
        )

    write_jsonl(output_jsonl, retained)
    summary = summarize_adjudications(ordered_adjudications)
    summary_path = (
        Path(summary_output_path)
        if summary_output_path is not None
        else Path(output_jsonl).parent / "adjudication_summary.json"
    )
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return retained
