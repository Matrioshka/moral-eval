"""Criterion-level comparison and adjudication preparation for two blind runs."""

from __future__ import annotations

import json
import hashlib
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .core import (
    CRITERION_KEYS,
    DEFAULT_INPUT_SCHEMA_PATH,
    DEFAULT_RESPONSE_SCHEMA_PATH,
    SemanticReviewError,
    canonical_json,
    load_schema,
    read_jsonl,
    sha256_path,
    sha256_text,
    utc_now,
    validate_instance,
    validation_errors,
)


COMPARISON_SCHEMA_VERSION = "airisk_jmcup_review_comparison_v1"
ADJUDICATION_SCHEMA_VERSION = COMPARISON_SCHEMA_VERSION
OPUS_INPUT_SCHEMA_VERSION = "airisk_jmcup_opus_adjudication_input_v1"
QC_SAMPLING_VERSION = "airisk_jmcup_qc_sampling_v1"
REVIEWER_ORDER_VERSION = "airisk_jmcup_reviewer_order_v1"
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
DEFAULT_CONSENSUS_CLEAN_QC_RATE = 0.10
DEFAULT_CONSENSUS_REJECT_QC_RATE = 0.05
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_COMPARISON_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{COMPARISON_SCHEMA_VERSION}.schema.json"
)
DEFAULT_OPUS_INPUT_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{OPUS_INPUT_SCHEMA_VERSION}.schema.json"
)


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
    requested_models = {record.get("requested_model") for record in all_records}
    providers = {record.get("provider") for record in all_records}
    if len(requested_models) != 1 or len(providers) != 1:
        raise SemanticReviewError(f"Expected one provider/model identity in {path}")
    metadata["provider"] = next(iter(providers))
    metadata["requested_model"] = next(iter(requested_models))
    metadata["model_family"] = _reviewer_model_family(
        metadata["provider"], metadata["requested_model"]
    )
    return completed, metadata


def _reviewer_model_family(provider: Any, requested_model: Any) -> str:
    if provider == "anthropic":
        return "anthropic"
    if provider == "gemini":
        return "google"
    if provider == "openrouter" and isinstance(requested_model, str):
        if requested_model.startswith("anthropic/"):
            return "anthropic"
        if requested_model.startswith("google/"):
            return "google"
    raise SemanticReviewError(
        f"Cannot establish Anthropic/Google model family for {provider!r}/{requested_model!r}"
    )


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
        "schema_version": COMPARISON_SCHEMA_VERSION,
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
            "reviewer_verdict_disagreement": (
                review_a["reviewer_verdict"] != review_b["reviewer_verdict"]
            ),
            "reviewer_confidence_disagreement": (
                review_a["reviewer_confidence"] != review_b["reviewer_confidence"]
            ),
            "selected_representative_disagreement": (
                review_a["selected_representative_occurrence_id"]
                != review_b["selected_representative_occurrence_id"]
            ),
            "materially_different_proposed_transformation_fields": materially_different,
            "materially_different_proposed_transformations": bool(materially_different),
        },
        "proposed_transformation_comparison": transformation_comparison,
        "reviewer_metadata_comparison": {
            field: {
                "reviewer_a": review_a[field],
                "reviewer_b": review_b[field],
                "disagreement": review_a[field] != review_b[field],
            }
            for field in (
                "source_fidelity",
                "rewrite_level",
                "reviewer_verdict",
                "reviewer_confidence",
            )
        },
        "adjudicator_fields": {
            "final_classification": None,
            "criterion_resolutions": {key: None for key in CRITERION_KEYS},
            "selected_transformation": None,
            "adjudication_notes": None,
        },
    }


def _seeded_digest(*, seed: str, domain: str, group_id: str) -> str:
    return hashlib.sha256(
        f"{domain}\0{seed}\0{group_id}".encode("utf-8")
    ).hexdigest()


