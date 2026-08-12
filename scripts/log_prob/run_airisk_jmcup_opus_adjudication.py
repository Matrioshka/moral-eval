#!/usr/bin/env python
"""Plan or explicitly execute blinded Opus adjudication records."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from moral_eval.airisk_semantic_review.adjudication_execution import (  # noqa: E402
    AdjudicationRunConfig,
    run_adjudications,
)
from moral_eval.airisk_semantic_review.core import (  # noqa: E402
    SemanticReviewError,
    read_jsonl,
)


DEFAULT_OUTPUT_ROOT = (
    ROOT / "tmp" / "airiskdilemmas_jmcup_semantic_review_v1" / "adjudicator_runs"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payloads", type=Path, required=True)
    parser.add_argument("--provider", choices=("anthropic", "openrouter"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--initial-retry-delay-seconds", type=float, default=2.0)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payloads = read_jsonl(args.payloads)
        if args.limit is not None:
            if args.limit < 1:
                raise SemanticReviewError("--limit must be at least 1")
            payloads = payloads[: args.limit]
        result = run_adjudications(
            config=AdjudicationRunConfig(
                provider=args.provider,
                requested_model=args.model,
                adjudicator_run_id=args.run_id,
                temperature=args.temperature,
                max_output_tokens=args.max_output_tokens,
                concurrency=args.concurrency,
                max_retries=args.max_retries,
                initial_retry_delay_seconds=args.initial_retry_delay_seconds,
            ),
            payloads=payloads,
            output_root=args.output_root,
            execute=args.execute,
            resume=args.resume,
        )
    except (OSError, ValueError, SemanticReviewError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not args.execute:
        print("No external API call was made. Add --execute only when authorised.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
