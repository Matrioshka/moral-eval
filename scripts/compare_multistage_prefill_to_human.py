#!/usr/bin/env python
"""Compare review-only AI prefills with completed human scores."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "scripts", ROOT / "src"):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from scripts.score_miscalibrated_corrigibility import connect_db, read_jsonl


DEFAULT_PREFILL_PATH: Final = ROOT / "tmp" / "manual_scoring" / "multi_stage_ai_prefill_draft.jsonl"


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def human_rows_from_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        {
            "response_id": record.get("response_id"),
            "label": record.get("final_failure_class"),
            "score": record.get("calibration_error_severity"),
            "rationale": record.get("rationale"),
        }
        for record in read_jsonl(path)
    ]


def human_rows_from_db(dsn: str | None) -> list[dict[str, Any]]:
    conn = connect_db(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT response_id, label, score, rationale
                FROM rpt.multi_stage_corrigibility_scores
                WHERE scorer_mode = 'human'
                ORDER BY response_id, score_event_id
                """
            )
            columns = [description.name for description in cur.description]
            return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
    finally:
        conn.close()


def compare_prefills_to_human(
    prefill_records: list[dict[str, Any]],
    human_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    human_by_response = {
        row.get("response_id"): row
        for row in human_rows
        if isinstance(row.get("response_id"), int)
    }
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    disagreements: list[dict[str, Any]] = []
    compared = 0
    label_agreement = 0
    severity_agreement = 0

    for wrapper in prefill_records:
        validation = wrapper.get("validation") or {}
        if not validation.get("valid_against_completed_extraction_schema"):
            continue
        response_id = wrapper.get("response_id")
        human = human_by_response.get(response_id)
        proposed = wrapper.get("proposed_extraction")
        if human is None or not isinstance(proposed, dict):
            continue
        human_label = human.get("label")
        ai_label = proposed.get("final_failure_class")
        human_score = human.get("score")
        ai_score = proposed.get("calibration_error_severity")
        compared += 1
        confusion[str(human_label)][str(ai_label)] += 1
        label_agreement += int(human_label == ai_label)
        severity_agreement += int(human_score == ai_score)
        if human_label != ai_label or human_score != ai_score:
            disagreements.append(
                {
                    "response_id": response_id,
                    "human_label": human_label,
                    "human_score": human_score,
                    "ai_label": ai_label,
                    "ai_score": ai_score,
                    "ai_confidence": proposed.get("confidence"),
                    "human_rationale": str(human.get("rationale") or "")[:180],
                    "ai_rationale": str(proposed.get("rationale") or "")[:180],
                }
            )

    return {
        "prefill_count": len(prefill_records),
        "human_count": len(human_rows),
        "count_compared": compared,
        "exact_label_agreement": label_agreement,
        "exact_label_agreement_rate": label_agreement / compared if compared else None,
        "severity_agreement": severity_agreement,
        "severity_agreement_rate": severity_agreement / compared if compared else None,
        "confusion_matrix": {
            human_label: dict(sorted(ai_counts.items()))
            for human_label, ai_counts in sorted(confusion.items())
        },
        "disagreements": disagreements,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare AI prefills with human multi-stage scores.")
    parser.add_argument("--prefill-jsonl", type=Path, default=DEFAULT_PREFILL_PATH)
    parser.add_argument("--human-jsonl", type=Path)
    parser.add_argument("--dsn")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.prefill_jsonl.exists():
        raise SystemExit(f"Prefill JSONL not found: {args.prefill_jsonl}")
    if args.human_jsonl and not args.human_jsonl.exists():
        raise SystemExit(f"Human JSONL not found: {args.human_jsonl}")
    human_rows = (
        human_rows_from_jsonl(args.human_jsonl)
        if args.human_jsonl
        else human_rows_from_db(args.dsn)
    )
    report = compare_prefills_to_human(read_jsonl(args.prefill_jsonl), human_rows)
    print(json.dumps(json_safe(report), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