def _sample_consensus_class(
    comparisons: Sequence[Mapping[str, Any]],
    *,
    classification: str,
    rate: float,
    seed: str,
) -> list[dict[str, Any]]:
    members = [
        item for item in comparisons if item["classification"] == classification
    ]
    sample_count = math.ceil(rate * len(members))
    ranked = sorted(
        (
            {
                "generation_group_id": item["generation_group_id"],
                "rank_digest": _seeded_digest(
                    seed=seed,
                    domain=f"{QC_SAMPLING_VERSION}:{classification}",
                    group_id=item["generation_group_id"],
                ),
            }
            for item in members
        ),
        key=lambda item: (item["rank_digest"], item["generation_group_id"]),
    )
    return [
        {**item, "rank": rank}
        for rank, item in enumerate(ranked[:sample_count], 1)
    ]


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def prepare_adjudication(
    review_a_path: Path,
    review_b_path: Path,
    *,
    output_dir: Path,
    blinded_payloads: Sequence[Mapping[str, Any]],
    seed: str,
    expected_group_ids: Sequence[str] | None = None,
    consensus_clean_qc_rate: float = DEFAULT_CONSENSUS_CLEAN_QC_RATE,
    consensus_reject_qc_rate: float = DEFAULT_CONSENSUS_REJECT_QC_RATE,
    response_schema_path: Path = DEFAULT_RESPONSE_SCHEMA_PATH,
    input_schema_path: Path = DEFAULT_INPUT_SCHEMA_PATH,
    comparison_schema_path: Path = DEFAULT_COMPARISON_SCHEMA_PATH,
    opus_input_schema_path: Path = DEFAULT_OPUS_INPUT_SCHEMA_PATH,
    overwrite: bool = False,
) -> dict[str, Any]:
    if not isinstance(seed, str) or not seed:
        raise SemanticReviewError("A non-empty explicit deterministic seed is required")
    for name, rate in (
        ("consensus_clean_qc_rate", consensus_clean_qc_rate),
        ("consensus_reject_qc_rate", consensus_reject_qc_rate),
    ):
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 <= rate <= 1:
            raise SemanticReviewError(f"{name} must be between 0 and 1 inclusive")

    reviews_a, metadata_a = _completed_reviews(review_a_path)
    reviews_b, metadata_b = _completed_reviews(review_b_path)
    if metadata_a["reviewer_run_id"] == metadata_b["reviewer_run_id"]:
        raise SemanticReviewError("Reviewer A and reviewer B must be separate run IDs")
    if {metadata_a["model_family"], metadata_b["model_family"]} != {
        "anthropic",
        "google",
    }:
        raise SemanticReviewError(
            "Comparison requires one Anthropic-family and one Google-family reviewer"
        )
    if set(reviews_a) != set(reviews_b):
        only_a = sorted(set(reviews_a) - set(reviews_b))
        only_b = sorted(set(reviews_b) - set(reviews_a))
        raise SemanticReviewError(
            f"Completed reviewer group sets differ; only A={only_a[:5]}, only B={only_b[:5]}"
        )
    if expected_group_ids is not None and set(reviews_a) != set(expected_group_ids):
        raise SemanticReviewError("Completed review sets do not match expected groups")

    payload_by_id: dict[str, dict[str, Any]] = {}
    input_schema = load_schema(input_schema_path)
    for value in blinded_payloads:
        payload = deepcopy(dict(value))
        validate_instance(payload, input_schema, label="blinded adjudication source payload")
        group_id = payload["generation_group_id"]
        if group_id in payload_by_id:
            raise SemanticReviewError(f"Duplicate blinded payload for {group_id}")
        payload_by_id[group_id] = payload
    if set(payload_by_id) != set(reviews_a):
        raise SemanticReviewError("Blinded payload group set differs from completed reviews")
    for label, records in (("A", reviews_a), ("B", reviews_b)):
        for group_id, record in records.items():
            expected_payload_sha = sha256_text(canonical_json(payload_by_id[group_id]))
            if record.get("input_payload_sha256") != expected_payload_sha:
                raise SemanticReviewError(
                    f"Reviewer {label} input payload hash differs for {group_id}"
                )

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

    comparison_schema = load_schema(comparison_schema_path)
    comparisons = []
    for group_id in sorted(reviews_a):
        comparison = compare_review_pair(reviews_a[group_id], reviews_b[group_id])
        validate_instance(comparison, comparison_schema, label="review comparison")
        comparisons.append(comparison)

    clean_sample = _sample_consensus_class(
        comparisons,
        classification="consensus_clean_candidate",
        rate=float(consensus_clean_qc_rate),
        seed=seed,
    )
    reject_sample = _sample_consensus_class(
        comparisons,
        classification="consensus_reject",
        rate=float(consensus_reject_qc_rate),
        seed=seed,
    )
    needs_ids = [
        item["generation_group_id"]
        for item in comparisons
        if item["classification"] == "needs_adjudication"
    ]
    selected_reasons = {
        group_id: "needs_adjudication" for group_id in needs_ids
    }
    selected_reasons.update(
        (item["generation_group_id"], "consensus_clean_candidate_qc")
        for item in clean_sample
    )
    selected_reasons.update(
        (item["generation_group_id"], "consensus_reject_qc")
        for item in reject_sample
    )

    opus_input_schema = load_schema(opus_input_schema_path)
    opus_payloads = []
    private_mappings = []
    for group_id in sorted(selected_reasons):
        order_digest = _seeded_digest(
            seed=seed,
            domain=REVIEWER_ORDER_VERSION,
            group_id=group_id,
        )
        source_pairs = (
            (("input_run_a", reviews_a[group_id], metadata_a),
             ("input_run_b", reviews_b[group_id], metadata_b))
            if int(order_digest[-1], 16) % 2 == 0
            else (("input_run_b", reviews_b[group_id], metadata_b),
                  ("input_run_a", reviews_a[group_id], metadata_a))
        )
        opus_payload = {
            "schema_version": OPUS_INPUT_SCHEMA_VERSION,
            "generation_group_id": group_id,
            "source_group": payload_by_id[group_id],
            "reviewer_a_response": deepcopy(
                source_pairs[0][1]["parsed_structured_response"]
            ),
            "reviewer_b_response": deepcopy(
                source_pairs[1][1]["parsed_structured_response"]
            ),
        }
        validate_instance(opus_payload, opus_input_schema, label="Opus input payload")
        opus_payloads.append(opus_payload)
        private_mappings.append(
            {
                "generation_group_id": group_id,
                "selection_reason": selected_reasons[group_id],
                "reviewer_order_digest": order_digest,
                "reviewer_a": {
                    "source_input_label": source_pairs[0][0],
                    "reviewer_run_id": source_pairs[0][2]["reviewer_run_id"],
                    "provider": source_pairs[0][2]["provider"],
                    "requested_model": source_pairs[0][2]["requested_model"],
                    "source_record_sha256": sha256_text(
                        canonical_json(source_pairs[0][1])
                    ),
                },
                "reviewer_b": {
                    "source_input_label": source_pairs[1][0],
                    "reviewer_run_id": source_pairs[1][2]["reviewer_run_id"],
                    "provider": source_pairs[1][2]["provider"],
                    "requested_model": source_pairs[1][2]["requested_model"],
                    "source_record_sha256": sha256_text(
                        canonical_json(source_pairs[1][1])
                    ),
                },
            }
        )

    output_dir = output_dir.resolve()
    paths = {
        "comparisons": output_dir / "criterion_level_comparison.jsonl",
        "summary": output_dir / "adjudication_preparation_summary.json",
        "opus_payloads": output_dir / "opus_adjudication_payloads.jsonl",
        "opus_manifest": output_dir / "opus_adjudication_manifest.json",
        "private_provenance": output_dir / "opus_adjudication_private_provenance.json",
    }
    existing = [path for path in paths.values() if path.exists()]
    if existing and not overwrite:
        raise SemanticReviewError(
            "Refusing to overwrite adjudication products: "
            + ", ".join(str(path) for path in existing)
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    paths["comparisons"].write_text(
        "".join(canonical_json(item) + "\n" for item in comparisons),
        encoding="utf-8",
        newline="\n",
    )
    paths["opus_payloads"].write_text(
        "".join(canonical_json(item) + "\n" for item in opus_payloads),
        encoding="utf-8",
        newline="\n",
    )

    classification_counts = dict(
        sorted(Counter(item["classification"] for item in comparisons).items())
    )
    public_manifest = {
        "schema_version": "airisk_jmcup_opus_adjudication_manifest_v1",
        "created_at_utc": utc_now(),
        "sampling_algorithm": QC_SAMPLING_VERSION,
        "reviewer_order_algorithm": REVIEWER_ORDER_VERSION,
        "seed_sha256": sha256_text(seed),
        "rates": {
            "consensus_clean_candidate": float(consensus_clean_qc_rate),
            "consensus_reject": float(consensus_reject_qc_rate),
        },
        "rounding": "ceil(rate * class_size)",
        "class_counts": classification_counts,
        "needs_adjudication_group_ids": needs_ids,
        "consensus_clean_qc_sample": clean_sample,
        "consensus_reject_qc_sample": reject_sample,
        "selected_group_count": len(opus_payloads),
        "selected_group_ids": [item["generation_group_id"] for item in opus_payloads],
        "payloads_path": str(paths["opus_payloads"]),
        "payloads_sha256": sha256_path(paths["opus_payloads"]),
        "model_visible_reviewer_identity_metadata": False,
    }
    _write_json(paths["opus_manifest"], public_manifest)
    private_provenance = {
        "schema_version": "airisk_jmcup_opus_private_provenance_v1",
        "created_at_utc": utc_now(),
        "deterministic_seed": seed,
        "deterministic_seed_sha256": sha256_text(seed),
        "reviewer_input_a": metadata_a,
        "reviewer_input_b": metadata_b,
        "reviewer_slot_mappings": private_mappings,
    }
    _write_json(paths["private_provenance"], private_provenance)

    summary = {
        "schema_version": "airisk_jmcup_adjudication_preparation_v2",
        "created_at_utc": utc_now(),
        "reviewer_input_a": metadata_a,
        "reviewer_input_b": metadata_b,
        "response_schema_path": str(response_schema_path.resolve()),
        "response_schema_sha256": sha256_path(response_schema_path),
        "input_schema_sha256": sha256_path(input_schema_path),
        "comparison_schema_sha256": sha256_path(comparison_schema_path),
        "opus_input_schema_sha256": sha256_path(opus_input_schema_path),
        "group_count": len(comparisons),
        "classification_counts": classification_counts,
        "flag_counts": {
            flag: sum(bool(item["flags"][flag]) for item in comparisons)
            for flag in (
                "any_h1_h7_disagreement",
                "any_uncertain",
                "source_fidelity_disagreement",
                "rewrite_level_disagreement",
                "reviewer_verdict_disagreement",
                "reviewer_confidence_disagreement",
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
                "least one H1-H7 judgement of no. Criterion disagreements remain "
                "explicitly unresolved."
            ),
            "needs_adjudication": "Every other combination.",
            "verdict_labels_averaged": False,
        },
        "cross_group_semantic_duplicate_removal_performed": False,
        "comparison_jsonl": str(paths["comparisons"]),
        "comparison_jsonl_sha256": sha256_path(paths["comparisons"]),
        "opus_adjudication_manifest": str(paths["opus_manifest"]),
        "opus_adjudication_manifest_sha256": sha256_path(paths["opus_manifest"]),
        "private_provenance": str(paths["private_provenance"]),
        "private_provenance_sha256": sha256_path(paths["private_provenance"]),
    }
    _write_json(paths["summary"], summary)
    return summary
