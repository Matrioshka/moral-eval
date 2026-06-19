#!/usr/bin/env python
"""Inspect raw .eval logs for passive usage metadata shapes.

This script is read-only. It does not ingest, mutate DB state, run evals, or call
model/provider APIs.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from moral_eval.diagnostics import discover_usage_locations, to_jsonable  # noqa: E402


def get_attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def classify_usage_shape(locations: list[dict[str, Any]]) -> str:
    has_aggregate = any(not location.get("confirmed_call_level") for location in locations)
    has_call = any(location.get("confirmed_call_level") for location in locations)
    if has_aggregate and has_call:
        return "mixed"
    if has_call:
        return "per_event_per_call"
    if has_aggregate:
        return "sample_level_only"
    return "absent"


def inspect_eval_log_usage_shape(path: Path, limit: int | None = None) -> dict[str, Any]:
    from inspect_ai.log import read_eval_log

    log = read_eval_log(path)
    samples = list(get_attr_or_key(log, "samples", []) or [])
    if limit is not None:
        samples = samples[:limit]

    sample_reports = []
    class_counts: Counter[str] = Counter()
    location_counts: Counter[str] = Counter()

    for index, sample in enumerate(samples):
        raw_sample = to_jsonable(sample)
        locations = discover_usage_locations(raw_sample)
        classification = classify_usage_shape(locations)
        class_counts[classification] += 1
        for location in locations:
            location_counts[str(location["path"])] += 1
        sample_reports.append(
            {
                "sample_index": index,
                "sample_id": str(get_attr_or_key(sample, "id", f"sample-{index + 1}")),
                "usage_shape": classification,
                "locations": [
                    {
                        "path": location["path"],
                        "source_scope": location["source_scope"],
                        "source_event_index": location.get("source_event_index"),
                        "confirmed_call_level": location["confirmed_call_level"],
                        "usage_keys": location["usage_keys"],
                    }
                    for location in locations
                ],
            }
        )

    return {
        "eval_log": str(path),
        "sample_count_inspected": len(samples),
        "usage_shape_counts": dict(sorted(class_counts.items())),
        "location_counts": dict(sorted(location_counts.items())),
        "samples": sample_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect .eval logs for passive usage metadata locations.")
    parser.add_argument("eval_log", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    report = inspect_eval_log_usage_shape(args.eval_log, args.limit)
    print(json.dumps({k: v for k, v in report.items() if k != "samples"}, indent=2, ensure_ascii=False))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
