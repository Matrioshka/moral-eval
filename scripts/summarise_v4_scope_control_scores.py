from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


MAX_SCORE_PER_SAMPLE = 3
DEFAULT_GLOB = "v4_scope_control_v1_*manual_scores.csv"
AUDIT_DIR = Path("docs/failure_audits")


@dataclass(frozen=True)
class ScoreRow:
    source_file: str
    model: str
    prompt_variant: str
    prompt_style: str
    sample_id: str
    case_id: str
    dataset_version: str
    moral_domain: str
    risk_track: str
    evidence_quality: str
    pressure_type: str
    expected_update: str
    target_scope: str
    minimum_acceptable_scope: str
    maximum_acceptable_scope: str
    score: int
    failure_class: str
    action: str
    confidence: str
    notes: str


def infer_model_and_variant(path: Path) -> tuple[str, str]:
    """Infer model and prompt variant from the current audit filename convention."""
    name = path.name
    prefix = "v4_scope_control_v1_"
    suffix = "_manual_scores.csv"

    if not name.startswith(prefix) or not name.endswith(suffix):
        raise ValueError(
            f"Cannot infer model/prompt variant from {path}. Expected filename like "
            "v4_scope_control_v1_<model>_<variant>_manual_scores.csv"
        )

    middle = name[len(prefix) : -len(suffix)]

    known_variants = [
        "scope_selection_v2_prompt",
        "scope_selection",
        "structured",
        "natural",
    ]

    for variant in known_variants:
        marker = f"_{variant}"
        if middle.endswith(marker):
            model = middle[: -len(marker)]
            return model, variant

    raise ValueError(f"Cannot infer prompt variant from {path}")


def require_columns(path: Path, fieldnames: Sequence[str] | None, required: set[str]) -> None:
    missing = required.difference(fieldnames or [])
    if missing:
        raise ValueError(f"{path} is missing required columns: {sorted(missing)}")


def parse_score(path: Path, line_no: int, raw: str) -> int:
    if raw == "":
        raise ValueError(f"Missing manual_score_0_to_3 on {path}:{line_no}")

    try:
        score = int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid manual_score_0_to_3 on {path}:{line_no}: {raw!r}") from exc

    if score < 0 or score > MAX_SCORE_PER_SAMPLE:
        raise ValueError(f"Score out of range on {path}:{line_no}: {score}")

    return score


def read_scores(paths: list[Path]) -> list[ScoreRow]:
    rows: list[ScoreRow] = []

    required = {
        "sample_id",
        "case_id",
        "dataset_version",
        "prompt_style",
        "moral_domain",
        "risk_track",
        "evidence_quality",
        "pressure_type",
        "expected_update",
        "target_scope",
        "minimum_acceptable_scope",
        "maximum_acceptable_scope",
        "manual_score_0_to_3",
        "primary_failure_class",
        "confidence",
        "action",
        "notes",
    }

    for path in paths:
        model, prompt_variant = infer_model_and_variant(path)
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            require_columns(path, reader.fieldnames, required)

            file_row_count = 0
            for line_no, row in enumerate(reader, start=2):
                score = parse_score(path, line_no, row["manual_score_0_to_3"])
                rows.append(
                    ScoreRow(
                        source_file=path.as_posix(),
                        model=model,
                        prompt_variant=prompt_variant,
                        prompt_style=row["prompt_style"],
                        sample_id=row["sample_id"],
                        case_id=row["case_id"],
                        dataset_version=row["dataset_version"],
                        moral_domain=row["moral_domain"],
                        risk_track=row["risk_track"],
                        evidence_quality=row["evidence_quality"],
                        pressure_type=row["pressure_type"],
                        expected_update=row["expected_update"],
                        target_scope=row["target_scope"],
                        minimum_acceptable_scope=row["minimum_acceptable_scope"],
                        maximum_acceptable_scope=row["maximum_acceptable_scope"],
                        score=score,
                        failure_class=row["primary_failure_class"] or "none",
                        action=row["action"],
                        confidence=row["confidence"],
                        notes=row["notes"],
                    )
                )
                file_row_count += 1

        if file_row_count == 0:
            raise ValueError(f"{path} contains no data rows")

    if not rows:
        raise ValueError("No score rows found")

    return rows


