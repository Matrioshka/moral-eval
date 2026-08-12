#!/usr/bin/env python
"""Prepare strongly blinded AIRisk JMCUP independent final-validation inputs."""

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
from moral_eval.airisk_semantic_review.final_validation import (  # noqa: E402
    prepare_final_validation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transformations", type=Path, required=True)
    parser.add_argument("--transformation-schema", type=Path)
    parser.add_argument("--source-payloads", type=Path, required=True)
    parser.add_argument("--resolved-reviews", type=Path, required=True)
    parser.add_argument("--eligibility", type=Path, required=True)
    parser.add_argument("--source-resolution-manifest", type=Path, required=True)
    parser.add_argument("--author-inputs", type=Path)
    parser.add_argument("--author-run-records", type=Path)
    parser.add_argument("--author-raw-outputs", type=Path)
    parser.add_argument("--author-run-manifest", type=Path)
    parser.add_argument("--author-preparation-manifest", type=Path)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--group-id", action="append", dest="group_ids")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kwargs = {}
    if args.transformation_schema is not None:
        kwargs["transformation_schema_path"] = args.transformation_schema
    try:
        result = prepare_final_validation(
            transformations_path=args.transformations,
            source_payloads_path=args.source_payloads,
            resolved_reviews_path=args.resolved_reviews,
            eligibility_path=args.eligibility,
            source_resolution_manifest_path=args.source_resolution_manifest,
            output_dir=args.output_dir,
            seed=args.seed,
            author_inputs_path=args.author_inputs,
            author_run_records_path=args.author_run_records,
            author_raw_outputs_path=args.author_raw_outputs,
            author_run_manifest_path=args.author_run_manifest,
            author_preparation_manifest_path=args.author_preparation_manifest,
            group_ids=args.group_ids,
            overwrite=args.overwrite,
            **kwargs,
        )
    except (OSError, ValueError, SemanticReviewError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    public_result = {
        key: value
        for key, value in result.items()
        if key not in {"deterministic_seed", "mappings"}
    }
    print(json.dumps(public_result, indent=2, ensure_ascii=False))
    print("No external API call was made; this command only prepares blinded inputs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
