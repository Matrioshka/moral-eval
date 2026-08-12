"""Safe execution and local validation for independent Opus adjudication."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import (
    CRITERION_KEYS,
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
from .execution import (
    _aggregate_attempt_usage,
    _append_checkpoint,
    _attempt_record,
    redact_secrets,
)
from .providers import ReviewerProvider, create_provider_adapter, parse_response_json


ADJUDICATION_PROMPT_VERSION = "airisk_jmcup_opus_adjudication_v1"
ADJUDICATION_INPUT_SCHEMA_VERSION = "airisk_jmcup_opus_adjudication_input_v1"
ADJUDICATION_RESPONSE_SCHEMA_VERSION = "airisk_jmcup_opus_adjudication_response_v1"
ADJUDICATOR_RUN_RECORD_SCHEMA_VERSION = "airisk_jmcup_adjudicator_run_record_v1"
ADJUDICATOR_RUN_MANIFEST_VERSION = "airisk_jmcup_adjudicator_run_manifest_v1"
DEFAULT_ADJUDICATION_PROMPT_PATH = (
    REPO_ROOT / "prompts" / f"{ADJUDICATION_PROMPT_VERSION}.txt"
)
DEFAULT_ADJUDICATION_INPUT_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{ADJUDICATION_INPUT_SCHEMA_VERSION}.schema.json"
)
DEFAULT_ADJUDICATION_RESPONSE_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{ADJUDICATION_RESPONSE_SCHEMA_VERSION}.schema.json"
)
DEFAULT_ADJUDICATOR_RUN_RECORD_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{ADJUDICATOR_RUN_RECORD_SCHEMA_VERSION}.schema.json"
)
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class AdjudicationRunConfig:
    provider: str
    requested_model: str
    adjudicator_run_id: str
    temperature: float | None = None
    max_output_tokens: int = 8_192
    concurrency: int = 1
    max_retries: int = 2
    initial_retry_delay_seconds: float = 2.0

    def validate(self) -> None:
        if self.provider not in {"anthropic", "openrouter"}:
            raise SemanticReviewError(
                "Opus adjudication supports only native Anthropic or OpenRouter"
            )
        if "opus" not in self.requested_model.casefold():
            raise SemanticReviewError("Adjudication --model must identify an Opus model")
        if self.provider == "openrouter" and not self.requested_model.startswith(
            "anthropic/"
        ):
            raise SemanticReviewError(
                "OpenRouter Opus adjudication requires an anthropic/* model slug"
            )
        if not RUN_ID_RE.fullmatch(self.adjudicator_run_id):
            raise SemanticReviewError("Invalid adjudicator --run-id")
        if self.max_output_tokens < 1 or self.concurrency < 1 or self.max_retries < 0:
            raise SemanticReviewError("Invalid adjudication execution limits")

    @property
    def model_settings(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
        }


def render_adjudication_prompt(prompt: str, payload: Mapping[str, Any]) -> str:
    return (
        f"{prompt.rstrip()}\n\n--- BEGIN BLINDED ADJUDICATION PAYLOAD ---\n"
        f"{canonical_json(payload)}\n"
    )


def validate_adjudication_response(
    response: Mapping[str, Any],
    payload: Mapping[str, Any],
    response_schema: Mapping[str, Any],
) -> list[str]:
    """Validate structure and declared relationships, not their semantic truth."""

    errors = validation_errors(response, response_schema)
    group_id = payload.get("generation_group_id")
    if response.get("generation_group_id") != group_id:
        errors.append("generation_group_id does not match adjudication input")
    selected = response.get("selected_representative_occurrence_id")
    source_group = payload.get("source_group")
    contextualisations = (
        source_group.get("contextualisations", [])
        if isinstance(source_group, Mapping)
        else []
    )
    occurrence_ids = {
        item.get("occurrence_id")
        for item in contextualisations
        if isinstance(item, Mapping)
    }
    if selected is not None and selected not in occurrence_ids:
        errors.append("selected representative is not a member of the source group")

    response_criteria = response.get("criteria")
    reviewer_a = payload.get("reviewer_a_response")
    reviewer_b = payload.get("reviewer_b_response")
    for key in CRITERION_KEYS:
        try:
            judgement_a = reviewer_a["criteria"][key]["judgement"]
            judgement_b = reviewer_b["criteria"][key]["judgement"]
            resolution = response_criteria[key]
        except (KeyError, TypeError):
            continue
        if judgement_a != judgement_b:
            if resolution.get("disagreement_explicitly_resolved") is not True:
                errors.append(f"{key} disagreement was not explicitly resolved")
            if resolution.get("resolution_basis") == "no_disagreement":
                errors.append(f"{key} incorrectly declares no disagreement")

    try:
        h245_all_yes = all(
            response_criteria[key]["judgement"] == "yes"
            for key in (CRITERION_KEYS[1], CRITERION_KEYS[3], CRITERION_KEYS[4])
        )
    except (KeyError, TypeError):
        h245_all_yes = False
    if h245_all_yes:
        for field in (
            "proposed_bounded_action",
            "proposed_broader_action",
            "proposed_baseline_blocker",
            "proposed_evidence",
        ):
            if not isinstance(response.get(field), str) or not response[field].strip():
                errors.append(f"{field} is required when H2/H4/H5 are all yes")
        coherence = response.get("blocker_transition_coherence")
        if not isinstance(coherence, Mapping) or coherence.get(
            "same_blocker_and_transition"
        ) != "yes":
            errors.append(
                "H2/H4/H5 all yes requires the adjudicator's declared coherence "
                "assessment to be yes"
            )
        substantive = response.get("substantive_evidence_assessment")
        if not isinstance(substantive, Mapping) or any(
            substantive.get(field) != "yes"
            for field in (
                "is_substantive_finding_or_observation",
                "resolves_blocker_rather_than_only_increasing_safety_or_stakes",
            )
        ):
            errors.append(
                "H2/H4/H5 all yes requires the adjudicator's declared "
                "substantive-evidence assessments to be yes"
            )
    return sorted(set(errors))


def _validate_input_payload(
    payload: Mapping[str, Any],
    *,
    input_schema: Mapping[str, Any],
    reviewer_input_schema: Mapping[str, Any],
    reviewer_response_schema: Mapping[str, Any],
) -> None:
    validate_instance(payload, input_schema, label="Opus adjudication input")
    validate_instance(
        payload["source_group"], reviewer_input_schema, label="blinded source group"
    )
    for label in ("reviewer_a_response", "reviewer_b_response"):
        validate_instance(
            payload[label], reviewer_response_schema, label=f"{label} response"
        )


def _attempt_adjudication(
    *,
    adapter: ReviewerProvider,
    config: AdjudicationRunConfig,
    payload: Mapping[str, Any],
    prompt: str,
    prompt_sha256: str,
    response_schema: Mapping[str, Any],
    response_schema_sha256: str,
    run_record_schema: Mapping[str, Any],
    sleep_fn: Callable[[float], None],
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    final: dict[str, Any] = {
        "raw_response": None,
        "raw_response_text": None,
        "parsed_structured_response": None,
        "resolved_reported_model": None,
        "provider_request_id": None,
        "provider_generation_id": None,
        "actual_routed_provider": None,
        "provider_usage": None,
        "output_termination": None,
        "provider_reasoning": None,
        "provider_block_metadata": None,
        "validation_errors": [],
        "status": "provider_error",
    }
    rendered_prompt = render_adjudication_prompt(prompt, payload)

    for attempt_index in range(config.max_retries + 1):
        attempt_number = attempt_index + 1
        timestamp = utc_now()
        try:
            result = adapter.review(
                requested_model=config.requested_model,
                rendered_prompt=rendered_prompt,
                response_schema=response_schema,
                model_settings=config.model_settings,
            )
            final.update(
                raw_response=redact_secrets(result.raw_response),
                raw_response_text=redact_secrets(result.raw_response_text),
                resolved_reported_model=result.resolved_reported_model,
                provider_request_id=result.provider_request_id,
                provider_generation_id=result.provider_generation_id,
                actual_routed_provider=result.actual_routed_provider,
                provider_usage=redact_secrets(result.provider_usage),
                output_termination=redact_secrets(result.output_termination),
                provider_reasoning=redact_secrets(result.provider_reasoning),
                provider_block_metadata=None,
            )
            if result.terminal_status in {"refused", "blocked"}:
                final["status"] = result.terminal_status
                final["provider_block_metadata"] = redact_secrets(
                    result.provider_block_metadata
                )
                attempts.append(
                    _attempt_record(
                        attempt_number=attempt_number,
                        timestamp_utc=timestamp,
                        status=final["status"],
                        resolved_reported_model=final["resolved_reported_model"],
                        provider_request_id=final["provider_request_id"],
                        provider_generation_id=final["provider_generation_id"],
                        actual_routed_provider=final["actual_routed_provider"],
                        provider_usage=final["provider_usage"],
                        output_termination=final["output_termination"],
                        provider_block_metadata=final["provider_block_metadata"],
                        validation_errors=[],
                        error_type=None,
                        error_message=None,
                        retry_delay_seconds=None,
                    )
                )
                break
            try:
                candidate = parse_response_json(result.raw_response_text)
                candidate_errors = validate_adjudication_response(
                    candidate, payload, response_schema
                )
            except Exception as exc:
                candidate = None
                candidate_errors = [f"{type(exc).__name__}: {redact_secrets(str(exc))}"]
            if candidate is not None and not candidate_errors:
                termination = final["output_termination"]
                if isinstance(termination, Mapping) and termination.get(
                    "output_limit_reached"
                ) is True:
                    final["output_termination"] = {
                        **termination,
                        "valid_complete_response_recovered": True,
                    }
                final.update(
                    status="completed",
                    parsed_structured_response=candidate,
                    validation_errors=[],
                )
                attempts.append(
                    _attempt_record(
                        attempt_number=attempt_number,
                        timestamp_utc=timestamp,
                        status="completed",
                        resolved_reported_model=final["resolved_reported_model"],
                        provider_request_id=final["provider_request_id"],
                        provider_generation_id=final["provider_generation_id"],
                        actual_routed_provider=final["actual_routed_provider"],
                        provider_usage=final["provider_usage"],
                        output_termination=final["output_termination"],
                        provider_block_metadata=None,
                        validation_errors=[],
                        error_type=None,
                        error_message=None,
                        retry_delay_seconds=None,
                    )
                )
                break
            final.update(
                parsed_structured_response=None,
                validation_errors=candidate_errors,
            )
            termination = final["output_termination"]
            if isinstance(termination, Mapping) and termination.get(
                "output_limit_reached"
            ) is True:
                final["status"] = "truncated"
                retry_delay = None
                error_type = "OutputLimitTermination"
            else:
                final["status"] = "validation_failed"
                retry_delay = (
                    config.initial_retry_delay_seconds * (2**attempt_index)
                    if attempt_index < config.max_retries
                    else None
                )
                error_type = "LocalAdjudicationValidationError"
            attempts.append(
                _attempt_record(
                    attempt_number=attempt_number,
                    timestamp_utc=timestamp,
                    status=final["status"],
                    resolved_reported_model=final["resolved_reported_model"],
                    provider_request_id=final["provider_request_id"],
                    provider_generation_id=final["provider_generation_id"],
                    actual_routed_provider=final["actual_routed_provider"],
                    provider_usage=final["provider_usage"],
                    output_termination=final["output_termination"],
                    provider_block_metadata=None,
                    validation_errors=candidate_errors,
                    error_type=error_type,
                    error_message="Adjudication output failed complete local validation",
                    retry_delay_seconds=retry_delay,
                )
            )
            if final["status"] == "truncated":
                break
        except Exception as exc:
            for field in (
                "raw_response",
                "raw_response_text",
                "resolved_reported_model",
                "provider_request_id",
                "provider_generation_id",
                "actual_routed_provider",
                "provider_usage",
                "output_termination",
                "provider_reasoning",
            ):
                final[field] = redact_secrets(getattr(exc, field, None))
            final.update(
                status="provider_error",
                parsed_structured_response=None,
                provider_block_metadata=None,
                validation_errors=[],
            )
            retry_delay = (
                config.initial_retry_delay_seconds * (2**attempt_index)
                if attempt_index < config.max_retries
                else None
            )
            attempts.append(
                _attempt_record(
                    attempt_number=attempt_number,
                    timestamp_utc=timestamp,
                    status="provider_error",
                    resolved_reported_model=final["resolved_reported_model"],
                    provider_request_id=final["provider_request_id"],
                    provider_generation_id=final["provider_generation_id"],
                    actual_routed_provider=final["actual_routed_provider"],
                    provider_usage=final["provider_usage"],
                    output_termination=final["output_termination"],
                    provider_block_metadata=None,
                    validation_errors=[],
                    error_type=type(exc).__name__,
                    error_message=redact_secrets(str(exc)),
                    retry_delay_seconds=retry_delay,
                )
            )
        if attempt_index < config.max_retries:
            sleep_fn(config.initial_retry_delay_seconds * (2**attempt_index))

    record = {
        "record_schema_version": ADJUDICATOR_RUN_RECORD_SCHEMA_VERSION,
        "adjudicator_run_id": config.adjudicator_run_id,
        "generation_group_id": payload["generation_group_id"],
        "provider": config.provider,
        "requested_model": config.requested_model,
        "resolved_reported_model": final["resolved_reported_model"],
        "provider_request_id": final["provider_request_id"],
        "provider_generation_id": final["provider_generation_id"],
        "actual_routed_provider": final["actual_routed_provider"],
        "provider_usage": final["provider_usage"],
        "aggregate_usage": _aggregate_attempt_usage(attempts),
        "output_termination": final["output_termination"],
        "provider_reasoning": final["provider_reasoning"],
        "prompt_version": ADJUDICATION_PROMPT_VERSION,
        "prompt_sha256": prompt_sha256,
        "response_schema_sha256": response_schema_sha256,
        "input_payload_sha256": sha256_text(canonical_json(payload)),
        "status": final["status"],
        "raw_response": final["raw_response"],
        "raw_response_text": final["raw_response_text"],
        "parsed_structured_response": final["parsed_structured_response"],
        "provider_block_metadata": final["provider_block_metadata"],
        "timestamp_utc": utc_now(),
        "model_settings": config.model_settings,
        "retry_information": {
            "max_retries": config.max_retries,
            "attempt_count": len(attempts),
            "attempts": attempts,
        },
        "validation_errors": final["validation_errors"],
        "semantic_truth_deterministically_established": False,
    }
    validate_instance(record, run_record_schema, label="adjudicator run record")
    return record


def run_adjudications(
    *,
    config: AdjudicationRunConfig,
    payloads: Sequence[Mapping[str, Any]],
    output_root: Path,
    execute: bool,
    resume: bool,
    adapter: ReviewerProvider | None = None,
    prompt_path: Path = DEFAULT_ADJUDICATION_PROMPT_PATH,
    input_schema_path: Path = DEFAULT_ADJUDICATION_INPUT_SCHEMA_PATH,
    response_schema_path: Path = DEFAULT_ADJUDICATION_RESPONSE_SCHEMA_PATH,
    run_record_schema_path: Path = DEFAULT_ADJUDICATOR_RUN_RECORD_SCHEMA_PATH,
    reviewer_input_schema_path: Path = (
        REPO_ROOT
        / "schemas"
        / "airisk_jmcup_group_semantic_review_input_v1.schema.json"
    ),
    reviewer_response_schema_path: Path = (
        REPO_ROOT / "schemas" / "airisk_jmcup_group_semantic_review_v1.schema.json"
    ),
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    config.validate()
    input_schema = load_schema(input_schema_path)
    reviewer_input_schema = load_schema(reviewer_input_schema_path)
    reviewer_response_schema = load_schema(reviewer_response_schema_path)
    for payload in payloads:
        _validate_input_payload(
            payload,
            input_schema=input_schema,
            reviewer_input_schema=reviewer_input_schema,
            reviewer_response_schema=reviewer_response_schema,
        )
    run_dir = output_root.resolve() / config.provider / config.adjudicator_run_id
    plan = {
        "execute": execute,
        "external_api_calls_planned": len(payloads) if execute else 0,
        "adjudicator_run_id": config.adjudicator_run_id,
        "selected_group_count": len(payloads),
        "run_directory": str(run_dir),
        "safe_default_no_execute": not execute,
    }
    if not execute:
        return plan

    prompt = load_prompt(prompt_path)
    response_schema = load_schema(response_schema_path)
    run_record_schema = load_schema(run_record_schema_path)
    records_path = run_dir / "adjudications.jsonl"
    manifest_path = run_dir / "run_manifest.json"
    manifest = {
        "schema_version": ADJUDICATOR_RUN_MANIFEST_VERSION,
        "created_at_utc": utc_now(),
        "adjudicator_run_id": config.adjudicator_run_id,
        "provider": config.provider,
        "requested_model": config.requested_model,
        "selected_generation_group_ids": [
            payload["generation_group_id"] for payload in payloads
        ],
        "prompt_version": ADJUDICATION_PROMPT_VERSION,
        "prompt_sha256": sha256_path(prompt_path),
        "input_schema_sha256": sha256_path(input_schema_path),
        "response_schema_sha256": sha256_path(response_schema_path),
        "run_record_schema_sha256": sha256_path(run_record_schema_path),
        "reviewer_input_schema_sha256": sha256_path(reviewer_input_schema_path),
        "reviewer_response_schema_sha256": sha256_path(
            reviewer_response_schema_path
        ),
        "model_settings": config.model_settings,
        "max_retries": config.max_retries,
        "semantic_truth_deterministically_established": False,
    }
    if run_dir.exists():
        if not resume:
            raise SemanticReviewError(f"Adjudicator run already exists: {run_dir}")
        existing = read_json(manifest_path)
        immutable = (
            "adjudicator_run_id",
            "provider",
            "requested_model",
            "selected_generation_group_ids",
            "prompt_sha256",
            "input_schema_sha256",
            "response_schema_sha256",
            "run_record_schema_sha256",
            "reviewer_input_schema_sha256",
            "reviewer_response_schema_sha256",
            "model_settings",
            "max_retries",
        )
        if any(existing.get(key) != manifest.get(key) for key in immutable):
            raise SemanticReviewError("Adjudicator resume configuration differs")
    else:
        run_dir.mkdir(parents=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    completed = {
        record["generation_group_id"]
        for record in read_jsonl(records_path)
        if record.get("status") == "completed"
    } if records_path.exists() else set()
    pending = [
        payload for payload in payloads
        if payload["generation_group_id"] not in completed
    ]
    active_adapter = adapter or create_provider_adapter(config.provider)

    def one(payload: Mapping[str, Any]) -> dict[str, Any]:
        return _attempt_adjudication(
            adapter=active_adapter,
            config=config,
            payload=payload,
            prompt=prompt,
            prompt_sha256=sha256_path(prompt_path),
            response_schema=response_schema,
            response_schema_sha256=sha256_path(response_schema_path),
            run_record_schema=run_record_schema,
            sleep_fn=sleep_fn,
        )

    records: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}

    def checkpoint(record: dict[str, Any]) -> None:
        # Only this parent/as-completed thread writes the shared JSONL. The
        # imported append helper flushes and fsyncs before returning.
        _append_checkpoint(records_path, record)
        records.append(record)
        status = record["status"]
        status_counts[status] = status_counts.get(status, 0) + 1

    if config.concurrency == 1:
        for payload in pending:
            checkpoint(one(payload))
    else:
        with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
            futures = {executor.submit(one, payload): payload for payload in pending}
            for future in as_completed(futures):
                checkpoint(future.result())
    return {
        **plan,
        "completed_before_resume": len(completed),
        "new_records_written": len(records),
        "status_counts": dict(sorted(status_counts.items())),
        "records_path": str(records_path),
        "manifest_path": str(manifest_path),
    }
