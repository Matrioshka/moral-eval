#!/usr/bin/env python
"""Audit a persisted AIRisk JMCUP adjudicator run entirely offline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from moral_eval.airisk_semantic_review.adjudication_audit import (  # noqa: E402
    audit_adjudicator_run,
)
from moral_eval.airisk_semantic_review.core import SemanticReviewError, sha256_path  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preparation-manifest", type=Path, required=True)
    parser.add_argument("--payloads", type=Path, required=True)
    parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument("--adjudications", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        audit = audit_adjudicator_run(
            preparation_manifest_path=args.preparation_manifest,
            adjudication_payloads_path=args.payloads,
            run_manifest_path=args.run_manifest,
            adjudication_records_path=args.adjudications,
            output_path=args.output,
            overwrite=args.overwrite,
        )
    except (OSError, ValueError, SemanticReviewError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    result = {
        "audit_path": str(args.output.resolve()),
        "audit_sha256": sha256_path(args.output),
        "selected_group_count": audit["selected_group_count"],
        "unique_completed_group_count": audit["unique_completed_group_count"],
        "unique_unresolved_group_count": audit["unique_unresolved_group_count"],
        "terminal_provider_block_group_ids": audit[
            "terminal_provider_block_group_ids"
        ],
        "external_api_calls": 0,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
