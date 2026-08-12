#!/usr/bin/env python
"""Prepare blinded authoring payloads for exactly eligible AIRisk source groups."""

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
from moral_eval.airisk_semantic_review.transformation import (  # noqa: E402
    prepare_transformation_inputs,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolved-reviews", type=Path, required=True)
    parser.add_argument("--eligibility", type=Path, required=True)
    parser.add_argument("--source-payloads", type=Path, required=True)
    parser.add_argument("--source-resolution-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--group-id", action="append", dest="group_ids")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = prepare_transformation_inputs(
            resolved_reviews_path=args.resolved_reviews,
            eligibility_path=args.eligibility,
            source_payloads_path=args.source_payloads,
            source_resolution_manifest_path=args.source_resolution_manifest,
            output_dir=args.output_dir,
            group_ids=args.group_ids,
            overwrite=args.overwrite,
        )
    except (OSError, ValueError, SemanticReviewError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("No external API call was made; this command only prepares blinded inputs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
