"""Safe provider-neutral execution for AIRisk JMCUP transformation authoring."""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import (
    SemanticReviewError,
    canonical_json,
    load_prompt,
    load_schema,
    read_json,
    read_jsonl,
    sha256_path,
    sha256_text,
    utc_now,
    validate_instance,
)
from .execution import redact_secrets
from .providers import (
    PROVIDER_CREDENTIAL_ENVIRONMENT_VARIABLES,
    PROVIDER_ENVIRONMENT_VARIABLES,
    ReviewerProvider,
    create_provider_adapter,
    openrouter_transport_schema,
    parse_response_json,
    provider_transport_schema,
)
from .transformation import (
    DEFAULT_INPUT_SCHEMA_PATH,
    DEFAULT_LEXICAL_DIAGNOSTICS_PATH,
    DEFAULT_PROMPT_PATH,
    DEFAULT_RESPONSE_SCHEMA_PATH,
    DEFAULT_TRANSFORMATION_SCHEMA_PATH,
    TRANSFORMATION_PREPARATION_MANIFEST_VERSION,
    TRANSFORMATION_PROMPT_VERSION,
    build_canonical_transformation_record,
    find_forbidden_author_visible_keys,
    render_transformation_author_prompt,
    validate_author_response,
    validate_canonical_transformation_record,
)


TRANSFORMATION_RUN_RECORD_SCHEMA_VERSION = (
    "airisk_jmcup_transformation_run_record_v1"
)
TRANSFORMATION_RAW_OUTPUT_SCHEMA_VERSION = (
    "airisk_jmcup_transformation_raw_provider_output_v1"
)
TRANSFORMATION_RUN_MANIFEST_VERSION = "airisk_jmcup_transformation_run_manifest_v1"
DEFAULT_RUN_RECORD_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "schemas"
    / f"{TRANSFORMATION_RUN_RECORD_SCHEMA_VERSION}.schema.json"
)
DEFAULT_RAW_OUTPUT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "schemas"
    / f"{TRANSFORMATION_RAW_OUTPUT_SCHEMA_VERSION}.schema.json"
)
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class TransformationRunConfig:
    provider: str
    requested_model: str
    transformation_run_id: str
    temperature: float | None = None
    max_output_tokens: int = 8_192
    concurrency: int = 1
    max_retries: int = 2
    initial_retry_delay_seconds: float = 2.0

    def validate(self) -> None:
        if self.provider not in PROVIDER_ENVIRONMENT_VARIABLES:
            raise SemanticReviewError(f"Unsupported provider: {self.provider}")
        if not self.requested_model.strip():
            raise SemanticReviewError("--model must not be empty")
        if self.provider == "openrouter" and not self.requested_model.startswith(
            ("anthropic/", "google/")
        ):
            raise SemanticReviewError(
                "OpenRouter authoring requires an anthropic/* or google/* model slug"
            )
        if not RUN_ID_RE.fullmatch(self.transformation_run_id):
            raise SemanticReviewError("Invalid transformation --run-id")
        if self.concurrency < 1:
            raise SemanticReviewError("--concurrency must be at least 1")
        if self.max_output_tokens < 1:
            raise SemanticReviewError("--max-output-tokens must be positive")
        if self.max_retries < 0:
            raise SemanticReviewError("--max-retries must not be negative")
        if self.initial_retry_delay_seconds < 0:
            raise SemanticReviewError("Retry delay must not be negative")

    @property
    def model_settings(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
        }


