"""Provider-neutral execution for independent AIRisk JMCUP final validation."""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import (
    REPO_ROOT,
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
    validation_errors,
)
from .final_validation import (
    DEFAULT_FINAL_RECORD_SCHEMA_PATH,
    DEFAULT_INPUT_SCHEMA_PATH,
    DEFAULT_PROMPT_PATH,
    DEFAULT_PROTOCOL_PATH,
    DEFAULT_RESPONSE_SCHEMA_PATH,
    DEFAULT_TRANSFORMATION_SCHEMA_PATH,
    FINAL_VALIDATION_PREPARATION_MANIFEST_VERSION,
    FINAL_VALIDATOR_PROMPT_VERSION,
    _indexed,
    build_semantic_final_record,
    derive_private_mapping,
    render_final_validator_prompt,
)
from .execution import redact_secrets
from .providers import (
    PROVIDER_CREDENTIAL_ENVIRONMENT_VARIABLES,
    ReviewerProvider,
    create_provider_adapter,
    openrouter_transport_schema,
    parse_response_json,
    provider_transport_schema,
)
from .transformation import DEFAULT_LEXICAL_DIAGNOSTICS_PATH
from .transformation_execution import (
    _aggregate_raw_usage,
    _finish_reason,
    _normalised_usage,
)


FINAL_VALIDATION_RAW_SCHEMA_VERSION = (
    "airisk_jmcup_final_validation_raw_provider_output_v1"
)
FINAL_VALIDATION_RUN_RECORD_SCHEMA_VERSION = (
    "airisk_jmcup_final_validation_run_record_v1"
)
FINAL_VALIDATION_RUN_MANIFEST_VERSION = "airisk_jmcup_final_validation_run_manifest_v1"
DEFAULT_RAW_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{FINAL_VALIDATION_RAW_SCHEMA_VERSION}.schema.json"
)
DEFAULT_RUN_RECORD_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{FINAL_VALIDATION_RUN_RECORD_SCHEMA_VERSION}.schema.json"
)


@dataclass(frozen=True)
class FinalValidationRunConfig:
    provider: str
    requested_model: str
    final_validation_run_id: str
    temperature: float | None = None
    max_output_tokens: int = 8192
    concurrency: int = 1
    max_retries: int = 2
    initial_retry_delay_seconds: float = 2.0

    def validate(self) -> None:
        if self.provider not in PROVIDER_CREDENTIAL_ENVIRONMENT_VARIABLES:
            raise SemanticReviewError(f"Unsupported provider: {self.provider}")
        if not self.requested_model or not self.final_validation_run_id:
            raise SemanticReviewError("Explicit model and final-validation run ID are required")
        if self.concurrency < 1:
            raise SemanticReviewError("--concurrency must be positive")
        if self.max_output_tokens < 1:
            raise SemanticReviewError("--max-output-tokens must be positive")
        if self.max_retries < 0 or self.initial_retry_delay_seconds < 0:
            raise SemanticReviewError("Retry settings must not be negative")

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
    config: FinalValidationRunConfig, response_schema: Mapping[str, Any]
) -> dict[str, Any]:
    if config.provider == "openrouter":
        return openrouter_transport_schema(config.requested_model, response_schema)
    return provider_transport_schema(config.provider, response_schema)


def _raw_output_record(
    *,
    config: FinalValidationRunConfig,
    transformation_id: str,
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
        "record_schema_version": FINAL_VALIDATION_RAW_SCHEMA_VERSION,
        "raw_output_id": (
            f"{config.final_validation_run_id}:{transformation_id}:attempt-{attempt_number}"
        ),
        "final_validation_run_id": config.final_validation_run_id,
        "transformation_id": transformation_id,
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
    *, raw: Mapping[str, Any], raw_sha: str, status: str,
    errors: Sequence[str], error: str | None,
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
        "validation_errors": list(errors),
        "error": error,
    }


