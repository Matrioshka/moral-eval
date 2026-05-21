from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {
    "id",
    "case_id",
    "variant",
    "moral_domain",
    "scenario",
    "initial_judgement",
    "user_followup",
    "expected_behaviour",
    "target",
    "answer_a",
    "answer_b",
    "relevant_moral_features",
    "notes",
}

ALLOWED_TARGETS = {"A", "B"}

ALLOWED_VARIANTS = {
    "pressure",
    "evidence",
    "irrelevant_detail",
    "neutral",
}

ALLOWED_EXPECTED_BEHAVIOURS = {
    "resist_pressure",
    "update_on_evidence",
    "maintain_judgement",
    "acknowledge_without_reversing",
}

PLACEHOLDER_VALUES = {"...", "TODO", "TBD", ""}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc

            if not isinstance(row, dict):
                raise ValueError(f"Line {line_number} is not a JSON object.")

            row["_line_number"] = line_number
            rows.append(row)

    return rows


def is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in PLACEHOLDER_VALUES
    return False


def validate_rows(
    rows: list[dict[str, Any]],
    expected_variants: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []

    ids: list[str] = []
    case_variants: dict[str, set[str]] = defaultdict(set)
    target_counts = Counter()
    target_by_variant: dict[str, Counter[str]] = defaultdict(Counter)

    for row in rows:
        line = row.get("_line_number", "?")
        row_id = row.get("id", f"<missing id on line {line}>")

        missing = sorted(REQUIRED_FIELDS - set(row.keys()))
        if missing:
            errors.append(f"{row_id}: missing required fields: {missing}")

        for field in REQUIRED_FIELDS:
            if field in row and field not in {"relevant_moral_features"}:
                if is_placeholder(row[field]):
                    errors.append(f"{row_id}: field {field!r} is blank or placeholder.")

        if "id" in row:
            ids.append(str(row["id"]))

        if row.get("target") not in ALLOWED_TARGETS:
            errors.append(f"{row_id}: target must be one of {sorted(ALLOWED_TARGETS)}, got {row.get('target')!r}.")

        variant = row.get("variant")
        if variant not in ALLOWED_VARIANTS:
            errors.append(f"{row_id}: variant must be one of {sorted(ALLOWED_VARIANTS)}, got {variant!r}.")

        expected_behaviour = row.get("expected_behaviour")
        if expected_behaviour not in ALLOWED_EXPECTED_BEHAVIOURS:
            errors.append(
                f"{row_id}: expected_behaviour must be one of "
                f"{sorted(ALLOWED_EXPECTED_BEHAVIOURS)}, got {expected_behaviour!r}."
            )

        features = row.get("relevant_moral_features")
        if not isinstance(features, list) or not features:
            errors.append(f"{row_id}: relevant_moral_features must be a non-empty list.")
        elif any(not isinstance(item, str) or not item.strip() for item in features):
            errors.append(f"{row_id}: relevant_moral_features must contain only non-empty strings.")

        answer_a = row.get("answer_a")
        answer_b = row.get("answer_b")
        if isinstance(answer_a, str) and isinstance(answer_b, str):
            if answer_a.strip() == answer_b.strip():
                errors.append(f"{row_id}: answer_a and answer_b are identical.")
            if len(answer_a.strip()) < 40:
                errors.append(f"{row_id}: answer_a looks too short.")
            if len(answer_b.strip()) < 40:
                errors.append(f"{row_id}: answer_b looks too short.")

        scenario = row.get("scenario")
        if isinstance(scenario, str) and len(scenario.strip()) < 80:
            errors.append(f"{row_id}: scenario looks too short.")

        case_id = row.get("case_id")
        if isinstance(case_id, str) and isinstance(variant, str):
            case_variants[case_id].add(variant)

        target = row.get("target")
        if target in ALLOWED_TARGETS:
            target_counts[target] += 1
            if isinstance(variant, str):
                target_by_variant[variant][target] += 1

    duplicate_ids = [item for item, count in Counter(ids).items() if count > 1]
    if duplicate_ids:
        errors.append(f"Duplicate ids found: {duplicate_ids}")

    if expected_variants:
        for case_id, variants in sorted(case_variants.items()):
            missing_variants = expected_variants - variants
            extra_variants = variants - expected_variants
            if missing_variants:
                errors.append(f"{case_id}: missing variants {sorted(missing_variants)}")
            if extra_variants:
                errors.append(f"{case_id}: unexpected variants {sorted(extra_variants)}")

    print(f"Rows: {len(rows)}")
    print(f"Targets: {dict(target_counts)}")
    print("Targets by variant:")
    for variant, counts in sorted(target_by_variant.items()):
        print(f"  {variant}: {dict(counts)}")

    print("Case variants:")
    for case_id, variants in sorted(case_variants.items()):
        print(f"  {case_id}: {sorted(variants)}")

    return errors


def parse_expected_variants(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    return {item.strip() for item in raw.split(",") if item.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate moral sycophancy / moral reasoning integrity JSONL datasets.")
    parser.add_argument("path", type=Path, help="Path to a JSONL dataset.")
    parser.add_argument(
        "--expected-variants",
        type=str,
        default=None,
        help="Comma-separated variants expected for each case_id, e.g. pressure,evidence",
    )

    args = parser.parse_args()

    if not args.path.exists():
        print(f"File not found: {args.path}", file=sys.stderr)
        return 2

    try:
        rows = load_jsonl(args.path)
        expected_variants = parse_expected_variants(args.expected_variants)
        errors = validate_rows(rows, expected_variants=expected_variants)
    except ValueError as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 1

    if errors:
        print("\nValidation errors:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("\nValidation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())