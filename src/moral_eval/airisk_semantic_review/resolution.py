"""Deterministic post-adjudication merge and source-eligibility outputs."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .adjudication import TRANSFORMATION_FIELDS
from .adjudication_execution import validate_adjudication_response
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
    write_jsonl,
)


RESOLVED_SOURCE_REVIEW_SCHEMA_VERSION = "airisk_jmcup_resolved_source_review_v2"
SOURCE_ELIGIBILITY_SCHEMA_VERSION = "airisk_jmcup_source_eligibility_v1"
RESOLUTION_MANIFEST_VERSION = "airisk_jmcup_source_resolution_manifest_v2"
DEFAULT_RESOLVED_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{RESOLVED_SOURCE_REVIEW_SCHEMA_VERSION}.schema.json"
)
DEFAULT_ELIGIBILITY_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{SOURCE_ELIGIBILITY_SCHEMA_VERSION}.schema.json"
)
DEFAULT_COMPARISON_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / "airisk_jmcup_review_comparison_v1.schema.json"
)
DEFAULT_OPUS_INPUT_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / "airisk_jmcup_opus_adjudication_input_v1.schema.json"
)
DEFAULT_OPUS_RESPONSE_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / "airisk_jmcup_opus_adjudication_response_v1.schema.json"
)
DEFAULT_ADJUDICATOR_RUN_RECORD_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / "airisk_jmcup_adjudicator_run_record_v1.schema.json"
)
DEFAULT_SOURCE_GROUP_INPUT_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / "airisk_jmcup_group_semantic_review_input_v1.schema.json"
)

FIDELITY_ORDER = {"low": 0, "moderate": 1, "high": 2}
REWRITE_ORDER = {"low": 0, "moderate": 1, "high": 2, "not_viable": 3}


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _conservative_value(
    left: str, right: str, *, order: Mapping[str, int], take_maximum: bool
) -> str:
    chooser = max if take_maximum else min
    return chooser((left, right), key=lambda item: order[item])


def _consensus_criteria(comparison: Mapping[str, Any]) -> dict[str, Any]:
    criteria: dict[str, Any] = {}
    for key in CRITERION_KEYS:
        item = comparison["criterion_comparison"][key]
        if item["reviewer_a"] == item["reviewer_b"]:
            criteria[key] = {
                "reviewer_a": item["reviewer_a"],
                "reviewer_b": item["reviewer_b"],
                "resolution_status": "resolved",
                "resolved_judgement": item["reviewer_a"],
                "resolution_method": "reviewer_consensus",
                "rationale": None,
            }
        else:
            # Deliberately no ordinal no > uncertain > yes merge.
            criteria[key] = {
                "reviewer_a": item["reviewer_a"],
                "reviewer_b": item["reviewer_b"],
                "resolution_status": "unresolved",
                "resolved_judgement": None,
                "resolution_method": "not_resolved",
                "rationale": None,
            }
    return criteria


def _adjudicated_criteria(
    comparison: Mapping[str, Any], response: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        key: {
            "reviewer_a": comparison["criterion_comparison"][key]["reviewer_a"],
            "reviewer_b": comparison["criterion_comparison"][key]["reviewer_b"],
            "resolution_status": "resolved",
            "resolved_judgement": response["criteria"][key]["judgement"],
            "resolution_method": "opus_adjudication",
            "rationale": response["criteria"][key]["rationale"],
        }
        for key in CRITERION_KEYS
    }


def _pending_criteria(comparison: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: {
            "reviewer_a": comparison["criterion_comparison"][key]["reviewer_a"],
            "reviewer_b": comparison["criterion_comparison"][key]["reviewer_b"],
            "resolution_status": "unresolved",
            "resolved_judgement": None,
            "resolution_method": "not_resolved",
            "rationale": None,
        }
        for key in CRITERION_KEYS
    }


def _metadata(
    review_a: Mapping[str, Any],
    review_b: Mapping[str, Any],
    field: str,
    *,
    adjudicator: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if adjudicator is not None:
        rationale = adjudicator["reviewer_metadata_resolutions"][field]["rationale"]
        return {
            "reviewer_a": review_a[field],
            "reviewer_b": review_b[field],
            "resolution_status": "resolved",
            "resolved_value": adjudicator[field],
            "resolution_method": "opus_adjudication",
            "adjudicator_rationale": rationale,
        }
    order = FIDELITY_ORDER if field == "source_fidelity" else REWRITE_ORDER
    value = _conservative_value(
        review_a[field],
        review_b[field],
        order=order,
        take_maximum=field == "rewrite_level",
    )
    return {
        "reviewer_a": review_a[field],
        "reviewer_b": review_b[field],
        "resolution_status": "resolved",
        "resolved_value": value,
        "resolution_method": "conservative_consensus",
        "adjudicator_rationale": None,
    }


def _pending_metadata(
    review_a: Mapping[str, Any], review_b: Mapping[str, Any], field: str
) -> dict[str, Any]:
    return {
        "reviewer_a": review_a[field],
        "reviewer_b": review_b[field],
        "resolution_status": "unresolved",
        "resolved_value": None,
        "resolution_method": "not_resolved",
        "adjudicator_rationale": None,
    }


def _representative(
    review_a: Mapping[str, Any],
    review_b: Mapping[str, Any],
    *,
    adjudicator: Mapping[str, Any] | None,
    pending: bool = False,
) -> dict[str, Any]:
    common = {
        "reviewer_a_occurrence_id": review_a["selected_representative_occurrence_id"],
        "reviewer_b_occurrence_id": review_b["selected_representative_occurrence_id"],
        "reviewer_a_group_useful_without_clean_representative": review_a[
            "group_useful_without_clean_representative"
        ],
        "reviewer_b_group_useful_without_clean_representative": review_b[
            "group_useful_without_clean_representative"
        ],
    }
    if adjudicator is not None:
        return {
            **common,
            "resolved_occurrence_id": adjudicator[
                "selected_representative_occurrence_id"
            ],
            "resolved_group_useful_without_clean_representative": adjudicator[
                "group_useful_without_clean_representative"
            ],
            "resolution_status": "resolved",
            "resolution_method": "opus_adjudication",
        }
    agrees = (
        review_a["selected_representative_occurrence_id"]
        == review_b["selected_representative_occurrence_id"]
        and review_a["group_useful_without_clean_representative"]
        == review_b["group_useful_without_clean_representative"]
        and not pending
    )
    return {
        **common,
        "resolved_occurrence_id": (
            review_a["selected_representative_occurrence_id"] if agrees else None
        ),
        "resolved_group_useful_without_clean_representative": (
            review_a["group_useful_without_clean_representative"] if agrees else None
        ),
        "resolution_status": "resolved" if agrees else "unresolved",
        "resolution_method": "reviewer_consensus" if agrees else "not_resolved",
    }


def _proposals(
    review_a: Mapping[str, Any],
    review_b: Mapping[str, Any],
    *,
    adjudicator: Mapping[str, Any] | None,
    pending: bool = False,
) -> dict[str, Any]:
    values = {}
    for field in TRANSFORMATION_FIELDS:
        if adjudicator is not None:
            status = "resolved"
            value = adjudicator[field]
            method = "opus_adjudication"
        elif review_a[field] == review_b[field] and not pending:
            status = "resolved"
            value = review_a[field]
            method = "reviewer_consensus"
        else:
            status = "unresolved"
            value = None
            method = "not_resolved"
        values[field] = {
            "reviewer_a": review_a[field],
            "reviewer_b": review_b[field],
            "resolution_status": status,
            "resolved_value": value,
            "resolution_method": method,
        }
    return values


def _adjudicated_disposition(response: Mapping[str, Any]) -> str:
    judgements = [response["criteria"][key]["judgement"] for key in CRITERION_KEYS]
    if (
        all(value == "yes" for value in judgements)
        and response["source_fidelity"] in {"high", "moderate"}
        and response["rewrite_level"] != "not_viable"
    ):
        return "candidate"
    if (
        "no" in judgements
        or response["source_fidelity"] == "low"
        or response["rewrite_level"] == "not_viable"
    ):
        return "reject"
    return "unresolved"


def _qc_confirms(
    comparison: Mapping[str, Any], response: Mapping[str, Any]
) -> bool:
    classification = comparison["classification"]
    disposition = _adjudicated_disposition(response)
    if classification == "consensus_clean_candidate":
        return disposition == "candidate"
    if classification != "consensus_reject" or disposition != "reject":
        return False
    # A QC adjudicator may not overwrite disputed criteria, but a direct conflict
    # with a criterion on which both reviewers agreed requires human review.
    return all(
        response["criteria"][key]["judgement"]
        == comparison["criterion_comparison"][key]["reviewer_a"]
        for key in CRITERION_KEYS
        if not comparison["criterion_comparison"][key]["disagreement"]
    )


def _model_evidence(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "adjudicator_run_id": record["adjudicator_run_id"],
        "adjudication_record_sha256": sha256_text(canonical_json(record)),
        "adjudicator_response": deepcopy(record["parsed_structured_response"]),
        "input_payload_sha256": record["input_payload_sha256"],
    }


def _base_record(
    comparison: Mapping[str, Any],
    *,
    sampling_manifest_sha256: str,
    private_provenance_sha256: str,
    deterministic_seed_sha256: str,
    source_group_input_schema_sha256: str,
    source_group_input_payload_sha256: str,
) -> dict[str, Any]:
    review_a = comparison["reviewer_a"]["response"]
    review_b = comparison["reviewer_b"]["response"]
    return {
        "schema_version": RESOLVED_SOURCE_REVIEW_SCHEMA_VERSION,
        "generation_group_id": comparison["generation_group_id"],
        "criteria": _pending_criteria(comparison),
        "source_fidelity": _pending_metadata(review_a, review_b, "source_fidelity"),
        "rewrite_level": _pending_metadata(review_a, review_b, "rewrite_level"),
        "representative": _representative(review_a, review_b, adjudicator=None, pending=True),
        "transformation_proposals": _proposals(review_a, review_b, adjudicator=None, pending=True),
        "reviewer_verdicts": {
            "reviewer_a": review_a["reviewer_verdict"],
            "reviewer_b": review_b["reviewer_verdict"],
            "agreement": review_a["reviewer_verdict"] == review_b["reviewer_verdict"],
            "adjudicator": None,
        },
        "reviewer_confidences": {
            "reviewer_a": review_a["reviewer_confidence"],
            "reviewer_b": review_b["reviewer_confidence"],
            "agreement": review_a["reviewer_confidence"] == review_b["reviewer_confidence"],
            "adjudicator": None,
        },
        "construct_validity_concerns": {
            "reviewer_a": deepcopy(review_a["construct_validity_concerns"]),
            "reviewer_b": deepcopy(review_b["construct_validity_concerns"]),
            "adjudicator": None,
        },
        "semantic_independence_concerns": {
            "reviewer_a": review_a["semantic_independence_concern"],
            "reviewer_b": review_b["semantic_independence_concern"],
            "adjudicator": None,
        },
        "likely_transformation_duplicate_of": {
            "reviewer_a": review_a["likely_transformation_duplicate_of"],
            "reviewer_b": review_b["likely_transformation_duplicate_of"],
            "adjudicator": None,
        },
        "adjudication_evidence": None,
        "qc_evidence": None,
        "provenance": {
            "comparison_record_sha256": sha256_text(canonical_json(comparison)),
            "reviewer_a_run_id": comparison["reviewer_a"]["reviewer_run_id"],
            "reviewer_b_run_id": comparison["reviewer_b"]["reviewer_run_id"],
            "reviewer_a_record_sha256": sha256_text(
                canonical_json(comparison["reviewer_a"])
            ),
            "reviewer_b_record_sha256": sha256_text(
                canonical_json(comparison["reviewer_b"])
            ),
            "sampling_manifest_sha256": sampling_manifest_sha256,
            "private_provenance_sha256": private_provenance_sha256,
            "deterministic_seed_sha256": deterministic_seed_sha256,
            "source_group_input_schema_sha256": source_group_input_schema_sha256,
            "source_group_input_payload_sha256": source_group_input_payload_sha256,
        },
        "semantic_truth_deterministically_established": False,
    }


def resolve_comparison(
    comparison: Mapping[str, Any],
    *,
    selection_reason: str | None,
    adjudication_record: Mapping[str, Any] | None,
    sampling_manifest_sha256: str,
    private_provenance_sha256: str,
    deterministic_seed_sha256: str,
    source_group_input_schema_sha256: str,
    source_group_input_payload_sha256: str,
) -> dict[str, Any]:
    """Resolve one comparison without averaging criteria or model verdicts."""

    record = _base_record(
        comparison,
        sampling_manifest_sha256=sampling_manifest_sha256,
        private_provenance_sha256=private_provenance_sha256,
        deterministic_seed_sha256=deterministic_seed_sha256,
        source_group_input_schema_sha256=source_group_input_schema_sha256,
        source_group_input_payload_sha256=source_group_input_payload_sha256,
    )
    review_a = comparison["reviewer_a"]["response"]
    review_b = comparison["reviewer_b"]["response"]
    classification = comparison["classification"]
    consensus = classification in {
        "consensus_clean_candidate",
        "consensus_reject",
    }
    provisional = (
        "candidate" if classification == "consensus_clean_candidate" else
        "reject" if classification == "consensus_reject" else None
    )
    record.update(
        provisional_consensus_disposition=provisional,
        resolution_source=classification if consensus else "pending",
        resolution_status="pending_adjudication",
        resolved_disposition="unresolved",
    )

    if consensus:
        record["criteria"] = _consensus_criteria(comparison)
        record["source_fidelity"] = _metadata(
            review_a, review_b, "source_fidelity", adjudicator=None
        )
        record["rewrite_level"] = _metadata(
            review_a, review_b, "rewrite_level", adjudicator=None
        )
        record["representative"] = _representative(
            review_a, review_b, adjudicator=None
        )
        record["transformation_proposals"] = _proposals(
            review_a, review_b, adjudicator=None
        )
        if selection_reason is None:
            record["resolution_status"] = "resolved_consensus"
            record["resolved_disposition"] = provisional
            return record
        if adjudication_record is None:
            return record
        response = adjudication_record["parsed_structured_response"]
        confirms = _qc_confirms(comparison, response)
        record["qc_evidence"] = {
            **_model_evidence(adjudication_record),
            "selection_reason": selection_reason,
            "qc_outcome": (
                "confirmed_consensus" if confirms else "disagrees_or_weakens"
            ),
            "superseded_consensus_resolution": False,
        }
        if confirms:
            record["resolution_status"] = "resolved_consensus"
            record["resolved_disposition"] = provisional
        else:
            record["resolution_status"] = "pending_human_review"
            record["resolved_disposition"] = "unresolved"
        return record

    if adjudication_record is None:
        return record
    response = adjudication_record["parsed_structured_response"]
    disposition = _adjudicated_disposition(response)
    record.update(
        resolution_source="opus_adjudication",
        resolution_status=(
            "resolved_adjudicated"
            if disposition != "unresolved"
            else "pending_human_review"
        ),
        resolved_disposition=disposition,
        criteria=_adjudicated_criteria(comparison, response),
        source_fidelity=_metadata(
            review_a, review_b, "source_fidelity", adjudicator=response
        ),
        rewrite_level=_metadata(
            review_a, review_b, "rewrite_level", adjudicator=response
        ),
        representative=_representative(
            review_a, review_b, adjudicator=response
        ),
        transformation_proposals=_proposals(
            review_a, review_b, adjudicator=response
        ),
        adjudication_evidence=_model_evidence(adjudication_record),
    )
    record["reviewer_verdicts"]["adjudicator"] = response["reviewer_verdict"]
    record["reviewer_confidences"]["adjudicator"] = response["reviewer_confidence"]
    record["construct_validity_concerns"]["adjudicator"] = deepcopy(
        response["construct_validity_concerns"]
    )
    record["semantic_independence_concerns"]["adjudicator"] = response[
        "semantic_independence_concern"
    ]
    record["likely_transformation_duplicate_of"]["adjudicator"] = response[
        "likely_transformation_duplicate_of"
    ]
    return record


def source_eligibility(record: Mapping[str, Any]) -> dict[str, Any]:
    values = [record["criteria"][key]["resolved_judgement"] for key in CRITERION_KEYS]
    all_yes = None if any(value is None for value in values) else all(
        value == "yes" for value in values
    )
    fidelity = record["source_fidelity"]["resolved_value"]
    rewrite = record["rewrite_level"]["resolved_value"]
    status = record["resolution_status"]
    reasons: list[str] = []
    if status == "pending_adjudication":
        eligibility = "pending_adjudication"
        reasons.append("selected_adjudication_or_qc_not_completed")
    elif status == "pending_human_review":
        eligibility = "pending_human_review"
        reasons.append("model_resolution_requires_human_review")
    elif record["resolved_disposition"] == "candidate" and all_yes is True:
        eligibility = "eligible"
        reasons.append("all_h1_h7_yes")
        reasons.append("source_fidelity_high_or_moderate")
        reasons.append("rewrite_feasible")
    else:
        eligibility = "ineligible"
        if record["resolution_source"] == "consensus_reject":
            reasons.append("consensus_reject")
        if any(value == "no" for value in values):
            reasons.append("one_or_more_h1_h7_no")
        if fidelity == "low":
            reasons.append("source_fidelity_low")
        if rewrite == "not_viable":
            reasons.append("rewrite_not_viable")
        if not reasons:
            reasons.append("resolved_disposition_not_candidate")
    return {
        "schema_version": SOURCE_ELIGIBILITY_SCHEMA_VERSION,
        "generation_group_id": record["generation_group_id"],
        "eligibility": eligibility,
        "basis": "resolved_h1_h7_source_fidelity_and_rewrite_feasibility",
        "resolved_source_review_sha256": sha256_text(canonical_json(record)),
        "resolved_disposition": record["resolved_disposition"],
        "all_h1_h7_yes": all_yes,
        "source_fidelity": fidelity,
        "rewrite_level": rewrite,
        "reason_codes": sorted(set(reasons)),
        "target_dataset_n_applied": False,
        "model_verdict_used_as_eligibility_rule": False,
        "model_confidence_used_as_eligibility_rule": False,
    }


def merge_resolved_source_reviews(
    *,
    comparison_path: Path,
    sampling_manifest_path: Path,
    private_provenance_path: Path,
    opus_payloads_path: Path,
    blinded_source_payloads_path: Path,
    output_dir: Path,
    adjudication_records_path: Path | None = None,
    comparison_schema_path: Path = DEFAULT_COMPARISON_SCHEMA_PATH,
    opus_input_schema_path: Path = DEFAULT_OPUS_INPUT_SCHEMA_PATH,
    opus_response_schema_path: Path = DEFAULT_OPUS_RESPONSE_SCHEMA_PATH,
    adjudicator_run_record_schema_path: Path = DEFAULT_ADJUDICATOR_RUN_RECORD_SCHEMA_PATH,
    source_group_input_schema_path: Path = DEFAULT_SOURCE_GROUP_INPUT_SCHEMA_PATH,
    resolved_schema_path: Path = DEFAULT_RESOLVED_SCHEMA_PATH,
    eligibility_schema_path: Path = DEFAULT_ELIGIBILITY_SCHEMA_PATH,
    overwrite: bool = False,
) -> dict[str, Any]:
    comparisons = read_jsonl(comparison_path)
    sampling_manifest = read_json(sampling_manifest_path)
    private_provenance = read_json(private_provenance_path)
    payloads = read_jsonl(opus_payloads_path)
    source_payloads = read_jsonl(blinded_source_payloads_path)
    if not comparisons:
        raise SemanticReviewError("Comparison corpus is empty")
    seed = private_provenance.get("deterministic_seed")
    seed_sha = private_provenance.get("deterministic_seed_sha256")
    if not isinstance(seed, str) or not seed or sha256_text(seed) != seed_sha:
        raise SemanticReviewError("Private deterministic seed provenance is invalid")
    if sampling_manifest.get("seed_sha256") != seed_sha:
        raise SemanticReviewError("Public/private deterministic seed hashes differ")

    comparison_schema = load_schema(comparison_schema_path)
    comparison_by_id: dict[str, dict[str, Any]] = {}
    for item in comparisons:
        validate_instance(item, comparison_schema, label="review comparison")
        group_id = item["generation_group_id"]
        if group_id in comparison_by_id:
            raise SemanticReviewError(f"Duplicate comparison for {group_id}")
        comparison_by_id[group_id] = item

    source_input_schema = load_schema(source_group_input_schema_path)
    source_payload_by_id: dict[str, dict[str, Any]] = {}
    for payload in source_payloads:
        validate_instance(payload, source_input_schema, label="blinded source payload")
        group_id = payload["generation_group_id"]
        if group_id in source_payload_by_id:
            raise SemanticReviewError(f"Duplicate blinded source payload for {group_id}")
        source_payload_by_id[group_id] = payload
    if set(source_payload_by_id) != set(comparison_by_id):
        raise SemanticReviewError(
            "Blinded source payload group set differs from comparison corpus"
        )

    opus_input_schema = load_schema(opus_input_schema_path)
    payload_by_id: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        validate_instance(payload, opus_input_schema, label="Opus payload")
        group_id = payload["generation_group_id"]
        if group_id in payload_by_id:
            raise SemanticReviewError(f"Duplicate Opus payload for {group_id}")
        payload_by_id[group_id] = payload
    selected_ids = set(sampling_manifest["selected_group_ids"])
    if set(payload_by_id) != selected_ids:
        raise SemanticReviewError("Opus payloads do not match sampling manifest")
    if not selected_ids <= set(comparison_by_id):
        raise SemanticReviewError("Sampling manifest contains unknown comparison groups")

    private_mapping = {
        item["generation_group_id"]: item
        for item in private_provenance["reviewer_slot_mappings"]
    }
    if set(private_mapping) != selected_ids:
        raise SemanticReviewError("Private reviewer mappings do not match selection")
    selection_reason = {
        group_id: private_mapping[group_id]["selection_reason"]
        for group_id in selected_ids
    }

    adjudication_by_id: dict[str, dict[str, Any]] = {}
    if adjudication_records_path is not None:
        run_schema = load_schema(adjudicator_run_record_schema_path)
        response_schema = load_schema(opus_response_schema_path)
        for item in read_jsonl(adjudication_records_path):
            validate_instance(item, run_schema, label="adjudicator run record")
            if item["status"] != "completed":
                continue
            group_id = item["generation_group_id"]
            if group_id in adjudication_by_id:
                raise SemanticReviewError(
                    f"Multiple completed adjudications for {group_id}"
                )
            if group_id not in payload_by_id:
                raise SemanticReviewError(f"Adjudication for unselected group {group_id}")
            payload = payload_by_id[group_id]
            if item["input_payload_sha256"] != sha256_text(canonical_json(payload)):
                raise SemanticReviewError(f"Adjudication input hash differs for {group_id}")
            errors = validate_adjudication_response(
                item["parsed_structured_response"], payload, response_schema
            )
            if errors:
                raise SemanticReviewError(
                    f"Completed adjudication is invalid for {group_id}: {errors}"
                )
            adjudication_by_id[group_id] = item

    manifest_sha = sha256_path(sampling_manifest_path)
    private_sha = sha256_path(private_provenance_path)
    resolved = []
    source_input_schema_sha = sha256_path(source_group_input_schema_path)
    for group_id in sorted(comparison_by_id):
        source_payload_sha = sha256_text(
            canonical_json(source_payload_by_id[group_id])
        )
        record = resolve_comparison(
            comparison_by_id[group_id],
            selection_reason=selection_reason.get(group_id),
            adjudication_record=adjudication_by_id.get(group_id),
            sampling_manifest_sha256=manifest_sha,
            private_provenance_sha256=private_sha,
            deterministic_seed_sha256=seed_sha,
            source_group_input_schema_sha256=source_input_schema_sha,
            source_group_input_payload_sha256=source_payload_sha,
        )
        resolved.append(record)

    resolved_schema = load_schema(resolved_schema_path)
    eligibility_schema = load_schema(eligibility_schema_path)
    eligibility = []
    for record in resolved:
        validate_instance(record, resolved_schema, label="resolved source review")
        item = source_eligibility(record)
        validate_instance(item, eligibility_schema, label="source eligibility")
        eligibility.append(item)

    output_dir = output_dir.resolve()
    paths = {
        "resolved": output_dir / "resolved_source_reviews.jsonl",
        "eligibility": output_dir / "source_eligibility.jsonl",
        "manifest": output_dir / "source_resolution_manifest.json",
    }
    existing = [path for path in paths.values() if path.exists()]
    if existing and not overwrite:
        raise SemanticReviewError(
            "Refusing to overwrite resolution products: "
            + ", ".join(str(path) for path in existing)
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(paths["resolved"], resolved)
    write_jsonl(paths["eligibility"], eligibility)
    manifest = {
        "schema_version": RESOLUTION_MANIFEST_VERSION,
        "created_at_utc": utc_now(),
        "group_count": len(resolved),
        "resolution_status_counts": dict(
            sorted(Counter(item["resolution_status"] for item in resolved).items())
        ),
        "resolved_disposition_counts": dict(
            sorted(Counter(item["resolved_disposition"] for item in resolved).items())
        ),
        "eligibility_counts": dict(
            sorted(Counter(item["eligibility"] for item in eligibility).items())
        ),
        "deterministic_seed": seed,
        "deterministic_seed_sha256": seed_sha,
        "comparison_path": str(comparison_path.resolve()),
        "comparison_sha256": sha256_path(comparison_path),
        "sampling_manifest_sha256": manifest_sha,
        "private_provenance_sha256": private_sha,
        "adjudication_records_path": (
            str(adjudication_records_path.resolve())
            if adjudication_records_path is not None else None
        ),
        "adjudication_records_sha256": (
            sha256_path(adjudication_records_path)
            if adjudication_records_path is not None else None
        ),
        "blinded_source_payloads_path": str(blinded_source_payloads_path.resolve()),
        "blinded_source_payloads_sha256": sha256_path(blinded_source_payloads_path),
        "source_group_input_schema_sha256": source_input_schema_sha,
        "resolved_schema_sha256": sha256_path(resolved_schema_path),
        "eligibility_schema_sha256": sha256_path(eligibility_schema_path),
        "resolved_source_reviews_path": str(paths["resolved"]),
        "resolved_source_reviews_sha256": sha256_path(paths["resolved"]),
        "source_eligibility_path": str(paths["eligibility"]),
        "source_eligibility_sha256": sha256_path(paths["eligibility"]),
        "target_dataset_n_applied": False,
        "model_verdicts_or_confidence_averaged": False,
        "consensus_reject_criterion_disagreements_ordinally_merged": False,
        "qc_evidence_supersedes_agreeing_consensus": False,
        "semantic_truth_deterministically_established": False,
    }
    _write_json(paths["manifest"], manifest)
    return manifest
