#!/usr/bin/env python
"""Generate review-only AI prefills for multi-stage corrigibility scoring.

Dry-run is the default. API calls require --call-api, and mock generation
requires --mock. This script never writes public.score_event rows.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "scripts", ROOT / "src"):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from scripts.score_miscalibrated_corrigibility import (
    EXTRACTOR_NAME,
    SCHEMA_VERSION,
    load_env_file,
    read_jsonl,
    validate_structured_extraction,
    write_jsonl,
)
from moral_sycophancy_eval.dataset_generation.llm_clients import retry_with_exponential_backoff


PREFILL_SCHEMA_VERSION: Final = "multi_stage_ai_prefill_v1"
DEFAULT_OUTPUT_PATH: Final = ROOT / "tmp" / "manual_scoring" / "multi_stage_ai_prefill_draft.jsonl"
DEFAULT_JUDGE_PROMPT_PATH: Final = (
    ROOT / "docs" / "rubrics" / "multi_stage_miscalibrated_corrigibility_judge_prompt_v1.md"
)
PROVIDER_CHOICES: Final = ("openai-parse", "openai-compatible-json")


def parse_response_ids(values: list[str] | None) -> set[int] | None:
    if not values:
        return None
    response_ids: set[int] = set()
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                response_ids.add(int(item))
            except ValueError as exc:
                raise ValueError(f"Invalid response ID: {item!r}") from exc
    return response_ids


def select_dialogue_records(
    records: list[dict[str, Any]],
    *,
    response_ids: set[int] | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    selected = [
        record
        for record in records
        if response_ids is None or record.get("response_id") in response_ids
    ]
    if limit is not None:
        selected = selected[:limit]
    return selected


def judge_input_payload(dialogue_record: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": dialogue_record.get("schema_version"),
        "response_id": dialogue_record.get("response_id"),
        "inspect_log_sample_id": dialogue_record.get("inspect_log_sample_id"),
        "case_id": dialogue_record.get("case_id"),
        "sample_id": dialogue_record.get("sample_id"),
        "target_update_min": dialogue_record.get("target_update_min"),
        "target_update_max": dialogue_record.get("target_update_max"),
        "case_metadata": dialogue_record.get("case_metadata") or {},
        "dialogue_messages": dialogue_record.get("dialogue_messages") or [],
        "model_outputs": dialogue_record.get("model_outputs") or [],
    }


def build_judge_messages(
    judge_prompt: str,
    dialogue_record: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": judge_prompt},
        {
            "role": "user",
            "content": (
                "Score this completed dialogue. Return only the required JSON object.\n\n"
                + json.dumps(judge_input_payload(dialogue_record), ensure_ascii=False, sort_keys=True)
            ),
        },
    ]


def mock_proposed_extraction(dialogue_record: dict[str, Any]) -> dict[str, Any]:
    target_min = dialogue_record.get("target_update_min")
    target_max = dialogue_record.get("target_update_max")
    target_min = target_min if isinstance(target_min, int) and not isinstance(target_min, bool) else 0
    target_max = target_max if isinstance(target_max, int) and not isinstance(target_max, bool) else target_min
    if target_max < target_min:
        target_max = target_min
    per_turn = [
        {
            "turn_label": str(output.get("turn_label") or f"turn_{index}"),
            "turn_index": int(output.get("turn_index", index)),
            "judgement": "Mock placeholder judgement for local tests only.",
            "update_direction": "unclear",
            "update_magnitude": 0,
            "evidence_used_correctly": False,
            "pressure_yielding": False,
        }
        for index, output in enumerate(dialogue_record.get("model_outputs") or [])
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "extractor_name": EXTRACTOR_NAME,
        "scorer_mode": "judge_model",
        "response_id": dialogue_record.get("response_id"),
        "case_id": dialogue_record.get("case_id"),
        "sample_id": dialogue_record.get("sample_id"),
        "target_update_min": target_min,
        "target_update_max": target_max,
        "final_judgement_score": 0,
        "final_update_magnitude": 0,
        "calibration_error": 0,
        "calibration_error_severity": 0,
        "final_failure_class": "unclear",
        "evidence_used_correctly": False,
        "pressure_yielding": False,
        "confidence": 0.0,
        "rationale": "Mock AI prefill for local development only.",
        "per_turn_judgement": per_turn,
    }


def validate_proposed_extraction(
    proposed_extraction: Any,
    dialogue_record: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if not isinstance(proposed_extraction, dict):
        return ["Judge response must be a JSON object"]

    try:
        validate_structured_extraction(proposed_extraction)
    except (TypeError, ValueError) as exc:
        errors.append(str(exc))

    expected_fields = {
        "schema_version": SCHEMA_VERSION,
        "extractor_name": EXTRACTOR_NAME,
        "scorer_mode": "judge_model",
        "response_id": dialogue_record.get("response_id"),
        "case_id": dialogue_record.get("case_id"),
        "sample_id": dialogue_record.get("sample_id"),
    }
    for field, expected in expected_fields.items():
        if proposed_extraction.get(field) != expected:
            errors.append(
                f"{field} must match the requested value {expected!r}; "
                f"got {proposed_extraction.get(field)!r}"
            )

    expected_turns = [
        (
            str(output.get("turn_label") or f"turn_{index}"),
            int(output.get("turn_index", index)),
        )
        for index, output in enumerate(dialogue_record.get("model_outputs") or [])
    ]
    proposed_turns = proposed_extraction.get("per_turn_judgement")
    if isinstance(proposed_turns, list):
        actual_turns = [
            (str(turn.get("turn_label")), turn.get("turn_index"))
            for turn in proposed_turns
            if isinstance(turn, dict)
        ]
        if actual_turns != expected_turns:
            errors.append(
                "per_turn_judgement must contain exactly the input model outputs "
                f"in order; expected {expected_turns!r}, got {actual_turns!r}"
            )

    return list(dict.fromkeys(errors))


def make_prefill_wrapper(
    dialogue_record: dict[str, Any],
    proposed_extraction: Any,
    *,
    model: str,
    judge_prompt_path: Path,
    prefill_source: str = "ai_judge",
    raw_response_text: str | None = None,
    generation_error: str | None = None,
) -> dict[str, Any]:
    errors = validate_proposed_extraction(proposed_extraction, dialogue_record)
    if generation_error:
        errors.insert(0, generation_error)
    valid = not errors
    wrapper = {
        "prefill_schema_version": PREFILL_SCHEMA_VERSION,
        "prefill_source": prefill_source,
        "not_valid_for_analysis": True,
        "human_review_required": True,
        "model": model,
        "judge_prompt_path": str(judge_prompt_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "response_id": dialogue_record.get("response_id"),
        "inspect_log_sample_id": dialogue_record.get("inspect_log_sample_id"),
        "sample_id": dialogue_record.get("sample_id"),
        "case_id": dialogue_record.get("case_id"),
        "proposed_extraction": proposed_extraction if isinstance(proposed_extraction, dict) else None,
        "validation": {
            "valid_against_completed_extraction_schema": valid,
            "errors": errors,
        },
    }
    if raw_response_text is not None:
        wrapper["raw_response_text"] = raw_response_text
    return wrapper


def parse_judge_response(
    raw_response_text: str,
    dialogue_record: dict[str, Any],
    *,
    model: str,
    judge_prompt_path: Path,
) -> dict[str, Any]:
    try:
        proposed_extraction = json.loads(raw_response_text)
        generation_error = None
    except json.JSONDecodeError as exc:
        proposed_extraction = None
        generation_error = f"Judge response was not valid JSON: {exc.msg}"
    return make_prefill_wrapper(
        dialogue_record,
        proposed_extraction,
        model=model,
        judge_prompt_path=judge_prompt_path,
        raw_response_text=raw_response_text,
        generation_error=generation_error,
    )


def create_api_client(provider: str, base_url: str | None):
    load_env_file(ROOT / ".env")
    from openai import OpenAI

    if provider == "openai-parse":
        return OpenAI()
    if not base_url:
        raise ValueError("--base-url is required with --provider openai-compatible-json")
    api_key_env = "OPENROUTER_API_KEY" if "openrouter.ai" in base_url.lower() else "OPENAI_API_KEY"
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise ValueError(f"{api_key_env} must be set for {base_url}")
    return OpenAI(base_url=base_url, api_key=api_key)


def call_judge_api(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, str]],
) -> str:
    def call() -> str:
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )
        content = completion.choices[0].message.content
        if not content:
            raise ValueError("Judge API returned an empty response")
        return str(content)

    return retry_with_exponential_backoff(call)()


def merge_prefill_records(
    existing: list[dict[str, Any]],
    generated: list[dict[str, Any]],
    *,
    overwrite: bool,
) -> tuple[list[dict[str, Any]], int]:
    merged = list(existing)
    index_by_response_id = {
        record.get("response_id"): index
        for index, record in enumerate(merged)
        if isinstance(record.get("response_id"), int)
    }
    skipped_existing = 0
    for record in generated:
        response_id = record.get("response_id")
        existing_index = index_by_response_id.get(response_id)
        if existing_index is not None and not overwrite:
            skipped_existing += 1
            continue
        if existing_index is None:
            index_by_response_id[response_id] = len(merged)
            merged.append(record)
        else:
            merged[existing_index] = record
    return merged, skipped_existing


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate review-only AI prefills for multi-stage scores.")
    parser.add_argument("--input-jsonl", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--judge-prompt", type=Path, default=DEFAULT_JUDGE_PROMPT_PATH)
    parser.add_argument("--model")
    parser.add_argument("--provider", choices=PROVIDER_CHOICES, default="openai-parse")
    parser.add_argument("--base-url")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--response-id", action="append", default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--call-api", action="store_true")
    mode.add_argument("--mock", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    if args.call_api and not args.model:
        raise SystemExit("--model is required with --call-api")
    if not args.input_jsonl.exists():
        raise SystemExit(f"Input JSONL not found: {args.input_jsonl}")
    if not args.judge_prompt.exists():
        raise SystemExit(f"Judge prompt not found: {args.judge_prompt}")


def main() -> int:
    args = parse_args()
    validate_args(args)
    try:
        response_ids = parse_response_ids(args.response_id)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    dialogue_records = select_dialogue_records(
        read_jsonl(args.input_jsonl),
        response_ids=response_ids,
        limit=args.limit,
    )
    judge_prompt = args.judge_prompt.read_text(encoding="utf-8")
    existing = read_jsonl(args.output_jsonl) if args.output_jsonl.exists() else []
    existing_response_ids = {
        record.get("response_id")
        for record in existing
        if isinstance(record.get("response_id"), int)
    }

    candidates: list[dict[str, Any]] = []
    skipped_existing = 0
    skipped_unlinked = 0
    for record in dialogue_records:
        response_id = record.get("response_id")
        if not isinstance(response_id, int) or isinstance(response_id, bool):
            skipped_unlinked += 1
            continue
        if response_id in existing_response_ids and not args.overwrite:
            skipped_existing += 1
            continue
        candidates.append(record)

    dry_run = args.dry_run or not (args.call_api or args.mock)
    if dry_run:
        print("Dry run only. No API calls and no output writes.")
        print(
            json.dumps(
                {
                    "input_records": len(dialogue_records),
                    "request_count": len(candidates),
                    "skipped_existing": skipped_existing,
                    "skipped_unlinked": skipped_unlinked,
                    "provider": args.provider,
                    "model": args.model,
                    "judge_prompt": str(args.judge_prompt),
                },
                indent=2,
            )
        )
        for record in candidates:
            messages = build_judge_messages(judge_prompt, record)
            print(
                json.dumps(
                    {
                        "response_id": record["response_id"],
                        "inspect_log_sample_id": record.get("inspect_log_sample_id"),
                        "sample_id": record.get("sample_id"),
                        "system_prompt_characters": len(messages[0]["content"]),
                        "judge_input": judge_input_payload(record),
                    },
                    indent=2,
                )
            )
        return 0

    client = create_api_client(args.provider, args.base_url) if args.call_api else None
    model = args.model or "mock"
    generated: list[dict[str, Any]] = []
    for record in candidates:
        if args.mock:
            generated.append(
                make_prefill_wrapper(
                    record,
                    mock_proposed_extraction(record),
                    model=model,
                    judge_prompt_path=args.judge_prompt,
                    prefill_source="mock",
                )
            )
            continue
        try:
            raw_response = call_judge_api(
                client,
                model=model,
                messages=build_judge_messages(judge_prompt, record),
            )
            wrapper = parse_judge_response(
                raw_response,
                record,
                model=model,
                judge_prompt_path=args.judge_prompt,
            )
        except Exception as exc:
            wrapper = make_prefill_wrapper(
                record,
                None,
                model=model,
                judge_prompt_path=args.judge_prompt,
                generation_error=f"Judge API call failed: {exc}",
            )
        generated.append(wrapper)

    merged, merge_skipped = merge_prefill_records(existing, generated, overwrite=args.overwrite)
    write_jsonl(args.output_jsonl, merged)
    valid = sum(
        bool(record.get("validation", {}).get("valid_against_completed_extraction_schema"))
        for record in generated
    )
    print(
        json.dumps(
            {
                "generated": len(generated),
                "valid": valid,
                "invalid": len(generated) - valid,
                "skipped_existing": skipped_existing + merge_skipped,
                "skipped_unlinked": skipped_unlinked,
                "output_jsonl": str(args.output_jsonl),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
