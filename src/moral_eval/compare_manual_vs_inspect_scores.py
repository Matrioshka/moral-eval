"""Compare manual audit scores against Inspect deterministic scorer outputs.

This is intended for narrow validation of the schema-v2.1 tuple/bounds scorer:
it checks whether the deterministic Inspect score agrees with the existing
manual audit labels on the same samples. It does not re-score model reasoning.

Example:

    python src/moral_eval/compare_manual_vs_inspect_scores.py \
        --eval-log logs/example.eval \
        --manual-csv docs/failure_audits/v4_release_governance_schema_v2_1_candidate_11item_gpt-4.1-mini_structured_access_decision_v2_1_manual_scores.csv \
        --output-md docs/reports/schema_v2_1_manual_vs_deterministic_scoring.md \
        --output-csv docs/failure_audits/schema_v2_1_manual_vs_deterministic_scoring.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from inspect_ai.log import read_eval_log

DEFAULT_SCORER = "schema_v2_1_access_scorer"
MAX_MANUAL_SCORE = 3

OUTPUT_FIELDNAMES = [
    "sample_id",
    "case_id",
    "manual_score_0_to_3",
    "manual_judgement",
    "manual_binary",
    "primary_failure_class",
    "inspect_score",
    "inspect_judgement",
    "inspect_binary",
    "exact_agreement",
    "binary_agreement",
    "inspect_answer",
    "inspect_explanation",
    "inspect_too_permissive",
    "inspect_too_conservative",
    "inspect_validation_errors",
    "inspect_consistency_issues",
]


def get_attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def parse_manual_score(value: str, sample_id: str) -> int:
    try:
        score = int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"Invalid manual score for {sample_id}: {value!r}") from exc
    if score < 0 or score > MAX_MANUAL_SCORE:
        raise ValueError(f"Manual score out of range for {sample_id}: {score}")
    return score


def manual_judgement(score: int) -> str:
    if score == 3:
        return "pass"
    if score == 2:
        return "borderline"
    return "fail"


def manual_binary(judgement: str) -> str:
    return "pass" if judgement == "pass" else "non_pass"


def inspect_judgement(score_value: Any) -> str:
    value = str(score_value).strip()
    if value == "C":
        return "pass"
    if value == "P":
        return "borderline"
    if value == "I":
        return "fail"
    return "unknown"


def inspect_binary(judgement: str) -> str:
    return "pass" if judgement == "pass" else "non_pass"


def read_manual_scores(path: Path, sample_id_col: str, score_col: str) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        required = {sample_id_col, score_col}
        missing = required.difference(fieldnames)
        if missing:
            raise ValueError(f"{path} is missing required columns: {sorted(missing)}")

        rows: dict[str, dict[str, str]] = {}
        for line_no, row in enumerate(reader, start=2):
            sample_id = (row.get(sample_id_col) or "").strip()
            if not sample_id:
                raise ValueError(f"Missing sample id on line {line_no} of {path}")
            if sample_id in rows:
                raise ValueError(f"Duplicate manual score row for sample id {sample_id!r}")
            rows[sample_id] = row

    if not rows:
        raise ValueError(f"{path} contains no manual score rows")
    return rows


def scorer_metadata(score: Any) -> dict[str, Any]:
    return get_attr_or_key(score, "metadata", {}) or {}


def build_comparison_rows(
    eval_log_path: Path,
    manual_csv_path: Path,
    scorer_name: str,
    sample_id_col: str,
    score_col: str,
) -> list[dict[str, str]]:
    manual_rows = read_manual_scores(manual_csv_path, sample_id_col, score_col)
    log = read_eval_log(eval_log_path)
    samples = get_attr_or_key(log, "samples", []) or []
    if not samples:
        raise ValueError(f"{eval_log_path} contains no samples")

    comparison_rows: list[dict[str, str]] = []
    seen_sample_ids: set[str] = set()

    for sample in samples:
        sample_id = str(get_attr_or_key(sample, "id", ""))
        seen_sample_ids.add(sample_id)
        manual_row = manual_rows.get(sample_id)
        if manual_row is None:
            raise ValueError(f"No manual score row found for eval sample {sample_id!r}")

        manual_score = parse_manual_score(manual_row.get(score_col, ""), sample_id)
        manual_label = manual_judgement(manual_score)
        manual_binary_label = manual_binary(manual_label)

        scores = get_attr_or_key(sample, "scores", {}) or {}
        if scorer_name not in scores:
            available = ", ".join(sorted(str(name) for name in scores))
            raise ValueError(
                f"Sample {sample_id!r} has no score named {scorer_name!r}. "
                f"Available scorers: {available}"
            )
        inspect_score = scores[scorer_name]
        inspect_label = inspect_judgement(get_attr_or_key(inspect_score, "value", ""))
        inspect_binary_label = inspect_binary(inspect_label)
        metadata = scorer_metadata(inspect_score)

        exact_agreement = manual_label == inspect_label
        binary_agreement = manual_binary_label == inspect_binary_label

        comparison_rows.append(
            {
                "sample_id": sample_id,
                "case_id": manual_row.get("case_id", ""),
                "manual_score_0_to_3": str(manual_score),
                "manual_judgement": manual_label,
                "manual_binary": manual_binary_label,
                "primary_failure_class": manual_row.get("primary_failure_class", ""),
                "inspect_score": str(get_attr_or_key(inspect_score, "value", "")),
                "inspect_judgement": inspect_label,
                "inspect_binary": inspect_binary_label,
                "exact_agreement": str(exact_agreement),
                "binary_agreement": str(binary_agreement),
                "inspect_answer": stringify(get_attr_or_key(inspect_score, "answer", "")),
                "inspect_explanation": stringify(get_attr_or_key(inspect_score, "explanation", "")),
                "inspect_too_permissive": stringify(metadata.get("schema_v2_1_too_permissive", [])),
                "inspect_too_conservative": stringify(metadata.get("schema_v2_1_too_conservative", [])),
                "inspect_validation_errors": stringify(metadata.get("schema_v2_1_validation_errors", [])),
                "inspect_consistency_issues": stringify(metadata.get("schema_v2_1_consistency_issues", [])),
            }
        )

    unused_manual_ids = sorted(set(manual_rows).difference(seen_sample_ids))
    if unused_manual_ids:
        raise ValueError(
            "Manual CSV contains sample ids not found in the eval log: "
            + ", ".join(unused_manual_ids)
        )

    return comparison_rows


def md_table(headers: list[str], rows: Iterable[list[object]]) -> str:
    row_list = [[str(cell) for cell in row] for row in rows]
    out = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    out.extend("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in row_list)
    return "\n".join(out)


def pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "n/a"
    return f"{numerator / denominator:.3f}"


def build_markdown(rows: list[dict[str, str]], eval_log_path: Path, manual_csv_path: Path) -> str:
    total = len(rows)
    exact_agreements = sum(row["exact_agreement"] == "True" for row in rows)
    binary_agreements = sum(row["binary_agreement"] == "True" for row in rows)
    manual_counts = Counter(row["manual_judgement"] for row in rows)
    inspect_counts = Counter(row["inspect_judgement"] for row in rows)

    disagreement_rows = [row for row in rows if row["exact_agreement"] != "True"]
    if disagreement_rows:
        disagreement_table = md_table(
            [
                "Sample",
                "Manual",
                "Inspect",
                "Manual failure class",
                "Inspect explanation",
            ],
            [
                [
                    row["sample_id"],
                    row["manual_judgement"],
                    row["inspect_judgement"],
                    row["primary_failure_class"],
                    row["inspect_explanation"],
                ]
                for row in disagreement_rows
            ],
        )
    else:
        disagreement_table = "No exact-label disagreements."

    per_sample_table = md_table(
        [
            "Sample",
            "Manual",
            "Inspect",
            "Exact agreement",
            "Failure class",
            "Inspect explanation",
        ],
        [
            [
                row["sample_id"],
                row["manual_judgement"],
                row["inspect_judgement"],
                row["exact_agreement"],
                row["primary_failure_class"],
                row["inspect_explanation"],
            ]
            for row in rows
        ],
    )

    return "\n".join(
        [
            "# Manual vs deterministic schema-v2.1 scoring agreement",
            "",
            f"Eval log: `{eval_log_path.as_posix()}`",
            f"Manual CSV: `{manual_csv_path.as_posix()}`",
            "",
            "This compares the existing manual audit labels against the deterministic Inspect schema-v2.1 tuple/bounds scorer. It validates agreement on selected access tuples; it does not replace the manual moral-reasoning audit.",
            "",
            "## Summary",
            "",
            md_table(
                ["Metric", "Value"],
                [
                    ["Samples compared", total],
                    ["Exact judgement agreement", f"{exact_agreements}/{total} ({pct(exact_agreements, total)})"],
                    ["Binary pass/non-pass agreement", f"{binary_agreements}/{total} ({pct(binary_agreements, total)})"],
                    ["Manual counts", dict(manual_counts)],
                    ["Inspect counts", dict(inspect_counts)],
                ],
            ),
            "",
            "## Exact-label disagreements",
            "",
            disagreement_table,
            "",
            "## Per-sample comparison",
            "",
            per_sample_table,
            "",
        ]
    )


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(markdown: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare manual audit labels with deterministic Inspect scorer outputs."
    )
    parser.add_argument("--eval-log", required=True, type=Path, help="Path to an Inspect .eval log")
    parser.add_argument("--manual-csv", required=True, type=Path, help="Path to manual audit CSV")
    parser.add_argument("--output-csv", type=Path, help="Optional CSV output path")
    parser.add_argument("--output-md", type=Path, help="Optional Markdown output path")
    parser.add_argument("--scorer", default=DEFAULT_SCORER, help=f"Inspect scorer name; default: {DEFAULT_SCORER}")
    parser.add_argument("--sample-id-col", default="sample_id", help="Manual CSV sample-id column")
    parser.add_argument("--manual-score-col", default="manual_score_0_to_3", help="Manual score column")
    args = parser.parse_args()

    rows = build_comparison_rows(
        eval_log_path=args.eval_log,
        manual_csv_path=args.manual_csv,
        scorer_name=args.scorer,
        sample_id_col=args.sample_id_col,
        score_col=args.manual_score_col,
    )

    markdown = build_markdown(rows, args.eval_log, args.manual_csv)

    if args.output_csv:
        write_csv(rows, args.output_csv)
    if args.output_md:
        write_markdown(markdown, args.output_md)

    print(markdown)


if __name__ == "__main__":
    main()
