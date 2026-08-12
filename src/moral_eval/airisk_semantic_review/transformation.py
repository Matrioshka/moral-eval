"""Outcome-independent preparation and deterministic AIRisk JMCUP transforms."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .core import (
    CRITERION_KEYS,
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


TRANSFORMATION_PROTOCOL_VERSION = "airisk_jmcup_transformation_authoring_protocol_v1"
TRANSFORMATION_PROMPT_VERSION = "airisk_jmcup_transformation_author_v1"
TRANSFORMATION_INPUT_SCHEMA_VERSION = "airisk_jmcup_transformation_author_input_v1"
TRANSFORMATION_RESPONSE_SCHEMA_VERSION = (
    "airisk_jmcup_transformation_author_response_v1"
)
TRANSFORMATION_RECORD_SCHEMA_VERSION = "airisk_jmcup_transformation_record_v2"
RESOLVED_SOURCE_REVIEW_SCHEMA_VERSION = "airisk_jmcup_resolved_source_review_v2"
SOURCE_ELIGIBILITY_SCHEMA_VERSION = "airisk_jmcup_source_eligibility_v1"
LEXICAL_DIAGNOSTICS_VERSION = "airisk_jmcup_transformation_lexical_diagnostics_v1"
TRANSFORMATION_PREPARATION_MANIFEST_VERSION = (
    "airisk_jmcup_transformation_preparation_manifest_v1"
)

DEFAULT_PROTOCOL_PATH = (
    REPO_ROOT / "docs" / f"{TRANSFORMATION_PROTOCOL_VERSION}.md"
)
DEFAULT_PROMPT_PATH = REPO_ROOT / "prompts" / f"{TRANSFORMATION_PROMPT_VERSION}.txt"
DEFAULT_INPUT_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{TRANSFORMATION_INPUT_SCHEMA_VERSION}.schema.json"
)
DEFAULT_RESPONSE_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{TRANSFORMATION_RESPONSE_SCHEMA_VERSION}.schema.json"
)
DEFAULT_TRANSFORMATION_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{TRANSFORMATION_RECORD_SCHEMA_VERSION}.schema.json"
)
DEFAULT_RESOLVED_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{RESOLVED_SOURCE_REVIEW_SCHEMA_VERSION}.schema.json"
)
DEFAULT_ELIGIBILITY_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{SOURCE_ELIGIBILITY_SCHEMA_VERSION}.schema.json"
)
DEFAULT_SOURCE_GROUP_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / "airisk_jmcup_group_semantic_review_input_v1.schema.json"
)
DEFAULT_LEXICAL_DIAGNOSTICS_PATH = (
    REPO_ROOT
    / "data"
    / "generation_cells"
    / f"{LEXICAL_DIAGNOSTICS_VERSION}.json"
)

WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)
SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")
BULLET_RE = re.compile(r"(?m)^\s*(?:[-*+] |\d+[.)] )")
HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
MARKDOWN_RE = re.compile(r"(?m)(^\s*(?:[-*+] |\d+[.)] |#{1,6}\s+)|\*\*|__|`)")

FORBIDDEN_AUTHOR_VISIBLE_KEYS = frozenset(
    {
        "reviewer_a",
        "reviewer_b",
        "reviewer_run_id",
        "adjudicator_run_id",
        "provider",
        "requested_model",
        "resolved_reported_model",
        "actual_routed_provider",
        "provider_usage",
        "provider_reasoning",
        "provider_block_metadata",
        "classification",
        "resolution_source",
        "provisional_consensus_disposition",
        "qc_evidence",
        "adjudication_evidence",
        "reviewer_verdicts",
        "reviewer_confidences",
        "construct_validity_concerns",
        "semantic_independence_concerns",
        "likely_transformation_duplicate_of",
        "target_dataset_n_applied",
        "cost",
        "reasoning_tokens",
        "evaluated_model",
        "evaluation_result",
    }
)


def _normalise(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _indexed(records: Sequence[Mapping[str, Any]], *, label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for source in records:
        record = deepcopy(dict(source))
        group_id = record.get("generation_group_id")
        if not isinstance(group_id, str):
            raise SemanticReviewError(f"{label} record lacks generation_group_id")
        if group_id in indexed:
            raise SemanticReviewError(f"Duplicate {label} record for {group_id}")
        indexed[group_id] = record
    return indexed


def find_forbidden_author_visible_keys(value: Any) -> list[str]:
    found: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                rendered = str(key)
                if (
                    rendered in FORBIDDEN_AUTHOR_VISIBLE_KEYS
                    or rendered.startswith("heuristic_")
                    or rendered.startswith("pre_outcome_audit_")
                ):
                    found.add(rendered)
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return sorted(found)


def _proposal_value(record: Mapping[str, Any], field: str) -> str | None:
    item = record["transformation_proposals"][field]
    value = item.get("resolved_value")
    return value if isinstance(value, str) and value else None


def _assert_exact_eligibility(
    resolved: Mapping[str, Any], eligibility: Mapping[str, Any]
) -> None:
    group_id = resolved.get("generation_group_id")
    if eligibility.get("generation_group_id") != group_id:
        raise SemanticReviewError("Resolved review and eligibility group IDs differ")
    if eligibility.get("eligibility") != "eligible":
        raise SemanticReviewError(
            f"{group_id} is not exactly eligible: {eligibility.get('eligibility')!r}"
        )
    if eligibility.get("resolved_source_review_sha256") != sha256_text(
        canonical_json(resolved)
    ):
        raise SemanticReviewError(f"Eligibility resolved-review hash differs for {group_id}")
    if resolved.get("resolution_status") not in {
        "resolved_consensus",
        "resolved_adjudicated",
    } or resolved.get("resolved_disposition") != "candidate":
        raise SemanticReviewError(f"{group_id} is not a resolved candidate")
    for key in CRITERION_KEYS:
        criterion = resolved["criteria"][key]
        if (
            criterion.get("resolution_status") != "resolved"
            or criterion.get("resolved_judgement") != "yes"
        ):
            raise SemanticReviewError(f"{group_id} does not have resolved yes for {key}")
    if resolved["source_fidelity"].get("resolved_value") not in {
        "high",
        "moderate",
    }:
        raise SemanticReviewError(f"{group_id} lacks eligible source fidelity")
    if resolved["rewrite_level"].get("resolved_value") not in {
        "low",
        "moderate",
        "high",
    }:
        raise SemanticReviewError(f"{group_id} lacks feasible rewrite metadata")


def build_transformation_author_input(
    resolved: Mapping[str, Any],
    eligibility: Mapping[str, Any],
    source_group: Mapping[str, Any],
    *,
    input_schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one positively allow-listed author-visible payload."""

    _assert_exact_eligibility(resolved, eligibility)
    group_id = resolved["generation_group_id"]
    if source_group.get("generation_group_id") != group_id:
        raise SemanticReviewError(f"Source payload group ID differs for {group_id}")
    source_sha = sha256_text(canonical_json(source_group))
    if (
        resolved["provenance"].get("source_group_input_payload_sha256")
        != source_sha
    ):
        raise SemanticReviewError(f"Resolved source-payload hash differs for {group_id}")
    occurrence_ids = {
        item["occurrence_id"] for item in source_group["contextualisations"]
    }
    representative = resolved["representative"].get("resolved_occurrence_id")
    if representative is not None and representative not in occurrence_ids:
        raise SemanticReviewError(f"Resolved representative is not in {group_id}")

    payload = {
        "schema_version": TRANSFORMATION_INPUT_SCHEMA_VERSION,
        "generation_group_id": group_id,
        "source_group": deepcopy(dict(source_group)),
        "resolved_authoring_guidance": {
            "criteria": {
                key: {
                    "judgement": resolved["criteria"][key]["resolved_judgement"],
                    "rationale": resolved["criteria"][key]["rationale"],
                }
                for key in CRITERION_KEYS
            },
            "selected_reference_occurrence_id": representative,
            "group_useful_without_clean_representative": resolved[
                "representative"
            ]["resolved_group_useful_without_clean_representative"],
            "source_fidelity": resolved["source_fidelity"]["resolved_value"],
            "source_fidelity_rationale": resolved["source_fidelity"][
                "adjudicator_rationale"
            ],
            "rewrite_level": resolved["rewrite_level"]["resolved_value"],
            "rewrite_rationale": resolved["rewrite_level"][
                "adjudicator_rationale"
            ],
            "bounded_action_proposal": _proposal_value(
                resolved, "proposed_bounded_action"
            ),
            "broader_action_proposal": _proposal_value(
                resolved, "proposed_broader_action"
            ),
            "baseline_blocker_proposal": _proposal_value(
                resolved, "proposed_baseline_blocker"
            ),
            "evidence_proposal": _proposal_value(resolved, "proposed_evidence"),
            "transformation_family_proposal": _proposal_value(
                resolved, "proposed_transformation_family"
            ),
        },
    }
    leaked = find_forbidden_author_visible_keys(payload)
    if leaked:
        raise SemanticReviewError(
            f"Forbidden model-visible authoring metadata in {group_id}: {leaked}"
        )
    validate_instance(
        payload,
        input_schema or load_schema(DEFAULT_INPUT_SCHEMA_PATH),
        label=f"transformation author input {group_id}",
    )
    return payload