def md_escape(value: object) -> str:
    text = str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def md_table(headers: list[str], rows: Iterable[list[object]]) -> str:
    out = [
        "| " + " | ".join(md_escape(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    out.extend("| " + " | ".join(md_escape(cell) for cell in row) + " |" for row in rows)
    return "\n".join(out)


def score_counts(rows: list[ScoreRow]) -> Counter[int]:
    return Counter(row.score for row in rows)


def summary_row(label_values: list[object], rows: list[ScoreRow]) -> list[object]:
    total = sum(row.score for row in rows)
    max_total = len(rows) * MAX_SCORE_PER_SAMPLE
    counts = score_counts(rows)
    failures = sum(1 for row in rows if row.score <= 1)
    borderline = counts[2]
    return label_values + [
        len(rows),
        f"{total}/{max_total}",
        f"{total / len(rows):.2f}",
        counts[3],
        borderline,
        failures,
    ]


def grouped(rows: list[ScoreRow], fields: list[str]) -> str:
    groups: dict[tuple[str, ...], list[ScoreRow]] = defaultdict(list)

    for row in rows:
        key = tuple(str(getattr(row, field)) for field in fields)
        groups[key].append(row)

    table_rows = []
    for key, group_rows in sorted(groups.items()):
        table_rows.append(summary_row(list(key), group_rows))

    headers = [field.replace("_", " ") for field in fields] + [
        "n",
        "total score",
        "mean score",
        "passes",
        "borderline",
        "failures",
    ]
    return md_table(headers, table_rows)


def failure_class_summary(rows: list[ScoreRow]) -> str:
    groups: dict[tuple[str, str, str, str], list[ScoreRow]] = defaultdict(list)
    for row in rows:
        if row.failure_class == "CALIBRATED_SCOPE_CONTROL":
            continue
        groups[(row.model, row.prompt_variant, row.failure_class, row.action)].append(row)

    table_rows = []
    for (model, prompt_variant, failure_class, action), group_rows in sorted(groups.items()):
        table_rows.append([model, prompt_variant, failure_class, action, len(group_rows)])

    if not table_rows:
        table_rows = [["none", "none", "none", "none", 0]]

    return md_table(["model", "prompt variant", "failure class", "action", "count"], table_rows)


def failure_rows(rows: list[ScoreRow]) -> str:
    failed = [row for row in rows if row.score <= 2]
    table_rows = [
        [
            row.model,
            row.prompt_variant,
            row.sample_id,
            row.evidence_quality,
            row.pressure_type,
            row.target_scope,
            row.score,
            row.failure_class,
            row.notes,
        ]
        for row in sorted(failed, key=lambda r: (r.model, r.prompt_variant, r.sample_id))
    ]

    if not table_rows:
        table_rows = [["none", "none", "none", "none", "none", "none", "", "none", ""]]

    return md_table(
        [
            "model",
            "prompt variant",
            "sample id",
            "evidence quality",
            "pressure type",
            "target scope",
            "score",
            "failure class",
            "notes",
        ],
        table_rows,
    )


def score_matrix(rows: list[ScoreRow]) -> str:
    run_keys = sorted({(row.model, row.prompt_variant) for row in rows})
    sample_ids = sorted({row.sample_id for row in rows})
    lookup = {(row.sample_id, row.model, row.prompt_variant): row.score for row in rows}

    table_rows = []
    for sample_id in sample_ids:
        table_rows.append(
            [sample_id]
            + [lookup.get((sample_id, model, prompt_variant), "") for model, prompt_variant in run_keys]
        )

    headers = ["sample id"] + [f"{model} / {prompt_variant}" for model, prompt_variant in run_keys]
    return md_table(headers, table_rows)


def source_files_table(paths: list[Path]) -> str:
    table_rows = []
    for path in paths:
        model, prompt_variant = infer_model_and_variant(path)
        table_rows.append([path.as_posix(), model, prompt_variant])
    return md_table(["file", "model", "prompt variant"], table_rows)


def build_markdown(rows: list[ScoreRow], paths: list[Path]) -> str:
    dataset_versions = sorted({row.dataset_version for row in rows})
    lines = [
        "# Phase 3 scope-control score summary",
        "",
        "Aggregated manual-score summary for v4 scope-control audit CSVs.",
        "",
        "Dataset version(s): " + ", ".join(f"`{version}`" for version in dataset_versions),
        "",
        "## Source files",
        "",
        source_files_table(paths),
        "",
        "## By model and prompt variant",
        "",
        grouped(rows, ["model", "prompt_variant"]),
        "",
        "## By model",
        "",
        grouped(rows, ["model"]),
        "",
        "## By prompt style",
        "",
        grouped(rows, ["prompt_style"]),
        "",
        "## By evidence quality",
        "",
        grouped(rows, ["model", "evidence_quality"]),
        "",
        "## By pressure type",
        "",
        grouped(rows, ["model", "pressure_type"]),
        "",
        "## By target scope",
        "",
        grouped(rows, ["model", "target_scope"]),
        "",
        "## By failure class",
        "",
        failure_class_summary(rows),
        "",
        "## Borderline and failure rows",
        "",
        failure_rows(rows),
        "",
        "## Compact score matrix",
        "",
        score_matrix(rows),
        "",
    ]
    return "\n".join(lines)


def discover_paths(audit_dir: Path, pattern: str, include_original_scope_prompt: bool) -> list[Path]:
    paths = sorted(audit_dir.glob(pattern))
    if not include_original_scope_prompt:
        paths = [
            path
            for path in paths
            if not path.name.endswith("gpt-4.1-mini_scope_selection_manual_scores.csv")
        ]
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Summarise Phase 3 v4 scope-control manual audit CSVs by model, prompt style, "
            "evidence quality, pressure type, target scope, and failure class."
        )
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Audit CSV paths. If omitted, discovers v4 scope-control audit CSVs in docs/failure_audits.",
    )
    parser.add_argument(
        "--audit-dir",
        type=Path,
        default=AUDIT_DIR,
        help="Directory used for auto-discovery when no paths are supplied.",
    )
    parser.add_argument(
        "--pattern",
        default=DEFAULT_GLOB,
        help=f"Glob pattern for auto-discovery. Defaults to {DEFAULT_GLOB!r}.",
    )
    parser.add_argument(
        "--include-original-scope-prompt",
        action="store_true",
        help="Include the original GPT-4.1-mini scope-selection prompt artefact run in auto-discovery.",
    )
    parser.add_argument("--md", type=Path, help="Optional Markdown output path.")
    args = parser.parse_args()

    paths = args.paths or discover_paths(
        args.audit_dir,
        args.pattern,
        include_original_scope_prompt=args.include_original_scope_prompt,
    )
    paths = sorted(paths)

    if not paths:
        raise SystemExit("No audit CSVs found")

    rows = read_scores(paths)
    markdown = build_markdown(rows, paths)

    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(markdown, encoding="utf-8")
        print(f"Wrote Markdown: {args.md}")
    else:
        print(markdown)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
