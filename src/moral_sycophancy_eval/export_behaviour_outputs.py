from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from inspect_ai.log import read_eval_log


@dataclass
class BehaviourOutput:
    sample_id: str
    case_id: str
    source_item_id: str
    moral_domain: str
    expected_behaviour: str
    difficulty: str
    ideal_behaviour: str
    output: str


RAW_FIELDNAMES = [
    "sample_id",
    "case_id",
    "source_item_id",
    "moral_domain",
    "expected_behaviour",
    "difficulty",
    "ideal_behaviour",
    "output",
]

REVIEW_FIELDNAMES = [
    "manual_score_0_to_3",
    "primary_failure_class",
    "confidence",
    "action",
    "notes",
]


def get_attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def latest_eval_log(log_dir: Path) -> Path:
    logs = sorted(log_dir.glob("*.eval"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not logs:
        raise FileNotFoundError(f"No .eval logs found in {log_dir}")
    return logs[0]


def extract_completion(sample: Any) -> str:
    output = get_attr_or_key(sample, "output", None)
    completion = get_attr_or_key(output, "completion", "")
    if completion:
        return str(completion)

    # Fallback for logs where the final assistant message is stored in messages.
    messages = get_attr_or_key(sample, "messages", []) or []
    for message in reversed(messages):
        role = str(get_attr_or_key(message, "role", "")).lower()
        content = get_attr_or_key(message, "content", "")
        if role == "assistant" and content:
            return str(content)

    return ""


def extract_outputs(log_path: Path) -> list[BehaviourOutput]:
    log = read_eval_log(log_path)

    if not log.samples:
        raise ValueError(
            "This log has no samples. Re-run the eval with sample logging enabled, "
            "or choose a completed log that contains samples."
        )

    rows: list[BehaviourOutput] = []

    for sample in log.samples:
        metadata = get_attr_or_key(sample, "metadata", {}) or {}
        rows.append(
            BehaviourOutput(
                sample_id=str(get_attr_or_key(sample, "id", "")),
                case_id=str(metadata.get("case_id", "")),
                source_item_id=str(metadata.get("source_item_id", "")),
                moral_domain=str(metadata.get("moral_domain", "")),
                expected_behaviour=str(metadata.get("expected_behaviour", "")),
                difficulty=str(metadata.get("difficulty", "")),
                ideal_behaviour=str(metadata.get("ideal_behaviour", get_attr_or_key(sample, "target", ""))),
                output=extract_completion(sample),
            )
        )

    return rows


def row_to_dict(row: BehaviourOutput, include_review_columns: bool) -> dict[str, str]:
    values = {
        "sample_id": row.sample_id,
        "case_id": row.case_id,
        "source_item_id": row.source_item_id,
        "moral_domain": row.moral_domain,
        "expected_behaviour": row.expected_behaviour,
        "difficulty": row.difficulty,
        "ideal_behaviour": row.ideal_behaviour,
        "output": row.output,
    }

    if include_review_columns:
        values.update({field: "" for field in REVIEW_FIELDNAMES})

    return values


def write_csv(rows: list[BehaviourOutput], output_path: Path, include_review_columns: bool) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = RAW_FIELDNAMES + (REVIEW_FIELDNAMES if include_review_columns else [])

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row_to_dict(row, include_review_columns=include_review_columns))


def escape_table_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", "<br>")


def write_markdown(rows: list[BehaviourOutput], output_path: Path, include_review_columns: bool) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if include_review_columns:
        title = "# Behavioural eval outputs for manual review\n\n"
        description = "Use the scoring scale in `docs/failure_audits/v3_2_behaviour_manual_audit.md`.\n\n"
        header = "| sample_id | case_id | moral_domain | ideal_behaviour | output | score_0_to_3 | failure_class | notes |\n"
        separator = "|---|---|---|---|---|---:|---|---|\n"
    else:
        title = "# Behavioural eval outputs\n\n"
        description = "Raw exported model outputs. Manual scoring belongs in a separate audit file.\n\n"
        header = "| sample_id | case_id | moral_domain | ideal_behaviour | output |\n"
        separator = "|---|---|---|---|---|\n"

    with output_path.open("w", encoding="utf-8") as f:
        f.write(title)
        f.write(description)
        f.write(header)
        f.write(separator)
        for row in rows:
            base_cells = [
                row.sample_id,
                row.case_id,
                row.moral_domain,
                row.ideal_behaviour,
                row.output,
            ]
            escaped_cells = [escape_table_cell(cell) for cell in base_cells]
            if include_review_columns:
                escaped_cells.extend(["", "", ""])
            f.write("| " + " | ".join(escaped_cells) + " |\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export behavioural Inspect eval outputs.")
    parser.add_argument(
        "log",
        nargs="?",
        type=Path,
        help="Path to a specific behavioural .eval log. If omitted, uses the latest .eval file in --log-dir.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("logs"),
        help="Directory containing Inspect .eval logs. Defaults to ./logs.",
    )
    parser.add_argument("--csv", type=Path, default=None, help="Optional CSV output path.")
    parser.add_argument("--md", type=Path, default=None, help="Optional Markdown output path.")
    parser.add_argument(
        "--include-review-columns",
        action="store_true",
        help="Include empty manual review columns in CSV/Markdown exports.",
    )

    args = parser.parse_args()

    try:
        log_path = args.log if args.log else latest_eval_log(args.log_dir)
        rows = extract_outputs(log_path)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Log: {log_path}")
    print(f"Samples: {len(rows)}")

    missing_outputs = sum(1 for row in rows if not row.output.strip())
    if missing_outputs:
        print(f"Missing outputs: {missing_outputs}")

    if args.csv:
        write_csv(rows, args.csv, include_review_columns=args.include_review_columns)
        print(f"Wrote CSV: {args.csv}")

    if args.md:
        write_markdown(rows, args.md, include_review_columns=args.include_review_columns)
        print(f"Wrote Markdown: {args.md}")

    if not args.csv and not args.md:
        for row in rows:
            print(f"\n{row.sample_id} | {row.case_id} | {row.moral_domain}")
            print(row.output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
