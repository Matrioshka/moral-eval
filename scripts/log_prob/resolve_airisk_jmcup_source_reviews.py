#!/usr/bin/env python
"""Merge reviewer comparisons and completed adjudications into source eligibility."""

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
from moral_eval.airisk_semantic_review.resolution import (  # noqa: E402
    merge_resolved_source_reviews,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparisons", type=Path, required=True)
    parser.add_argument("--sampling-manifest", type=Path, required=True)
    parser.add_argument("--private-provenance", type=Path, required=True)
    parser.add_argument("--opus-payloads", type=Path, required=True)
    parser.add_argument("--adjudications", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = merge_resolved_source_reviews(
            comparison_path=args.comparisons,
            sampling_manifest_path=args.sampling_manifest,
            private_provenance_path=args.private_provenance,
            opus_payloads_path=args.opus_payloads,
            adjudication_records_path=args.adjudications,
            output_dir=args.output_dir,
            overwrite=args.overwrite,
        )
    except (OSError, ValueError, SemanticReviewError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
