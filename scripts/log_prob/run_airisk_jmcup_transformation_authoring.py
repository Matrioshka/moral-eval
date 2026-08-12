#!/usr/bin/env python
"""Plan or explicitly execute provider-neutral AIRisk transformation authoring."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from moral_eval.airisk_semantic_review.core import (  # noqa: E402
    SemanticReviewError,
    read_jsonl,
)
from moral_eval.airisk_semantic_review.execution import select_payloads  # noqa: E402
from moral_eval.airisk_semantic_review.providers import SUPPORTED_PROVIDERS  # noqa: E402
from moral_eval.airisk_semantic_review.transformation_execution import (  # noqa: E402
    TransformationRunConfig,
    run_transformations,
)


DEFAULT_OUTPUT_ROOT = (
    ROOT / "tmp" / "airiskdilemmas_jmcup_semantic_review_v1" / "transformation_runs"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payloads", type=Path, required=True)
    parser.add_argument("--preparation-manifest", type=Path, required=True)
    parser.add_argument("--provider", choices=SUPPORTED_PROVIDERS, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--group-id", action="append", dest="group_ids")
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
        payloads = select_payloads(
            read_jsonl(args.payloads),
            group_ids=args.group_ids,
            limit=args.limit,
        )
        result = run_transformations(
            config=TransformationRunConfig(
                provider=args.provider,
                requested_model=args.model,
                transformation_run_id=args.run_id,
                temperature=args.temperature,
                max_output_tokens=args.max_output_tokens,
                concurrency=args.concurrency,
                max_retries=args.max_retries,
                initial_retry_delay_seconds=args.initial_retry_delay_seconds,
            ),
            payloads=payloads,
            payloads_path=args.payloads,
            preparation_manifest_path=args.preparation_manifest,
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
