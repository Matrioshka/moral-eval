#!/usr/bin/env python
"""Append or audit offline AIRisk JMCUP human decisions."""

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
    FINAL_STAGE,
    SOURCE_STAGE,
    append_human_decisions,
    audit_human_decisions,
)


def _add_stage(subparsers: argparse._SubParsersAction, command: str) -> None:
    for label in ("source", "final"):
        parser = subparsers.add_parser(f"{command}-{label}")
        parser.set_defaults(review_stage=SOURCE_STAGE if label == "source" else FINAL_STAGE)
        parser.add_argument("--queue", type=Path, required=True)
        parser.add_argument("--ledger", type=Path, required=True)
        if command == "record":
            parser.add_argument("--decisions", type=Path, required=True)
        else:
            parser.add_argument("--output", type=Path)
            parser.add_argument("--overwrite", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_stage(subparsers, "record")
    _add_stage(subparsers, "audit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command.startswith("record-"):
            result = append_human_decisions(
                stage=args.review_stage,
                queue_path=args.queue,
                submissions_path=args.decisions,
                ledger_path=args.ledger,
            )
        else:
            result = audit_human_decisions(
                stage=args.review_stage,
                queue_path=args.queue,
                ledger_path=args.ledger,
                output_path=args.output,
                overwrite=args.overwrite,
            )
    except (OSError, ValueError, SemanticReviewError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("No external API call was made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