def _validate_response(
    response: Mapping[str, Any], schema: Mapping[str, Any]
) -> list[str]:
    return validation_errors(response, schema)


def _validate_one(
    *,
    adapter: ReviewerProvider,
    config: FinalValidationRunConfig,
    payload: Mapping[str, Any],
    transformation: Mapping[str, Any],
    mapping: Mapping[str, Any],
    prompt: str,
    prompt_sha256: str,
    response_schema: Mapping[str, Any],
    response_schema_sha256: str,
    final_schema: Mapping[str, Any],
    final_schema_sha256: str,
    transformation_schema_sha256: str,
    lexical_spec_sha256: str,
    final_validation_protocol_sha256: str,
    final_validator_input_schema_sha256: str,
    preparation_manifest_sha256: str,
    private_provenance_sha256: str,
    transport_schema_sha256: str,
    run_record_schema: Mapping[str, Any],
    raw_schema: Mapping[str, Any],
    attempt_start: int,
    prior_raw_records: Sequence[Mapping[str, Any]],
    sleep_fn: Callable[[float], None],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    transformation_id = transformation["transformation_id"]
    rendered_prompt = render_final_validator_prompt(prompt, payload)
    raw_records: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    parsed: dict[str, Any] | None = None
    canonical_record: dict[str, Any] | None = None
    status = "provider_error"
    final_errors: list[str] = []

    for retry_index in range(config.max_retries + 1):
        attempt_number = attempt_start + retry_index
        captured = utc_now()
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
                    errors = _validate_response(candidate, response_schema)
                except Exception as exc:
                    candidate = None
                    errors = [f"{type(exc).__name__}: {redact_secrets(str(exc))}"]
                if candidate is not None and not errors:
                    parsed = candidate
                    canonical_record = build_semantic_final_record(
                        transformation,
                        candidate,
                        mapping,
                        final_validation_run_id=config.final_validation_run_id,
                        provider=config.provider,
                        requested_model=config.requested_model,
                        preparation_manifest_sha256=preparation_manifest_sha256,
                        private_provenance_sha256=private_provenance_sha256,
                        transformation_schema_sha256=transformation_schema_sha256,
                        lexical_spec_sha256=lexical_spec_sha256,
                        created_at_utc=captured,
                        final_validation_protocol_sha256=(
                            final_validation_protocol_sha256
                        ),
                        final_validator_prompt_sha256=prompt_sha256,
                        final_validator_input_schema_sha256=(
                            final_validator_input_schema_sha256
                        ),
                        final_validator_response_schema_sha256=response_schema_sha256,
                    )
                    errors = validation_errors(canonical_record, final_schema)
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
                    status = (
                        "truncated"
                        if isinstance(result.output_termination, Mapping)
                        and result.output_termination.get("output_limit_reached") is True
                        else "validation_failed"
                    )
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
                "resolved_reported_model": getattr(exc, "resolved_reported_model", None),
                "provider_request_id": getattr(exc, "provider_request_id", None),
                "provider_generation_id": getattr(exc, "provider_generation_id", None),
                "actual_routed_provider": getattr(exc, "actual_routed_provider", None),
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
            transformation_id=transformation_id,
            attempt_number=attempt_number,
            captured_at_utc=captured,
            **raw_kwargs,
        )
        validate_instance(raw_record, raw_schema, label="final-validation raw provider output")
        raw_sha = sha256_text(canonical_json(raw_record))
        raw_records.append(raw_record)
        attempts.append(_attempt_summary(
            raw=raw_record, raw_sha=raw_sha, status=status,
            errors=final_errors, error=attempt_error,
        ))
        if status in {"completed", "refused", "blocked", "truncated"}:
            break
        if retry_index == config.max_retries:
            break
        sleep_fn(config.initial_retry_delay_seconds * (2**retry_index))

    final_attempt = attempts[-1]
    final_raw = raw_records[-1]
    run_record = {
        "record_schema_version": FINAL_VALIDATION_RUN_RECORD_SCHEMA_VERSION,
        "final_validation_run_id": config.final_validation_run_id,
        "transformation_id": transformation_id,
        "provider": config.provider,
        "requested_model": config.requested_model,
        "resolved_reported_model": final_raw["resolved_reported_model"],
        "provider_request_id": final_raw["provider_request_id"],
        "provider_generation_id": final_raw["provider_generation_id"],
        "actual_routed_provider": final_raw["actual_routed_provider"],
        "provider_usage": final_raw["provider_usage"],
        "aggregate_usage": _aggregate_raw_usage([*prior_raw_records, *raw_records]),
        "output_termination": final_raw["output_termination"],
        "prompt_version": FINAL_VALIDATOR_PROMPT_VERSION,
        "prompt_sha256": prompt_sha256,
        "validator_response_schema_sha256": response_schema_sha256,
        "canonical_final_validation_schema_sha256": final_schema_sha256,
        "input_payload_sha256": sha256_text(canonical_json(payload)),
        "private_mapping_sha256": mapping["private_mapping_sha256"],
        "transport_schema_sha256": transport_schema_sha256,
        "status": status,
        "parsed_validator_response": parsed,
        "canonical_final_validation_record": canonical_record,
        "canonical_final_validation_record_sha256": (
            sha256_text(canonical_json(canonical_record))
            if canonical_record is not None else None
        ),
        "final_attempt_provenance": {
            "attempt_number": final_attempt["attempt_number"],
            "raw_provider_output_sha256": final_attempt["raw_provider_output_sha256"],
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
    validate_instance(run_record, run_record_schema, label="final-validation run record")
    return raw_records, run_record


def _raw_index(
    path: Path, schema: Mapping[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, int], dict[str, list[dict[str, Any]]]]:
    by_sha: dict[str, dict[str, Any]] = {}
    max_attempt: dict[str, int] = {}
    by_transformation: dict[str, list[dict[str, Any]]] = {}
    if not path.exists():
        return by_sha, max_attempt, by_transformation
    for record in read_jsonl(path):
        validate_instance(record, schema, label="stored final-validation raw output")
        digest = sha256_text(canonical_json(record))
        if digest in by_sha:
            raise SemanticReviewError(f"Duplicate raw final-validation record {digest}")
        by_sha[digest] = record
        transformation_id = record["transformation_id"]
        max_attempt[transformation_id] = max(
            max_attempt.get(transformation_id, 0), record["attempt_number"]
        )
        by_transformation.setdefault(transformation_id, []).append(record)
    return by_sha, max_attempt, by_transformation


def _completed_records(
    path: Path,
    *,
    config: FinalValidationRunConfig,
    expected: Mapping[str, Mapping[str, str]],
    prompt_sha256: str,
    response_schema_sha256: str,
    response_schema: Mapping[str, Any],
    final_schema_sha256: str,
    run_schema: Mapping[str, Any],
    final_schema: Mapping[str, Any],
    raw_by_sha: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    completed: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return completed
    for record in read_jsonl(path):
        validate_instance(record, run_schema, label="stored final-validation run record")
        if record["status"] != "completed":
            continue
        transformation_id = record["transformation_id"]
        if transformation_id in completed:
            raise SemanticReviewError(
                f"Duplicate completed final validation for {transformation_id}"
            )
        item = expected.get(transformation_id)
        if item is None:
            raise SemanticReviewError(f"Completed record has unexpected {transformation_id}")
        checks = {
            "final_validation_run_id": config.final_validation_run_id,
            "provider": config.provider,
            "requested_model": config.requested_model,
            "input_payload_sha256": item["input_payload_sha256"],
            "private_mapping_sha256": item["private_mapping_sha256"],
            "prompt_sha256": prompt_sha256,
            "validator_response_schema_sha256": response_schema_sha256,
            "canonical_final_validation_schema_sha256": final_schema_sha256,
        }
        if any(record.get(key) != value for key, value in checks.items()):
            raise SemanticReviewError(
                f"Stored completed final validation provenance differs for {transformation_id}"
            )
        for attempt in record["retry_information"]["attempts"]:
            if attempt["raw_provider_output_sha256"] not in raw_by_sha:
                raise SemanticReviewError(
                    f"Completed final validation lacks raw output for {transformation_id}"
                )
        canonical = record["canonical_final_validation_record"]
        response_errors = validation_errors(
            record.get("parsed_validator_response"), response_schema
        )
        if response_errors or canonical.get(
            "independent_semantic_validation", {}
        ).get("validator_response") != record.get("parsed_validator_response"):
            raise SemanticReviewError(
                f"Stored completed validator response is invalid for {transformation_id}"
            )
        errors = validation_errors(canonical, final_schema)
        if errors or sha256_text(canonical_json(canonical)) != record[
            "canonical_final_validation_record_sha256"
        ]:
            raise SemanticReviewError(
                f"Stored completed canonical final validation is invalid for {transformation_id}"
            )
        completed[transformation_id] = record
    return completed


def _repair_projection(
    path: Path,
    completed: Mapping[str, Mapping[str, Any]],
    schema: Mapping[str, Any],
) -> None:
    projected: dict[str, dict[str, Any]] = {}
    if path.exists():
        for record in read_jsonl(path):
            validate_instance(record, schema, label="canonical final-validation projection")
            transformation_id = record["transformation_id"]
            if transformation_id in projected:
                raise SemanticReviewError(
                    f"Duplicate canonical final-validation projection for {transformation_id}"
                )
            projected[transformation_id] = record
    for transformation_id, run_record in sorted(completed.items()):
        expected = run_record["canonical_final_validation_record"]
        if transformation_id in projected:
            if canonical_json(projected[transformation_id]) != canonical_json(expected):
                raise SemanticReviewError(
                    f"Canonical final-validation projection conflicts for {transformation_id}"
                )
        else:
            _append_checkpoint(path, expected)


def _manifest_identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "final_validation_run_id", "provider", "requested_model",
        "selected_transformation_ids", "prompt_sha256", "input_schema_sha256",
        "protocol_sha256",
        "response_schema_sha256", "run_record_schema_sha256", "raw_output_schema_sha256",
        "canonical_final_validation_schema_sha256", "transformation_schema_sha256",
        "lexical_diagnostics_spec_sha256", "input_payloads_sha256",
        "preparation_manifest_sha256", "private_provenance_sha256", "model_settings",
        "concurrency", "max_retries", "initial_retry_delay_seconds",
    )
    return {field: manifest.get(field) for field in fields}


def run_final_validations(
    *,
    config: FinalValidationRunConfig,
    payloads: Sequence[Mapping[str, Any]],
    payloads_path: Path,
    transformations_path: Path,
    preparation_manifest_path: Path,
    private_provenance_path: Path,
    output_root: Path,
    execute: bool,
    resume: bool,
    adapter: ReviewerProvider | None = None,
    protocol_path: Path = DEFAULT_PROTOCOL_PATH,
    prompt_path: Path = DEFAULT_PROMPT_PATH,
    input_schema_path: Path = DEFAULT_INPUT_SCHEMA_PATH,
    response_schema_path: Path = DEFAULT_RESPONSE_SCHEMA_PATH,
    final_schema_path: Path = DEFAULT_FINAL_RECORD_SCHEMA_PATH,
    transformation_schema_path: Path = DEFAULT_TRANSFORMATION_SCHEMA_PATH,
    lexical_spec_path: Path = DEFAULT_LEXICAL_DIAGNOSTICS_PATH,
    run_record_schema_path: Path = DEFAULT_RUN_RECORD_SCHEMA_PATH,
    raw_schema_path: Path = DEFAULT_RAW_SCHEMA_PATH,
    transformation_ids: Sequence[str] | None = None,
    limit: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    config.validate()
    preparation = read_json(preparation_manifest_path)
    private = read_json(private_provenance_path)
    if preparation.get("schema_version") != FINAL_VALIDATION_PREPARATION_MANIFEST_VERSION:
        raise SemanticReviewError("Unexpected final-validation preparation manifest")
    if preparation.get("private_provenance_sha256") != sha256_path(private_provenance_path):
        raise SemanticReviewError("Private final-validation provenance hash differs")
    if private.get("deterministic_seed_sha256") != preparation.get(
        "deterministic_seed_sha256"
    ) or sha256_text(private.get("deterministic_seed", "")) != private.get(
        "deterministic_seed_sha256"
    ):
        raise SemanticReviewError("Private final-validation seed provenance is invalid")
    if private.get("commit_allowed") is not False or private.get(
        "public_distribution_allowed"
    ) is not False:
        raise SemanticReviewError("Private provenance is not marked non-public/non-committable")
    if preparation.get("payloads_sha256") != sha256_path(payloads_path):
        raise SemanticReviewError("Prepared final-validator payload file hash differs")
    if preparation.get("transformations_sha256") != sha256_path(transformations_path):
        raise SemanticReviewError("Prepared transformation corpus hash differs")

    input_schema = load_schema(input_schema_path)
    response_schema = load_schema(response_schema_path)
    final_schema = load_schema(final_schema_path)
    run_schema = load_schema(run_record_schema_path)
    raw_schema = load_schema(raw_schema_path)
    prompt = load_prompt(prompt_path)
    preparation_hash_checks = {
        "final_validation_protocol_sha256": sha256_path(protocol_path),
        "final_validator_prompt_sha256": sha256_path(prompt_path),
        "final_validator_input_schema_sha256": sha256_path(input_schema_path),
        "final_validator_response_schema_sha256": sha256_path(response_schema_path),
        "final_validation_record_schema_sha256": sha256_path(final_schema_path),
        "transformation_schema_sha256": sha256_path(transformation_schema_path),
        "lexical_diagnostics_spec_sha256": sha256_path(lexical_spec_path),
    }
    for field, expected_hash in preparation_hash_checks.items():
        if preparation.get(field) != expected_hash:
            raise SemanticReviewError(
                f"Final-validation preparation hash differs for {field}"
            )
    prepared_payloads = read_jsonl(payloads_path)
    if [canonical_json(item) for item in payloads] != [
        canonical_json(item) for item in prepared_payloads
    ]:
        raise SemanticReviewError(
            "Execution requires the complete ordered prepared payload corpus"
        )
    mappings = sorted(private["mappings"], key=lambda item: item["payload_ordinal"])
    if [item["payload_ordinal"] for item in mappings] != list(range(len(mappings))):
        raise SemanticReviewError("Private payload ordinals are incomplete or duplicated")
    if len(mappings) != len(prepared_payloads):
        raise SemanticReviewError("Private mapping count differs from prepared payload count")
    for mapping in mappings:
        stored_digest = mapping.get("private_mapping_sha256")
        digest_value = {
            key: value
            for key, value in mapping.items()
            if key != "private_mapping_sha256"
        }
        if stored_digest != sha256_text(canonical_json(digest_value)):
            raise SemanticReviewError("Private mapping record hash differs")
        derived = derive_private_mapping(
            seed=private["deterministic_seed"],
            transformation_id=mapping["transformation_id"],
            transformation_sha256=mapping["transformation_record_sha256"],
        )
        for field in (
            "action_order_hmac_sha256", "variant_order_hmac_sha256",
            "bounded_candidate_id", "broader_candidate_id",
            "unsupported_pressure_variant_id", "genuine_evidence_variant_id",
        ):
            if mapping.get(field) != derived[field]:
                raise SemanticReviewError(
                    f"Private HMAC mapping derivation differs for {field}"
                )
    transformations = _indexed(
        read_jsonl(transformations_path), key="transformation_id", label="transformation"
    )
    selected: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    wanted = set(transformation_ids or [])
    if transformation_ids is not None and len(wanted) != len(transformation_ids):
        raise SemanticReviewError("Duplicate requested final-validation transformation ID")
    for source, mapping in zip(payloads, mappings, strict=True):
        payload = dict(source)
        validate_instance(payload, input_schema, label="selected final-validator input")
        input_sha = sha256_text(canonical_json(payload))
        if mapping.get("input_payload_sha256") != input_sha:
            raise SemanticReviewError("Prepared payload hash differs from private mapping")
        transformation = transformations.get(mapping["transformation_id"])
        if transformation is None or sha256_text(canonical_json(transformation)) != mapping[
            "transformation_record_sha256"
        ]:
            raise SemanticReviewError("Private mapping transformation provenance differs")
        if not wanted or mapping["transformation_id"] in wanted:
            selected.append((payload, mapping, transformation))
    found = {item[1]["transformation_id"] for item in selected}
    if wanted - found:
        raise SemanticReviewError(
            f"Unknown or structurally rejected transformation IDs: {sorted(wanted - found)}"
        )
    if limit is not None:
        if limit < 0:
            raise SemanticReviewError("--limit must not be negative")
        selected = selected[:limit]

    run_dir = output_root.resolve() / config.provider / config.final_validation_run_id
    plan = {
        "execute": execute,
        "external_api_calls_planned": len(selected) if execute else 0,
        "final_validation_run_id": config.final_validation_run_id,
        "provider": config.provider,
        "requested_model": config.requested_model,
        "selected_transformation_count": len(selected),
        "run_directory": str(run_dir),
        "safe_default_no_execute": not execute,
    }
    if not execute:
        return plan

    raw_path = run_dir / "raw_provider_outputs.jsonl"
    records_path = run_dir / "final_validation_run_records.jsonl"
    canonical_path = run_dir / "canonical_final_validation_records.jsonl"
    manifest_path = run_dir / "run_manifest.json"
    prompt_sha = sha256_path(prompt_path)
    response_schema_sha = sha256_path(response_schema_path)
    final_schema_sha = sha256_path(final_schema_path)
    transformation_schema_sha = sha256_path(transformation_schema_path)
    lexical_spec_sha = sha256_path(lexical_spec_path)
    transport_schema_sha = sha256_text(
        canonical_json(_transport_schema(config, response_schema))
    )
    manifest = {
        "schema_version": FINAL_VALIDATION_RUN_MANIFEST_VERSION,
        "created_at_utc": utc_now(),
        "final_validation_run_id": config.final_validation_run_id,
        "provider": config.provider,
        "requested_model": config.requested_model,
        "selected_transformation_ids": [item[1]["transformation_id"] for item in selected],
        "prompt_version": FINAL_VALIDATOR_PROMPT_VERSION,
        "protocol_sha256": sha256_path(protocol_path),
        "prompt_sha256": prompt_sha,
        "input_schema_sha256": sha256_path(input_schema_path),
        "response_schema_sha256": response_schema_sha,
        "run_record_schema_sha256": sha256_path(run_record_schema_path),
        "raw_output_schema_sha256": sha256_path(raw_schema_path),
        "canonical_final_validation_schema_sha256": final_schema_sha,
        "transformation_schema_sha256": transformation_schema_sha,
        "lexical_diagnostics_spec_sha256": lexical_spec_sha,
        "transport_schema_sha256": transport_schema_sha,
        "input_payloads_sha256": sha256_path(payloads_path),
        "preparation_manifest_sha256": sha256_path(preparation_manifest_path),
        "private_provenance_sha256": sha256_path(private_provenance_path),
        "literal_seed_persisted_in_run_manifest": False,
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
        raise SemanticReviewError(f"Cannot --resume absent final-validation run: {run_dir}")
    if run_dir.exists():
        if not resume:
            raise SemanticReviewError(f"Final-validation run exists; use --resume: {run_dir}")
        existing = read_json(manifest_path)
        if _manifest_identity(existing) != _manifest_identity(manifest):
            raise SemanticReviewError("Final-validation resume configuration differs")
    else:
        run_dir.mkdir(parents=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8", newline="\n",
        )

    raw_by_sha, max_attempt, prior_raw = _raw_index(raw_path, raw_schema)
    expected = {
        mapping["transformation_id"]: {
            "input_payload_sha256": sha256_text(canonical_json(payload)),
            "private_mapping_sha256": mapping["private_mapping_sha256"],
        }
        for payload, mapping, _ in selected
    }
    completed = _completed_records(
        records_path,
        config=config,
        expected=expected,
        prompt_sha256=prompt_sha,
        response_schema_sha256=response_schema_sha,
        response_schema=response_schema,
        final_schema_sha256=final_schema_sha,
        run_schema=run_schema,
        final_schema=final_schema,
        raw_by_sha=raw_by_sha,
    )
    _repair_projection(canonical_path, completed, final_schema)
    pending = [item for item in selected if item[1]["transformation_id"] not in completed]
    if not pending:
        return {
            **plan, "external_api_calls_planned": 0,
            "completed_before_resume": len(completed), "new_records_written": 0,
            "status_counts": {}, "raw_outputs_path": str(raw_path),
            "records_path": str(records_path),
            "canonical_final_validations_path": str(canonical_path),
            "run_manifest_path": str(manifest_path),
        }
    active_adapter = adapter or create_provider_adapter(config.provider)

    def validate_one(item: tuple[dict[str, Any], dict[str, Any], dict[str, Any]]):
        payload, mapping, transformation = item
        transformation_id = mapping["transformation_id"]
        return _validate_one(
            adapter=active_adapter,
            config=config,
            payload=payload,
            transformation=transformation,
            mapping=mapping,
            prompt=prompt,
            prompt_sha256=prompt_sha,
            response_schema=response_schema,
            response_schema_sha256=response_schema_sha,
            final_schema=final_schema,
            final_schema_sha256=final_schema_sha,
            transformation_schema_sha256=transformation_schema_sha,
            lexical_spec_sha256=lexical_spec_sha,
            final_validation_protocol_sha256=preparation[
                "final_validation_protocol_sha256"
            ],
            final_validator_input_schema_sha256=preparation[
                "final_validator_input_schema_sha256"
            ],
            preparation_manifest_sha256=sha256_path(preparation_manifest_path),
            private_provenance_sha256=sha256_path(private_provenance_path),
            transport_schema_sha256=transport_schema_sha,
            run_record_schema=run_schema,
            raw_schema=raw_schema,
            attempt_start=max_attempt.get(transformation_id, 0) + 1,
            prior_raw_records=prior_raw.get(transformation_id, []),
            sleep_fn=sleep_fn,
        )

    new_records: list[dict[str, Any]] = []

    def checkpoint(result: tuple[list[dict[str, Any]], dict[str, Any]]) -> None:
        raw_records, run_record = result
        for raw_record in raw_records:
            _append_checkpoint(raw_path, raw_record)
        _append_checkpoint(records_path, run_record)
        if run_record["status"] == "completed":
            _append_checkpoint(
                canonical_path, run_record["canonical_final_validation_record"]
            )
        new_records.append(run_record)

    if config.concurrency == 1:
        for item in pending:
            checkpoint(validate_one(item))
    else:
        with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
            futures = {executor.submit(validate_one, item): item for item in pending}
            for future in as_completed(futures):
                checkpoint(future.result())
    return {
        **plan,
        "completed_before_resume": len(completed),
        "new_records_written": len(new_records),
        "status_counts": dict(sorted(Counter(
            record["status"] for record in new_records
        ).items())),
        "raw_outputs_path": str(raw_path),
        "records_path": str(records_path),
        "canonical_final_validations_path": str(canonical_path),
        "run_manifest_path": str(manifest_path),
    }
