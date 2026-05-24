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


def write_csv(rows: list[BehaviourOutput], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "sample_id",
                "case_id",
                "source_item_id",
                "moral_domain",
                "expected_behaviour",
                "difficulty",
                "ideal_behaviour",
                "output",
                "manual_score_0_to_3",
                "primary_failure_class",
                "confidence",
                "action",
                "notes",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "sample_id": row.sample_id,
                    "case_id": row.case_id,
                    "source_item_id": row.source_item_id,
                    "moral_domain": row.moral_domain,
                    "expected_behaviour": row.expected_behaviour,
                    "difficulty": row.difficulty,
                    "ideal_behaviour": row.ideal_behaviour,
                    "output": row.output,
                    "manual_score_0_to_3": "",
                    "primary_failure_class": "",
                    "confidence": "",
                    "action": "",
                    "notes": "",
                }
            )


def escape_table_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", "<br>")


def write_markdown(rows: list[BehaviourOutput], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        f.write("# Behavioural eval outputs for manual review\n\n")
        f.write("Use the scoring scale in `docs/failure_audits/v3_2_behaviour_manual_audit.md`.\n\n")
        f.write(
            "| sample_id | case_id | moral_domain | ideal_behaviour | output | score_0_to_3 | failure_class | notes |\n"
        )
        f.write("|---|---|---|---|---|---:|---|---|\n")
        for row in rows:
            f.write(
                "| "
                f"{escape_table_cell(row.sample_id)} | "
                f"{escape_table_cell(row.case_id)} | "
                f"{escape_table_cell(row.moral_domain)} | "
                f"{escape_table_cell(row.ideal_behaviour)} | "
                f"{escape_table_cell(row.output)} | "
                " |  |  |\n"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Export behavioural Inspect eval outputs for manual review.")
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
        write_csv(rows, args.csv)
        print(f"Wrote CSV: {args.csv}")

    if args.md:
        write_markdown(rows, args.md)
        print(f"Wrote Markdown: {args.md}")

    if not args.csv and not args.md:
        for row in rows:
            print(f"\n{row.sample_id} | {row.case_id} | {row.moral_domain}")
            print(row.output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
