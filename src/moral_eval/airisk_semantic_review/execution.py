"""Safe checkpointed execution for future independent reviewer runs."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .core import (
    DEFAULT_PROMPT_PATH,
    DEFAULT_RESPONSE_SCHEMA_PATH,
    DEFAULT_RUN_RECORD_SCHEMA_PATH,
    PROMPT_VERSION,
    RUN_RECORD_SCHEMA_VERSION,
    SemanticReviewError,
    canonical_json,
    input_payload_sha256,
    load_prompt,
    load_schema,
    read_json,
    read_jsonl,
    render_reviewer_prompt,
    sha256_path,
    utc_now,
    validate_instance,
    validate_review_response,
)
from .providers import (
    PROVIDER_ENVIRONMENT_VARIABLES,
    ReviewerProvider,
    create_provider_adapter,
    parse_response_json,
)


RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RUN_MANIFEST_SCHEMA_VERSION = "airisk_jmcup_reviewer_run_manifest_v1"


@dataclass(frozen=True)
class ReviewRunConfig:
    provider: str
    requested_model: str
    reviewer_run_id: str
    temperature: float | None = None
    max_output_tokens: int = 4_096
    concurrency: int = 1
    max_retries: int = 2
    initial_retry_delay_seconds: float = 2.0

    def validate(self) -> None:
        if self.provider not in PROVIDER_ENVIRONMENT_VARIABLES:
            raise SemanticReviewError(f"Unsupported provider: {self.provider}")
        if not self.requested_model.strip():
            raise SemanticReviewError("--model must not be empty")
        if not RUN_ID_RE.fullmatch(self.reviewer_run_id):
            raise SemanticReviewError(
                "--run-id must contain only letters, digits, dot, underscore, or hyphen"
            )
        if self.concurrency < 1:
            raise SemanticReviewError("--concurrency must be at least 1")
        if self.max_output_tokens < 1:
            raise SemanticReviewError("max_output_tokens must be positive")
        if self.max_retries < 0:
            raise SemanticReviewError("max_retries must not be negative")

    @property
    def model_settings(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
        }


def select_payloads(
    payloads: Sequence[Mapping[str, Any]],
    *,
    group_ids: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    by_id = {payload["generation_group_id"]: payload for payload in payloads}
    if len(by_id) != len(payloads):
        raise SemanticReviewError("Payload corpus contains duplicate generation_group_id values")
    if group_ids:
        missing = sorted(set(group_ids) - set(by_id))
        if missing:
            raise SemanticReviewError(f"Unknown --group-id value(s): {missing}")
        selected = [by_id[group_id] for group_id in group_ids]
    else:
        selected = list(payloads)
    if limit is not None:
        if limit < 1:
            raise SemanticReviewError("--limit must be at least 1")
        selected = selected[:limit]
    return selected


def _sdk_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _run_manifest(
    config: ReviewRunConfig,
    *,
    prompt_path: Path,
    response_schema_path: Path,
    queue_path: Path,
    payloads: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "created_at_utc": utc_now(),
        "reviewer_run_id": config.reviewer_run_id,
        "provider": config.provider,
        "requested_model": config.requested_model,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": sha256_path(prompt_path),
        "response_schema_sha256": sha256_path(response_schema_path),
        "source_queue_path": str(queue_path.resolve()),
        "source_queue_sha256": sha256_path(queue_path),
        "selected_group_count": len(payloads),
        "selected_generation_group_ids": [
            payload["generation_group_id"] for payload in payloads
        ],
        "model_settings": config.model_settings,
        "concurrency": config.concurrency,
        "max_retries": config.max_retries,
        "initial_retry_delay_seconds": config.initial_retry_delay_seconds,
        "sdk_versions": {
            "anthropic": _sdk_version("anthropic"),
            "google_genai": _sdk_version("google-genai"),
            "jsonschema": _sdk_version("jsonschema"),
        },
        "credential_source": PROVIDER_ENVIRONMENT_VARIABLES[config.provider],
        "credentials_persisted": False,
        "independence_statement": (
            "This run receives only frozen prompt text and blinded source payloads. "
            "No other reviewer run or response is loaded or exposed."
        ),
    }


def _manifest_identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "schema_version",
        "reviewer_run_id",
        "provider",
        "requested_model",
        "prompt_version",
        "prompt_sha256",
        "response_schema_sha256",
        "source_queue_sha256",
        "selected_generation_group_ids",
        "model_settings",
        "max_retries",
    )
    return {field: manifest.get(field) for field in fields}


def _redaction_values() -> list[str]:
    return [
        value
        for name in PROVIDER_ENVIRONMENT_VARIABLES.values()
        if (value := os.environ.get(name))
    ]


def redact_secrets(value: Any) -> Any:
    secrets = _redaction_values()

    def redact(item: Any) -> Any:
        if isinstance(item, str):
            rendered = item
            for secret in secrets:
                rendered = rendered.replace(secret, "[REDACTED]")
            return rendered
        if isinstance(item, Mapping):
            return {redact(str(key)): redact(child) for key, child in item.items()}
        if isinstance(item, list):
            return [redact(child) for child in item]
        return item

    return redact(value)


def completed_group_ids(
    records_path: Path,
    *,
    config: ReviewRunConfig,
    prompt_sha256: str,
    response_schema_sha256: str,
    payloads: Sequence[Mapping[str, Any]],
) -> set[str]:
    if not records_path.exists():
        return set()
    payload_hashes = {
        payload["generation_group_id"]: input_payload_sha256(payload)
        for payload in payloads
    }
    completed = set()
    for record in read_jsonl(records_path):
        group_id = record.get("generation_group_id")
        if (
            record.get("status") == "completed"
            and record.get("reviewer_run_id") == config.reviewer_run_id
            and record.get("provider") == config.provider
            and record.get("requested_model") == config.requested_model
            and record.get("prompt_sha256") == prompt_sha256
            and record.get("response_schema_sha256") == response_schema_sha256
            and record.get("input_payload_sha256") == payload_hashes.get(group_id)
        ):
            completed.add(group_id)
    return completed


def _attempt_review(
    *,
    adapter: ReviewerProvider,
    config: ReviewRunConfig,
    payload: Mapping[str, Any],
    prompt: str,
    prompt_sha256: str,
    response_schema: Mapping[str, Any],
    response_schema_sha256: str,
    run_record_schema: Mapping[str, Any],
    sleep_fn: Callable[[float], None],
) -> dict[str, Any]:
    attempts = []
    final_raw: Any = None
    final_text: str | None = None
    parsed: dict[str, Any] | None = None
    resolved_model: str | None = None
    final_provider_block_metadata: dict[str, Any] | None = None
    final_validation_errors: list[str] = []
    status = "provider_error"
    rendered_prompt = render_reviewer_prompt(prompt, payload)

    for attempt_index in range(config.max_retries + 1):
        attempt_number = attempt_index + 1
        attempt_timestamp = utc_now()
        try:
            provider_result = adapter.review(
                requested_model=config.requested_model,
                rendered_prompt=rendered_prompt,
                response_schema=response_schema,
                model_settings=config.model_settings,
            )
            final_raw = redact_secrets(provider_result.raw_response)
            final_text = redact_secrets(provider_result.raw_response_text)
            resolved_model = provider_result.resolved_reported_model
            if provider_result.terminal_status is not None:
                if provider_result.terminal_status not in {"refused", "blocked"}:
                    raise SemanticReviewError(
                        "Provider returned an unknown terminal non-response status: "
                        f"{provider_result.terminal_status}"
                    )
                if not isinstance(provider_result.provider_block_metadata, Mapping):
                    raise SemanticReviewError(
                        "Provider terminal non-response omitted provider_block_metadata"
                    )
                status = provider_result.terminal_status
                final_provider_block_metadata = redact_secrets(
                    provider_result.provider_block_metadata
                )
                parsed = None
                final_validation_errors = []
                attempts.append(
                    {
                        "attempt_number": attempt_number,
                        "timestamp_utc": attempt_timestamp,
                        "status": status,
                        "provider_request_id": provider_result.provider_request_id,
                        "provider_block_metadata": final_provider_block_metadata,
                        "validation_errors": [],
                        "error_type": None,
                        "error_message": None,
                        "retry_delay_seconds": None,
                    }
                )
                break
            try:
                candidate = parse_response_json(provider_result.raw_response_text)
                candidate_errors = validate_review_response(
                    candidate, payload, response_schema
                )
            except Exception as exc:
                candidate = None
                candidate_errors = [f"{type(exc).__name__}: {redact_secrets(str(exc))}"]
            if not candidate_errors and candidate is not None:
                parsed = candidate
                final_validation_errors = []
                status = "completed"
                attempts.append(
                    {
                        "attempt_number": attempt_number,
                        "timestamp_utc": attempt_timestamp,
                        "status": "completed",
                        "provider_request_id": provider_result.provider_request_id,
                        "validation_errors": [],
                        "error_type": None,
                        "error_message": None,
                        "retry_delay_seconds": None,
                    }
                )
                break
            final_validation_errors = candidate_errors
            status = "validation_failed"
            retry_delay = (
                config.initial_retry_delay_seconds * (2**attempt_index)
                if attempt_index < config.max_retries
                else None
            )
            attempts.append(
                {
                    "attempt_number": attempt_number,
                    "timestamp_utc": attempt_timestamp,
                    "status": "validation_failed",
                    "provider_request_id": provider_result.provider_request_id,
                    "validation_errors": candidate_errors,
                    "error_type": "LocalResponseValidationError",
                    "error_message": "Provider response failed canonical local validation",
                    "retry_delay_seconds": retry_delay,
                }
            )
        except Exception as exc:
            message = redact_secrets(str(exc))
            retry_delay = (
                config.initial_retry_delay_seconds * (2**attempt_index)
                if attempt_index < config.max_retries
                else None
            )
            status = "provider_error"
            final_validation_errors = []
            attempts.append(
                {
                    "attempt_number": attempt_number,
                    "timestamp_utc": attempt_timestamp,
                    "status": "provider_error",
                    "provider_request_id": None,
                    "validation_errors": [],
                    "error_type": type(exc).__name__,
                    "error_message": message,
                    "retry_delay_seconds": retry_delay,
                }
            )
        if attempt_index < config.max_retries:
            sleep_fn(config.initial_retry_delay_seconds * (2**attempt_index))

    record = {
        "record_schema_version": RUN_RECORD_SCHEMA_VERSION,
        "reviewer_run_id": config.reviewer_run_id,
        "generation_group_id": payload["generation_group_id"],
        "provider": config.provider,
        "requested_model": config.requested_model,
        "resolved_reported_model": resolved_model,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": prompt_sha256,
        "response_schema_sha256": response_schema_sha256,
        "input_payload_sha256": input_payload_sha256(payload),
        "status": status,
        "raw_response": final_raw,
        "raw_response_text": final_text,
        "parsed_structured_response": parsed,
        "provider_block_metadata": final_provider_block_metadata,
        "timestamp_utc": utc_now(),
        "model_settings": config.model_settings,
        "retry_information": {
            "max_retries": config.max_retries,
            "attempt_count": len(attempts),
            "attempts": attempts,
        },
        "validation_errors": final_validation_errors,
    }
    validate_instance(record, run_record_schema, label="review run record")
    if parsed is not None:
        response_errors = validate_review_response(parsed, payload, response_schema)
        if response_errors:
            raise SemanticReviewError(
                "Completed review failed post-storage canonical validation: "
                + " | ".join(response_errors)
            )
    return record


def _append_checkpoint(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(record) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def run_reviews(
    *,
    config: ReviewRunConfig,
    payloads: Sequence[Mapping[str, Any]],
    queue_path: Path,
    output_root: Path,
    execute: bool,
    resume: bool,
    adapter: ReviewerProvider | None = None,
    prompt_path: Path = DEFAULT_PROMPT_PATH,
    response_schema_path: Path = DEFAULT_RESPONSE_SCHEMA_PATH,
    run_record_schema_path: Path = DEFAULT_RUN_RECORD_SCHEMA_PATH,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Plan or execute one independent run; no adapter is created unless execute=True."""

    config.validate()
    prompt = load_prompt(prompt_path)
    response_schema = load_schema(response_schema_path)
    run_record_schema = load_schema(run_record_schema_path)
    prompt_hash = sha256_path(prompt_path)
    schema_hash = sha256_path(response_schema_path)
    run_dir = output_root.resolve() / config.provider / config.reviewer_run_id
    records_path = run_dir / "reviews.jsonl"
    manifest_path = run_dir / "run_manifest.json"

    plan = {
        "execute": execute,
        "external_api_calls_planned": len(payloads) if execute else 0,
        "reviewer_run_id": config.reviewer_run_id,
        "provider": config.provider,
        "requested_model": config.requested_model,
        "selected_group_count": len(payloads),
        "run_directory": str(run_dir),
        "safe_default_no_execute": not execute,
    }
    if not execute:
        return plan

    proposed_manifest = _run_manifest(
        config,
        prompt_path=prompt_path,
        response_schema_path=response_schema_path,
        queue_path=queue_path,
        payloads=payloads,
    )
    if resume and not run_dir.exists():
        raise SemanticReviewError(f"Cannot --resume a run that does not exist: {run_dir}")
    if run_dir.exists():
        if not resume:
            raise SemanticReviewError(
                f"Run directory already exists; use --resume: {run_dir}"
            )
        if not manifest_path.is_file():
            raise SemanticReviewError(f"Cannot resume without run manifest: {manifest_path}")
        existing_manifest = read_json(manifest_path)
        if _manifest_identity(existing_manifest) != _manifest_identity(proposed_manifest):
            raise SemanticReviewError(
                "Resume configuration differs from the stored immutable run manifest"
            )
    else:
        run_dir.mkdir(parents=True)
        manifest_path.write_text(
            json.dumps(proposed_manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    already_completed = completed_group_ids(
        records_path,
        config=config,
        prompt_sha256=prompt_hash,
        response_schema_sha256=schema_hash,
        payloads=payloads,
    )
    pending = [
        payload
        for payload in payloads
        if payload["generation_group_id"] not in already_completed
    ]
    if not pending:
        return {
            **plan,
            "external_api_calls_planned": 0,
            "completed_before_resume": len(already_completed),
            "new_records_written": 0,
            "status_counts": {},
        }
    active_adapter = adapter or create_provider_adapter(config.provider)

    records: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}

    def review_one(payload: Mapping[str, Any]) -> dict[str, Any]:
        return _attempt_review(
            adapter=active_adapter,
            config=config,
            payload=payload,
            prompt=prompt,
            prompt_sha256=prompt_hash,
            response_schema=response_schema,
            response_schema_sha256=schema_hash,
            run_record_schema=run_record_schema,
            sleep_fn=sleep_fn,
        )

    if config.concurrency == 1:
        for payload in pending:
            record = review_one(payload)
            _append_checkpoint(records_path, record)
            records.append(record)
    else:
        with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
            future_to_group = {
                executor.submit(review_one, payload): payload["generation_group_id"]
                for payload in pending
            }
            for future in as_completed(future_to_group):
                record = future.result()
                _append_checkpoint(records_path, record)
                records.append(record)
    for record in records:
        status = record["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        **plan,
        "completed_before_resume": len(already_completed),
        "new_records_written": len(records),
        "status_counts": dict(sorted(status_counts.items())),
        "reviews_path": str(records_path),
        "run_manifest_path": str(manifest_path),
    }
