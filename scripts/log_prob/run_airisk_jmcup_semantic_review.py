#!/usr/bin/env python
"""Plan or explicitly execute one provider-neutral blinded reviewer run."""

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
    DEFAULT_INPUT_SCHEMA_PATH,
    DEFAULT_PROMPT_PATH,
    DEFAULT_RESPONSE_SCHEMA_PATH,
    DEFAULT_RUN_RECORD_SCHEMA_PATH,
    DEFAULT_V3_QUEUE_PATH,
    SemanticReviewError,
    build_payload_corpus,
    load_schema,
    read_jsonl,
)
from moral_eval.airisk_semantic_review.execution import (  # noqa: E402
    ReviewRunConfig,
    run_reviews,
    select_payloads,
)
from moral_eval.airisk_semantic_review.providers import SUPPORTED_PROVIDERS  # noqa: E402


DEFAULT_OUTPUT_ROOT = (
    ROOT / "tmp" / "airiskdilemmas_jmcup_semantic_review_v1" / "reviewer_runs"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=SUPPORTED_PROVIDERS, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--group-id", action="append", dest="group_ids")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Optional sampling temperature; omitted by default for model compatibility.",
    )
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--initial-retry-delay-seconds", type=float, default=2.0)
    parser.add_argument("--queue", type=Path, default=DEFAULT_V3_QUEUE_PATH)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT_PATH)
    parser.add_argument("--input-schema", type=Path, default=DEFAULT_INPUT_SCHEMA_PATH)
    parser.add_argument("--response-schema", type=Path, default=DEFAULT_RESPONSE_SCHEMA_PATH)
    parser.add_argument("--run-record-schema", type=Path, default=DEFAULT_RUN_RECORD_SCHEMA_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.execute and args.dry_run:
        print("ERROR: --execute and --dry-run are mutually exclusive", file=sys.stderr)
        return 2
    try:
        queue_records = read_jsonl(args.queue)
        payloads = build_payload_corpus(
            queue_records,
            input_schema=load_schema(args.input_schema),
        )
        selected = select_payloads(
            payloads,
            group_ids=args.group_ids,
            limit=args.limit,
        )
        config = ReviewRunConfig(
            provider=args.provider,
            requested_model=args.model,
            reviewer_run_id=args.run_id,
            temperature=args.temperature,
            max_output_tokens=args.max_output_tokens,
            concurrency=args.concurrency,
            max_retries=args.max_retries,
            initial_retry_delay_seconds=args.initial_retry_delay_seconds,
        )
        result = run_reviews(
            config=config,
            payloads=selected,
            queue_path=args.queue,
            output_root=args.output_root,
            execute=args.execute,
            resume=args.resume,
            prompt_path=args.prompt,
            response_schema_path=args.response_schema,
            run_record_schema_path=args.run_record_schema,
        )
    except (OSError, ValueError, SemanticReviewError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not args.execute:
        print("No external API call was made. Add --execute only when the run is authorised.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
