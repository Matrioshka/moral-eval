"""Deterministic offline integrity audit for a persisted adjudicator run."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .adjudication_execution import (
    ADJUDICATION_PROMPT_VERSION,
    DEFAULT_ADJUDICATION_INPUT_SCHEMA_PATH,
    DEFAULT_ADJUDICATION_PROMPT_PATH,
    DEFAULT_ADJUDICATION_RESPONSE_SCHEMA_PATH,
    DEFAULT_ADJUDICATOR_RUN_RECORD_SCHEMA_PATH,
    validate_adjudication_response,
)
from .core import (
    DEFAULT_INPUT_SCHEMA_PATH as DEFAULT_REVIEWER_INPUT_SCHEMA_PATH,
    DEFAULT_RESPONSE_SCHEMA_PATH as DEFAULT_REVIEWER_RESPONSE_SCHEMA_PATH,
    REPO_ROOT,
    SemanticReviewError,
    canonical_json,
    load_schema,
    read_json,
    read_jsonl,
    sha256_path,
    sha256_text,
    validate_instance,
)


AUDIT_SCHEMA_VERSION = "airisk_jmcup_adjudicator_run_audit_v1"
AUDIT_PROTOCOL_VERSION = "airisk_jmcup_adjudicator_run_audit_protocol_v1"
TERMINAL_BLOCK_RULE_VERSION = "airisk_jmcup_terminal_provider_block_rule_v1"
EXPECTED_REQUESTED_MODEL = "anthropic/claude-opus-5"
FAILURE_STATUSES = ("validation_failed", "truncated", "provider_error")
USAGE_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "cached_tokens",
    "total_tokens",
    "cost",
)

DEFAULT_AUDIT_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{AUDIT_SCHEMA_VERSION}.schema.json"
)
DEFAULT_RUN_MANIFEST_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / "airisk_jmcup_adjudicator_run_manifest_v1.schema.json"
)
DEFAULT_AUDIT_PROTOCOL_PATH = (
    REPO_ROOT / "docs" / f"{AUDIT_PROTOCOL_VERSION}.md"
)


def _artifact(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": sha256_path(path)}


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _number(value: Any, *, integer: bool) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if integer:
        if isinstance(value, float) and not value.is_integer():
            return None
        return int(value)
    return value


def _normalise_usage(value: Any) -> dict[str, int | float | None]:
    usage = value if isinstance(value, Mapping) else {}
    prompt_details = usage.get("prompt_tokens_details")
    if not isinstance(prompt_details, Mapping):
        prompt_details = {}
    completion_details = usage.get("completion_tokens_details")
    if not isinstance(completion_details, Mapping):
        completion_details = {}
    return {
        "prompt_tokens": _number(usage.get("prompt_tokens"), integer=True),
        "completion_tokens": _number(usage.get("completion_tokens"), integer=True),
        "reasoning_tokens": _number(
            completion_details.get(
                "reasoning_tokens", usage.get("reasoning_tokens")
            ),
            integer=True,
        ),
        "cached_tokens": _number(
            prompt_details.get("cached_tokens", usage.get("cached_tokens")),
            integer=True,
        ),
        "total_tokens": _number(usage.get("total_tokens"), integer=True),
        "cost": _number(usage.get("cost"), integer=False),
    }


def _attempt_usage(attempt: Mapping[str, Any]) -> dict[str, int | float | None]:
    normalised = attempt.get("normalised_usage")
    if isinstance(normalised, Mapping):
        return {
            field: _number(normalised.get(field), integer=field != "cost")
            for field in USAGE_FIELDS
        }
    return _normalise_usage(attempt.get("provider_usage"))


def _record_contributions(
    record: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    attempts = record["retry_information"]["attempts"]
    if attempts:
        return [
            {
                "status": attempt.get("status"),
                "usage": _attempt_usage(attempt),
            }
            for attempt in attempts
        ], False
    aggregate = record.get("aggregate_usage")
    if not isinstance(aggregate, Mapping):
        aggregate = {}
    usage = {
        field: _number(aggregate.get(field), integer=field != "cost")
        for field in USAGE_FIELDS
    }
    return [{"status": record["status"], "usage": usage}], True


def _usage_total(
    contributions: Sequence[Mapping[str, Any]], *, fallback_count: int
) -> dict[str, Any]:
    totals: dict[str, Any] = {}
    reported_counts: dict[str, int] = {}
    for field in USAGE_FIELDS:
        values = [
            contribution["usage"].get(field)
            for contribution in contributions
            if isinstance(contribution.get("usage"), Mapping)
        ]
        numeric = [
            value
            for value in values
            if not isinstance(value, bool) and isinstance(value, (int, float))
        ]
        totals[field] = sum(numeric) if numeric else None
        reported_counts[field] = len(numeric)
    totals["reported_contribution_counts"] = reported_counts
    totals["attempt_contribution_count"] = len(contributions)
    totals["aggregate_fallback_record_count"] = fallback_count
    return totals


def _distribution(values: Sequence[int | float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "sum": None,
            "minimum": None,
            "maximum": None,
            "mean": None,
            "median": None,
        }
    return {
        "count": len(values),
        "sum": sum(values),
        "minimum": min(values),
        "maximum": max(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
    }


def _distribution_set(
    contributions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in USAGE_FIELDS:
        values = [
            contribution["usage"].get(field)
            for contribution in contributions
            if isinstance(contribution.get("usage"), Mapping)
        ]
        result[field] = _distribution(
            [
                value
                for value in values
                if not isinstance(value, bool) and isinstance(value, (int, float))
            ]
        )
    return result


def _explicit_block_signal(record: Mapping[str, Any]) -> bool:
    if record.get("status") not in {"blocked", "refused"}:
        return False
    metadata = record.get("provider_block_metadata")
    if not isinstance(metadata, Mapping) or not metadata:
        return False
    attempts = record["retry_information"]["attempts"]
    if not attempts:
        return False
    final_attempt = attempts[-1]
    return (
        final_attempt.get("status") in {"blocked", "refused"}
        and isinstance(final_attempt.get("provider_block_metadata"), Mapping)
        and bool(final_attempt["provider_block_metadata"])
    )


def _terminal_block_summary(
    group_id: str, history: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    if any(record["status"] == "completed" for record in history):
        return None
    if len(history) < 2:
        return None
    latest = list(history[-2:])
    if not all(_explicit_block_signal(record) for record in latest):
        return None
    final_attempts = [record["retry_information"]["attempts"][-1] for record in latest]
    return {
        "generation_group_id": group_id,
        "terminal_status": "terminal_provider_block",
        "journal_record_count": len(history),
        "latest_two_record_statuses": [record["status"] for record in latest],
        "latest_two_attempt_statuses": [attempt["status"] for attempt in final_attempts],
        "provider_signals": [
            dict(attempt["provider_block_metadata"]) for attempt in final_attempts
        ],
        "output_terminations": [
            attempt.get("output_termination") for attempt in final_attempts
        ],
        "semantic_judgement": False,
    }


def audit_adjudicator_run(
    *,
    preparation_manifest_path: Path,
    adjudication_payloads_path: Path,
    run_manifest_path: Path,
    adjudication_records_path: Path,
    output_path: Path | None = None,
    prompt_path: Path = DEFAULT_ADJUDICATION_PROMPT_PATH,
    input_schema_path: Path = DEFAULT_ADJUDICATION_INPUT_SCHEMA_PATH,
    response_schema_path: Path = DEFAULT_ADJUDICATION_RESPONSE_SCHEMA_PATH,
    run_record_schema_path: Path = DEFAULT_ADJUDICATOR_RUN_RECORD_SCHEMA_PATH,
    reviewer_input_schema_path: Path = DEFAULT_REVIEWER_INPUT_SCHEMA_PATH,
    reviewer_response_schema_path: Path = DEFAULT_REVIEWER_RESPONSE_SCHEMA_PATH,
    run_manifest_schema_path: Path = DEFAULT_RUN_MANIFEST_SCHEMA_PATH,
    audit_schema_path: Path = DEFAULT_AUDIT_SCHEMA_PATH,
    audit_protocol_path: Path = DEFAULT_AUDIT_PROTOCOL_PATH,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Audit an adjudicator journal without interpreting semantic judgements."""

    preparation = read_json(preparation_manifest_path)
    if preparation.get("schema_version") != "airisk_jmcup_opus_adjudication_manifest_v1":
        raise SemanticReviewError("Unexpected adjudication preparation manifest")
    payloads = read_jsonl(adjudication_payloads_path)
    run_manifest = read_json(run_manifest_path)
    validate_instance(
        run_manifest, load_schema(run_manifest_schema_path),
        label="adjudicator run manifest",
    )
    if run_manifest["requested_model"] != EXPECTED_REQUESTED_MODEL:
        raise SemanticReviewError(
            f"Adjudicator model must be exactly {EXPECTED_REQUESTED_MODEL}"
        )

    input_schema = load_schema(input_schema_path)
    reviewer_input_schema = load_schema(reviewer_input_schema_path)
    reviewer_response_schema = load_schema(reviewer_response_schema_path)
    response_schema = load_schema(response_schema_path)
    run_record_schema = load_schema(run_record_schema_path)
    payload_by_id: dict[str, dict[str, Any]] = {}
    payload_ids: list[str] = []
    for payload in payloads:
        validate_instance(payload, input_schema, label="Opus adjudication payload")
        validate_instance(
            payload["source_group"], reviewer_input_schema,
            label="Opus source-group payload",
        )
        validate_instance(
            payload["reviewer_a_response"], reviewer_response_schema,
            label="Opus reviewer A response",
        )
        validate_instance(
            payload["reviewer_b_response"], reviewer_response_schema,
            label="Opus reviewer B response",
        )
        group_id = payload["generation_group_id"]
        if group_id in payload_by_id:
            raise SemanticReviewError(f"Duplicate adjudication payload {group_id}")
        if payload["source_group"]["generation_group_id"] != group_id:
            raise SemanticReviewError(f"Source-group ID differs in payload {group_id}")
        for reviewer_label in ("reviewer_a_response", "reviewer_b_response"):
            if payload[reviewer_label]["generation_group_id"] != group_id:
                raise SemanticReviewError(
                    f"{reviewer_label} group ID differs in payload {group_id}"
                )
        payload_by_id[group_id] = payload
        payload_ids.append(group_id)

    selected_ids = preparation.get("selected_group_ids")
    if not isinstance(selected_ids, list) or preparation.get(
        "selected_group_count"
    ) != len(selected_ids):
        raise SemanticReviewError("Invalid selected groups in preparation manifest")
    if preparation.get("payloads_sha256") != sha256_path(adjudication_payloads_path):
        raise SemanticReviewError("Preparation payload hash differs")
    if payload_ids != selected_ids:
        raise SemanticReviewError(
            "Ordered adjudication payload IDs differ from preparation selection"
        )
    if run_manifest["selected_generation_group_ids"] != selected_ids:
        raise SemanticReviewError(
            "Ordered run-manifest selection differs from frozen preparation"
        )

    expected_hashes = {
        "prompt_sha256": sha256_path(prompt_path),
        "input_schema_sha256": sha256_path(input_schema_path),
        "response_schema_sha256": sha256_path(response_schema_path),
        "run_record_schema_sha256": sha256_path(run_record_schema_path),
        "reviewer_input_schema_sha256": sha256_path(reviewer_input_schema_path),
        "reviewer_response_schema_sha256": sha256_path(
            reviewer_response_schema_path
        ),
    }
    for field, expected in expected_hashes.items():
        if run_manifest.get(field) != expected:
            raise SemanticReviewError(f"Run manifest {field} differs from canonical artefact")

    records = read_jsonl(adjudication_records_path)
    histories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    completed_by_id: dict[str, dict[str, Any]] = {}
    record_status_counts: Counter[str] = Counter()
    attempt_status_counts: Counter[str] = Counter()
    all_contributions: list[dict[str, Any]] = []
    completed_record_contributions: list[dict[str, Any]] = []
    completed_attempt_contributions: list[dict[str, Any]] = []
    all_fallback_count = 0
    completed_fallback_count = 0
    for journal_index, record in enumerate(records):
        validate_instance(record, run_record_schema, label="adjudicator run record")
        group_id = record["generation_group_id"]
        if group_id not in payload_by_id:
            raise SemanticReviewError(f"Adjudication journal contains unselected group {group_id}")
        immutable_record_fields = {
            "adjudicator_run_id": run_manifest["adjudicator_run_id"],
            "provider": run_manifest["provider"],
            "requested_model": run_manifest["requested_model"],
            "prompt_version": ADJUDICATION_PROMPT_VERSION,
            "prompt_sha256": run_manifest["prompt_sha256"],
            "response_schema_sha256": run_manifest["response_schema_sha256"],
            "model_settings": run_manifest["model_settings"],
        }
        for field, expected in immutable_record_fields.items():
            if record.get(field) != expected:
                raise SemanticReviewError(
                    f"Journal record {journal_index} {field} differs from run manifest"
                )
        if record["retry_information"]["max_retries"] != run_manifest["max_retries"]:
            raise SemanticReviewError(
                f"Journal record {journal_index} max_retries differs from run manifest"
            )
        payload_sha = sha256_text(canonical_json(payload_by_id[group_id]))
        if record["input_payload_sha256"] != payload_sha:
            raise SemanticReviewError(f"Adjudication input hash differs for {group_id}")
        attempts = record["retry_information"]["attempts"]
        attempt_count = record["retry_information"]["attempt_count"]
        if attempts and len(attempts) != attempt_count:
            raise SemanticReviewError(
                f"Attempt count differs from persisted attempts for {group_id}"
            )
        if attempts and attempts[-1].get("status") != record["status"]:
            raise SemanticReviewError(
                f"Final attempt status differs from record status for {group_id}"
            )
        if record["status"] == "completed":
            if group_id in completed_by_id:
                raise SemanticReviewError(
                    f"Multiple completed adjudications for {group_id}"
                )
            errors = validate_adjudication_response(
                record["parsed_structured_response"],
                payload_by_id[group_id],
                response_schema,
            )
            if errors:
                raise SemanticReviewError(
                    f"Completed adjudication is invalid for {group_id}: {errors}"
                )
            completed_by_id[group_id] = record
        histories[group_id].append(record)
        record_status_counts[record["status"]] += 1
        attempt_status_counts.update(
            attempt.get("status", "missing") for attempt in attempts
        )
        contributions, fallback = _record_contributions(record)
        all_contributions.extend(contributions)
        all_fallback_count += int(fallback)
        completed_attempt_contributions.extend(
            item for item in contributions if item["status"] == "completed"
        )
        if record["status"] == "completed":
            completed_record_contributions.extend(contributions)
            completed_fallback_count += int(fallback)

    completed_ids = sorted(completed_by_id)
    terminal_summaries = []
    for group_id in selected_ids:
        summary = _terminal_block_summary(group_id, histories.get(group_id, []))
        if summary is not None:
            terminal_summaries.append(summary)
    terminal_ids = sorted(
        item["generation_group_id"] for item in terminal_summaries
    )
    unresolved_ids = sorted(set(selected_ids) - set(completed_ids))
    nonterminal_ids = sorted(set(unresolved_ids) - set(terminal_ids))

    history_summaries = []
    blocked_later_completed = []
    for group_id in selected_ids:
        history = histories.get(group_id, [])
        attempt_statuses = [
            attempt.get("status", "missing")
            for record in history
            for attempt in record["retry_information"]["attempts"]
        ]
        attempt_count = sum(
            record["retry_information"]["attempt_count"] for record in history
        )
        if len(history) > 1 or any(
            record["retry_information"]["attempt_count"] > 1 for record in history
        ):
            history_summaries.append({
                "generation_group_id": group_id,
                "journal_record_count": len(history),
                "attempt_count": attempt_count,
                "record_statuses": [record["status"] for record in history],
                "attempt_statuses": attempt_statuses,
            })
        completed_indexes = [
            index for index, record in enumerate(history)
            if record["status"] == "completed"
        ]
        if completed_indexes:
            completion_index = completed_indexes[0]
            if any(
                record["status"] in {"blocked", "refused"}
                for record in history[:completion_index]
            ):
                blocked_later_completed.append(group_id)

    failure_record_counts = {
        status: record_status_counts.get(status, 0) for status in FAILURE_STATUSES
    }
    failure_attempt_counts = {
        status: attempt_status_counts.get(status, 0) for status in FAILURE_STATUSES
    }
    retry_distribution = Counter(
        str(record["retry_information"]["attempt_count"]) for record in records
    )
    audit = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "audit_protocol_version": AUDIT_PROTOCOL_VERSION,
        "adjudicator_run_id": run_manifest["adjudicator_run_id"],
        "provider": run_manifest["provider"],
        "requested_model": run_manifest["requested_model"],
        "model_settings": run_manifest["model_settings"],
        "terminal_provider_block_rule": {
            "version": TERMINAL_BLOCK_RULE_VERSION,
            "minimum_persisted_records": 2,
            "records_examined": "latest_two_persisted_records",
            "required_final_statuses": ["blocked", "refused"],
            "explicit_provider_signal_required": True,
            "semantic_judgement": False,
        },
        "selected_group_count": len(selected_ids),
        "unique_completed_group_count": len(completed_ids),
        "unique_unresolved_group_count": len(unresolved_ids),
        "journal_record_count": len(records),
        "record_status_counts": dict(sorted(record_status_counts.items())),
        "attempt_status_counts": dict(sorted(attempt_status_counts.items())),
        "failure_status_counts": {
            "record": failure_record_counts,
            "attempt": failure_attempt_counts,
        },
        "groups_with_retry_or_resume_histories": history_summaries,
        "groups_with_blocked_histories_later_completed": sorted(
            blocked_later_completed
        ),
        "completed_group_ids": completed_ids,
        "terminal_provider_block_group_ids": terminal_ids,
        "nonterminal_unresolved_group_ids": nonterminal_ids,
        "terminal_unresolved_groups": terminal_summaries,
        "retry_attempt_distribution": dict(sorted(retry_distribution.items())),
        "usage": {
            "all_journal_records": _usage_total(
                all_contributions, fallback_count=all_fallback_count
            ),
            "completed_journal_records": _usage_total(
                completed_record_contributions,
                fallback_count=completed_fallback_count,
            ),
            "completed_attempts": _usage_total(
                completed_attempt_contributions, fallback_count=0
            ),
            "aggregation_method": (
                "sum each persisted attempt's normalised usage once; never add "
                "top-level final-attempt provider_usage; use aggregate_usage once "
                "only for a record with no persisted attempt details"
            ),
        },
        "token_distributions": {
            "all_attempts": _distribution_set(all_contributions),
            "completed_attempts": _distribution_set(
                completed_attempt_contributions
            ),
        },
        "artifacts": {
            "preparation_manifest": _artifact(preparation_manifest_path),
            "adjudication_payloads": _artifact(adjudication_payloads_path),
            "run_manifest": _artifact(run_manifest_path),
            "adjudication_journal": _artifact(adjudication_records_path),
            "audit_protocol": _artifact(audit_protocol_path),
            "audit_schema": _artifact(audit_schema_path),
            "run_manifest_schema": _artifact(run_manifest_schema_path),
            "prompt": _artifact(prompt_path),
            "input_schema": _artifact(input_schema_path),
            "response_schema": _artifact(response_schema_path),
            "run_record_schema": _artifact(run_record_schema_path),
            "reviewer_input_schema": _artifact(reviewer_input_schema_path),
            "reviewer_response_schema": _artifact(
                reviewer_response_schema_path
            ),
        },
        "invariants": {
            "selected_sets_exact": True,
            "ordered_selected_ids_exact": True,
            "unknown_groups": [],
            "duplicate_completed_groups": [],
            "bad_input_hash_groups": [],
            "invalid_completed_groups": [],
            "usage_double_counting_detected": False,
        },
        "external_api_calls": 0,
        "semantic_content_interpreted": False,
        "semantic_truth_deterministically_established": False,
    }
    if len(completed_ids) + len(unresolved_ids) != len(selected_ids):
        raise SemanticReviewError("Completed/unresolved group partition is invalid")
    validate_instance(audit, load_schema(audit_schema_path), label="adjudicator run audit")
    if output_path is not None:
        if output_path.exists() and not overwrite:
            raise SemanticReviewError(f"Refusing to overwrite adjudicator audit {output_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(output_path, audit)
    return audit