def _append_checkpoint(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(record) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _transport_schema(
    config: TransformationRunConfig, response_schema: Mapping[str, Any]
) -> dict[str, Any]:
    if config.provider == "openrouter":
        return openrouter_transport_schema(config.requested_model, response_schema)
    return provider_transport_schema(config.provider, response_schema)


def _normalised_usage(provider_usage: Mapping[str, Any] | None) -> dict[str, Any]:
    usage = provider_usage or {}
    prompt_details = usage.get("prompt_tokens_details")
    prompt_details = prompt_details if isinstance(prompt_details, Mapping) else {}
    completion_details = usage.get("completion_tokens_details")
    completion_details = (
        completion_details if isinstance(completion_details, Mapping) else {}
    )

    def integer(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return None

    def number(value: Any) -> int | float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value

    return {
        "prompt_tokens": integer(usage.get("prompt_tokens")),
        "completion_tokens": integer(usage.get("completion_tokens")),
        "reasoning_tokens": integer(
            completion_details.get("reasoning_tokens", usage.get("reasoning_tokens"))
        ),
        "cached_tokens": integer(
            prompt_details.get("cached_tokens", usage.get("cached_tokens"))
        ),
        "total_tokens": integer(usage.get("total_tokens")),
        "cost": number(usage.get("cost")),
        "native_usage": dict(usage) if isinstance(provider_usage, Mapping) else None,
    }


def _aggregate_usage(attempts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fields = (
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "total_tokens",
        "cost",
    )
    aggregate: dict[str, Any] = {}
    counts = {}
    for field in fields:
        values = [
            attempt["usage"][field]
            for attempt in attempts
            if isinstance(attempt["usage"].get(field), (int, float))
            and not isinstance(attempt["usage"].get(field), bool)
        ]
        aggregate[field] = sum(values) if values else None
        counts[field] = len(values)
    aggregate["reported_attempt_counts"] = counts
    return aggregate


def _aggregate_raw_usage(raw_records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return _aggregate_usage(
        [{"usage": _normalised_usage(record.get("provider_usage"))} for record in raw_records]
    )


def _finish_reason(output_termination: Any) -> str | None:
    if not isinstance(output_termination, Mapping):
        return None
    value = output_termination.get("finish_reason")
    return value if isinstance(value, str) else None


def _raw_output_record(
    *,
    config: TransformationRunConfig,
    group_id: str,
    attempt_number: int,
    captured_at_utc: str,
    resolved_reported_model: str | None,
    provider_request_id: str | None,
    provider_generation_id: str | None,
    actual_routed_provider: str | None,
    terminal_status: str | None,
    output_termination: Any,
    provider_usage: Any,
    provider_block_metadata: Any,
    provider_reasoning: Any,
    provider_exception: Any,
    raw_response: Any,
    raw_response_text: str | None,
) -> dict[str, Any]:
    return {
        "record_schema_version": TRANSFORMATION_RAW_OUTPUT_SCHEMA_VERSION,
        "raw_output_id": (
            f"{config.transformation_run_id}:{group_id}:attempt-{attempt_number}"
        ),
        "transformation_run_id": config.transformation_run_id,
        "generation_group_id": group_id,
        "attempt_number": attempt_number,
        "provider": config.provider,
        "requested_model": config.requested_model,
        "captured_at_utc": captured_at_utc,
        "resolved_reported_model": resolved_reported_model,
        "provider_request_id": provider_request_id,
        "provider_generation_id": provider_generation_id,
        "actual_routed_provider": actual_routed_provider,
        "terminal_status": terminal_status,
        "output_termination": redact_secrets(output_termination),
        "provider_usage": redact_secrets(provider_usage),
        "provider_block_metadata": redact_secrets(provider_block_metadata),
        "provider_reasoning": redact_secrets(provider_reasoning),
        "provider_exception": redact_secrets(provider_exception),
        "raw_response": redact_secrets(raw_response),
        "raw_response_text": redact_secrets(raw_response_text),
        "secrets_redacted": True,
        "credential_material_persisted": False,
    }


def _attempt_summary(
    *,
    raw: Mapping[str, Any],
    raw_sha: str,
    status: str,
    validation_errors: Sequence[str],
    error: str | None,
) -> dict[str, Any]:
    return {
        "attempt_number": raw["attempt_number"],
        "status": status,
        "raw_provider_output_sha256": raw_sha,
        "resolved_reported_model": raw["resolved_reported_model"],
        "provider_request_id": raw["provider_request_id"],
        "provider_generation_id": raw["provider_generation_id"],
        "actual_routed_provider": raw["actual_routed_provider"],
        "finish_reason": _finish_reason(raw["output_termination"]),
        "output_termination": raw["output_termination"],
        "usage": _normalised_usage(raw["provider_usage"]),
        "provider_block_metadata": raw["provider_block_metadata"],
        "validation_errors": list(validation_errors),
        "error": error,
    }


def _author_one(
    *,
    adapter: ReviewerProvider,
    config: TransformationRunConfig,
    payload: Mapping[str, Any],
    group_provenance: Mapping[str, Any],
    source_review_stage_manifest_sha256: str,
    prompt: str,
    prompt_sha256: str,
    response_schema: Mapping[str, Any],
    response_schema_sha256: str,
    canonical_schema: Mapping[str, Any],
    canonical_schema_sha256: str,
    lexical_spec: Mapping[str, Any],
    lexical_spec_sha256: str,
    transport_schema_sha256: str,
    run_record_schema: Mapping[str, Any],
    raw_output_schema: Mapping[str, Any],
    attempt_start: int,
    prior_raw_records: Sequence[Mapping[str, Any]],
    sleep_fn: Callable[[float], None],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    group_id = payload["generation_group_id"]
    rendered_prompt = render_transformation_author_prompt(prompt, payload)
    raw_records: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    parsed: dict[str, Any] | None = None
    canonical_record: dict[str, Any] | None = None
    status = "provider_error"
    final_errors: list[str] = []

    for retry_index in range(config.max_retries + 1):
        attempt_number = attempt_start + retry_index
        captured = utc_now()
        raw_kwargs: dict[str, Any]
        attempt_error: str | None = None
        try:
            result = adapter.review(
                requested_model=config.requested_model,
                rendered_prompt=rendered_prompt,
                response_schema=response_schema,
                model_settings=config.model_settings,
            )
            raw_kwargs = {
                "resolved_reported_model": result.resolved_reported_model,
                "provider_request_id": result.provider_request_id,
                "provider_generation_id": result.provider_generation_id,
                "actual_routed_provider": result.actual_routed_provider,
                "terminal_status": result.terminal_status,
                "output_termination": result.output_termination,
                "provider_usage": result.provider_usage,
                "provider_block_metadata": result.provider_block_metadata,
                "provider_reasoning": result.provider_reasoning,
                "provider_exception": None,
                "raw_response": result.raw_response,
                "raw_response_text": result.raw_response_text,
            }
            if result.terminal_status in {"refused", "blocked"}:
                status = result.terminal_status
                final_errors = []
            elif result.terminal_status is not None:
                raise SemanticReviewError(
                    f"Unknown provider terminal status: {result.terminal_status}"
                )
            else:
                try:
                    candidate = parse_response_json(result.raw_response_text)
                    errors = validate_author_response(
                        candidate, payload, response_schema, lexical_spec
                    )
                except Exception as exc:
                    candidate = None
                    errors = [f"{type(exc).__name__}: {redact_secrets(str(exc))}"]
                if candidate is not None and not errors:
                    parsed = candidate
                    canonical_record = build_canonical_transformation_record(
                        candidate,
                        payload,
                        resolved_source_review_sha256=group_provenance[
                            "resolved_source_review_sha256"
                        ],
                        source_eligibility_sha256=group_provenance[
                            "source_eligibility_sha256"
                        ],
                        source_group_input_payload_sha256=group_provenance[
                            "source_group_input_payload_sha256"
                        ],
                        source_review_stage_manifest_sha256=(
                            source_review_stage_manifest_sha256
                        ),
                        transformation_run_id=config.transformation_run_id,
                        provider=config.provider,
                        requested_model=config.requested_model,
                        prompt_sha256=prompt_sha256,
                        response_schema_sha256=response_schema_sha256,
                        canonical_schema_sha256=canonical_schema_sha256,
                        lexical_spec=lexical_spec,
                        lexical_spec_sha256=lexical_spec_sha256,
                        created_at_utc=captured,
                    )
                    errors = validate_canonical_transformation_record(
                        canonical_record, canonical_schema, lexical_spec
                    )
                if not errors and canonical_record is not None:
                    status = "completed"
                    final_errors = []
                    if (
                        isinstance(result.output_termination, Mapping)
                        and result.output_termination.get("output_limit_reached") is True
                    ):
                        raw_kwargs["output_termination"] = {
                            **result.output_termination,
                            "valid_complete_response_recovered": True,
                        }
                else:
                    parsed = None
                    canonical_record = None
                    final_errors = list(errors)
                    if (
                        isinstance(result.output_termination, Mapping)
                        and result.output_termination.get("output_limit_reached") is True
                    ):
                        status = "truncated"
                    else:
                        status = "validation_failed"
                    attempt_error = "Provider response failed complete local validation"
        except Exception as exc:
            output_termination = getattr(exc, "output_termination", None)
            status = (
                "truncated"
                if isinstance(output_termination, Mapping)
                and output_termination.get("output_limit_reached") is True
                else "provider_error"
            )
            final_errors = []
            attempt_error = f"{type(exc).__name__}: {redact_secrets(str(exc))}"
            raw_kwargs = {
                "resolved_reported_model": getattr(
                    exc, "resolved_reported_model", None
                ),
                "provider_request_id": getattr(exc, "provider_request_id", None),
                "provider_generation_id": getattr(
                    exc, "provider_generation_id", None
                ),
                "actual_routed_provider": getattr(
                    exc, "actual_routed_provider", None
                ),
                "terminal_status": "truncated" if status == "truncated" else None,
                "output_termination": output_termination,
                "provider_usage": getattr(exc, "provider_usage", None),
                "provider_block_metadata": None,
                "provider_reasoning": getattr(exc, "provider_reasoning", None),
                "provider_exception": {
                    "exception_type": type(exc).__name__,
                    "message": redact_secrets(str(exc)),
                },
                "raw_response": getattr(exc, "raw_response", None),
                "raw_response_text": getattr(exc, "raw_response_text", None),
            }

        raw_record = _raw_output_record(
            config=config,
            group_id=group_id,
            attempt_number=attempt_number,
            captured_at_utc=captured,
            **raw_kwargs,
        )
        validate_instance(raw_record, raw_output_schema, label="raw provider output")
        raw_sha = sha256_text(canonical_json(raw_record))
        raw_records.append(raw_record)
        attempts.append(
            _attempt_summary(
                raw=raw_record,
                raw_sha=raw_sha,
                status=status,
                validation_errors=final_errors,
                error=attempt_error,
            )
        )
        no_retry = status in {"completed", "refused", "blocked", "truncated"}
        if no_retry or retry_index == config.max_retries:
            break
        sleep_fn(config.initial_retry_delay_seconds * (2**retry_index))

    final_attempt = attempts[-1]
    final_raw = raw_records[-1]
    record = {
        "record_schema_version": TRANSFORMATION_RUN_RECORD_SCHEMA_VERSION,
        "transformation_run_id": config.transformation_run_id,
        "generation_group_id": group_id,
        "provider": config.provider,
        "requested_model": config.requested_model,
        "resolved_reported_model": final_raw["resolved_reported_model"],
        "provider_request_id": final_raw["provider_request_id"],
        "provider_generation_id": final_raw["provider_generation_id"],
        "actual_routed_provider": final_raw["actual_routed_provider"],
        "provider_usage": final_raw["provider_usage"],
        "aggregate_usage": _aggregate_raw_usage([*prior_raw_records, *raw_records]),
        "output_termination": final_raw["output_termination"],
        "prompt_version": TRANSFORMATION_PROMPT_VERSION,
        "prompt_sha256": prompt_sha256,
        "author_response_schema_sha256": response_schema_sha256,
        "canonical_transformation_schema_sha256": canonical_schema_sha256,
        "input_payload_sha256": sha256_text(canonical_json(payload)),
        "transport_schema_sha256": transport_schema_sha256,
        "status": status,
        "parsed_author_response": parsed,
        "canonical_transformation_record": canonical_record,
        "canonical_transformation_record_sha256": (
            sha256_text(canonical_json(canonical_record))
            if canonical_record is not None
            else None
        ),
        "final_attempt_provenance": {
            "attempt_number": final_attempt["attempt_number"],
            "raw_provider_output_sha256": final_attempt[
                "raw_provider_output_sha256"
            ],
            "resolved_reported_model": final_raw["resolved_reported_model"],
            "provider_request_id": final_raw["provider_request_id"],
            "provider_generation_id": final_raw["provider_generation_id"],
            "actual_routed_provider": final_raw["actual_routed_provider"],
            "provider_usage": final_raw["provider_usage"],
            "output_termination": final_raw["output_termination"],
            "provider_block_metadata": final_raw["provider_block_metadata"],
        },
        "timestamp_utc": utc_now(),
        "model_settings": config.model_settings,
        "retry_information": {
            "max_retries": config.max_retries,
            "attempt_count": len(attempts),
            "attempts": attempts,
        },
        "validation_errors": final_errors,
        "provider_block_metadata": final_raw["provider_block_metadata"],
        "semantic_truth_deterministically_established": False,
    }
    validate_instance(record, run_record_schema, label="transformation run record")
    return raw_records, record


def _manifest_identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "schema_version",
        "transformation_run_id",
        "provider",
        "requested_model",
        "prompt_sha256",
        "author_response_schema_sha256",
        "canonical_transformation_schema_sha256",
        "raw_output_schema_sha256",
        "run_record_schema_sha256",
        "input_payloads_sha256",
        "selected_generation_group_ids",
        "model_settings",
        "max_retries",
    )
    return {key: manifest.get(key) for key in keys}


def _raw_index(
    path: Path, schema: Mapping[str, Any]
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, int],
    dict[str, list[dict[str, Any]]],
]:
    by_sha: dict[str, dict[str, Any]] = {}
    max_attempt: dict[str, int] = {}
    by_group: dict[str, list[dict[str, Any]]] = {}
    if not path.exists():
        return by_sha, max_attempt, by_group
    raw_ids = set()
    for record in read_jsonl(path):
        validate_instance(record, schema, label="stored raw provider output")
        raw_id = record["raw_output_id"]
        if raw_id in raw_ids:
            raise SemanticReviewError(f"Duplicate raw provider output ID: {raw_id}")
        raw_ids.add(raw_id)
        digest = sha256_text(canonical_json(record))
        by_sha[digest] = record
        group_id = record["generation_group_id"]
        max_attempt[group_id] = max(
            max_attempt.get(group_id, 0), record["attempt_number"]
        )
        by_group.setdefault(group_id, []).append(record)
    return by_sha, max_attempt, by_group


def _completed_records(
    path: Path,
    *,
    config: TransformationRunConfig,
    payload_hashes: Mapping[str, str],
    prompt_sha256: str,
    response_schema_sha256: str,
    canonical_schema_sha256: str,
    run_record_schema: Mapping[str, Any],
    canonical_schema: Mapping[str, Any],
    raw_by_sha: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    completed: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return completed
    for record in read_jsonl(path):
        validate_instance(record, run_record_schema, label="stored transformation run record")
        if record["status"] != "completed":
            continue
        group_id = record["generation_group_id"]
        if group_id in completed:
            raise SemanticReviewError(f"Duplicate completed transformation for {group_id}")
        identity_ok = (
            record["transformation_run_id"] == config.transformation_run_id
            and record["provider"] == config.provider
            and record["requested_model"] == config.requested_model
            and record["prompt_sha256"] == prompt_sha256
            and record["author_response_schema_sha256"] == response_schema_sha256
            and record["canonical_transformation_schema_sha256"]
            == canonical_schema_sha256
            and record["input_payload_sha256"] == payload_hashes.get(group_id)
        )
        if not identity_ok:
            raise SemanticReviewError(
                f"Completed transformation provenance differs for {group_id}"
            )
        canonical_record = record["canonical_transformation_record"]
        errors = validate_canonical_transformation_record(
            canonical_record, canonical_schema
        )
        if errors:
            raise SemanticReviewError(
                f"Stored completed canonical transformation is invalid for {group_id}: {errors}"
            )
        if record["canonical_transformation_record_sha256"] != sha256_text(
            canonical_json(canonical_record)
        ):
            raise SemanticReviewError(f"Canonical record hash differs for {group_id}")
        for attempt in record["retry_information"]["attempts"]:
            if attempt["raw_provider_output_sha256"] not in raw_by_sha:
                raise SemanticReviewError(
                    f"Run record references missing raw provider output for {group_id}"
                )
        completed[group_id] = record
    return completed


def _repair_canonical_projection(
    path: Path,
    completed: Mapping[str, Mapping[str, Any]],
    canonical_schema: Mapping[str, Any],
) -> None:
    projected: dict[str, dict[str, Any]] = {}
    if path.exists():
        for record in read_jsonl(path):
            errors = validate_canonical_transformation_record(record, canonical_schema)
            if errors:
                raise SemanticReviewError(
                    f"Stored canonical projection is invalid: {errors}"
                )
            group_id = record["generation_group_id"]
            if group_id in projected:
                raise SemanticReviewError(
                    f"Duplicate canonical transformation projection for {group_id}"
                )
            projected[group_id] = record
    for group_id, run_record in sorted(completed.items()):
        expected = run_record["canonical_transformation_record"]
        if group_id in projected:
            if canonical_json(projected[group_id]) != canonical_json(expected):
                raise SemanticReviewError(
                    f"Canonical projection conflicts with run journal for {group_id}"
                )
        else:
            _append_checkpoint(path, expected)


def run_transformations(
    *,
    config: TransformationRunConfig,
    payloads: Sequence[Mapping[str, Any]],
    payloads_path: Path,
    preparation_manifest_path: Path,
    output_root: Path,
    execute: bool,
    resume: bool,
    adapter: ReviewerProvider | None = None,
    prompt_path: Path = DEFAULT_PROMPT_PATH,
    input_schema_path: Path = DEFAULT_INPUT_SCHEMA_PATH,
    response_schema_path: Path = DEFAULT_RESPONSE_SCHEMA_PATH,
    canonical_schema_path: Path = DEFAULT_TRANSFORMATION_SCHEMA_PATH,
    lexical_spec_path: Path = DEFAULT_LEXICAL_DIAGNOSTICS_PATH,
    run_record_schema_path: Path = DEFAULT_RUN_RECORD_SCHEMA_PATH,
    raw_output_schema_path: Path = DEFAULT_RAW_OUTPUT_SCHEMA_PATH,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Plan or execute a transformation-authoring run; safe default is no call."""

    config.validate()
    input_schema = load_schema(input_schema_path)
    response_schema = load_schema(response_schema_path)
    canonical_schema = load_schema(canonical_schema_path)
    lexical_spec = read_json(lexical_spec_path)
    run_record_schema = load_schema(run_record_schema_path)
    raw_output_schema = load_schema(raw_output_schema_path)
    prompt = load_prompt(prompt_path)
    preparation = read_json(preparation_manifest_path)
    if preparation.get("schema_version") != TRANSFORMATION_PREPARATION_MANIFEST_VERSION:
        raise SemanticReviewError("Unexpected transformation preparation manifest")
    if preparation.get("payloads_sha256") != sha256_path(payloads_path):
        raise SemanticReviewError("Prepared author-input payload hash differs")
    if preparation.get("author_input_schema_sha256") != sha256_path(
        input_schema_path
    ):
        raise SemanticReviewError("Prepared author-input schema hash differs")
    prepared_payload_records = read_jsonl(payloads_path)
    prepared_payloads = {
        item["generation_group_id"]: item for item in prepared_payload_records
    }
    if len(prepared_payloads) != len(prepared_payload_records):
        raise SemanticReviewError("Prepared payload file contains duplicate group IDs")
    by_id: dict[str, dict[str, Any]] = {}
    for source in payloads:
        payload = dict(source)
        validate_instance(payload, input_schema, label="transformation author input")
        leaked = find_forbidden_author_visible_keys(payload)
        if leaked:
            raise SemanticReviewError(f"Forbidden author payload metadata: {leaked}")
        group_id = payload["generation_group_id"]
        if group_id in by_id:
            raise SemanticReviewError(f"Duplicate transformation input for {group_id}")
        if group_id not in prepared_payloads or canonical_json(
            prepared_payloads[group_id]
        ) != canonical_json(payload):
            raise SemanticReviewError(
                f"Selected author input differs from prepared payload for {group_id}"
            )
        by_id[group_id] = payload
    prepared_ids = set(preparation.get("selected_group_ids", []))
    if set(prepared_payloads) != prepared_ids or not set(by_id) <= prepared_ids:
        raise SemanticReviewError("Payload group set differs from preparation manifest")
    provenance = {
        item["generation_group_id"]: item
        for item in preparation.get("group_provenance", [])
    }
    if set(provenance) != prepared_ids:
        raise SemanticReviewError("Per-group preparation provenance is incomplete")

    prompt_sha = sha256_path(prompt_path)
    response_schema_sha = sha256_path(response_schema_path)
    canonical_schema_sha = sha256_path(canonical_schema_path)
    raw_schema_sha = sha256_path(raw_output_schema_path)
    run_schema_sha = sha256_path(run_record_schema_path)
    lexical_spec_sha = sha256_path(lexical_spec_path)
    transport_schema_sha = sha256_text(
        canonical_json(_transport_schema(config, response_schema))
    )
    run_dir = output_root.resolve() / config.provider / config.transformation_run_id
    raw_path = run_dir / "raw_provider_outputs.jsonl"
    records_path = run_dir / "transformation_run_records.jsonl"
    canonical_path = run_dir / "canonical_transformations.jsonl"
    manifest_path = run_dir / "run_manifest.json"
    plan = {
        "execute": execute,
        "external_api_calls_planned": len(payloads) if execute else 0,
        "transformation_run_id": config.transformation_run_id,
        "provider": config.provider,
        "requested_model": config.requested_model,
        "selected_group_count": len(payloads),
        "run_directory": str(run_dir),
        "safe_default_no_execute": not execute,
    }
    if not execute:
        return plan

    manifest = {
        "schema_version": TRANSFORMATION_RUN_MANIFEST_VERSION,
        "created_at_utc": utc_now(),
        "transformation_run_id": config.transformation_run_id,
        "provider": config.provider,
        "requested_model": config.requested_model,
        "prompt_version": TRANSFORMATION_PROMPT_VERSION,
        "prompt_sha256": prompt_sha,
        "author_input_schema_sha256": sha256_path(input_schema_path),
        "author_response_schema_sha256": response_schema_sha,
        "canonical_transformation_schema_sha256": canonical_schema_sha,
        "raw_output_schema_sha256": raw_schema_sha,
        "run_record_schema_sha256": run_schema_sha,
        "lexical_diagnostics_spec_sha256": lexical_spec_sha,
        "transport_schema_sha256": transport_schema_sha,
        "input_payloads_path": str(payloads_path.resolve()),
        "input_payloads_sha256": sha256_path(payloads_path),
        "preparation_manifest_sha256": sha256_path(preparation_manifest_path),
        "source_review_stage_manifest_sha256": preparation[
            "source_resolution_manifest_sha256"
        ],
        "selected_generation_group_ids": sorted(by_id),
        "model_settings": config.model_settings,
        "concurrency": config.concurrency,
        "max_retries": config.max_retries,
        "initial_retry_delay_seconds": config.initial_retry_delay_seconds,
        "credential_source_order": list(
            PROVIDER_CREDENTIAL_ENVIRONMENT_VARIABLES[config.provider]
        ),
        "credentials_persisted": False,
        "alternate_model_fallbacks_requested": False,
        "raw_provider_output_authoritative_path": str(raw_path),
        "run_records_reference_raw_by_sha256": True,
    }
    if resume and not run_dir.exists():
        raise SemanticReviewError(f"Cannot --resume absent run: {run_dir}")
    if run_dir.exists():
        if not resume:
            raise SemanticReviewError(f"Run exists; use --resume: {run_dir}")
        if not manifest_path.is_file():
            raise SemanticReviewError("Cannot resume without immutable run manifest")
        existing_manifest = read_json(manifest_path)
        if _manifest_identity(existing_manifest) != _manifest_identity(manifest):
            raise SemanticReviewError("Resume configuration differs from run manifest")
    else:
        run_dir.mkdir(parents=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    raw_by_sha, max_attempt, prior_raw_by_group = _raw_index(
        raw_path, raw_output_schema
    )
    payload_hashes = {
        group_id: sha256_text(canonical_json(payload))
        for group_id, payload in by_id.items()
    }
    completed = _completed_records(
        records_path,
        config=config,
        payload_hashes=payload_hashes,
        prompt_sha256=prompt_sha,
        response_schema_sha256=response_schema_sha,
        canonical_schema_sha256=canonical_schema_sha,
        run_record_schema=run_record_schema,
        canonical_schema=canonical_schema,
        raw_by_sha=raw_by_sha,
    )
    _repair_canonical_projection(canonical_path, completed, canonical_schema)
    pending = [by_id[group_id] for group_id in sorted(set(by_id) - set(completed))]
    if not pending:
        return {
            **plan,
            "external_api_calls_planned": 0,
            "completed_before_resume": len(completed),
            "new_records_written": 0,
            "status_counts": {},
            "raw_outputs_path": str(raw_path),
            "records_path": str(records_path),
            "canonical_transformations_path": str(canonical_path),
            "run_manifest_path": str(manifest_path),
        }
    active_adapter = adapter or create_provider_adapter(config.provider)

    def author_one(payload: Mapping[str, Any]):
        group_id = payload["generation_group_id"]
        return _author_one(
            adapter=active_adapter,
            config=config,
            payload=payload,
            group_provenance=provenance[group_id],
            source_review_stage_manifest_sha256=preparation[
                "source_resolution_manifest_sha256"
            ],
            prompt=prompt,
            prompt_sha256=prompt_sha,
            response_schema=response_schema,
            response_schema_sha256=response_schema_sha,
            canonical_schema=canonical_schema,
            canonical_schema_sha256=canonical_schema_sha,
            lexical_spec=lexical_spec,
            lexical_spec_sha256=lexical_spec_sha,
            transport_schema_sha256=transport_schema_sha,
            run_record_schema=run_record_schema,
            raw_output_schema=raw_output_schema,
            attempt_start=max_attempt.get(group_id, 0) + 1,
            prior_raw_records=prior_raw_by_group.get(group_id, []),
            sleep_fn=sleep_fn,
        )

    new_records = []

    def checkpoint(result: tuple[list[dict[str, Any]], dict[str, Any]]) -> None:
        raw_records, run_record = result
        for raw_record in raw_records:
            _append_checkpoint(raw_path, raw_record)
        _append_checkpoint(records_path, run_record)
        if run_record["status"] == "completed":
            _append_checkpoint(
                canonical_path, run_record["canonical_transformation_record"]
            )
        new_records.append(run_record)

    if config.concurrency == 1:
        for payload in pending:
            checkpoint(author_one(payload))
    else:
        with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
            futures = {executor.submit(author_one, payload): payload for payload in pending}
            for future in as_completed(futures):
                checkpoint(future.result())
    return {
        **plan,
        "completed_before_resume": len(completed),
        "new_records_written": len(new_records),
        "status_counts": dict(
            sorted(Counter(record["status"] for record in new_records).items())
        ),
        "raw_outputs_path": str(raw_path),
        "records_path": str(records_path),
        "canonical_transformations_path": str(canonical_path),
        "run_manifest_path": str(manifest_path),
    }
