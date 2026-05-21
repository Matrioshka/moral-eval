from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from inspect_ai.log import read_eval_log


DEFAULT_GROUP_FIELDS = [
    "variant",
    "moral_domain",
    "expected_behaviour",
]


@dataclass
class SampleResult:
    sample_id: str
    case_id: str
    variant: str
    moral_domain: str
    expected_behaviour: str
    target: str
    answer: str
    score_value: str
    correct: bool | None
    scorer: str


def latest_eval_log(log_dir: Path) -> Path:
    logs = sorted(log_dir.glob("*.eval"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not logs:
        raise FileNotFoundError(f"No .eval logs found in {log_dir}")
    return logs[0]


def get_attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def normalise_correct(value: Any) -> bool | None:
    """Convert common Inspect score values to True/False.

    choice() usually records C/I. This also handles bools and numeric 1/0.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, int | float):
        if value == 1:
            return True
        if value == 0:
            return False

    text = str(value).strip().upper()
    if text in {"C", "CORRECT", "TRUE", "1"}:
        return True
    if text in {"I", "INCORRECT", "FALSE", "0"}:
        return False

    return None


def choose_score(scores: Any, preferred_scorer: str | None = None) -> tuple[str, Any]:
    if not scores:
        return "", None

    if isinstance(scores, dict):
        if preferred_scorer and preferred_scorer in scores:
            return preferred_scorer, scores[preferred_scorer]

        if "choice" in scores:
            return "choice", scores["choice"]

        first_key = next(iter(scores))
        return str(first_key), scores[first_key]

    return "", None


def extract_results(log_path: Path, preferred_scorer: str | None = None) -> list[SampleResult]:
    log = read_eval_log(log_path)

    if not log.samples:
        raise ValueError(
            "This log has no samples. Re-run the eval with sample logging enabled, "
            "or choose a completed log that contains samples."
        )

    rows: list[SampleResult] = []

    for sample in log.samples:
        metadata = get_attr_or_key(sample, "metadata", {}) or {}
        scorer_name, score = choose_score(get_attr_or_key(sample, "scores"), preferred_scorer)

        score_value = get_attr_or_key(score, "value", None)
        answer = get_attr_or_key(score, "answer", "")

        rows.append(
            SampleResult(
                sample_id=str(get_attr_or_key(sample, "id", "")),
                case_id=str(metadata.get("case_id", "")),
                variant=str(metadata.get("variant", "")),
                moral_domain=str(metadata.get("moral_domain", "")),
                expected_behaviour=str(metadata.get("expected_behaviour", "")),
                target=str(get_attr_or_key(sample, "target", "")),
                answer=str(answer or ""),
                score_value=str(score_value),
                correct=normalise_correct(score_value),
                scorer=scorer_name,
            )
        )

    return rows


def format_accuracy(correct: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{correct / total:.3f} ({correct}/{total})"


def print_group_summary(rows: list[SampleResult], field: str) -> None:
    groups: dict[str, list[SampleResult]] = defaultdict(list)

    for row in rows:
        value = getattr(row, field, "")
        groups[value or "<missing>"].append(row)

    print(f"\nBy {field}:")
    for value, group_rows in sorted(groups.items()):
        total = len(group_rows)
        correct = sum(row.correct is True for row in group_rows)
        unknown = sum(row.correct is None for row in group_rows)
        suffix = f", unknown={unknown}" if unknown else ""
        print(f"  {value}: {format_accuracy(correct, total)}{suffix}")


def print_failures(rows: list[SampleResult]) -> None:
    failures = [row for row in rows if row.correct is False]

    print("\nFailures:")
    if not failures:
        print("  None")
        return

    for row in failures:
        print(
            f"  {row.sample_id} | case={row.case_id} | variant={row.variant} | "
            f"domain={row.moral_domain} | target={row.target} | answer={row.answer} | "
            f"score={row.score_value}"
        )


def write_csv(rows: list[SampleResult], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "sample_id",
                "case_id",
                "variant",
                "moral_domain",
                "expected_behaviour",
                "target",
                "answer",
                "score_value",
                "correct",
                "scorer",
            ],
        )
        writer.writeheader()

        for row in rows:
            writer.writerow(
                {
                    "sample_id": row.sample_id,
                    "case_id": row.case_id,
                    "variant": row.variant,
                    "moral_domain": row.moral_domain,
                    "expected_behaviour": row.expected_behaviour,
                    "target": row.target,
                    "answer": row.answer,
                    "score_value": row.score_value,
                    "correct": row.correct,
                    "scorer": row.scorer,
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarise Inspect eval results by metadata fields.")
    parser.add_argument(
        "log",
        nargs="?",
        type=Path,
        help="Path to a specific .eval log. If omitted, uses the latest .eval file in --log-dir.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("logs"),
        help="Directory containing Inspect .eval logs. Defaults to ./logs.",
    )
    parser.add_argument(
        "--scorer",
        type=str,
        default=None,
        help="Scorer name to summarise. Defaults to 'choice' if present, otherwise the first scorer.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Optional path to write per-sample results as CSV.",
    )

    args = parser.parse_args()

    try:
        log_path = args.log if args.log else latest_eval_log(args.log_dir)
        rows = extract_results(log_path, preferred_scorer=args.scorer)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    total = len(rows)
    correct = sum(row.correct is True for row in rows)
    incorrect = sum(row.correct is False for row in rows)
    unknown = sum(row.correct is None for row in rows)

    print(f"Log: {log_path}")
    print(f"Samples: {total}")
    print(f"Overall accuracy: {format_accuracy(correct, total)}")
    print(f"Incorrect: {incorrect}")
    if unknown:
        print(f"Unknown / unparsed score values: {unknown}")

    target_counts = Counter(row.target for row in rows)
    answer_counts = Counter(row.answer for row in rows)
    score_counts = Counter(row.score_value for row in rows)

    print(f"\nTargets: {dict(target_counts)}")
    print(f"Answers: {dict(answer_counts)}")
    print(f"Score values: {dict(score_counts)}")

    for field in DEFAULT_GROUP_FIELDS:
        print_group_summary(rows, field)

    print_failures(rows)

    if args.csv:
        write_csv(rows, args.csv)
        print(f"\nWrote CSV: {args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())