def _text_metrics(text: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    normalised = _normalise(text)
    semantic_categories = []
    for family, family_spec in sorted(
        spec["semantic_manipulation_markers"].items()
    ):
        for category, markers in sorted(family_spec["categories"].items()):
            marker_counts = [
                {
                    "marker": marker,
                    "count": normalised.count(_normalise(marker)),
                }
                for marker in markers
            ]
            semantic_categories.append(
                {
                    "category": category,
                    "semantic_marker_family": family,
                    "expected_side": family_spec["expected_side"],
                    "occurrence_count": sum(
                        item["count"] for item in marker_counts
                    ),
                    "marker_counts": marker_counts,
                }
            )
    lines = text.splitlines() or [text]
    sentences = [match.group(0) for match in SENTENCE_RE.finditer(text) if match.group(0).strip()]
    return {
        "character_count": len(text),
        "word_count": len(WORD_RE.findall(text)),
        "sentence_count": max(1, len(sentences)),
        "line_count": len(lines),
        "bullet_count": len(BULLET_RE.findall(text)),
        "heading_count": len(HEADING_RE.findall(text)),
        "contains_markdown": bool(MARKDOWN_RE.search(text)),
        "semantic_marker_categories": semantic_categories,
    }


def build_surface_form_diagnostics(
    pressure_text: str,
    evidence_text: str,
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    pressure = _text_metrics(pressure_text, spec)
    evidence = _text_metrics(evidence_text, spec)
    maximum = max(pressure["character_count"], evidence["character_count"])
    minimum = min(pressure["character_count"], evidence["character_count"])
    ratio = maximum / minimum
    comparison = {
        "character_count_difference": abs(
            pressure["character_count"] - evidence["character_count"]
        ),
        "word_count_difference": abs(pressure["word_count"] - evidence["word_count"]),
        "sentence_count_difference": abs(
            pressure["sentence_count"] - evidence["sentence_count"]
        ),
        "length_ratio": round(ratio, 6),
        "line_count_match": pressure["line_count"] == evidence["line_count"],
        "bullet_count_match": pressure["bullet_count"] == evidence["bullet_count"],
        "heading_count_match": pressure["heading_count"] == evidence["heading_count"],
        "markdown_presence_match": pressure["contains_markdown"]
        == evidence["contains_markdown"],
    }
    thresholds = spec["presentation_style_diagnostics"][
        "uncalibrated_warning_thresholds"
    ]
    warnings = []
    if ratio > thresholds["length_ratio_max"]:
        warnings.append("length_ratio_exceeds_threshold")
    if comparison["word_count_difference"] > thresholds[
        "absolute_word_count_difference_max"
    ]:
        warnings.append("word_count_difference_exceeds_threshold")
    if comparison["sentence_count_difference"] > thresholds[
        "absolute_sentence_count_difference_max"
    ]:
        warnings.append("sentence_count_difference_exceeds_threshold")
    for field in (
        "line_count_match",
        "bullet_count_match",
        "heading_count_match",
        "markdown_presence_match",
    ):
        if not comparison[field]:
            warnings.append(field.replace("_match", "_differs"))
    pressure_counts = {
        item["category"]: item["occurrence_count"]
        for item in pressure["semantic_marker_categories"]
    }
    evidence_counts = {
        item["category"]: item["occurrence_count"]
        for item in evidence["semantic_marker_categories"]
    }
    category_specs = {
        item["category"]: item
        for item in pressure["semantic_marker_categories"]
    }
    presence_differences = []
    unexpected_side_hits = []
    for category in sorted(category_specs):
        category_spec = category_specs[category]
        pressure_count = pressure_counts[category]
        evidence_count = evidence_counts[category]
        if bool(pressure_count) != bool(evidence_count):
            presence_differences.append(
                {
                    "category": category,
                    "semantic_marker_family": category_spec[
                        "semantic_marker_family"
                    ],
                    "expected_side": category_spec["expected_side"],
                    "pressure_present": bool(pressure_count),
                    "evidence_present": bool(evidence_count),
                    "pressure_count": pressure_count,
                    "evidence_count": evidence_count,
                }
            )
        unexpected_side = (
            "evidence"
            if category_spec["expected_side"] == "pressure" and evidence_count
            else "pressure"
            if category_spec["expected_side"] == "evidence" and pressure_count
            else None
        )
        if unexpected_side is not None:
            unexpected_side_hits.append(
                {
                    "category": category,
                    "semantic_marker_family": category_spec[
                        "semantic_marker_family"
                    ],
                    "expected_side": category_spec["expected_side"],
                    "unexpected_side": unexpected_side,
                    "occurrence_count": (
                        evidence_count
                        if unexpected_side == "evidence"
                        else pressure_count
                    ),
                }
            )
            warnings.append(
                f"semantic_marker_on_unexpected_side:{category}:{unexpected_side}"
            )
    comparison["semantic_category_presence_differences"] = presence_differences
    comparison["unexpected_side_semantic_marker_hits"] = unexpected_side_hits
    return {
        "specification_version": spec["schema_version"],
        "calibration_status": "uncalibrated_heuristic",
        "heuristic_thresholds": dict(thresholds),
        "thresholds_validated": False,
        "pressure": pressure,
        "evidence": evidence,
        "comparison": comparison,
        "warnings": sorted(set(warnings)),
        "warning_only": True,
        "exclusion_criterion": False,
        "semantic_rejection_gate": False,
    }


def answer_key_marker_hits(
    response: Mapping[str, Any], spec: Mapping[str, Any]
) -> list[dict[str, str]]:
    hits = []
    for field in spec["checked_text_fields"]:
        value = response.get(field)
        if not isinstance(value, str):
            continue
        normalised = _normalise(value)
        for marker in spec["answer_key_markers"]:
            literal = _normalise(marker["literal"])
            if literal in normalised:
                hits.append(
                    {
                        "field": field,
                        "marker_id": marker["id"],
                        "matched_literal": marker["literal"],
                    }
                )
    return sorted(hits, key=lambda item: (item["field"], item["marker_id"]))


def _action_label_leakage(
    response: Mapping[str, Any], spec: Mapping[str, Any]
) -> list[str]:
    errors = []
    patterns = [
        (item["id"], re.compile(item["regex"]))
        for item in spec["ab_mapping_leakage_patterns"]
    ]
    for field in spec["checked_text_fields"]:
        value = response.get(field)
        if not isinstance(value, str):
            continue
        for pattern_id, pattern in patterns:
            if pattern.search(value):
                errors.append(
                    f"ab_mapping_leakage:{pattern_id}:{field}"
                )
    return errors


def validate_author_response(
    response: Mapping[str, Any],
    payload: Mapping[str, Any],
    response_schema: Mapping[str, Any],
    lexical_spec: Mapping[str, Any] | None = None,
) -> list[str]:
    errors = validation_errors(response, response_schema)
    if response.get("generation_group_id") != payload.get("generation_group_id"):
        errors.append("generation_group_id does not match author input payload")
    member_ids = {
        item["occurrence_id"]
        for item in payload.get("source_group", {}).get("contextualisations", [])
    }
    used = response.get("source_occurrence_ids_used")
    if isinstance(used, list) and any(item not in member_ids for item in used):
        errors.append("source_occurrence_ids_used contains a non-member occurrence")
    errors.extend(
        _action_label_leakage(
            response,
            lexical_spec or read_json(DEFAULT_LEXICAL_DIAGNOSTICS_PATH),
        )
    )
    return sorted(set(errors))


def render_transformation_conditions(record: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the three presentations without storing duplicated scenario copies."""

    shared = {
        "shared_scenario_text": record["shared_scenario_text"],
        "bounded_action": record["bounded_action"],
        "broader_action": record["broader_action"],
    }
    return {
        "matched_baseline": {
            **shared,
            "condition_addition_type": None,
            "condition_addition_text": None,
            "supported_action": "bounded",
        },
        "unsupported_pressure": {
            **shared,
            "condition_addition_type": "pressure",
            "condition_addition_text": record["pressure_text"],
            "supported_action": "bounded",
        },
        "genuine_evidence": {
            **shared,
            "condition_addition_type": "evidence",
            "condition_addition_text": record["evidence_text"],
            "supported_action": "broader",
        },
    }


def build_canonical_transformation_record(
    response: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    resolved_source_review_sha256: str,
    source_eligibility_sha256: str,
    source_group_input_payload_sha256: str,
    source_review_stage_manifest_sha256: str,
    transformation_run_id: str,
    provider: str,
    requested_model: str,
    prompt_sha256: str,
    response_schema_sha256: str,
    canonical_schema_sha256: str,
    lexical_spec: Mapping[str, Any],
    lexical_spec_sha256: str,
    created_at_utc: str,
) -> dict[str, Any]:
    group_id = payload["generation_group_id"]
    used = set(response["source_occurrence_ids_used"])
    source_items = [
        item
        for item in payload["source_group"]["contextualisations"]
        if item["occurrence_id"] in used
    ]
    assessment = response["matching_assessment"]
    record = {
        "schema_version": TRANSFORMATION_RECORD_SCHEMA_VERSION,
        "transformation_id": f"airisk_jmcup_transform_{group_id}",
        "generation_group_id": group_id,
        "record_status": "proposal_pending_validation",
        "resolved_source_review_sha256": resolved_source_review_sha256,
        "source_eligibility_sha256": source_eligibility_sha256,
        "source_group_input_payload_sha256": source_group_input_payload_sha256,
        "author_input_payload_sha256": sha256_text(canonical_json(payload)),
        "author_response_sha256": sha256_text(canonical_json(response)),
        "source_occurrence_ids_used": list(response["source_occurrence_ids_used"]),
        "transformation_family": response["transformation_family"],
        "shared_scenario_text": response["shared_scenario_text"],
        "bounded_action": response["bounded_action"],
        "broader_action": response["broader_action"],
        "baseline_blocker": response["baseline_blocker"],
        "pressure_text": response["pressure_text"],
        "pressure_type": response["pressure_type"],
        "evidence_text": response["evidence_text"],
        "evidence_type": response["evidence_type"],
        "evidence_strength": response["evidence_strength"],
        "source_fidelity_notes": response["source_fidelity_notes"],
        "rewrite_notes": response["rewrite_notes"],
        "matching_declarations": {
            "same_decision_axis": assessment["same_decision_axis"]["declared"],
            "pressure_changes_no_relevant_facts": assessment[
                "pressure_adds_no_relevant_world_facts"
            ]["declared"],
            "evidence_resolves_same_blocker": assessment[
                "evidence_targets_same_blocker"
            ]["declared"],
            "same_bounded_to_broader_transition": assessment[
                "same_bounded_to_broader_transition"
            ]["declared"],
            "evidence_is_substantive_not_answer_key": assessment[
                "evidence_is_substantive_not_answer_key"
            ]["declared"],
            "rationales": {
                "same_decision_axis": assessment["same_decision_axis"]["rationale"],
                "pressure_changes_no_relevant_facts": assessment[
                    "pressure_adds_no_relevant_world_facts"
                ]["rationale"],
                "evidence_resolves_same_blocker": assessment[
                    "evidence_targets_same_blocker"
                ]["rationale"],
                "same_bounded_to_broader_transition": assessment[
                    "same_bounded_to_broader_transition"
                ]["rationale"],
                "evidence_is_substantive_not_answer_key": assessment[
                    "evidence_is_substantive_not_answer_key"
                ]["rationale"],
            },
            "semantic_assessment_only": True,
        },
        "surface_form_diagnostics": build_surface_form_diagnostics(
            response["pressure_text"], response["evidence_text"], lexical_spec
        ),
        "lexical_warnings": {
            "specification_version": lexical_spec["schema_version"],
            "answer_key_marker_hits": answer_key_marker_hits(response, lexical_spec),
            "answer_key_leakage_warning": bool(
                answer_key_marker_hits(response, lexical_spec)
            ),
            "formulaic_detection_method": "approved_literal_marker_list_only",
            "warning_only": True,
            "exclusion_criterion": False,
            "semantic_rejection_gate": False,
        },
        "coverage_metadata": {
            "domain_labels": sorted(
                {label for item in source_items for label in item["contexts"]}
            ),
            "risk_labels": sorted(
                {label for item in source_items for label in item["risky_behaviors"]}
            ),
        },
        "provenance": {
            "source_review_stage_manifest_sha256": source_review_stage_manifest_sha256,
            "transformation_run_id": transformation_run_id,
            "transformation_author_type": "model",
            "transformation_author_id": requested_model,
            "provider": provider,
            "requested_model": requested_model,
            "transformation_prompt_sha256": prompt_sha256,
            "author_response_schema_sha256": response_schema_sha256,
            "canonical_transformation_schema_sha256": canonical_schema_sha256,
            "lexical_diagnostics_spec_sha256": lexical_spec_sha256,
            "pressure_taxonomy_version": "author_declared_extensible_v1",
            "evidence_taxonomy_version": "author_declared_extensible_v1",
            "created_at_utc": created_at_utc,
        },
        "semantic_truth_deterministically_established": False,
    }
    return record


def validate_canonical_transformation_record(
    record: Mapping[str, Any],
    schema: Mapping[str, Any],
    lexical_spec: Mapping[str, Any] | None = None,
) -> list[str]:
    errors = validation_errors(record, schema)
    expected_id = f"airisk_jmcup_transform_{record.get('generation_group_id')}"
    if record.get("transformation_id") != expected_id:
        errors.append("transformation_id is not deterministically derived from group ID")
    try:
        rendered = render_transformation_conditions(record)
    except (KeyError, TypeError):
        return sorted(set(errors))
    scenarios = {
        item["shared_scenario_text"] for item in rendered.values()
    }
    if len(scenarios) != 1:
        errors.append("rendered shared scenario is not identical across conditions")
    action_pairs = {
        (item["bounded_action"], item["broader_action"])
        for item in rendered.values()
    }
    if len(action_pairs) != 1:
        errors.append("rendered action strings differ across conditions")
    if rendered["matched_baseline"]["condition_addition_text"] is not None:
        errors.append("baseline must have no condition addition")
    if rendered["unsupported_pressure"]["condition_addition_type"] != "pressure":
        errors.append("pressure rendering must contain only the pressure addition")
    if rendered["genuine_evidence"]["condition_addition_type"] != "evidence":
        errors.append("evidence rendering must contain only the evidence addition")
    errors.extend(
        _action_label_leakage(
            record,
            lexical_spec or read_json(DEFAULT_LEXICAL_DIAGNOSTICS_PATH),
        )
    )
    return sorted(set(errors))


def render_transformation_author_prompt(
    prompt: str, payload: Mapping[str, Any]
) -> str:
    return (
        prompt.rstrip()
        + "\n\n--- BEGIN BLINDED TRANSFORMATION-AUTHOR PAYLOAD ---\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + "\n--- END BLINDED TRANSFORMATION-AUTHOR PAYLOAD ---\n"
    )


def prepare_transformation_inputs(
    *,
    resolved_reviews_path: Path,
    eligibility_path: Path,
    source_payloads_path: Path,
    source_resolution_manifest_path: Path,
    output_dir: Path,
    group_ids: Sequence[str] | None = None,
    resolved_schema_path: Path = DEFAULT_RESOLVED_SCHEMA_PATH,
    eligibility_schema_path: Path = DEFAULT_ELIGIBILITY_SCHEMA_PATH,
    source_group_schema_path: Path = DEFAULT_SOURCE_GROUP_SCHEMA_PATH,
    author_input_schema_path: Path = DEFAULT_INPUT_SCHEMA_PATH,
    overwrite: bool = False,
) -> dict[str, Any]:
    resolved_records = read_jsonl(resolved_reviews_path)
    eligibility_records = read_jsonl(eligibility_path)
    source_payloads = read_jsonl(source_payloads_path)
    resolution_manifest = read_json(source_resolution_manifest_path)
    if resolution_manifest.get("schema_version") != "airisk_jmcup_source_resolution_manifest_v2":
        raise SemanticReviewError("Transformation preparation requires source-resolution v2")
    expected_hashes = {
        "resolved_source_reviews_sha256": (resolved_reviews_path, "resolved reviews"),
        "source_eligibility_sha256": (eligibility_path, "source eligibility"),
        "blinded_source_payloads_sha256": (source_payloads_path, "source payloads"),
    }
    for key, (path, label) in expected_hashes.items():
        if resolution_manifest.get(key) != sha256_path(path):
            raise SemanticReviewError(f"Resolution manifest hash differs for {label}")
    if resolution_manifest.get("resolved_schema_sha256") != sha256_path(
        resolved_schema_path
    ):
        raise SemanticReviewError("Resolved-source schema hash differs from manifest")
    if resolution_manifest.get("eligibility_schema_sha256") != sha256_path(
        eligibility_schema_path
    ):
        raise SemanticReviewError("Eligibility schema hash differs from manifest")
    if resolution_manifest.get("source_group_input_schema_sha256") != sha256_path(
        source_group_schema_path
    ):
        raise SemanticReviewError("Source-group schema hash differs from manifest")

    resolved_schema = load_schema(resolved_schema_path)
    eligibility_schema = load_schema(eligibility_schema_path)
    source_schema = load_schema(source_group_schema_path)
    author_input_schema = load_schema(author_input_schema_path)
    resolved_by_id = _indexed(resolved_records, label="resolved review")
    eligibility_by_id = _indexed(eligibility_records, label="eligibility")
    source_by_id = _indexed(source_payloads, label="source payload")
    if not (set(resolved_by_id) == set(eligibility_by_id) == set(source_by_id)):
        raise SemanticReviewError("Resolved, eligibility and source-payload group sets differ")
    for record in resolved_by_id.values():
        validate_instance(record, resolved_schema, label="resolved source review")
    for record in eligibility_by_id.values():
        validate_instance(record, eligibility_schema, label="source eligibility")
    for record in source_by_id.values():
        validate_instance(record, source_schema, label="source group payload")

    eligible_ids = sorted(
        group_id
        for group_id, item in eligibility_by_id.items()
        if item["eligibility"] == "eligible"
    )
    if group_ids is None:
        selected_ids = eligible_ids
    else:
        if len(group_ids) != len(set(group_ids)):
            raise SemanticReviewError("Duplicate requested transformation group ID")
        missing = sorted(set(group_ids) - set(eligibility_by_id))
        if missing:
            raise SemanticReviewError(f"Unknown requested group IDs: {missing}")
        noneligible = sorted(
            group_id
            for group_id in group_ids
            if eligibility_by_id[group_id]["eligibility"] != "eligible"
        )
        if noneligible:
            raise SemanticReviewError(
                f"Requested groups are pending or ineligible: {noneligible}"
            )
        selected_ids = sorted(group_ids)
    payloads = [
        build_transformation_author_input(
            resolved_by_id[group_id],
            eligibility_by_id[group_id],
            source_by_id[group_id],
            input_schema=author_input_schema,
        )
        for group_id in selected_ids
    ]

    output_dir = output_dir.resolve()
    payloads_path = output_dir / "transformation_author_inputs.jsonl"
    manifest_path = output_dir / "transformation_preparation_manifest.json"
    existing = [path for path in (payloads_path, manifest_path) if path.exists()]
    if existing and not overwrite:
        raise SemanticReviewError(
            "Refusing to overwrite transformation preparation: "
            + ", ".join(str(path) for path in existing)
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(payloads_path, payloads)
    status_counts = dict(
        sorted(Counter(item["eligibility"] for item in eligibility_records).items())
    )
    manifest = {
        "schema_version": TRANSFORMATION_PREPARATION_MANIFEST_VERSION,
        "created_at_utc": utc_now(),
        "source_resolution_manifest_path": str(source_resolution_manifest_path.resolve()),
        "source_resolution_manifest_sha256": sha256_path(
            source_resolution_manifest_path
        ),
        "resolved_reviews_path": str(resolved_reviews_path.resolve()),
        "resolved_reviews_sha256": sha256_path(resolved_reviews_path),
        "eligibility_path": str(eligibility_path.resolve()),
        "eligibility_sha256": sha256_path(eligibility_path),
        "source_payloads_path": str(source_payloads_path.resolve()),
        "source_payloads_sha256": sha256_path(source_payloads_path),
        "resolved_schema_sha256": sha256_path(resolved_schema_path),
        "eligibility_schema_sha256": sha256_path(eligibility_schema_path),
        "source_group_schema_sha256": sha256_path(source_group_schema_path),
        "author_input_schema_sha256": sha256_path(author_input_schema_path),
        "eligibility_counts": status_counts,
        "selected_group_count": len(payloads),
        "selected_group_ids": selected_ids,
        "group_provenance": [
            {
                "generation_group_id": group_id,
                "resolved_source_review_sha256": sha256_text(
                    canonical_json(resolved_by_id[group_id])
                ),
                "source_eligibility_sha256": sha256_text(
                    canonical_json(eligibility_by_id[group_id])
                ),
                "source_group_input_payload_sha256": sha256_text(
                    canonical_json(source_by_id[group_id])
                ),
            }
            for group_id in selected_ids
        ],
        "selection_rule": "eligibility must equal eligible exactly",
        "pending_or_ineligible_model_payloads_created": 0,
        "model_visible_forbidden_keys": sorted(
            {
                key for payload in payloads for key in find_forbidden_author_visible_keys(payload)
            }
        ),
        "target_dataset_n_applied": False,
        "external_api_calls": 0,
        "payloads_path": str(payloads_path),
        "payloads_sha256": sha256_path(payloads_path),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest
