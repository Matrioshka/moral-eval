#!/usr/bin/env python
"""Prepare criterion-level comparison for two completed independent review runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from moral_eval.airisk_semantic_review.adjudication import prepare_adjudication  # noqa: E402
from moral_eval.airisk_semantic_review.core import (  # noqa: E402
    DEFAULT_INPUT_SCHEMA_PATH,
    DEFAULT_RESPONSE_SCHEMA_PATH,
    DEFAULT_V3_QUEUE_PATH,
    SemanticReviewError,
    build_payload_corpus,
    read_jsonl,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-a", type=Path, required=True)
    parser.add_argument("--review-b", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--queue", type=Path, default=DEFAULT_V3_QUEUE_PATH)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--consensus-candidate-qc-rate", type=float, default=0.10)
    parser.add_argument("--consensus-reject-qc-rate", type=float, default=0.05)
    parser.add_argument("--input-schema", type=Path, default=DEFAULT_INPUT_SCHEMA_PATH)
    parser.add_argument("--response-schema", type=Path, default=DEFAULT_RESPONSE_SCHEMA_PATH)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        queue_records = read_jsonl(args.queue)
        expected_group_ids = [record["generation_group_id"] for record in queue_records]
        blinded_payloads = build_payload_corpus(
            queue_records, expected_group_count=None
        )
        summary = prepare_adjudication(
            args.review_a,
            args.review_b,
            output_dir=args.output_dir,
            blinded_payloads=blinded_payloads,
            seed=args.seed,
            expected_group_ids=expected_group_ids,
            consensus_clean_qc_rate=args.consensus_candidate_qc_rate,
            consensus_reject_qc_rate=args.consensus_reject_qc_rate,
            input_schema_path=args.input_schema,
            response_schema_path=args.response_schema,
            overwrite=args.overwrite,
        )
    except (OSError, ValueError, SemanticReviewError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
