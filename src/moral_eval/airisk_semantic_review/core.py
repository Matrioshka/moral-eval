"""Blinded payload construction and canonical schema validation."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
PROMPT_VERSION = "airisk_jmcup_group_semantic_review_v1"
INPUT_SCHEMA_VERSION = "airisk_jmcup_group_semantic_review_input_v1"
RESPONSE_SCHEMA_VERSION = "airisk_jmcup_group_semantic_review_v1"
RUN_RECORD_SCHEMA_VERSION = "airisk_jmcup_reviewer_run_record_v1"

DEFAULT_PROMPT_PATH = REPO_ROOT / "prompts" / f"{PROMPT_VERSION}.txt"
DEFAULT_INPUT_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{INPUT_SCHEMA_VERSION}.schema.json"
)
DEFAULT_RESPONSE_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{RESPONSE_SCHEMA_VERSION}.schema.json"
)
DEFAULT_RUN_RECORD_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{RUN_RECORD_SCHEMA_VERSION}.schema.json"
)
DEFAULT_V3_QUEUE_PATH = (
    REPO_ROOT
    / "tmp"
    / "airiskdilemmas_jmcup_whole_corpus_v3"
    / "airisk_jmcup_group_review_queue.jsonl"
)
DEFAULT_V3_MANIFEST_PATH = DEFAULT_V3_QUEUE_PATH.with_name("manifest_v3.json")

EXPECTED_GROUP_COUNT = 1_040
EXPECTED_AUDIT_GROUP_COUNT = 104
TOKEN_ESTIMATE_METHOD = "ceil(Unicode character count / 4); heuristic, not provider tokenisation"

FORBIDDEN_MODEL_VISIBLE_KEYS = frozenset(
    {
        "metadata_summary",
        "semantic_family",
        "high_stakes_domain",
        "blocker_family_hint",
        "heuristic_group_queue_priority_band",
        "heuristic_best_member_score",
        "heuristic_group_score_median",
        "heuristic_disclaimer",
        "heuristic_queue_priority_band",
        "heuristic_jmcup_source_priority_score",
        "representative_candidates",
        "deterministic_ranking_reasons",
        "reviewer_fields",
        "pre_outcome_audit_sample",
        "pre_outcome_audit_sample_rank",
        "candidate_matches",
        "answer_matching_behavior",
        "answer_not_matching_behavior",
    }
)

CRITERION_KEYS = (
    "h1_same_decision_axis",
    "h2_specific_baseline_safety_blocker",
    "h3_pressure_changes_no_relevant_facts",
    "h4_evidence_removes_same_blocker",
    "h5_same_broader_action_becomes_justified",
    "h6_broader_action_not_intrinsically_wrongful",
    "h7_calibrated_updating_not_contested_first_order_morality",
)


class SemanticReviewError(RuntimeError):
    """A frozen-interface or semantic-review invariant failed."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SemanticReviewError(f"Expected JSON object in {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise SemanticReviewError(
                    f"Expected JSON object at {path}:{line_number}"
                )
            records.append(value)
    return records


def write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(canonical_json(record) + "\n")


def load_prompt(path: Path = DEFAULT_PROMPT_PATH) -> str:
    prompt = path.read_text(encoding="utf-8")
    if not prompt.strip():
        raise SemanticReviewError(f"Frozen prompt is empty: {path}")
    return prompt


def load_schema(path: Path) -> dict[str, Any]:
    return read_json(path)


def _jsonschema_validator(schema: Mapping[str, Any]):
    try:
        from jsonschema import validators
    except ImportError as exc:  # pragma: no cover - exercised by deployment setup
        raise SemanticReviewError(
            "jsonschema is required; install requirements-airisk-semantic-review.txt"
        ) from exc
    validator_class = validators.validator_for(schema)
    validator_class.check_schema(schema)
    return validator_class(schema)


def validation_errors(
    instance: Any,
    schema: Mapping[str, Any],
) -> list[str]:
    errors = _jsonschema_validator(schema).iter_errors(instance)
    rendered = []
    for error in sorted(errors, key=lambda item: tuple(str(part) for part in item.path)):
        location = "/".join(str(part) for part in error.absolute_path) or "$"
        rendered.append(f"{location}: {error.message}")
    return rendered


def validate_instance(
    instance: Any,
    schema: Mapping[str, Any],
    *,
    label: str,
) -> None:
    errors = validation_errors(instance, schema)
    if errors:
        raise SemanticReviewError(
            f"{label} failed JSON Schema validation: " + " | ".join(errors)
        )


def _allow_source_seed(source: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "source_record_id",
        "source_path",
        "source_row_index",
        "source_dataset_layer",
        "question",
        "question_sha256",
    )
    return {field: source[field] for field in fields}


def _allow_action(action: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "source_row_index",
        "action",
        "action_body",
        "values",
        "risky_behaviors",
        "context",
        "targets",
        "targets_available",
        "targets_source_configuration",
        "targets_source_row_index",
        "targets_join_method",
        "targets_source_dilemma_sha256",
    )
    return {field: action[field] for field in fields}


def _allow_contextualisation(item: Mapping[str, Any]) -> dict[str, Any]:
    actions = item["actions"]
    return {
        "group_occurrence_ordinal": item["group_occurrence_ordinal"],
        "occurrence_id": item["occurrence_id"],
        "pair_index": item["pair_index"],
        "dilemma_id": item["dilemma_id"],
        "dilemma_sha256": item["dilemma_sha256"],
        "dilemma": item["dilemma"],
        "actions": {
            "action_1": _allow_action(actions["action_1"]),
            "action_2": _allow_action(actions["action_2"]),
        },
        "risky_behaviors": list(item["risky_behaviors"]),
        "contexts": list(item["contexts"]),
    }


def find_forbidden_model_visible_keys(value: Any) -> list[str]:
    found: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                rendered_key = str(key)
                if (
                    rendered_key in FORBIDDEN_MODEL_VISIBLE_KEYS
                    or rendered_key.startswith("heuristic_")
                    or rendered_key.startswith("h1_")
                    or rendered_key.startswith("h2_")
                    or rendered_key.startswith("h3_")
                    or rendered_key.startswith("h4_")
                    or rendered_key.startswith("h5_")
                    or rendered_key.startswith("h6_")
                    or rendered_key.startswith("h7_")
                ):
                    found.add(rendered_key)
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return sorted(found)


def build_blinded_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    """Build a model-visible payload by positive allow-list only."""

    reconstruction = record["generation_group_reconstruction"]
    source_match = record["source_seed_match"]
    source_record = source_match.get("source_seed_record")
    exact_source_seed = (
        _allow_source_seed(source_record)
        if source_match.get("status") == "exact_publication_anchor"
        and isinstance(source_record, Mapping)
        else None
    )
    contextualisations = sorted(
        record["all_contextualisations"],
        key=lambda item: item["group_occurrence_ordinal"],
    )
    observed_ordinals = [item["group_occurrence_ordinal"] for item in contextualisations]
    if observed_ordinals != list(range(len(contextualisations))):
        raise SemanticReviewError(
            f"Non-contiguous source order in {record.get('generation_group_id')}"
        )
    payload = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "generation_group_id": record["generation_group_id"],
        "lineage": {
            "status": "reconstructed_contiguous_generation_group",
            "method": reconstruction["method"],
            "confidence": reconstruction["confidence"],
            "occurrence_count": reconstruction["occurrence_count"],
            "unique_dilemma_count": reconstruction["unique_dilemma_count"],
            "pair_index_start": reconstruction["pair_index_start"],
            "pair_index_end": reconstruction["pair_index_end"],
            "primary_hypothesis_clearly_preferred": reconstruction[
                "primary_hypothesis_clearly_preferred"
            ],
        },
        "source_seed_lineage": {
            "status": source_match["status"],
            "confidence": source_match["confidence"],
            "provenance": source_match["provenance"],
            "exact_source_seed": exact_source_seed,
        },
        "contextualisations": [
            _allow_contextualisation(item) for item in contextualisations
        ],
    }
    leaked = find_forbidden_model_visible_keys(payload)
    if leaked:
        raise SemanticReviewError(
            f"Forbidden model-visible keys in {record['generation_group_id']}: {leaked}"
        )
    return payload


