"""Independent, strongly blinded AIRisk JMCUP final-validation preparation."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .core import (
    REPO_ROOT,
    SemanticReviewError,
    canonical_json,
    load_schema,
    read_json,
    read_jsonl,
    sha256_path,
    sha256_text,
    utc_now,
    validate_instance,
    validation_errors,
    write_jsonl,
)
from .transformation import (
    DEFAULT_ELIGIBILITY_SCHEMA_PATH,
    DEFAULT_INPUT_SCHEMA_PATH as DEFAULT_TRANSFORMATION_AUTHOR_INPUT_SCHEMA_PATH,
    DEFAULT_LEXICAL_DIAGNOSTICS_PATH,
    DEFAULT_PROMPT_PATH as DEFAULT_TRANSFORMATION_PROMPT_PATH,
    DEFAULT_RESOLVED_SCHEMA_PATH,
    DEFAULT_RESPONSE_SCHEMA_PATH as DEFAULT_TRANSFORMATION_AUTHOR_RESPONSE_SCHEMA_PATH,
    DEFAULT_SOURCE_GROUP_SCHEMA_PATH,
    DEFAULT_TRANSFORMATION_SCHEMA_PATH,
    validate_canonical_transformation_record,
)


FINAL_VALIDATION_PROTOCOL_VERSION = "airisk_jmcup_final_validation_protocol_v1"
FINAL_VALIDATOR_PROMPT_VERSION = "airisk_jmcup_final_validator_v1"
FINAL_VALIDATOR_INPUT_SCHEMA_VERSION = "airisk_jmcup_final_validator_input_v1"
FINAL_VALIDATOR_RESPONSE_SCHEMA_VERSION = "airisk_jmcup_final_validator_response_v1"
FINAL_VALIDATION_RECORD_SCHEMA_VERSION = "airisk_jmcup_final_validation_record_v2"
FINAL_VALIDATION_PREPARATION_MANIFEST_VERSION = (
    "airisk_jmcup_final_validation_preparation_manifest_v1"
)
FINAL_VALIDATION_PRIVATE_PROVENANCE_VERSION = (
    "airisk_jmcup_final_validation_private_provenance_v1"
)
ACTION_ORDER_DOMAIN = "airisk_jmcup_final_validation_action_order_v1"
VARIANT_ORDER_DOMAIN = "airisk_jmcup_final_validation_variant_order_v1"
SEMANTIC_CHECK_KEYS = tuple(f"v{index}" for index in range(1, 15))

DEFAULT_PROTOCOL_PATH = REPO_ROOT / "docs" / f"{FINAL_VALIDATION_PROTOCOL_VERSION}.md"
DEFAULT_PROMPT_PATH = REPO_ROOT / "prompts" / f"{FINAL_VALIDATOR_PROMPT_VERSION}.txt"
DEFAULT_INPUT_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{FINAL_VALIDATOR_INPUT_SCHEMA_VERSION}.schema.json"
)
DEFAULT_RESPONSE_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{FINAL_VALIDATOR_RESPONSE_SCHEMA_VERSION}.schema.json"
)
DEFAULT_FINAL_RECORD_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{FINAL_VALIDATION_RECORD_SCHEMA_VERSION}.schema.json"
)
DEFAULT_TRANSFORMATION_RUN_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / "airisk_jmcup_transformation_run_record_v1.schema.json"
)
DEFAULT_TRANSFORMATION_RAW_SCHEMA_PATH = (
    REPO_ROOT
    / "schemas"
    / "airisk_jmcup_transformation_raw_provider_output_v1.schema.json"
)

FORBIDDEN_VALIDATOR_VISIBLE_KEYS = frozenset(
    {
        "transformation_id", "generation_group_id", "occurrence_id", "pair_index",
        "dilemma_id", "dilemma_sha256", "bounded_action", "broader_action",
        "pressure_text", "evidence_text", "baseline_blocker", "pressure_type",
        "evidence_type", "evidence_strength", "matching_declarations",
        "matching_assessment", "source_fidelity_notes", "rewrite_notes",
        "surface_form_diagnostics", "lexical_warnings", "provider",
        "requested_model", "actual_routed_provider", "reviewer_a", "reviewer_b",
        "reviewer_run_id", "adjudicator_run_id", "qc_evidence", "classification",
        "heuristics", "target_dataset_n", "evaluated_model", "evaluation_result",
    }
)


def _indexed(records: Sequence[Mapping[str, Any]], *, key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for source in records:
        record = deepcopy(dict(source))
        value = record.get(key)
        if not isinstance(value, str) or not value:
            raise SemanticReviewError(f"{label} record lacks {key}")
        if value in result:
            raise SemanticReviewError(f"Duplicate {label} record for {value}")
        result[value] = record
    return result


def find_forbidden_validator_visible_keys(value: Any) -> list[str]:
    found: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                rendered = str(key)
                if (
                    rendered in FORBIDDEN_VALIDATOR_VISIBLE_KEYS
                    or rendered.startswith("heuristic_")
                    or rendered.startswith("pre_outcome_audit_")
                    or rendered.startswith("h1_")
                    or rendered.startswith("h2_")
                    or rendered.startswith("h3_")
                    or rendered.startswith("h4_")
                    or rendered.startswith("h5_")
                    or rendered.startswith("h6_")
                    or rendered.startswith("h7_")
                ):
                    found.add(rendered)
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return sorted(found)


def _hmac_digest(*, seed: str, domain: str, transformation_sha256: str) -> str:
    message = f"{domain}\0{transformation_sha256}".encode("utf-8")
    return hmac.new(seed.encode("utf-8"), message, hashlib.sha256).hexdigest()


def derive_private_mapping(
    *, seed: str, transformation_id: str, transformation_sha256: str
) -> dict[str, Any]:
    """Derive independently domain-separated action and variant mappings."""

    if not isinstance(seed, str) or not seed:
        raise SemanticReviewError("A non-empty explicit private blinding seed is required")
    action_digest = _hmac_digest(
        seed=seed,
        domain=ACTION_ORDER_DOMAIN,
        transformation_sha256=transformation_sha256,
    )
    variant_digest = _hmac_digest(
        seed=seed,
        domain=VARIANT_ORDER_DOMAIN,
        transformation_sha256=transformation_sha256,
    )
    action_swapped = int(action_digest[-1], 16) % 2 == 1
    variant_swapped = int(variant_digest[-1], 16) % 2 == 1
    mapping = {
        "transformation_id": transformation_id,
        "transformation_record_sha256": transformation_sha256,
        "action_order_hmac_sha256": action_digest,
        "variant_order_hmac_sha256": variant_digest,
        "bounded_candidate_id": (
            "candidate_action_2" if action_swapped else "candidate_action_1"
        ),
        "broader_candidate_id": (
            "candidate_action_1" if action_swapped else "candidate_action_2"
        ),
        "unsupported_pressure_variant_id": (
            "variant_2" if variant_swapped else "variant_1"
        ),
        "genuine_evidence_variant_id": (
            "variant_1" if variant_swapped else "variant_2"
        ),
    }
    mapping["private_mapping_sha256"] = sha256_text(canonical_json(mapping))
    return mapping


def _refresh_private_mapping_sha256(mapping: dict[str, Any]) -> None:
    value = {key: item for key, item in mapping.items() if key != "private_mapping_sha256"}
    mapping["private_mapping_sha256"] = sha256_text(canonical_json(value))


def _source_occurrence_for_validator(item: Mapping[str, Any]) -> dict[str, Any]:
    actions = item["actions"]
    return {
        "dilemma_text": item["dilemma"],
        "source_actions": [
            {
                "action_text": actions[key]["action"],
                "action_body": actions[key]["action_body"],
                "context_text": actions[key]["context"],
            }
            for key in ("action_1", "action_2")
        ],
        "context_texts": list(item["contexts"]),
    }


def build_blinded_validator_payload(
    transformation: Mapping[str, Any],
    source_group: Mapping[str, Any],
    mapping: Mapping[str, Any],
    *,
    input_schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    transformation_id = transformation["transformation_id"]
    if mapping.get("transformation_id") != transformation_id:
        raise SemanticReviewError("Private mapping transformation ID differs")
    source_ids = set(transformation["source_occurrence_ids_used"])
    members = {
        item["occurrence_id"]: item for item in source_group["contextualisations"]
    }
    missing = sorted(source_ids - set(members))
    if missing:
        raise SemanticReviewError(f"Selected source occurrences are not members: {missing}")
    selected = [members[item] for item in sorted(source_ids)]
    action_values = {
        mapping["bounded_candidate_id"]: transformation["bounded_action"],
        mapping["broader_candidate_id"]: transformation["broader_action"],
    }
    variant_values = {
        mapping["unsupported_pressure_variant_id"]: transformation["pressure_text"],
        mapping["genuine_evidence_variant_id"]: transformation["evidence_text"],
    }
    payload = {
        "schema_version": FINAL_VALIDATOR_INPUT_SCHEMA_VERSION,
        "selected_source_material": [
            _source_occurrence_for_validator(item) for item in selected
        ],
        "transformed_case": {
            "shared_scenario_text": transformation["shared_scenario_text"],
            "candidate_actions": {
                key: action_values[key]
                for key in ("candidate_action_1", "candidate_action_2")
            },
            "baseline": {"condition_addition_text": None},
            "variants": {
                key: {"condition_addition_text": variant_values[key]}
                for key in ("variant_1", "variant_2")
            },
        },
    }
    leaked = find_forbidden_validator_visible_keys(payload)
    if leaked:
        raise SemanticReviewError(f"Forbidden validator-visible metadata: {leaked}")
    validate_instance(
        payload,
        input_schema or load_schema(DEFAULT_INPUT_SCHEMA_PATH),
        label="blinded final-validator input",
    )
    return payload


def render_final_validator_prompt(prompt: str, payload: Mapping[str, Any]) -> str:
    marker = "{{BLINDED_VALIDATION_PAYLOAD_JSON}}"
    if prompt.count(marker) != 1:
        raise SemanticReviewError("Frozen final-validator prompt must contain one payload marker")
    return prompt.replace(marker, json.dumps(payload, indent=2, ensure_ascii=False))


def compare_role_identifications(
    response: Mapping[str, Any], mapping: Mapping[str, Any]
) -> dict[str, Any]:
    expected = {
        "baseline_preferred_action": mapping["bounded_candidate_id"],
        "unsupported_pressure_variant": mapping["unsupported_pressure_variant_id"],
        "preferred_action_after_unsupported_pressure": mapping["bounded_candidate_id"],
        "genuine_evidence_variant": mapping["genuine_evidence_variant_id"],
        "preferred_action_after_genuine_evidence": mapping["broader_candidate_id"],
    }
    comparisons: dict[str, Any] = {}
    for field, expected_value in expected.items():
        reported = response[field]
        comparisons[field] = {
            "reported": reported,
            "expected_private_mapping_digest": sha256_text(
                canonical_json({"field": field, "expected": expected_value})
            ),
            "matches": reported == expected_value,
        }
    comparisons["all_match"] = all(item["matches"] for item in comparisons.values())
    return comparisons


def resolve_final_disposition(
    response: Mapping[str, Any], mapping_comparison: Mapping[str, Any]
) -> tuple[str, list[str]]:
    judgements = {
        key: response["semantic_checks"][key]["judgement"]
        for key in SEMANTIC_CHECK_KEYS
    }
    failed = [key for key, value in judgements.items() if value == "fail"]
    if failed:
        return "reject", [f"mandatory_semantic_fail:{key}" for key in failed]
    reasons: list[str] = []
    uncertain = [key for key, value in judgements.items() if value == "uncertain"]
    reasons.extend(f"mandatory_semantic_uncertain:{key}" for key in uncertain)
    if not mapping_comparison["all_match"]:
        reasons.extend(
            f"hidden_role_identification_mismatch:{field}"
            for field, item in mapping_comparison.items()
            if field != "all_match" and not item["matches"]
        )
    nuisance = response["nuisance_cue_assessment"][
        "materially_capable_of_substituting_for_semantic_reasoning"
    ]
    if nuisance in {"yes", "uncertain"}:
        reasons.append(f"unresolved_nuisance_cue_materiality:{nuisance}")
    if response["unresolved_semantic_concerns"]:
        reasons.append("validator_reported_unresolved_semantic_concerns")
    if response["overall_recommendation"] != "accept":
        reasons.append("overall_recommendation_conflicts_with_all_pass_resolution")
    if reasons:
        return "human_review", reasons
    return "accept", ["all_structural_role_and_v1_v14_checks_pass"]


def _diagnostics(transformation: Mapping[str, Any], lexical_spec_sha256: str) -> dict[str, Any]:
    return {
        "lexical_diagnostics_spec_sha256": lexical_spec_sha256,
        "surface_form_diagnostics": deepcopy(
            transformation.get("surface_form_diagnostics", {})
        ),
        "lexical_warnings": deepcopy(transformation.get("lexical_warnings", {})),
        "warning_only": True,
        "semantic_rejection_gate": False,
    }


def build_local_structural_rejection(
    transformation: Mapping[str, Any],
    *,
    transformation_sha256: str,
    author_type: str,
    reason_codes: Sequence[str],
    reasons: Sequence[str],
    preparation_manifest_sha256: str,
    private_provenance_sha256: str,
    transformation_schema_sha256: str,
    lexical_spec_sha256: str,
    created_at_utc: str,
    final_validation_protocol_sha256: str | None = None,
    final_validator_prompt_sha256: str | None = None,
    final_validator_input_schema_sha256: str | None = None,
    final_validator_response_schema_sha256: str | None = None,
) -> dict[str, Any]:
    transformation_id = transformation["transformation_id"]
    provenance = transformation.get("provenance", {})
    return {
        "schema_version": FINAL_VALIDATION_RECORD_SCHEMA_VERSION,
        "validation_id": f"airisk_jmcup_final_validation_local_{transformation_sha256[:24]}",
        "transformation_id": transformation_id,
        "transformation_record_sha256": transformation_sha256,
        "validation_route": "local_structural_reject",
        "author_type": author_type,
        "local_structural_validation": {
            "status": "fail",
            "reason_codes": sorted(set(reason_codes)),
            "reasons": list(reasons),
            "semantic_truth_checked": False,
        },
        "independent_semantic_validation": None,
        "deterministic_diagnostics": _diagnostics(
            transformation, lexical_spec_sha256
        ),
        "final_disposition": "reject",
        "disposition_reasons": ["local_structural_validation_failed"],
        "provenance": {
            "preparation_manifest_sha256": preparation_manifest_sha256,
            "private_provenance_sha256": private_provenance_sha256,
            "source_group_input_payload_sha256": provenance.get(
                "source_group_input_payload_sha256",
                transformation.get("source_group_input_payload_sha256"),
            ),
            "resolved_source_review_sha256": transformation.get(
                "resolved_source_review_sha256"
            ),
            "source_eligibility_sha256": transformation.get(
                "source_eligibility_sha256"
            ),
            "transformation_schema_sha256": transformation_schema_sha256,
            "final_validation_protocol_sha256": (
                final_validation_protocol_sha256 or sha256_path(DEFAULT_PROTOCOL_PATH)
            ),
            "final_validator_prompt_sha256": (
                final_validator_prompt_sha256 or sha256_path(DEFAULT_PROMPT_PATH)
            ),
            "final_validator_input_schema_sha256": (
                final_validator_input_schema_sha256
                or sha256_path(DEFAULT_INPUT_SCHEMA_PATH)
            ),
            "final_validator_response_schema_sha256": (
                final_validator_response_schema_sha256
                or sha256_path(DEFAULT_RESPONSE_SCHEMA_PATH)
            ),
            "final_validation_run_id": None,
            "provider": None,
            "requested_model": None,
            "created_at_utc": created_at_utc,
        },
        "semantic_truth_deterministically_established": False,
    }


def build_semantic_final_record(
    transformation: Mapping[str, Any],
    response: Mapping[str, Any],
    mapping: Mapping[str, Any],
    *,
    final_validation_run_id: str,
    provider: str,
    requested_model: str,
    preparation_manifest_sha256: str,
    private_provenance_sha256: str,
    transformation_schema_sha256: str,
    lexical_spec_sha256: str,
    created_at_utc: str,
    final_validation_protocol_sha256: str | None = None,
    final_validator_prompt_sha256: str | None = None,
    final_validator_input_schema_sha256: str | None = None,
    final_validator_response_schema_sha256: str | None = None,
) -> dict[str, Any]:
    comparison = compare_role_identifications(response, mapping)
    disposition, reasons = resolve_final_disposition(response, comparison)
    transformation_sha = sha256_text(canonical_json(transformation))
    provenance = transformation["provenance"]
    author_type = provenance["transformation_author_type"]
    return {
        "schema_version": FINAL_VALIDATION_RECORD_SCHEMA_VERSION,
        "validation_id": (
            f"airisk_jmcup_final_validation_{final_validation_run_id}_"
            f"{transformation_sha[:16]}"
        ),
        "transformation_id": transformation["transformation_id"],
        "transformation_record_sha256": transformation_sha,
        "validation_route": "independent_semantic_validation",
        "author_type": author_type,
        "local_structural_validation": {
            "status": "pass", "reason_codes": [], "reasons": [],
            "semantic_truth_checked": False,
        },
        "independent_semantic_validation": {
            "validator_response": deepcopy(dict(response)),
            "validator_response_sha256": sha256_text(canonical_json(response)),
            "role_mapping_comparison": comparison,
            "overall_recommendation_agrees_with_disposition": (
                response["overall_recommendation"] == disposition
            ),
        },
        "deterministic_diagnostics": _diagnostics(
            transformation, lexical_spec_sha256
        ),
        "final_disposition": disposition,
        "disposition_reasons": reasons,
        "provenance": {
            "preparation_manifest_sha256": preparation_manifest_sha256,
            "private_provenance_sha256": private_provenance_sha256,
            "source_group_input_payload_sha256": transformation[
                "source_group_input_payload_sha256"
            ],
            "resolved_source_review_sha256": transformation[
                "resolved_source_review_sha256"
            ],
            "source_eligibility_sha256": transformation["source_eligibility_sha256"],
            "transformation_schema_sha256": transformation_schema_sha256,
            "final_validation_protocol_sha256": (
                final_validation_protocol_sha256 or sha256_path(DEFAULT_PROTOCOL_PATH)
            ),
            "final_validator_prompt_sha256": (
                final_validator_prompt_sha256 or sha256_path(DEFAULT_PROMPT_PATH)
            ),
            "final_validator_input_schema_sha256": (
                final_validator_input_schema_sha256
                or sha256_path(DEFAULT_INPUT_SCHEMA_PATH)
            ),
            "final_validator_response_schema_sha256": (
                final_validator_response_schema_sha256
                or sha256_path(DEFAULT_RESPONSE_SCHEMA_PATH)
            ),
            "final_validation_run_id": final_validation_run_id,
            "provider": provider,
            "requested_model": requested_model,
            "created_at_utc": created_at_utc,
        },
        "semantic_truth_deterministically_established": False,
    }


def _read_optional_records(path: Path | None) -> list[dict[str, Any]]:
    return read_jsonl(path) if path is not None else []


def _author_chain_errors(
    transformation: Mapping[str, Any],
    *,
    author_input_by_group: Mapping[str, Mapping[str, Any]],
    author_run_by_group: Mapping[str, Mapping[str, Any]],
    author_raw_by_sha: Mapping[str, Mapping[str, Any]],
    author_run_manifest: Mapping[str, Any] | None,
    author_preparation_manifest: Mapping[str, Any] | None,
    provider_chain_supplied: bool,
) -> list[str]:
    errors: list[str] = []
    provenance = transformation.get("provenance", {})
    author_type = provenance.get("transformation_author_type")
    if author_type not in {"model", "human", "mixed"}:
        return ["declared author type is not model, human or mixed"]
    group_id = transformation.get("generation_group_id")
    provider_provenance_fields = (
        "provider", "requested_model", "transformation_run_id",
        "transformation_prompt_sha256", "author_response_schema_sha256",
    )
    has_canonical_provider_provenance = any(
        provenance.get(field) is not None for field in provider_provenance_fields
    )
    has_author_input = group_id in author_input_by_group
    has_author_run = group_id in author_run_by_group
    group_has_any_provider_record = has_author_input or has_author_run
    if author_type == "human" and (
        group_has_any_provider_record or has_canonical_provider_provenance
    ):
        return ["human authorship conflicts with supplied provider authoring chain"]
    if author_type == "model" and not has_canonical_provider_provenance:
        return ["model authorship lacks canonical provider provenance"]
    if author_type == "model" and not (
        provider_chain_supplied and has_author_input and has_author_run
    ):
        return ["model authorship requires complete provider authoring chain"]
    if author_type == "mixed" and not group_has_any_provider_record:
        return (
            ["mixed authorship has canonical provider provenance but no provider chain"]
            if has_canonical_provider_provenance
            else []
        )
    if author_type == "mixed" and not has_canonical_provider_provenance:
        return ["mixed authorship has provider records but no canonical provider provenance"]
    if author_type == "mixed" and not (
        provider_chain_supplied and has_author_input and has_author_run
    ):
        return ["mixed authorship has a partial provider authoring chain"]
    if author_type == "human":
        return []
    if not provider_chain_supplied:
        return errors

    author_input = author_input_by_group.get(group_id)
    run_record = author_run_by_group.get(group_id)
    if author_input is None or run_record is None:
        return ["complete author input and completed run record are required"]
    if author_run_manifest is None or author_preparation_manifest is None:
        return ["complete author run and preparation manifests are required"]
    if sha256_text(canonical_json(author_input)) != transformation.get(
        "author_input_payload_sha256"
    ):
        errors.append("author input payload hash differs")
    if run_record.get("status") != "completed":
        errors.append("author run record is not completed")
    if run_record.get("canonical_transformation_record_sha256") != sha256_text(
        canonical_json(transformation)
    ):
        errors.append("author run canonical transformation hash differs")
    if run_record.get("canonical_transformation_record") != transformation:
        errors.append("author run canonical transformation differs")
    if run_record.get("input_payload_sha256") != transformation.get(
        "author_input_payload_sha256"
    ):
        errors.append("author run input hash differs")
    attempts = run_record.get("retry_information", {}).get("attempts", [])
    if not attempts:
        errors.append("author run has no attempt provenance")
    for attempt in attempts:
        raw_sha = attempt.get("raw_provider_output_sha256")
        if raw_sha not in author_raw_by_sha:
            errors.append(f"author raw output missing for attempt hash {raw_sha}")
    if author_run_manifest.get("transformation_run_id") != provenance.get(
        "transformation_run_id"
    ):
        errors.append("author run manifest identity differs")
    if author_run_manifest.get("input_payloads_sha256") != author_preparation_manifest.get(
        "payloads_sha256"
    ):
        errors.append("author run/preparation payload hashes differ")
    return errors


def prepare_final_validation(
    *,
    transformations_path: Path,
    source_payloads_path: Path,
    resolved_reviews_path: Path,
    eligibility_path: Path,
    source_resolution_manifest_path: Path,
    output_dir: Path,
    seed: str,
    transformation_schema_path: Path = DEFAULT_TRANSFORMATION_SCHEMA_PATH,
    source_group_schema_path: Path = DEFAULT_SOURCE_GROUP_SCHEMA_PATH,
    input_schema_path: Path = DEFAULT_INPUT_SCHEMA_PATH,
    final_record_schema_path: Path = DEFAULT_FINAL_RECORD_SCHEMA_PATH,
    lexical_spec_path: Path = DEFAULT_LEXICAL_DIAGNOSTICS_PATH,
    author_inputs_path: Path | None = None,
    author_run_records_path: Path | None = None,
    author_raw_outputs_path: Path | None = None,
    author_run_manifest_path: Path | None = None,
    author_preparation_manifest_path: Path | None = None,
    group_ids: Sequence[str] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    if not isinstance(seed, str) or not seed:
        raise SemanticReviewError("A non-empty explicit private blinding seed is required")
    transformations = read_jsonl(transformations_path)
    if group_ids is not None:
        if len(group_ids) != len(set(group_ids)):
            raise SemanticReviewError("Duplicate requested transformation group ID")
        wanted = set(group_ids)
        transformations = [
            item for item in transformations if item.get("generation_group_id") in wanted
        ]
        if {item.get("generation_group_id") for item in transformations} != wanted:
            raise SemanticReviewError("Requested transformation group IDs are incomplete")
    by_transformation = _indexed(
        transformations, key="transformation_id", label="transformation"
    )
    source_records = read_jsonl(source_payloads_path)
    resolved_records = read_jsonl(resolved_reviews_path)
    eligibility_records = read_jsonl(eligibility_path)
    source_by_group = _indexed(source_records, key="generation_group_id", label="source")
    resolved_by_group = _indexed(resolved_records, key="generation_group_id", label="resolved review")
    eligibility_by_group = _indexed(eligibility_records, key="generation_group_id", label="eligibility")
    resolution_manifest = read_json(source_resolution_manifest_path)
    if resolution_manifest.get("schema_version") != "airisk_jmcup_source_resolution_manifest_v2":
        raise SemanticReviewError("Final validation requires source-resolution manifest v2")
    required_source_hashes = {
        "blinded_source_payloads_sha256": source_payloads_path,
        "resolved_source_reviews_sha256": resolved_reviews_path,
        "source_eligibility_sha256": eligibility_path,
    }
    for field, path in required_source_hashes.items():
        if resolution_manifest.get(field) != sha256_path(path):
            raise SemanticReviewError(f"Source-resolution manifest differs for {field}")

    transformation_schema = load_schema(transformation_schema_path)
    source_schema = load_schema(source_group_schema_path)
    resolved_schema = load_schema(DEFAULT_RESOLVED_SCHEMA_PATH)
    eligibility_schema = load_schema(DEFAULT_ELIGIBILITY_SCHEMA_PATH)
    input_schema = load_schema(input_schema_path)
    final_schema = load_schema(final_record_schema_path)
    lexical_spec_sha = sha256_path(lexical_spec_path)
    source_schema_hash_checks = {
        "source_group_input_schema_sha256": sha256_path(source_group_schema_path),
        "resolved_schema_sha256": sha256_path(DEFAULT_RESOLVED_SCHEMA_PATH),
        "eligibility_schema_sha256": sha256_path(DEFAULT_ELIGIBILITY_SCHEMA_PATH),
    }
    for field, expected_hash in source_schema_hash_checks.items():
        if resolution_manifest.get(field) != expected_hash:
            raise SemanticReviewError(
                f"Source-resolution manifest schema hash differs for {field}"
            )
    for record in resolved_records:
        validate_instance(record, resolved_schema, label="resolved source review provenance")
    for record in eligibility_records:
        validate_instance(record, eligibility_schema, label="source eligibility provenance")

    provider_paths = (
        author_inputs_path,
        author_run_records_path,
        author_raw_outputs_path,
        author_run_manifest_path,
        author_preparation_manifest_path,
    )
    any_provider = any(path is not None for path in provider_paths)
    all_provider = all(path is not None for path in provider_paths)
    if any_provider and not all_provider:
        raise SemanticReviewError("Provider authoring provenance must be supplied completely")
    author_inputs = _read_optional_records(author_inputs_path)
    author_runs = _read_optional_records(author_run_records_path)
    author_raw = _read_optional_records(author_raw_outputs_path)
    author_input_by_group = _indexed(
        author_inputs, key="generation_group_id", label="author input"
    ) if author_inputs else {}
    author_run_by_group: dict[str, dict[str, Any]] = {}
    for record in author_runs:
        if record.get("status") != "completed":
            continue
        group_id = record.get("generation_group_id")
        if not isinstance(group_id, str) or not group_id:
            raise SemanticReviewError("Completed author run record lacks generation_group_id")
        if group_id in author_run_by_group:
            raise SemanticReviewError(
                f"Duplicate completed author run record for {group_id}"
            )
        author_run_by_group[group_id] = record
    author_raw_by_sha: dict[str, dict[str, Any]] = {}
    for raw in author_raw:
        digest = sha256_text(canonical_json(raw))
        if digest in author_raw_by_sha:
            raise SemanticReviewError(f"Duplicate authoritative author raw output {digest}")
        author_raw_by_sha[digest] = raw
    author_run_manifest = read_json(author_run_manifest_path) if author_run_manifest_path else None
    author_preparation_manifest = read_json(author_preparation_manifest_path) if author_preparation_manifest_path else None
    if all_provider:
        author_input_schema = load_schema(DEFAULT_TRANSFORMATION_AUTHOR_INPUT_SCHEMA_PATH)
        author_response_schema = load_schema(
            DEFAULT_TRANSFORMATION_AUTHOR_RESPONSE_SCHEMA_PATH
        )
        author_run_schema = load_schema(DEFAULT_TRANSFORMATION_RUN_SCHEMA_PATH)
        author_raw_schema = load_schema(DEFAULT_TRANSFORMATION_RAW_SCHEMA_PATH)
        for record in author_inputs:
            validate_instance(record, author_input_schema, label="author input provenance")
        for record in author_runs:
            validate_instance(record, author_run_schema, label="author run provenance")
            if record.get("status") == "completed":
                validate_instance(
                    record.get("parsed_author_response"),
                    author_response_schema,
                    label="completed author response provenance",
                )
        for record in author_raw:
            validate_instance(record, author_raw_schema, label="author raw provenance")
        manifest_hash_checks = {
            "author_input_schema_sha256": sha256_path(
                DEFAULT_TRANSFORMATION_AUTHOR_INPUT_SCHEMA_PATH
            ),
            "run_record_schema_sha256": sha256_path(
                DEFAULT_TRANSFORMATION_RUN_SCHEMA_PATH
            ),
            "raw_output_schema_sha256": sha256_path(
                DEFAULT_TRANSFORMATION_RAW_SCHEMA_PATH
            ),
            "canonical_transformation_schema_sha256": sha256_path(
                transformation_schema_path
            ),
            "author_response_schema_sha256": sha256_path(
                DEFAULT_TRANSFORMATION_AUTHOR_RESPONSE_SCHEMA_PATH
            ),
            "prompt_sha256": sha256_path(DEFAULT_TRANSFORMATION_PROMPT_PATH),
            "lexical_diagnostics_spec_sha256": sha256_path(lexical_spec_path),
        }
        for field, expected_hash in manifest_hash_checks.items():
            if author_run_manifest.get(field) != expected_hash:
                raise SemanticReviewError(
                    f"Author run manifest hash differs for {field}"
                )
        if author_preparation_manifest.get(
            "source_resolution_manifest_sha256"
        ) != sha256_path(source_resolution_manifest_path):
            raise SemanticReviewError(
                "Author preparation source-resolution manifest hash differs"
            )
        author_chain_hash_checks = {
            "payloads_sha256": sha256_path(author_inputs_path),
            "resolved_reviews_sha256": sha256_path(resolved_reviews_path),
            "eligibility_sha256": sha256_path(eligibility_path),
            "source_payloads_sha256": sha256_path(source_payloads_path),
        }
        for field, expected_hash in author_chain_hash_checks.items():
            if author_preparation_manifest.get(field) != expected_hash:
                raise SemanticReviewError(
                    f"Author preparation manifest hash differs for {field}"
                )
        if author_run_manifest.get("input_payloads_sha256") != sha256_path(
            author_inputs_path
        ):
            raise SemanticReviewError("Author run input-payload file hash differs")
        if author_run_manifest.get("preparation_manifest_sha256") != sha256_path(
            author_preparation_manifest_path
        ):
            raise SemanticReviewError("Author run preparation-manifest hash differs")

    prepared: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    structural_results: list[dict[str, Any]] = []
    rejected_sources: list[tuple[dict[str, Any], list[str], list[str]]] = []
    for transformation_id in sorted(by_transformation):
        transformation = by_transformation[transformation_id]
        transformation_sha = sha256_text(canonical_json(transformation))
        group_id = transformation.get("generation_group_id")
        author_type = transformation.get("provenance", {}).get(
            "transformation_author_type", "unknown"
        )
        reason_pairs: list[tuple[str, str]] = []
        for error in validation_errors(transformation, transformation_schema):
            reason_pairs.append(("transformation_schema_invalid", error))
        if not reason_pairs:
            for error in validate_canonical_transformation_record(
                transformation, transformation_schema
            ):
                reason_pairs.append(("transformation_structural_invariant_failed", error))
        source = source_by_group.get(group_id)
        resolved = resolved_by_group.get(group_id)
        eligibility = eligibility_by_group.get(group_id)
        if source is None or resolved is None or eligibility is None:
            reason_pairs.append(("source_chain_member_missing", "source, resolved review or eligibility record is missing"))
        else:
            for error in validation_errors(source, source_schema):
                reason_pairs.append(("source_payload_schema_invalid", error))
            if transformation.get("source_group_input_payload_sha256") != sha256_text(canonical_json(source)):
                reason_pairs.append(("source_payload_hash_mismatch", "canonical transformation source payload hash differs"))
            if transformation.get("resolved_source_review_sha256") != sha256_text(canonical_json(resolved)):
                reason_pairs.append(("resolved_review_hash_mismatch", "canonical transformation resolved-review hash differs"))
            if transformation.get("source_eligibility_sha256") != sha256_text(canonical_json(eligibility)):
                reason_pairs.append(("eligibility_hash_mismatch", "canonical transformation eligibility hash differs"))
            member_ids = {item["occurrence_id"] for item in source.get("contextualisations", [])}
            if not set(transformation.get("source_occurrence_ids_used", [])) <= member_ids:
                reason_pairs.append(("source_occurrence_membership_invalid", "selected source occurrence is not a group member"))
        for error in _author_chain_errors(
            transformation,
            author_input_by_group=author_input_by_group,
            author_run_by_group=author_run_by_group,
            author_raw_by_sha=author_raw_by_sha,
            author_run_manifest=author_run_manifest,
            author_preparation_manifest=author_preparation_manifest,
            provider_chain_supplied=all_provider,
        ):
            reason_pairs.append(("author_provenance_invalid", error))

        if reason_pairs:
            codes = [code for code, _ in reason_pairs]
            reasons = [reason for _, reason in reason_pairs]
            structural_results.append({
                "transformation_id": transformation_id,
                "transformation_record_sha256": transformation_sha,
                "validation_route": "local_structural_reject",
                "status": "fail",
                "reason_codes": sorted(set(codes)),
                "reasons": reasons,
                "semantic_truth_checked": False,
            })
            rejected_sources.append((transformation, codes, reasons))
            continue

        mapping = derive_private_mapping(
            seed=seed,
            transformation_id=transformation_id,
            transformation_sha256=transformation_sha,
        )
        payload = build_blinded_validator_payload(
            transformation, source, mapping, input_schema=input_schema
        )
        mapping["payload_ordinal"] = len(prepared)
        mapping["input_payload_sha256"] = sha256_text(canonical_json(payload))
        mapping["source_group_input_payload_sha256"] = transformation[
            "source_group_input_payload_sha256"
        ]
        mapping["selected_source_occurrence_ids"] = sorted(
            transformation["source_occurrence_ids_used"]
        )
        _refresh_private_mapping_sha256(mapping)
        prepared.append(payload)
        mappings.append(mapping)
        structural_results.append({
            "transformation_id": transformation_id,
            "transformation_record_sha256": transformation_sha,
            "validation_route": "independent_semantic_validation",
            "status": "pass", "reason_codes": [], "reasons": [],
            "semantic_truth_checked": False,
        })

    output_dir = output_dir.resolve()
    paths = {
        "payloads": output_dir / "blinded_final_validation_inputs.jsonl",
        "private": output_dir / "final_validation_private_provenance.json",
        "structural": output_dir / "prevalidation_structural_results.jsonl",
        "local_rejections": output_dir / "structural_rejection_records.jsonl",
        "manifest": output_dir / "final_validation_preparation_manifest.json",
    }
    existing = [path for path in paths.values() if path.exists()]
    if existing and not overwrite:
        raise SemanticReviewError(
            "Refusing to overwrite final-validation preparation: "
            + ", ".join(str(path) for path in existing)
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(paths["payloads"], prepared)
    write_jsonl(paths["structural"], structural_results)
    private_provenance = {
        "schema_version": FINAL_VALIDATION_PRIVATE_PROVENANCE_VERSION,
        "created_at_utc": utc_now(),
        "classification": "PRIVATE_GENERATED_NON_PUBLIC_NON_COMMITTABLE",
        "public_distribution_allowed": False,
        "commit_allowed": False,
        "generated_sensitive_provenance": True,
        "deterministic_seed": seed,
        "deterministic_seed_sha256": sha256_text(seed),
        "action_order_algorithm": f"HMAC-SHA-256:{ACTION_ORDER_DOMAIN}",
        "variant_order_algorithm": f"HMAC-SHA-256:{VARIANT_ORDER_DOMAIN}",
        "mappings": mappings,
    }
    paths["private"].write_text(
        json.dumps(private_provenance, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    manifest = {
        "schema_version": FINAL_VALIDATION_PREPARATION_MANIFEST_VERSION,
        "created_at_utc": utc_now(),
        "transformations_path": str(transformations_path.resolve()),
        "transformations_sha256": sha256_path(transformations_path),
        "transformation_schema_sha256": sha256_path(transformation_schema_path),
        "source_payloads_sha256": sha256_path(source_payloads_path),
        "resolved_reviews_sha256": sha256_path(resolved_reviews_path),
        "eligibility_sha256": sha256_path(eligibility_path),
        "source_resolution_manifest_sha256": sha256_path(source_resolution_manifest_path),
        "final_validation_protocol_sha256": sha256_path(DEFAULT_PROTOCOL_PATH),
        "final_validator_prompt_sha256": sha256_path(DEFAULT_PROMPT_PATH),
        "final_validator_input_schema_sha256": sha256_path(input_schema_path),
        "final_validator_response_schema_sha256": sha256_path(DEFAULT_RESPONSE_SCHEMA_PATH),
        "final_validation_record_schema_sha256": sha256_path(final_record_schema_path),
        "lexical_diagnostics_spec_sha256": lexical_spec_sha,
        "deterministic_seed_sha256": sha256_text(seed),
        "literal_seed_publicly_recorded": False,
        "action_order_algorithm": f"HMAC-SHA-256:{ACTION_ORDER_DOMAIN}",
        "variant_order_algorithm": f"HMAC-SHA-256:{VARIANT_ORDER_DOMAIN}",
        "private_provenance_path": str(paths["private"]),
        "private_provenance_sha256": sha256_path(paths["private"]),
        "private_provenance_non_public_non_committable": True,
        "payloads_path": str(paths["payloads"]),
        "payloads_sha256": sha256_path(paths["payloads"]),
        "structural_results_path": str(paths["structural"]),
        "structural_results_sha256": sha256_path(paths["structural"]),
        "semantic_validation_count": len(prepared),
        "local_structural_rejection_count": len(rejected_sources),
        "selected_transformation_ids": sorted(by_transformation),
        "semantic_validation_transformation_ids": [
            item["transformation_id"] for item in mappings
        ],
        "author_provenance_mode": "declared_author_type_aware_fail_closed",
        "author_type_counts": dict(sorted(Counter(
            item.get("provenance", {}).get("transformation_author_type", "unknown")
            for item in by_transformation.values()
        ).items())),
        "author_provider_chain": (
            {
                "author_inputs_sha256": sha256_path(author_inputs_path),
                "author_run_records_sha256": sha256_path(author_run_records_path),
                "author_raw_outputs_sha256": sha256_path(author_raw_outputs_path),
                "author_run_manifest_sha256": sha256_path(author_run_manifest_path),
                "author_preparation_manifest_sha256": sha256_path(
                    author_preparation_manifest_path
                ),
            }
            if all_provider
            else None
        ),
        "external_api_calls": 0,
    }
    paths["manifest"].write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    preparation_sha = sha256_path(paths["manifest"])
    private_sha = sha256_path(paths["private"])
    local_records = [
        build_local_structural_rejection(
            transformation,
            transformation_sha256=sha256_text(canonical_json(transformation)),
            author_type=transformation.get("provenance", {}).get(
                "transformation_author_type", "unknown"
            ),
            reason_codes=codes,
            reasons=reasons,
            preparation_manifest_sha256=preparation_sha,
            private_provenance_sha256=private_sha,
            transformation_schema_sha256=sha256_path(transformation_schema_path),
            lexical_spec_sha256=lexical_spec_sha,
            created_at_utc=utc_now(),
            final_validation_protocol_sha256=sha256_path(DEFAULT_PROTOCOL_PATH),
            final_validator_prompt_sha256=sha256_path(DEFAULT_PROMPT_PATH),
            final_validator_input_schema_sha256=sha256_path(input_schema_path),
            final_validator_response_schema_sha256=sha256_path(
                DEFAULT_RESPONSE_SCHEMA_PATH
            ),
        )
        for transformation, codes, reasons in rejected_sources
    ]
    for record in local_records:
        validate_instance(record, final_schema, label="local structural rejection")
    write_jsonl(paths["local_rejections"], local_records)
    return {
        **manifest,
        "preparation_manifest_path": str(paths["manifest"]),
        "preparation_manifest_sha256": preparation_sha,
        "local_structural_rejections_path": str(paths["local_rejections"]),
        "local_structural_rejections_sha256": sha256_path(paths["local_rejections"]),
        "route_counts": dict(sorted(Counter(
            item["validation_route"] for item in structural_results
        ).items())),
    }
