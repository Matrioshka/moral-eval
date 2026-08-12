"""Deterministic structural, duplicate and coverage audits for future transforms."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .core import (
    REPO_ROOT,
    SemanticReviewError,
    load_schema,
    read_jsonl,
    sha256_path,
    sha256_text,
    validate_instance,
)


TRANSFORMATION_SCHEMA_VERSION = "airisk_jmcup_transformation_record_v2"
POST_TRANSFORMATION_AUDIT_SCHEMA_VERSION = (
    "airisk_jmcup_post_transformation_audit_v1"
)
DEFAULT_TRANSFORMATION_SCHEMA_PATH = (
    REPO_ROOT / "schemas" / f"{TRANSFORMATION_SCHEMA_VERSION}.schema.json"
)
DEFAULT_AUDIT_SCHEMA_PATH = (
    REPO_ROOT
    / "schemas"
    / f"{POST_TRANSFORMATION_AUDIT_SCHEMA_VERSION}.schema.json"
)
DEFAULT_NEAR_DUPLICATE_THRESHOLD = 0.80
TOKEN_RE = re.compile(r"[a-z0-9]+")


def validate_transformation_record(
    record: Mapping[str, Any], schema: Mapping[str, Any]
) -> list[str]:
    """Check deterministic shape/matching invariants, not semantic truth."""

    from .core import validation_errors

    errors = validation_errors(record, schema)
    if record.get("schema_version") == "airisk_jmcup_transformation_record_v2":
        from .transformation import validate_canonical_transformation_record

        return validate_canonical_transformation_record(record, schema)
    conditions = record.get("conditions")
    if not isinstance(conditions, Mapping):
        return sorted(set(errors))
    expected = {
        "matched_baseline": {
            "pressure_text": None,
            "evidence_text": None,
            "supported_action": "bounded",
        },
        "unsupported_pressure": {
            "evidence_text": None,
            "supported_action": "bounded",
        },
        "genuine_evidence": {
            "pressure_text": None,
            "supported_action": "broader",
        },
    }
    scenario_texts = []
    for condition_id, invariants in expected.items():
        condition = conditions.get(condition_id)
        if not isinstance(condition, Mapping):
            continue
        if condition.get("condition_id") != condition_id:
            errors.append(f"{condition_id} condition_id does not match its slot")
        scenario_texts.append(condition.get("scenario_text"))
        for field, required in invariants.items():
            if condition.get(field) != required:
                errors.append(f"{condition_id}.{field} must be {required!r}")
    pressure = conditions.get("unsupported_pressure")
    if isinstance(pressure, Mapping) and not isinstance(
        pressure.get("pressure_text"), str
    ):
        errors.append("unsupported_pressure.pressure_text must be substantive text")
    evidence = conditions.get("genuine_evidence")
    if isinstance(evidence, Mapping) and not isinstance(
        evidence.get("evidence_text"), str
    ):
        errors.append("genuine_evidence.evidence_text must be substantive text")
    if len(scenario_texts) == 3 and len(set(scenario_texts)) != 1:
        errors.append(
            "condition scenario_text fields must be byte-identical; pressure and "
            "evidence belong only in their dedicated fields"
        )
    return sorted(set(errors))


def _normalise_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _audit_text(record: Mapping[str, Any]) -> str:
    if record.get("schema_version") == "airisk_jmcup_transformation_record_v2":
        fields = [
            record["bounded_action"],
            record["broader_action"],
            record["baseline_blocker"],
            record["shared_scenario_text"],
            record["pressure_text"],
            record["evidence_text"],
        ]
    else:
        conditions = record["conditions"]
        fields = [
            record["bounded_action"],
            record["broader_action"],
            record["baseline_blocker"],
            conditions["matched_baseline"]["scenario_text"],
            conditions["unsupported_pressure"]["pressure_text"],
            conditions["genuine_evidence"]["evidence_text"],
        ]
    return "\n".join(_normalise_text(value) for value in fields)


def _token_set(value: str) -> set[str]:
    return set(TOKEN_RE.findall(value))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _count_map(values: Sequence[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def build_post_transformation_audit(
    records: Sequence[Mapping[str, Any]],
    *,
    near_duplicate_threshold: float = DEFAULT_NEAR_DUPLICATE_THRESHOLD,
) -> dict[str, Any]:
    if isinstance(near_duplicate_threshold, bool) or not isinstance(
        near_duplicate_threshold, (int, float)
    ) or not 0 <= near_duplicate_threshold <= 1:
        raise SemanticReviewError("Near-duplicate threshold must be between 0 and 1")
    ordered = sorted(records, key=lambda item: item["transformation_id"])
    texts = {item["transformation_id"]: _audit_text(item) for item in ordered}
    hashes: dict[str, list[str]] = defaultdict(list)
    for transformation_id, text in texts.items():
        hashes[sha256_text(text)].append(transformation_id)
    exact_clusters = [
        {"content_sha256": digest, "transformation_ids": sorted(ids)}
        for digest, ids in sorted(hashes.items())
        if len(ids) > 1
    ]
    token_sets = {
        transformation_id: _token_set(text)
        for transformation_id, text in texts.items()
    }
    near_pairs = []
    identifiers = sorted(texts)
    for left_index, left in enumerate(identifiers):
        for right in identifiers[left_index + 1 :]:
            if sha256_text(texts[left]) == sha256_text(texts[right]):
                continue
            similarity = _jaccard(token_sets[left], token_sets[right])
            if similarity >= near_duplicate_threshold:
                near_pairs.append(
                    {
                        "left_transformation_id": left,
                        "right_transformation_id": right,
                        "token_jaccard_similarity": round(similarity, 6),
                        "review_required": True,
                    }
                )

    domain_labels = [
        label for item in ordered for label in item["coverage_metadata"]["domain_labels"]
    ]
    risk_labels = [
        label for item in ordered for label in item["coverage_metadata"]["risk_labels"]
    ]
    return {
        "schema_version": POST_TRANSFORMATION_AUDIT_SCHEMA_VERSION,
        "created_at_utc": (
            max(item["provenance"]["created_at_utc"] for item in ordered)
            if ordered else "1970-01-01T00:00:00+00:00"
        ),
        "transformation_count": len(ordered),
        "duplicate_audit": {
            "normalisation": "Unicode NFKC, casefold, collapse whitespace",
            "near_duplicate_method": "token_set_jaccard",
            "near_duplicate_threshold": float(near_duplicate_threshold),
            "exact_duplicate_clusters": exact_clusters,
            "near_duplicate_pairs": near_pairs,
        },
        "coverage_audit": {
            "by_transformation_family": _count_map(
                [item["transformation_family"] for item in ordered]
            ),
            "by_pressure_type": _count_map(
                [item["pressure_type"] for item in ordered]
            ),
            "by_evidence_type": _count_map(
                [item["evidence_type"] for item in ordered]
            ),
            "by_evidence_strength": _count_map(
                [item["evidence_strength"] for item in ordered]
            ),
            "by_domain_label": _count_map(domain_labels),
            "by_risk_label": _count_map(risk_labels),
        },
        "target_dataset_n_applied": False,
        "automatic_duplicate_exclusion_performed": False,
        "semantic_truth_deterministically_established": False,
    }


def audit_transformation_file(
    *,
    transformations_path: Path,
    output_path: Path,
    near_duplicate_threshold: float = DEFAULT_NEAR_DUPLICATE_THRESHOLD,
    transformation_schema_path: Path | None = None,
    audit_schema_path: Path = DEFAULT_AUDIT_SCHEMA_PATH,
    overwrite: bool = False,
) -> dict[str, Any]:
    if output_path.exists() and not overwrite:
        raise SemanticReviewError(f"Refusing to overwrite audit: {output_path}")
    records = read_jsonl(transformations_path)
    seen = set()
    for record in records:
        transformation_id = record.get("transformation_id")
        if transformation_id in seen:
            raise SemanticReviewError(f"Duplicate transformation ID: {transformation_id}")
        seen.add(transformation_id)
        if transformation_schema_path is None:
            version = record.get("schema_version")
            if version not in {
                "airisk_jmcup_transformation_record_v1",
                "airisk_jmcup_transformation_record_v2",
            }:
                raise SemanticReviewError(
                    f"Unsupported transformation schema version: {version!r}"
                )
            schema_path = REPO_ROOT / "schemas" / f"{version}.schema.json"
        else:
            schema_path = transformation_schema_path
        errors = validate_transformation_record(record, load_schema(schema_path))
        if errors:
            raise SemanticReviewError(
                f"Transformation {transformation_id} failed local structural "
                f"validation: {errors}"
            )
    report = build_post_transformation_audit(
        records, near_duplicate_threshold=near_duplicate_threshold
    )
    validate_instance(report, load_schema(audit_schema_path), label="audit report")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "transformations_path": str(transformations_path.resolve()),
        "transformations_sha256": sha256_path(transformations_path),
        "report_path": str(output_path.resolve()),
        "report_sha256": sha256_path(output_path),
        "transformation_count": len(records),
        "semantic_truth_deterministically_established": False,
    }