def build_payload_corpus(
    queue_records: Sequence[Mapping[str, Any]],
    *,
    input_schema: Mapping[str, Any] | None = None,
    expected_group_count: int | None = EXPECTED_GROUP_COUNT,
) -> list[dict[str, Any]]:
    schema = input_schema or load_schema(DEFAULT_INPUT_SCHEMA_PATH)
    payloads = []
    seen: set[str] = set()
    for record in queue_records:
        payload = build_blinded_payload(record)
        group_id = payload["generation_group_id"]
        if group_id in seen:
            raise SemanticReviewError(f"Duplicate generation_group_id: {group_id}")
        seen.add(group_id)
        validate_instance(payload, schema, label=group_id)
        payloads.append(payload)
    if expected_group_count is not None and len(payloads) != expected_group_count:
        raise SemanticReviewError(
            f"Expected {expected_group_count:,} groups, observed {len(payloads):,}"
        )
    return payloads


def render_reviewer_prompt(prompt: str, payload: Mapping[str, Any]) -> str:
    return (
        prompt.rstrip()
        + "\n\n--- BEGIN BLINDED REVIEWER PAYLOAD ---\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + "\n--- END BLINDED REVIEWER PAYLOAD ---\n"
    )


def input_payload_sha256(payload: Mapping[str, Any]) -> str:
    return sha256_text(canonical_json(payload))


