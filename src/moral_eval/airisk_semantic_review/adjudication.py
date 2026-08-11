"""Criterion-level comparison and adjudication preparation for two blind runs."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .core import (
    CRITERION_KEYS,
    DEFAULT_RESPONSE_SCHEMA_PATH,
    SemanticReviewError,
    canonical_json,
    load_schema,
    read_jsonl,
    sha256_path,
    utc_now,
    validation_errors,
)


ADJUDICATION_SCHEMA_VERSION = "airisk_jmcup_review_adjudication_preparation_v1"
CLASSIFICATIONS = (
    "consensus_clean_candidate",
    "consensus_reject",
    "needs_adjudication",
)
TRANSFORMATION_FIELDS = (
    "proposed_bounded_action",
    "proposed_broader_action",
    "proposed_baseline_blocker",
    "proposed_evidence",
    "proposed_transformation_family",
)
TOKEN_RE = re.compile(r"[a-z0-9]+")
MATERIAL_DIFFERENCE_JACCARD_THRESHOLD = 0.5


def _completed_reviews(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    completed: dict[str, dict[str, Any]] = {}
    all_records = read_jsonl(path)
    for record in all_records:
        if record.get("status") != "completed":
            continue
        group_id = record["generation_group_id"]
        if group_id in completed:
            raise SemanticReviewError(
                f"Multiple completed reviews for {group_id} in {path}"
            )
        completed[group_id] = record
    run_ids = {record.get("reviewer_run_id") for record in all_records}
    if len(run_ids) != 1:
        raise SemanticReviewError(f"Expected one reviewer_run_id in {path}")
    metadata = {
        "path": str(path.resolve()),
        "sha256": sha256_path(path),
        "reviewer_run_id": next(iter(run_ids)),
        "completed_count": len(completed),
    }
    return completed, metadata


def _all_yes(response: Mapping[str, Any]) -> bool:
    return all(
        response["criteria"][key]["judgement"] == "yes" for key in CRITERION_KEYS
    )


def _has_hard_failure(response: Mapping[str, Any]) -> bool:
    return any(
        response["criteria"][key]["judgement"] == "no" for key in CRITERION_KEYS
    )


def _rejects_as_non_viable(response: Mapping[str, Any]) -> bool:
    return (
        response["reviewer_verdict"] == "reject"
        or response["rewrite_level"] == "not_viable"
    )


def classify_pair(
    review_a: Mapping[str, Any], review_b: Mapping[str, Any]
) -> str:
    clean = all(
        _all_yes(review)
        and review["source_fidelity"] in {"high", "moderate"}
        and review["rewrite_level"] != "not_viable"
        for review in (review_a, review_b)
    )
    if clean:
        return "consensus_clean_candidate"
    rejected = all(
        _rejects_as_non_viable(review) and _has_hard_failure(review)
        for review in (review_a, review_b)
    )
    if rejected:
        return "consensus_reject"
    return "needs_adjudication"


def _normalised_tokens(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    return set(TOKEN_RE.findall(value.casefold()))


def _text_comparison(left: Any, right: Any) -> dict[str, Any]:
    left_tokens = _normalised_tokens(left)
    right_tokens = _normalised_tokens(right)
    if left == right:
        similarity = 1.0
    elif not left_tokens and not right_tokens:
        similarity = 1.0
    elif not left_tokens or not right_tokens:
        similarity = 0.0
    else:
        similarity = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    return {
        "reviewer_a": left,
        "reviewer_b": right,
        "token_jaccard_similarity": round(similarity, 6),
        "materially_different": similarity < MATERIAL_DIFFERENCE_JACCARD_THRESHOLD,
        "method": (
            "deterministic token-Jaccard preparation flag; requires human adjudication"
        ),
    }


def compare_review_pair(
    record_a: Mapping[str, Any], record_b: Mapping[str, Any]
) -> dict[str, Any]:
    group_id = record_a["generation_group_id"]
    if record_b["generation_group_id"] != group_id:
        raise SemanticReviewError("Cannot compare different generation groups")
    if record_a["input_payload_sha256"] != record_b["input_payload_sha256"]:
        raise SemanticReviewError(f"Reviewer inputs differ for {group_id}")
    if record_a["prompt_sha256"] != record_b["prompt_sha256"]:
        raise SemanticReviewError(f"Reviewer prompts differ for {group_id}")
    if record_a["response_schema_sha256"] != record_b["response_schema_sha256"]:
        raise SemanticReviewError(f"Reviewer response schemas differ for {group_id}")
    review_a = record_a["parsed_structured_response"]
    review_b = record_b["parsed_structured_response"]
    if not isinstance(review_a, Mapping) or not isinstance(review_b, Mapping):
        raise SemanticReviewError(f"Completed review lacks parsed response for {group_id}")

    criterion_comparison = {}
    disagreements = []
    uncertain = []
    for key in CRITERION_KEYS:
        judgement_a = review_a["criteria"][key]["judgement"]
        judgement_b = review_b["criteria"][key]["judgement"]
        disagrees = judgement_a != judgement_b
        has_uncertain = "uncertain" in {judgement_a, judgement_b}
        if disagrees:
            disagreements.append(key)
        if has_uncertain:
            uncertain.append(key)
        criterion_comparison[key] = {
            "reviewer_a": judgement_a,
            "reviewer_b": judgement_b,
            "disagreement": disagrees,
            "any_uncertain": has_uncertain,
        }
    transformation_comparison = {
        field: _text_comparison(review_a.get(field), review_b.get(field))
        for field in TRANSFORMATION_FIELDS
    }
    materially_different = [
        field
        for field, comparison in transformation_comparison.items()
        if comparison["materially_different"]
    ]
    return {
        "schema_version": ADJUDICATION_SCHEMA_VERSION,
        "generation_group_id": group_id,
        "classification": classify_pair(review_a, review_b),
        "reviewer_a": {
            "reviewer_run_id": record_a["reviewer_run_id"],
            "provider": record_a["provider"],
            "requested_model": record_a["requested_model"],
            "resolved_reported_model": record_a["resolved_reported_model"],
            "response": review_a,
        },
        "reviewer_b": {
            "reviewer_run_id": record_b["reviewer_run_id"],
            "provider": record_b["provider"],
            "requested_model": record_b["requested_model"],
            "resolved_reported_model": record_b["resolved_reported_model"],
            "response": review_b,
        },
        "criterion_comparison": criterion_comparison,
        "flags": {
            "h1_h7_disagreements": disagreements,
            "any_h1_h7_disagreement": bool(disagreements),
            "uncertain_criteria": uncertain,
            "any_uncertain": bool(uncertain),
            "source_fidelity_disagreement": (
                review_a["source_fidelity"] != review_b["source_fidelity"]
            ),
            "rewrite_level_disagreement": (
                review_a["rewrite_level"] != review_b["rewrite_level"]
            ),
            "selected_representative_disagreement": (
                review_a["selected_representative_occurrence_id"]
                != review_b["selected_representative_occurrence_id"]
            ),
            "materially_different_proposed_transformation_fields": materially_different,
            "materially_different_proposed_transformations": bool(materially_different),
        },
        "proposed_transformation_comparison": transformation_comparison,
        "adjudicator_fields": {
            "final_classification": None,
            "criterion_resolutions": {key: None for key in CRITERION_KEYS},
            "selected_transformation": None,
            "adjudication_notes": None,
        },
    }


def prepare_adjudication(
    review_a_path: Path,
    review_b_path: Path,
    *,
    output_dir: Path,
    expected_group_ids: Sequence[str] | None = None,
    response_schema_path: Path = DEFAULT_RESPONSE_SCHEMA_PATH,
    overwrite: bool = False,
) -> dict[str, Any]:
    reviews_a, metadata_a = _completed_reviews(review_a_path)
    reviews_b, metadata_b = _completed_reviews(review_b_path)
    if metadata_a["reviewer_run_id"] == metadata_b["reviewer_run_id"]:
        raise SemanticReviewError("Reviewer A and reviewer B must be separate run IDs")
    if set(reviews_a) != set(reviews_b):
        only_a = sorted(set(reviews_a) - set(reviews_b))
        only_b = sorted(set(reviews_b) - set(reviews_a))
        raise SemanticReviewError(
            f"Completed reviewer group sets differ; only A={only_a[:5]}, only B={only_b[:5]}"
        )
    if expected_group_ids is not None and set(reviews_a) != set(expected_group_ids):
        raise SemanticReviewError("Completed review sets do not match expected groups")

    response_schema = load_schema(response_schema_path)
    for label, records in (("A", reviews_a), ("B", reviews_b)):
        for group_id, record in records.items():
            errors = validation_errors(
                record["parsed_structured_response"], response_schema
            )
            if errors:
                raise SemanticReviewError(
                    f"Reviewer {label} response for {group_id} is invalid: {errors}"
                )

    comparisons = [
        compare_review_pair(reviews_a[group_id], reviews_b[group_id])
        for group_id in sorted(reviews_a)
    ]
    output_dir = output_dir.resolve()
    output_jsonl = output_dir / "criterion_level_comparison.jsonl"
    summary_path = output_dir / "adjudication_preparation_summary.json"
    existing = [path for path in (output_jsonl, summary_path) if path.exists()]
    if existing and not overwrite:
        raise SemanticReviewError(
            "Refusing to overwrite adjudication products: "
            + ", ".join(str(path) for path in existing)
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_jsonl.write_text(
        "".join(canonical_json(item) + "\n" for item in comparisons),
        encoding="utf-8",
        newline="\n",
    )
    summary = {
        "schema_version": ADJUDICATION_SCHEMA_VERSION,
        "created_at_utc": utc_now(),
        "reviewer_a": metadata_a,
        "reviewer_b": metadata_b,
        "response_schema_path": str(response_schema_path.resolve()),
        "response_schema_sha256": sha256_path(response_schema_path),
        "group_count": len(comparisons),
        "classification_counts": dict(
            sorted(Counter(item["classification"] for item in comparisons).items())
        ),
        "flag_counts": {
            flag: sum(bool(item["flags"][flag]) for item in comparisons)
            for flag in (
                "any_h1_h7_disagreement",
                "any_uncertain",
                "source_fidelity_disagreement",
                "rewrite_level_disagreement",
                "selected_representative_disagreement",
                "materially_different_proposed_transformations",
            )
        },
        "classification_rule": {
            "consensus_clean_candidate": (
                "Both reviewers mark H1-H7 yes, source fidelity high/moderate, "
                "and rewrite level not not_viable."
            ),
            "consensus_reject": (
                "Both reviewers mark reject or not_viable and each identifies at "
                "least one H1-H7 judgement of no."
            ),
            "needs_adjudication": "Every other combination.",
            "verdict_labels_averaged": False,
        },
        "cross_group_semantic_duplicate_removal_performed": False,
        "output_jsonl": str(output_jsonl),
        "output_jsonl_sha256": sha256_path(output_jsonl),
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return summary
