#!/usr/bin/env python
"""Prepare deterministic offline AIRisk JMCUP human-review bundles."""

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
from moral_eval.airisk_semantic_review.human_review import (  # noqa: E402
    prepare_final_human_review_bundle,
    prepare_source_human_review_bundle,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="stage", required=True)

    source = subparsers.add_parser("source", help="Prepare source-review human holds")
    source.add_argument("--source-payloads", type=Path, required=True)
    source.add_argument("--resolved-reviews", type=Path, required=True)
    source.add_argument("--eligibility", type=Path, required=True)
    source.add_argument("--comparisons", type=Path, required=True)
    source.add_argument("--source-resolution-manifest", type=Path)
    source.add_argument("--output-dir", type=Path, required=True)
    source.add_argument("--overwrite", action="store_true")

    final = subparsers.add_parser(
        "final", help="Prepare final-transformation human reviews"
    )
    final.add_argument("--transformations", type=Path, required=True)
    final.add_argument("--final-validations", type=Path, required=True)
    final.add_argument("--source-payloads", type=Path, required=True)
    final.add_argument("--output-dir", type=Path, required=True)
    final.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.stage == "source":
            result = prepare_source_human_review_bundle(
                source_payloads_path=args.source_payloads,
                resolved_reviews_path=args.resolved_reviews,
                eligibility_path=args.eligibility,
                comparisons_path=args.comparisons,
                source_resolution_manifest_path=args.source_resolution_manifest,
                output_dir=args.output_dir,
                overwrite=args.overwrite,
            )
        else:
            result = prepare_final_human_review_bundle(
                transformations_path=args.transformations,
                final_validations_path=args.final_validations,
                source_payloads_path=args.source_payloads,
                output_dir=args.output_dir,
                overwrite=args.overwrite,
            )
    except (OSError, ValueError, SemanticReviewError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("No external API call was made; this command only prepares offline review files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
