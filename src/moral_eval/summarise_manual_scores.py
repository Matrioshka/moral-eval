"""Summarise manual behavioural audit scores.

This script reads a machine-readable manual scoring CSV and prints Markdown
summary tables for model totals, follow-up strength, moral domain, and failure
classes.

Example:

    python src/moral_eval/summarise_manual_scores.py \
        docs/failure_audits/v3_behaviour_evidence_strength_v1_manual_scores.csv

Optionally write the Markdown summary to a file:

    python src/moral_eval/summarise_manual_scores.py \
        docs/failure_audits/v3_behaviour_evidence_strength_v1_manual_scores.csv \
        --md docs/failure_audits/v3_behaviour_evidence_strength_v1_score_summary.md
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MAX_SCORE_PER_SAMPLE = 3


@dataclass(frozen=True)
class ScoreRow:
    dataset_version: str
    model: str
    provider: str
    sample_id: str
    case_id: str
    moral_domain: str
    followup_strength: str
    score: int
    failure_class: str


def read_scores(path: Path) -> list[ScoreRow]:
    rows: list[ScoreRow] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = {
            "dataset_version",
            "model",
            "provider",
            "sample_id",
            "case_id",
            "moral_domain",
            "followup_strength",
            "score_0_to_3",
            "failure_class",
        }
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} is missing required columns: {sorted(missing)}")

        for line_no, row in enumerate(reader, start=2):
            try:
                score = int(row["score_0_to_3"])
            except ValueError as exc:
                raise ValueError(
                    f"Invalid score on line {line_no}: {row['score_0_to_3']!r}"
                ) from exc

            if score < 0 or score > MAX_SCORE_PER_SAMPLE:
                raise ValueError(f"Score out of range on line {line_no}: {score}")

            rows.append(
                ScoreRow(
                    dataset_version=row["dataset_version"],
                    model=row["model"],
                    provider=row["provider"],
                    sample_id=row["sample_id"],
                    case_id=row["case_id"],
                    moral_domain=row["moral_domain"],
                    followup_strength=row["followup_strength"],
                    score=score,
                    failure_class=row["failure_class"] or "none",
                )
            )

    if not rows:
        raise ValueError(f"{path} contains no score rows")

    return rows


def md_table(headers: list[str], rows: Iterable[list[object]]) -> str:
    row_list = [[str(cell) for cell in row] for row in rows]
    out = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    out.extend("| " + " | ".join(row) + " |" for row in row_list)
    return "\n".join(out)


def model_summary(rows: list[ScoreRow]) -> str:
    by_model: dict[str, list[ScoreRow]] = defaultdict(list)
    for row in rows:
        by_model[row.model].append(row)

    table_rows = []
    for model in sorted(by_model):
        model_rows = by_model[model]
        total = sum(row.score for row in model_rows)
        max_total = len(model_rows) * MAX_SCORE_PER_SAMPLE
        counts = Counter(row.score for row in model_rows)
        failures = Counter(
            row.failure_class
            for row in model_rows
            if row.failure_class and row.failure_class != "none"
        )
        main_failure = failures.most_common(1)[0][0] if failures else "none"
        table_rows.append(
            [
                model,
                f"{total}/{max_total}",
                f"{total / len(model_rows):.2f}",
                counts[3],
                counts[2],
                counts[1],
                counts[0],
                main_failure,
            ]
        )

    return md_table(
        ["Model", "Total score", "Mean score", "3s", "2s", "1s", "0s", "Main failure class"],
        table_rows,
    )


def grouped_summary(rows: list[ScoreRow], group_field: str) -> str:
    groups: dict[tuple[str, str], list[ScoreRow]] = defaultdict(list)
    for row in rows:
        key = getattr(row, group_field)
        groups[(row.model, key)].append(row)

    table_rows = []
    for (model, key), group_rows in sorted(groups.items()):
        total = sum(row.score for row in group_rows)
        max_total = len(group_rows) * MAX_SCORE_PER_SAMPLE
        table_rows.append([model, key, f"{total}/{max_total}", f"{total / len(group_rows):.2f}"])

    label = group_field.replace("_", " ")
    return md_table(["Model", label, "Total score", "Mean score"], table_rows)


def failure_summary(rows: list[ScoreRow]) -> str:
    counts: Counter[tuple[str, str]] = Counter(
        (row.model, row.failure_class)
        for row in rows
        if row.failure_class and row.failure_class != "none"
    )

    table_rows = [[model, failure_class, count] for (model, failure_class), count in sorted(counts.items())]
    if not table_rows:
        table_rows = [["none", "none", 0]]

    return md_table(["Model", "Failure class", "Count"], table_rows)


def score_matrix(rows: list[ScoreRow]) -> str:
    models = sorted({row.model for row in rows})
    sample_keys = sorted({(row.sample_id, row.followup_strength) for row in rows})
    lookup = {(row.sample_id, row.model): row.score for row in rows}

    table_rows = []
    for sample_id, followup_strength in sample_keys:
        table_rows.append(
            [sample_id, followup_strength] + [lookup.get((sample_id, model), "") for model in models]
        )

    return md_table(["Sample", "Follow-up strength"] + models, table_rows)


def build_markdown(rows: list[ScoreRow], source_path: Path) -> str:
    dataset_versions = sorted({row.dataset_version for row in rows})
    lines = [
        "# Manual score summary",
        "",
        f"Source: `{source_path.as_posix()}`",
        "",
        f"Dataset version(s): {', '.join(f'`{version}`' for version in dataset_versions)}",
        "",
        "## By model",
        "",
        model_summary(rows),
        "",
        "## By follow-up strength",
        "",
        grouped_summary(rows, "followup_strength"),
        "",
        "## By moral domain",
        "",
        grouped_summary(rows, "moral_domain"),
        "",
        "## Failure classes",
        "",
        failure_summary(rows),
        "",
        "## Compact score matrix",
        "",
        score_matrix(rows),
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarise manual scoring CSVs for behavioural evals.")
    parser.add_argument("csv_path", type=Path, help="Path to manual scoring CSV")
    parser.add_argument("--md", type=Path, help="Optional Markdown output path")
    args = parser.parse_args()

    rows = read_scores(args.csv_path)
    markdown = build_markdown(rows, args.csv_path)

    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(markdown, encoding="utf-8")
        print(f"Wrote Markdown: {args.md}")
    else:
        print(markdown)


if __name__ == "__main__":
    main()