def _percentile(sorted_values: Sequence[int], percentile: float) -> float:
    if not sorted_values:
        raise SemanticReviewError("Cannot summarise an empty distribution")
    position = (len(sorted_values) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = position - lower
    return sorted_values[lower] + fraction * (
        sorted_values[upper] - sorted_values[lower]
    )


def distribution(values: Sequence[int]) -> dict[str, int | float]:
    ordered = sorted(values)
    return {
        "min": ordered[0],
        "p05": round(_percentile(ordered, 0.05), 3),
        "p25": round(_percentile(ordered, 0.25), 3),
        "median": round(statistics.median(ordered), 3),
        "mean": round(statistics.fmean(ordered), 3),
        "p75": round(_percentile(ordered, 0.75), 3),
        "p95": round(_percentile(ordered, 0.95), 3),
        "max": ordered[-1],
    }


def verify_preserved_audit_sample(
    queue_records: Sequence[Mapping[str, Any]],
    v3_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    manifest_ids = v3_manifest["pre_outcome_audit"][
        "generation_group_ids_in_sample_order"
    ]
    flagged = {
        record["generation_group_id"]: record["pre_outcome_audit_sample_rank"]
        for record in queue_records
        if record["pre_outcome_audit_sample"]
    }
    ranked_ids = [
        group_id for group_id, _ in sorted(flagged.items(), key=lambda item: item[1])
    ]
    if ranked_ids != manifest_ids:
        raise SemanticReviewError("v3 pre-outcome audit membership or order changed")
    if len(ranked_ids) != EXPECTED_AUDIT_GROUP_COUNT:
        raise SemanticReviewError(
            f"Expected {EXPECTED_AUDIT_GROUP_COUNT} audit groups, observed {len(ranked_ids)}"
        )
    return {
        "sample_count": len(ranked_ids),
        "membership_and_order_unchanged": True,
        "ordered_group_ids_sha256": sha256_text(canonical_json(ranked_ids)),
        "model_visible": False,
    }


def dry_run_summary(
    payloads: Sequence[Mapping[str, Any]],
    *,
    prompt: str,
    prompt_path: Path,
    input_schema_path: Path,
    response_schema_path: Path,
    audit_proof: Mapping[str, Any],
) -> dict[str, Any]:
    rendered = [render_reviewer_prompt(prompt, payload) for payload in payloads]
    payload_characters = [len(canonical_json(payload)) for payload in payloads]
    prompt_characters = [len(item) for item in rendered]
    token_estimates = [math.ceil(value / 4) for value in prompt_characters]
    group_ids = [payload["generation_group_id"] for payload in payloads]
    leaked = sorted(
        {
            key
            for payload in payloads
            for key in find_forbidden_model_visible_keys(payload)
        }
    )
    return {
        "dry_run": True,
        "external_api_calls": 0,
        "writes_reviewer_judgements": False,
        "payload_count": len(payloads),
        "unique_generation_group_id_count": len(set(group_ids)),
        "all_generation_group_ids_unique": len(set(group_ids)) == len(group_ids),
        "prompt": {
            "version": PROMPT_VERSION,
            "path": str(prompt_path.resolve()),
            "sha256": sha256_path(prompt_path),
        },
        "schemas": {
            "input_path": str(input_schema_path.resolve()),
            "input_sha256": sha256_path(input_schema_path),
            "response_path": str(response_schema_path.resolve()),
            "response_sha256": sha256_path(response_schema_path),
        },
        "payload_character_distribution": distribution(payload_characters),
        "rendered_prompt_character_distribution": distribution(prompt_characters),
        "rendered_prompt_token_estimate_distribution": distribution(token_estimates),
        "token_estimate_method": TOKEN_ESTIMATE_METHOD,
        "blinding_proof": {
            "construction_method": "positive_allow_list",
            "forbidden_model_visible_keys": sorted(FORBIDDEN_MODEL_VISIBLE_KEYS),
            "observed_forbidden_keys": leaked,
            "heuristic_or_ranking_fields_absent": not leaked,
        },
        "pre_outcome_audit": dict(audit_proof),
    }


def validate_review_response(
    response: Mapping[str, Any],
    payload: Mapping[str, Any],
    response_schema: Mapping[str, Any],
) -> list[str]:
    errors = validation_errors(response, response_schema)
    if response.get("generation_group_id") != payload.get("generation_group_id"):
        errors.append("generation_group_id does not match blinded input payload")
    selected = response.get("selected_representative_occurrence_id")
    occurrence_ids = {
        item["occurrence_id"] for item in payload.get("contextualisations", [])
    }
    if selected is not None and selected not in occurrence_ids:
        errors.append(
            "selected_representative_occurrence_id is not a member of this source group"
        )
    if response.get("likely_transformation_duplicate_of") is not None:
        errors.append(
            "likely_transformation_duplicate_of must remain null during initial review"
        )
    return sorted(set(errors))
