#!/usr/bin/env python
"""Run deterministic structural, duplicate and coverage audits on transformations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from moral_eval.airisk_semantic_review.core import SemanticReviewError  # noqa: E402
from moral_eval.airisk_semantic_review.transformation_audit import (  # noqa: E402
    audit_transformation_file,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transformations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--near-duplicate-threshold", type=float, default=0.80)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = audit_transformation_file(
            transformations_path=args.transformations,
            output_path=args.output,
            near_duplicate_threshold=args.near_duplicate_threshold,
            overwrite=args.overwrite,
        )
    except (OSError, ValueError, SemanticReviewError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
