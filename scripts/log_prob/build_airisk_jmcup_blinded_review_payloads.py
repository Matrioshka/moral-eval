#!/usr/bin/env python
"""Build and audit all blinded AIRiskDilemmas semantic-review payloads offline."""

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
    DEFAULT_V3_MANIFEST_PATH,
    DEFAULT_V3_QUEUE_PATH,
    SemanticReviewError,
    build_payload_corpus,
    dry_run_summary,
    input_payload_sha256,
    load_prompt,
    load_schema,
    read_json,
    read_jsonl,
    sha256_path,
    validation_errors,
    verify_preserved_audit_sample,
    write_jsonl,
)


def build_and_report(args: argparse.Namespace) -> dict:
    queue_records = read_jsonl(args.queue)
    v3_manifest = read_json(args.v3_manifest)
    prompt = load_prompt(args.prompt)
    input_schema = load_schema(args.input_schema)
    response_schema = load_schema(args.response_schema)
    run_record_schema = load_schema(args.run_record_schema)
    # Constructing validators checks the canonical schemas even though an empty
    # object is intentionally not a valid review/run record.
    validation_errors({}, response_schema)
    validation_errors({}, run_record_schema)
    payloads = build_payload_corpus(queue_records, input_schema=input_schema)
    audit_proof = verify_preserved_audit_sample(queue_records, v3_manifest)
    report = dry_run_summary(
        payloads,
        prompt=prompt,
        prompt_path=args.prompt,
        input_schema_path=args.input_schema,
        response_schema_path=args.response_schema,
        audit_proof=audit_proof,
    )
    report["source_queue"] = {
        "path": str(args.queue.resolve()),
        "sha256": sha256_path(args.queue),
        "review_schema_versions": sorted(
            {record["review_schema_version"] for record in queue_records}
        ),
    }
    report["payload_corpus_sha256"] = input_payload_sha256(
        {
            "ordered_payload_sha256": [
                input_payload_sha256(payload) for payload in payloads
            ]
        }
    )

    if args.output_dir is not None:
        output_dir = args.output_dir.resolve()
        payload_path = output_dir / "blinded_group_payloads.jsonl"
        report_path = output_dir / "blinded_payload_dry_run_report.json"
        existing = [path for path in (payload_path, report_path) if path.exists()]
        if existing and not args.overwrite:
            raise SemanticReviewError(
                "Refusing to overwrite blinded payload products: "
                + ", ".join(str(path) for path in existing)
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        write_jsonl(payload_path, payloads)
        report["outputs"] = {
            "blinded_payloads": str(payload_path),
            "blinded_payloads_sha256": sha256_path(payload_path),
            "reviewer_judgements_written": 0,
            "report": str(report_path),
        }
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=DEFAULT_V3_QUEUE_PATH)
    parser.add_argument("--v3-manifest", type=Path, default=DEFAULT_V3_MANIFEST_PATH)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT_PATH)
    parser.add_argument("--input-schema", type=Path, default=DEFAULT_INPUT_SCHEMA_PATH)
    parser.add_argument("--response-schema", type=Path, default=DEFAULT_RESPONSE_SCHEMA_PATH)
    parser.add_argument("--run-record-schema", type=Path, default=DEFAULT_RUN_RECORD_SCHEMA_PATH)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly document that this offline command writes no reviewer judgements.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_and_report(args)
    except (OSError, ValueError, SemanticReviewError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
